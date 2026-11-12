"""Content retrieval with explicit chunk -> original table mapping."""

import threading
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter

import numpy as np

from app.generation.context import build_context
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.table_rag.hg import (
    complete_sources_with_dates as complete_sources,
)
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.embedding_ablation.__main__ import (
    DEFAULT_RUN,
    encode,
    validate_assets,
)
from experimental.u_10o0o.embedding_ablation.assets import MODELS
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import page_key
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
    LegacyBaseline,
    KURE,
    MODELS as RERANK_MODELS,
)
from experimental.u_10o0o.table_rag.store import TableStore


def model_loader(device):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    name, revision = MODELS["kure_v3"]
    kwargs = {"dtype": torch.bfloat16}
    if device == "shared_cuda":
        torch.cuda.set_per_process_memory_fraction(0.22, 0)
        kwargs.update(
            device_map="auto",
            max_memory={0: "1000MiB", "cpu": "5GiB"},
            attn_implementation="sdpa",
        )
        model = SentenceTransformer(
            name,
            revision=revision,
            local_files_only=True,
            config_kwargs={"use_cache": False},
            model_kwargs=kwargs,
        )
    else:
        model = SentenceTransformer(
            name,
            revision=revision,
            device="cpu",
            local_files_only=True,
            model_kwargs=kwargs,
        )
    return model


def vectors(directory, device):
    directory = Path(directory)
    TableStore(directory)
    texts = [c["text"] for c in read_json(directory / "table-chunks.json")]
    state_path = directory / "vectors.json"
    state = (
        read_json(state_path)
        if state_path.exists()
        else {
            "status": "running",
            "model": MODELS["kure_v3"],
            "device": device,
            "dtype": "bfloat16",
            "threads": 2,
            "batch": 1,
            "chunks": [],
            "seconds": 0,
            "input_sha256": file_sha(directory / "table-chunks.json"),
            "max_input_tokens": 2048,
        }
    )
    if (
        state["input_sha256"] != file_sha(directory / "table-chunks.json")
        or state["device"] != device
    ):
        raise ValueError("Embedding inputs or execution device changed")
    completed = sum(c["count"] for c in state["chunks"])
    for c in state["chunks"]:
        if file_sha(directory / c["file"]) != c["sha256"]:
            raise ValueError("Saved table vector chunk changed")
    if state["status"] == "complete":
        print("Table vectors already complete", flush=True)
        return
    model = model_loader(device)
    model.max_seq_length = 2048
    stats = {"inputs": 0, "raw_tokens": 0, "truncated": 0}
    for begin in range(completed, len(texts), 32):
        batch = texts[begin : begin + 32]
        tokens = model.tokenizer(
            [model.prompts.get("document", "") + text for text in batch],
            truncation=False,
            verbose=False,
        )["input_ids"]
        stats = {
            "inputs": len(batch),
            "raw_tokens": sum(map(len, tokens)),
            "truncated": sum(len(t) > 2048 for t in tokens),
        }
        started = perf_counter()
        result = encode(model, batch, kind="document", batch_size=1)
        filename = f"tables-{begin:05d}-{begin + len(batch):05d}.npy"
        np.save(directory / filename, result, allow_pickle=False)
        elapsed = perf_counter() - started
        state["chunks"].append(
            {
                "file": filename,
                "count": len(batch),
                "start": begin,
                "sha256": file_sha(directory / filename),
                "seconds": elapsed,
                "tokens": stats,
            }
        )
        state["seconds"] += elapsed
        write_json(state_path, state)
        print(
            f"IS content vectors {begin + len(batch)}/{len(texts)}, last batch {elapsed:.1f}s",
            flush=True,
        )
    combined = np.concatenate(
        [np.load(directory / c["file"], allow_pickle=False) for c in state["chunks"]]
    )
    np.save(directory / "table-vectors.npy", combined, allow_pickle=False)
    state.update(
        status="complete", output_sha256=file_sha(directory / "table-vectors.npy")
    )
    write_json(state_path, state)


def source_id(page):
    return f"{page_key(page)[0]}:p{page_key(page)[1]:04d}"


