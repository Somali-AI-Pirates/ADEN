"""The three frozen retrieval arms; this module never opens evaluation references."""

import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

import numpy as np
import tiktoken

from app.generation.context import build_context
from experimental.u_10o0o.bm25 import BM25Index, tokenize
from shared.contracts import Chunk, RetrievedChunk

from .common import (
    ARMS,
    CONFIG,
    CONTEXT_TOKENS,
    EMBED_MODEL,
    EMBED_REVISION,
    FINAL_K,
    FIRST_K,
    MAX_INPUT,
    POOL_K,
    RERANK_MODEL,
    RERANK_REVISION,
    RRF_K,
    append_json,
    check_runtime,
    code_tokens,
    codes_in,
    file_sha,
    load_questions,
    logger,
    normalize_code,
    object_sha,
    read_json,
    read_jsonl,
    runtime_files,
    text_sha,
    write_json,
)
from .models import (
    embedder,
    model_assets,
    predict_scores,
    reranker,
    synchronize,
    token_lengths,
)

MULTIPLE = re.compile(r"각각|비교|차이|대비|두\s*(?:건|사건|예약|계약)|둘")
EXCLUSION = re.compile(r"제외|말고|아닌|빼고")


def code_tokenize(text):
    return tokenize(text) + [code.lower() for code in code_tokens(text)]


def rrf(lists, limit):
    scores = defaultdict(float)
    for rows in lists:
        for rank, unit_id in enumerate(rows, 1):
            scores[unit_id] += 0.5 / (RRF_K + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))[:limit], scores