def merge_pages(groups, limit=20):
    result, seen = [], set()
    for rank in range(max(map(len, groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            page, score = group[rank]
            key = page_key(page)
            if key not in seen:
                result.append((page, score))
                seen.add(key)
                if len(result) == limit:
                    return result
    return result


def distinct_page_units(ranked, limit=20):
    """A raw page and its table do not consume two page-candidate slots."""
    result, seen = [], set()
    for unit, score in ranked:
        key = page_key(unit)
        if key not in seen:
            result.append((unit, score))
            seen.add(key)
            if len(result) == limit:
                break
    return result


class Retriever:
    def __init__(self, directory, *, device="cpu"):
        import torch
        from sentence_transformers import CrossEncoder

        self._prepare_inputs(directory)
        assets = validate_assets(DEFAULT_RUN, "kure_v3")
        doc_vectors = np.load(ROOT / assets["document_path"], allow_pickle=False)
        state = read_json(self.directory / "vectors.json")
        if (
            state["status"] != "complete"
            or file_sha(self.directory / "table-vectors.npy") != state["output_sha256"]
        ):
            raise ValueError("Table embeddings incomplete")
        table_vectors = np.load(
            self.directory / "table-vectors.npy", allow_pickle=False
        )
        if state["input_sha256"] != file_sha(self.directory / "table-chunks.json"):
            raise ValueError("Table embedding row identity changed")
        if table_vectors.shape != (len(self.chunks), doc_vectors.shape[1]):
            raise ValueError("Table vector shape differs from source units/model")
        if len(doc_vectors) != len(self.pages) or not np.isfinite(table_vectors).all():
            raise ValueError("Invalid source/vector alignment")
        if not np.allclose(np.linalg.norm(table_vectors, axis=1), 1, atol=0.002):
            raise ValueError("Table vectors must be L2 normalized")
        self.vectors = np.concatenate((doc_vectors, table_vectors))
        self.embedder = model_loader(device)
        self.embedder.max_seq_length = 2048
        self.reranker_device = "cuda" if device == "cpu_gpu_rerank" else "cpu"
        if self.reranker_device == "cuda":
            torch.cuda.set_per_process_memory_fraction(0.12, 0)
        self.reranker = CrossEncoder(
            KURE,
            revision=RERANK_MODELS[KURE],
            device=self.reranker_device,
            max_length=2048,
            local_files_only=True,
            model_kwargs={"dtype": torch.float32, "attn_implementation": "sdpa"},
        )
        self.lock = threading.Lock()
        self.embedding_lock = threading.Lock()
        self.reranking_lock = threading.Lock()
        self.calls = 0

    def _prepare_inputs(self, directory):
        self.directory = Path(directory)
        self.store = TableStore(directory)
        self.engine = LegacyBaseline()
        self.engine._ensure_corpus()
        self.pages = self.engine.pages
        self.page_map = {page_key(p): p for p in self.pages}
        self.by_source = {source_id(p): p for p in self.pages}
        self.chunks = read_json(self.directory / "table-chunks.json")
        self.units = [*self.pages, *self.chunks]
        # Preserve the existing whole-code tokenizer and heading bonus.
        # Only the indexed content units change when table bodies are added.
        self.bm25 = PatternIndex(self.units)
        self.bm25.base_index = FastBM25(self.bm25.base_index)
        if self.bm25.heading_index is not None:
            self.bm25.heading_index = FastBM25(self.bm25.heading_index)

    def _encode_query(self, question):
        return encode(self.embedder, [question], kind="query", batch_size=1)[0]

    def _dense_rank(self, query):
        dense_scores = self.vectors @ query
        indices = np.argsort(-dense_scores, kind="stable")[:50]
        return [(self.units[i], float(dense_scores[i])) for i in indices]

    def search(self, question):
        started = perf_counter()
        # The CUDA path overlaps one request's CPU embedding with another's
        # GPU reranking. CPU-only/shared-CUDA modes retain the coarse guard.
        outer = nullcontext() if self.reranker_device == "cuda" else self.lock
        with outer:
            with self.embedding_lock:
                self.calls += 1
                query = self._encode_query(question)
            dense = self._dense_rank(query)
            sparse = self.bm25.fielded(question, top_k=50)
            candidates = distinct_page_units(
                weighted_rrf(sparse, dense, (0.5, 0.5), top_k=100), 20
            )
            with self.reranking_lock:
                input_ids = self.reranker.tokenizer(
                    [question] * len(candidates),
                    [p["text"] for p, _ in candidates],
                    truncation=False,
                    verbose=False,
                )["input_ids"]
                work = {
                    "rerank_pairs": len(candidates),
                    "raw_pair_tokens": sum(map(len, input_ids)),
                    "truncated_pairs": sum(
                        len(t) > self.reranker.max_length for t in input_ids
                    ),
                }
                scores = self.reranker.predict(
                    [(question, p["text"]) for p, _ in candidates],
                    batch_size=4,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
        reranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: (
                -float(pair[1]),
                pair[0][0]["file_name"],
                pair[0][0]["page"],
                pair[0][0].get("chunk_id", ""),
            ),
        )
        units = [(p, float(score)) for ((p, _), score) in reranked]
        ordered = merge_pages(
            [[(self.page_map[page_key(p)], score) for p, score in units]]
        )
        after_hg, chosen, route = complete_sources(
            question, ordered, self.engine.lookup, self.engine.storage, 8000
        )
        ranked, context, cases, source_route, selection = chosen
        table_ids = []
        for unit, score in units:
            ids = (
                [unit["table_id"]]
                if "table_id" in unit
                else self.store.by_page.get(page_key(unit), [])
            )
            for tid in ids:
                if tid not in table_ids:
                    table_ids.append(tid)
        for page, score in ranked[:8]:
            for tid in self.store.by_page.get(page_key(page), []):
                if tid not in table_ids:
                    table_ids.append(tid)
        return {
            "query": question,
            "work": work,
            "ranked": ranked[:20],
            "tables": table_ids[:6],
            "unit_candidates": [
                {"source_id": source_id(p), "table_id": p.get("table_id")}
                for p, _ in candidates
            ],
            "seconds": perf_counter() - started,
            "hg_route": route,
            "source_route": source_route,
        }

    def context(self, groups, preferred=(), sql_results=(), *, preserve_evidence=False):
        if preserve_evidence:
            # Only observed pages can be promoted. Keep explicitly selected
            # evidence before truncating the cross-query union to twenty pages.
            observed = {
                source_id(p): (p, score) for group in groups for p, score in group
            }
            promoted = [observed[s] for s in dict.fromkeys(preferred) if s in observed][
                :8
            ]
            promoted_ids = {source_id(p) for p, _ in promoted}
            remainder = merge_pages(
                [
                    [
                        (p, score)
                        for p, score in group
                        if source_id(p) not in promoted_ids
                    ]
                    for group in groups
                ],
                20,
            )
            ranked = (promoted + remainder)[:20]
        else:
            ranked = merge_pages(groups, 20)
        allowed = {source_id(p): (p, score) for p, score in ranked}
        selected = [allowed[s] for s in dict.fromkeys(preferred) if s in allowed]
        selected += [
            (p, score) for p, score in ranked if source_id(p) not in set(preferred)
        ]
        # Include SQL values only when all contributing physical pages can be
        # retained inside the same eight-page provenance budget.
        sql_kept, required = [], set()
        for result in sql_results:
            if result.get("status") != "ok":
                continue
            sources = {f"{s['book_id']}:p{s['page']:04d}" for s in result["sources"]}
            if sources <= allowed.keys() and len(required | sources) <= 8:
                sql_kept.append(result)
                required |= sources
        if required:
            selected = [allowed[s] for s in sorted(required)] + [
                pair for pair in selected if source_id(pair[0]) not in required
            ]
        sql_text = json_payload(sql_kept)
        if _count_tokens(sql_text) > 2000:
            sql_kept, sql_text = [], ""
        context = build_context(
            _context_candidates(selected),
            8,
            8000 - _count_tokens(sql_text),
            _count_tokens,
        )
        actual = {
            f"{c.chunk.book_id}:p{c.chunk.page:04d}" for c in context.final_chunks
        }
        if not required <= actual:
            sql_kept, sql_text = [], ""
            context = build_context(
                _context_candidates(selected), 8, 8000, _count_tokens
            )
        return ranked, context, sql_kept


def json_payload(value):
    import json

    return json.dumps(value, ensure_ascii=False) if value else ""