class Store:
    def __init__(self, index_dir, *, vectors=True):
        self.directory = Path(index_dir)
        self.database = self.directory / "corpus.sqlite3"
        uri = "file:" + quote(str(self.database.resolve())) + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            self.pages = {
                r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM pages")
            }
            self.units = [
                dict(r) for r in conn.execute("SELECT * FROM units ORDER BY unit_id")
            ]
            self.occurrences = defaultdict(list)
            for row in conn.execute(
                "SELECT * FROM occurrences ORDER BY unit_id,source_id,line_start,line_end"
            ):
                self.occurrences[row["unit_id"]].append(dict(row))
            self.code_cases = defaultdict(set)
            for row in conn.execute("SELECT code_norm,case_id FROM code_links"):
                self.code_cases[normalize_code(row["code_norm"])].add(row["case_id"])
        self.by_id = {row["unit_id"]: row for row in self.units}
        self.arm_indices = {
            arm: np.array([i for i, row in enumerate(self.units) if row["arm"] == arm])
            for arm in ("A", "B")
        }
        if not all(len(rows) for rows in self.arm_indices.values()):
            raise ValueError("Both A and B corpus units are required.")
        self.page_cases = {
            occ["source_id"]: occ["case_id"]
            for unit in self.units
            if unit["arm"] == "A"
            for occ in self.occurrences[unit["unit_id"]]
            if occ["case_id"]
        }
        self.case_units = defaultdict(list)
        for row in self.units:
            if row["arm"] != "B" or row["kind"] != "record":
                continue
            for occ in self.occurrences[row["unit_id"]]:
                if occ["case_id"]:
                    self.case_units[occ["case_id"]].append(
                        (occ["role"] or "", occ["source_id"], row["unit_id"])
                    )
        for case, entries in self.case_units.items():
            self.case_units[case] = sorted(set(entries))
        if vectors:
            corpus_manifest = read_json(self.directory / "corpus-manifest.json")
            if corpus_manifest.get("status") != "complete" or corpus_manifest.get(
                "corpus_sha256"
            ) != file_sha(self.database):
                raise ValueError("Published corpus fingerprint mismatch.")
            manifest = read_json(self.directory / "vectors-manifest.json")
            if manifest["status"] != "complete":
                raise ValueError("Vectors are incomplete.")
            if manifest["identity"]["corpus_sha256"] != file_sha(self.database):
                raise ValueError("Corpus/vector fingerprint mismatch.")
            if manifest["identity"]["config_sha256"] != object_sha(CONFIG):
                raise ValueError("Vector configuration mismatch.")
            if manifest["identity"]["unit_ids_sha256"] != object_sha(
                [r["unit_id"] for r in self.units]
            ):
                raise ValueError("Vector row ordering mismatch.")
            if manifest["vectors_sha256"] != file_sha(self.directory / "vectors.npy"):
                raise ValueError("Vector data changed.")
            if manifest["identity"]["embedding_assets"] != model_assets(
                EMBED_MODEL, EMBED_REVISION
            ):
                raise ValueError("Embedding asset bytes changed.")
            expected_runtime = {
                name: file_sha(Path(__file__).with_name(name))
                for name in ("models.py", "common.py")
            }
            if manifest["identity"]["embedding_runtime"] != expected_runtime:
                raise ValueError("Embedding runtime changed since vector build.")
            self.vectors = np.load(self.directory / "vectors.npy", mmap_mode="r")
            self.arm_vectors = {
                arm: np.asarray(self.vectors[indices])
                for arm, indices in self.arm_indices.items()
            }

    def occurrence(self, unit_id, question, cases=()):
        rows = self.occurrences[unit_id]
        if not rows:
            raise ValueError(f"Unit has no source: {unit_id}")
        literal_cases = set(codes_in(question))
        targets = set(cases) | literal_cases
        preferred = [
            row
            for row in rows
            if row["case_id"] in targets
            or self.page_cases.get(row["source_id"]) in targets
        ]
        return (preferred or rows)[0]

    def page_info(self, unit_id, question, cases=(), score=0.0):
        occ = self.occurrence(unit_id, question, cases)
        page = self.pages[occ["source_id"]]
        return {
            "unit_id": unit_id,
            "source_id": occ["source_id"],
            "book_id": page["book_id"],
            "page": page["page"],
            "score": float(score),
        }

    def relation(self, question):
        codes = codes_in(question)
        result = {
            "status": "none",
            "cases": [],
            "matched_codes": [],
            "proposed_units": [],
        }
        if not codes:
            return result
        if EXCLUSION.search(question):
            return {**result, "status": "unsupported_exclusion"}
        unknown = [code for code in codes if code not in self.code_cases]
        matches = {
            code: sorted(self.code_cases[code])
            for code in codes
            if code in self.code_cases
        }
        result["matched_codes"] = list(matches)
        if unknown:
            return {**result, "status": "unknown_code"}
        if any(len(cases) != 1 for cases in matches.values()):
            return {**result, "status": "ambiguous"}
        cases = list(dict.fromkeys(cases[0] for cases in matches.values()))
        if len(cases) > 2:
            return {**result, "status": "too_many_targets"}
        if len(cases) == 2 and not MULTIPLE.search(question):
            return {**result, "status": "conflict"}
        if not cases:
            return result
        # The evidence links are source-derived. No title or page score grants a case identity.
        lists = [
            list(dict.fromkeys(unit_id for _, _, unit_id in self.case_units[case]))
            for case in cases
        ]
        proposed = []
        for offset in range(max(map(len, lists), default=0)):
            for units in lists:
                if offset < len(units) and units[offset] not in proposed:
                    proposed.append(units[offset])
                    if len(proposed) == 8:
                        break
            if len(proposed) == 8:
                break
        return {
            **result,
            "status": "unique" if len(cases) == 1 else "multiple_targets",
            "cases": cases,
            "proposed_units": proposed,
        }


class Engine:
    def __init__(self, index_dir, device):
        started = perf_counter()
        self.device = device
        self.store = Store(index_dir)
        self.keyword = {
            arm: BM25Index(
                [
                    {
                        "unit_id": self.store.units[i]["unit_id"],
                        "text": self.store.units[i]["text"],
                    }
                    for i in indices
                ],
                tokenizer=code_tokenize,
            )
            for arm, indices in self.store.arm_indices.items()
        }
        self.embedder = embedder(device)
        self.reranker = reranker(device)
        self.context_tokenizer = tiktoken.get_encoding("cl100k_base")
        self.model_assets = {
            "embedding": model_assets(EMBED_MODEL, EMBED_REVISION),
            "reranker": model_assets(RERANK_MODEL, RERANK_REVISION),
        }
        self.load_seconds = perf_counter() - started

    def retrieve(self, row, arm):
        if arm not in ARMS:
            raise ValueError("Unknown arm.")
        question = row["question"]
        base_arm = "A" if arm == "A" else "B"
        started = perf_counter()
        stage = perf_counter()
        query = self.embedder.encode_query(
            [question],
            batch_size=1,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        synchronize(self.device)
        elapsed = {"query_embedding": (perf_counter() - stage) * 1000}
        stage = perf_counter()
        sparse_rows = self.keyword[base_arm].search(question, top_k=FIRST_K)
        sparse = [r["unit_id"] for r, _ in sparse_rows]
        elapsed["bm25"] = (perf_counter() - stage) * 1000
        stage = perf_counter()
        similarities = self.store.arm_vectors[base_arm] @ query
        local_order = np.lexsort((np.arange(len(similarities)), -similarities))[
            :FIRST_K
        ]
        dense = [
            self.store.units[self.store.arm_indices[base_arm][i]]["unit_id"]
            for i in local_order
        ]
        elapsed["dense"] = (perf_counter() - stage) * 1000
        stage = perf_counter()
        hybrid, hybrid_scores = rrf([sparse, dense], FIRST_K * 2)
        relation = (
            self.store.relation(question)
            if arm == "C"
            else {
                "status": "not_applicable",
                "cases": [],
                "matched_codes": [],
                "proposed_units": [],
            }
        )
        if relation["proposed_units"]:
            pool, fused_scores = rrf([relation["proposed_units"], hybrid], POOL_K)
        else:
            pool, fused_scores = hybrid[:POOL_K], hybrid_scores
        elapsed["relation"] = (perf_counter() - stage) * 1000
        stage = perf_counter()
        scores, work = predict_scores(
            self.reranker,
            question,
            [self.store.by_id[u]["text"] for u in pool],
            self.device,
        )
        ranked = sorted(
            zip(pool, map(float, scores)), key=lambda item: (-item[1], item[0])
        )
        elapsed["rerank"] = (perf_counter() - stage) * 1000
        stage = perf_counter()
        cases = relation["cases"]
        pool_info = [
            self.store.page_info(u, question, cases, fused_scores[u]) for u in pool
        ]
        ranked_info = [
            self.store.page_info(u, question, cases, score) for u, score in ranked
        ]
        candidates = []
        seen = set()
        for item in ranked_info:
            source = item["source_id"]
            if source in seen:
                continue
            seen.add(source)
            page = self.store.pages[source]
            candidates.append(
                RetrievedChunk(
                    chunk=Chunk(
                        chunk_id=source,
                        book_id=page["book_id"],
                        file_name=page["file_name"],
                        page=page["page"],
                        text=page["text"],
                    ),
                    rank=len(candidates) + 1,
                    score=item["score"],
                    retriever=arm,
                )
            )
        context = build_context(
            candidates,
            FINAL_K,
            CONTEXT_TOKENS,
            lambda text: len(
                self.context_tokenizer.encode(text, disallowed_special=())
            ),
        )
        final_pages = [
            {
                "source_id": c.chunk.chunk_id,
                "book_id": c.chunk.book_id,
                "page": c.chunk.page,
            }
            for c in context.final_chunks
        ]
        elapsed["restore"] = (perf_counter() - stage) * 1000
        elapsed["total"] = (perf_counter() - started) * 1000
        query_length = token_lengths(self.embedder.tokenizer, ["query: " + question])[0]
        work.update(
            query_tokens=min(query_length, MAX_INPUT),
            query_raw_tokens=query_length,
            query_truncated=query_length > MAX_INPUT,
        )
        trace = {
            "id": row["id"],
            "arm": arm,
            "question_sha256": text_sha(question),
            "pool": pool_info,
            "ranked": ranked_info,
            "final_pages": final_pages,
            "context_sha256": context.context_hash,
            "context_tokens": context.token_count,
            "excluded_sources": context.excluded_chunk_ids,
            "relation": relation,
            "timing_ms": elapsed,
            "work": work,
            "bm25_units": sparse,
            "dense_units": dense,
            "hybrid_units": hybrid,
            "relation_displaced_units": [u for u in hybrid[:POOL_K] if u not in pool],
            "device": self.device,
        }
        if (
            len(pool) > POOL_K
            or len(final_pages) > FINAL_K
            or context.token_count > CONTEXT_TOKENS
        ):
            raise AssertionError("Frozen evidence budget exceeded.")
        return trace, {
            "id": row["id"],
            "arm": arm,
            "pages": [
                {"book_id": c.chunk.book_id, "page": c.chunk.page, "text": c.chunk.text}
                for c in context.final_chunks
            ],
        }


def run_retrieval(index_dir, questions_path, output_dir):
    """Write every result before a separate score command is allowed to load references."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    log = logger(output)
    questions = load_questions(questions_path)
    stamps = runtime_files()
    manifest = {
        "status": "running",
        "questions_sha256": file_sha(questions_path),
        "corpus_sha256": file_sha(Path(index_dir) / "corpus.sqlite3"),
        "vectors_sha256": file_sha(Path(index_dir) / "vectors.npy"),
        "vectors_manifest_sha256": file_sha(Path(index_dir) / "vectors-manifest.json"),
        "config": CONFIG,
        "runtime_files": stamps,
        "code_sha256": object_sha(stamps),
        "question_ids": [r["id"] for r in questions],
        "arms": list(ARMS),
        "device": "cuda",
        "external_api_calls": 0,
    }
    write_json(output / "manifest.json", manifest)
    engine = Engine(index_dir, "cuda")
    for arm in ARMS:
        engine.retrieve(questions[0], arm)
    log("Pinned GPU models and all three arms warmed.")
    for number, row in enumerate(questions, 1):
        shift = (number - 1) % len(ARMS)
        for arm in ARMS[shift:] + ARMS[:shift]:
            trace, context = engine.retrieve(row, arm)
            append_json(output / "traces.jsonl", trace)
            append_json(output / "contexts.jsonl", context)
        if number % 10 == 0 or number == len(questions):
            log(f"Retrieval saved for {number}/{len(questions)} questions.")
    check_runtime(stamps)
    if file_sha(engine.store.database) != manifest["corpus_sha256"]:
        raise ValueError("Corpus changed during retrieval.")
    if file_sha(Path(index_dir) / "vectors.npy") != manifest["vectors_sha256"]:
        raise ValueError("Vectors changed during retrieval.")
    if file_sha(questions_path) != manifest["questions_sha256"]:
        raise ValueError("Questions changed during retrieval.")
    manifest.update(
        status="complete",
        load_seconds=engine.load_seconds,
        model_assets=engine.model_assets,
        trace_count=len(questions) * len(ARMS),
        traces_sha256=file_sha(output / "traces.jsonl"),
        contexts_sha256=file_sha(output / "contexts.jsonl"),
    )
    write_json(output / "manifest.json", manifest)
    log("All GPU retrievals saved. References were not opened.")
    return manifest


def run_timing(index_dir, evaluation_dir, gpu_dir, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    log = logger(output)
    evaluation_dir, gpu_dir = Path(evaluation_dir), Path(gpu_dir)
    dataset = read_json(evaluation_dir / "manifest.json")
    questions_path = evaluation_dir / "questions.json"
    questions = {r["id"]: r for r in load_questions(questions_path)}
    timing_ids = dataset["timing_ids"]
    if len(timing_ids) != 30 or len(set(timing_ids)) != 30:
        raise ValueError("CPU timing requires the frozen 30 unique question IDs.")
    gpu_manifest = read_json(gpu_dir / "manifest.json")
    if gpu_manifest["status"] != "complete":
        raise ValueError("GPU quality run must finish first.")
    if gpu_manifest["questions_sha256"] != file_sha(questions_path):
        raise ValueError("GPU and CPU question inputs differ.")
    if dataset["questions_sha256"] != file_sha(questions_path):
        raise ValueError("Evaluation manifest/question mismatch.")
    if any(qid not in dataset["split_ids"]["validation"] for qid in timing_ids):
        raise ValueError("Timing questions must come from the frozen validation split.")
    for ids in dataset["categories"].values():
        if len(set(ids) & set(timing_ids)) != 5:
            raise ValueError("CPU timing must contain five questions per category.")
    if file_sha(gpu_dir / "traces.jsonl") != gpu_manifest["traces_sha256"]:
        raise ValueError("GPU traces changed.")
    gpu_traces = {(r["id"], r["arm"]): r for r in read_jsonl(gpu_dir / "traces.jsonl")}
    stamps = runtime_files()
    if object_sha(stamps) != gpu_manifest["code_sha256"]:
        raise ValueError("Runtime changed between GPU and CPU evaluation.")
    manifest = {
        "status": "running",
        "config": CONFIG,
        "device": "cpu",
        "threads": 4,
        "repeats": 3,
        "arms": list(ARMS),
        "timing_ids": timing_ids,
        "questions_sha256": file_sha(questions_path),
        "evaluation_manifest_sha256": file_sha(evaluation_dir / "manifest.json"),
        "gpu_manifest_sha256": file_sha(gpu_dir / "manifest.json"),
        "corpus_sha256": file_sha(Path(index_dir) / "corpus.sqlite3"),
        "vectors_sha256": file_sha(Path(index_dir) / "vectors.npy"),
        "vectors_manifest_sha256": file_sha(Path(index_dir) / "vectors-manifest.json"),
        "runtime_files": stamps,
        "code_sha256": object_sha(stamps),
    }
    if manifest["corpus_sha256"] != gpu_manifest["corpus_sha256"]:
        raise ValueError("GPU/CPU corpus mismatch.")
    for field in ("vectors_sha256", "vectors_manifest_sha256"):
        if manifest[field] != gpu_manifest[field]:
            raise ValueError(f"GPU/CPU {field} mismatch.")
    write_json(output / "manifest.json", manifest)
    engine = Engine(index_dir, "cpu")
    if engine.model_assets != gpu_manifest["model_assets"]:
        raise ValueError("GPU/CPU model asset bytes differ.")
    warm_id = dataset["split_ids"]["development"][0]
    for arm in ARMS:
        engine.retrieve(questions[warm_id], arm)
    log("CPU warmed on a development question; starting independent per-arm requests.")
    traces, differences = [], []
    for repeat in range(3):
        for number, qid in enumerate(timing_ids):
            shift = (number + repeat) % len(ARMS)
            for arm in ARMS[shift:] + ARMS[:shift]:
                trace, _ = engine.retrieve(questions[qid], arm)
                trace["repeat"] = repeat + 1
                traces.append(trace)
                append_json(output / "traces.jsonl", trace)
                gpu = gpu_traces[qid, arm]
                if trace["context_sha256"] != gpu["context_sha256"]:
                    differences.append(
                        {
                            "id": qid,
                            "arm": arm,
                            "repeat": repeat + 1,
                            "gpu_sha": gpu["context_sha256"],
                            "cpu_sha": trace["context_sha256"],
                            "same_final_sources": trace["final_pages"]
                            == gpu["final_pages"],
                        }
                    )
            if (number + 1) % 5 == 0:
                log(f"CPU repeat {repeat + 1}/3 saved {number + 1}/30 questions.")
    summary = {
        "arms": {},
        "timing_ids": timing_ids,
        "gpu_cpu_context_differences": differences,
    }
    for arm in ARMS:
        selected = [t for t in traces if t["arm"] == arm]
        summary["arms"][arm] = {
            key: {
                "count": len(selected),
                "p50_ms": float(np.median([t["timing_ms"][key] for t in selected])),
                "p95_ms": float(
                    np.percentile([t["timing_ms"][key] for t in selected], 95)
                ),
            }
            for key in selected[0]["timing_ms"]
        }
    check_runtime(stamps)
    if file_sha(engine.store.database) != manifest["corpus_sha256"]:
        raise ValueError("Corpus changed during timing.")
    if (
        file_sha(evaluation_dir / "manifest.json")
        != manifest["evaluation_manifest_sha256"]
    ):
        raise ValueError("Evaluation split/timing selection changed.")
    for path, field in (
        (questions_path, "questions_sha256"),
        (Path(index_dir) / "vectors.npy", "vectors_sha256"),
        (Path(index_dir) / "vectors-manifest.json", "vectors_manifest_sha256"),
    ):
        if file_sha(path) != manifest[field]:
            raise ValueError(f"Timing input changed: {field}")
    write_json(output / "summary.json", summary)
    manifest.update(
        status="complete",
        load_seconds=engine.load_seconds,
        model_assets=engine.model_assets,
        trace_count=len(traces),
        traces_sha256=file_sha(output / "traces.jsonl"),
    )
    write_json(output / "manifest.json", manifest)
    log("CPU timing complete; no query-result caching was used.")
    return summary
