import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from experimental.u_10o0o.bm25 import BM25Index, identifier_matches, load_tokenizer
from experimental.u_10o0o.embeding import (
    CPU_THREADS,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.evaluate import (
    RECORDS_DIR,
    SPLITS,
    fingerprint,
    score_question,
    summarize,
)
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET
from experimental.u_10o0o.rerank import BATCH_SIZE, KST, MAX_TOKENS, MODELS, WEIGHTS
from experimental.u_10o0o.search import TOP_K, load_index, rank_pages
from experimental.u_10o0o.structure_compare import (
    CONTEXT_TOKENS,
    KURE_ID,
    PAGE_RUN,
    load_json,
    rerank_candidates,
    score_row,
    source_key,
    summarize_rows,
)

PROBE_PATTERN = re.compile(r"[a-z]{2,8}-[0-9]{3,10}")
PROBE_COUNT = 30


def identifier_probes(pages):
    labels = defaultdict(set)
    pages_with_id = 0
    for page in pages:
        matches = identifier_matches(page["text"])
        pages_with_id += bool(matches)
        for match in matches:
            value = match[0].lower()
            if PROBE_PATTERN.fullmatch(value):
                labels[value].add(source_key(page))

    parts = [code.split("-") for code in labels]
    prefixes = Counter(prefix for prefix, _ in parts)
    suffixes = Counter(suffix for _, suffix in parts)
    eligible = [
        code
        for code, (prefix, suffix) in zip(labels, parts, strict=True)
        if prefixes[prefix] > 1 and suffixes[suffix] > 1
    ]
    selected = sorted(eligible, key=lambda code: sha256(code.encode()).digest())[
        :PROBE_COUNT
    ]
    probes = [
        (
            code,
            [[{"book_id": book, "page": page} for book, page in sorted(labels[code])]],
        )
        for code in selected
    ]
    return probes, {
        "pages_with_any_identifier": pages_with_id,
        "distinct_simple_identifiers": len(labels),
        "eligible_with_prefix_and_suffix_collisions": len(eligible),
        "simple_pattern": PROBE_PATTERN.pattern,
    }


def summarize_groups(rows):
    return {
        "overall": summarize_rows(rows),
        "identifier_queries": summarize_rows(
            [row for row in rows if row["identifier_in_question"]]
        ),
        "other_queries": summarize_rows(
            [row for row in rows if not row["identifier_in_question"]]
        ),
        "bm25_top3": summarize(
            [{"metrics": row["bm25_top3_page_metrics"]} for row in rows]
        ),
        "bm25_top20": summarize(
            [{"metrics": row["bm25_top20_page_metrics"]} for row in rows]
        ),
    }


def score_identifier_probes(index, probes):
    rankings = [index.search(code, top_k=CANDIDATES) for code, _ in probes]
    summaries = {}
    for k in (TOP_K, CANDIDATES):
        records = [
            score_question(number, ranked, evidence, top_k=k)
            for number, (ranked, (_, evidence)) in enumerate(
                zip(rankings, probes, strict=True)
            )
        ]
        returned = sum(len(row["retrieved_pages"]) for row in records)
        matching = sum(len(row["matched_pages"]) for row in records)
        summaries[f"top{k}"] = {
            **summarize(records),
            "returned_pages": returned,
            "matching_pages": matching,
            "nonmatching_pages": returned - matching,
        }
    return summaries


def main() -> None:
    argparse.ArgumentParser(description="Advanced Kiwi 식별자 토큰 비교").parse_args()
    if DATASET != "advanced" or not torch.cuda.is_available():
        raise SystemExit("Advanced 페이지 캐시와 CUDA가 필요합니다.")

    torch.set_num_threads(CPU_THREADS)
    torch.set_num_interop_threads(1)
    baseline_candidates = PAGE_RUN / "candidates/results.json"
    baseline_top3 = PAGE_RUN / KURE_ID.replace("/", "-") / "results.json"
    frozen = load_json(baseline_candidates)
    old_top3 = load_json(baseline_top3)
    inputs = frozen["inputs"]
    if (
        inputs != old_top3["inputs"]
        or inputs["pages"] != fingerprint(CACHE_PATH)
        or inputs["vectors"] != fingerprint(VECTORS_PATH)
        or old_top3["revision"] != MODELS[KURE_ID]
    ):
        raise ValueError("보관된 기준선의 입력 또는 모델 버전이 다릅니다.")

    questions = {}
    for split in SPLITS:
        question_path = DATA_DIR / "evaluation" / f"{split}_questions.json"
        reference_path = DATA_DIR / "evaluation" / f"{split}.json"
        if inputs["evaluation"][split] != {
            "questions": fingerprint(question_path),
            "references": fingerprint(reference_path),
        }:
            raise ValueError(f"보관된 기준선의 {split} 평가 파일이 다릅니다.")
        questions[split] = load_json(question_path)

    run_at = datetime.now(KST)
    run_dir = RECORDS_DIR / f"advanced-kiwi-identifiers-{run_at:%Y%m%d-%H%M%S-%f}"
    run_dir.mkdir(parents=True)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (run_dir / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    pages, vectors = load_index()
    probes, corpus = identifier_probes(pages)
    log(f"Advanced 페이지 {len(pages):,}쪽 · 식별자 단독 검색 진단 {len(probes)}개")
    encoder = load_model()
    dense_candidates = {
        split: [
            rank_pages(
                encoder.encode_query(row["question"], normalize_embeddings=True),
                vectors,
                pages,
                top_k=CANDIDATES,
            )
            for row in rows
        ]
        for split, rows in questions.items()
    }
    reranker = CrossEncoder(
        KURE_ID,
        revision=MODELS[KURE_ID],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float32},
    )
    if reranker.max_seq_length != MAX_TOKENS:
        raise ValueError("리랭커 입력 상한이 기준선과 다릅니다.")

    arms = {}
    for name in ("kiwi", "kiwi-id"):
        started = perf_counter()
        tokenizer, runtime = load_tokenizer(name)
        index = BM25Index(pages, tokenizer=tokenizer)
        index_seconds = perf_counter() - started
        log(f"{name} 색인 완료 · {index_seconds:.2f}초")
        records = {}
        for split, rows in questions.items():
            rankings = []
            for row, dense in zip(rows, dense_candidates[split], strict=True):
                question = row["question"]
                sparse = index.search(question, top_k=CANDIDATES)
                hybrid = weighted_rrf(sparse, dense, WEIGHTS, top_k=CANDIDATES)
                top, _ = rerank_candidates(question, hybrid, reranker)
                rankings.append((sparse, hybrid, top))

            references = {
                row["id"]: row["evidence_groups"]
                for row in load_json(DATA_DIR / "evaluation" / f"{split}.json")
            }
            records[split] = []
            for item, (sparse, hybrid, top), old_candidates, old_final in zip(
                rows,
                rankings,
                frozen["records"][split],
                old_top3["records"][split],
                strict=True,
            ):
                question_id = item["id"]
                if (
                    not question_id
                    == old_candidates["question_id"]
                    == old_final["question_id"]
                ):
                    raise ValueError("보관된 기준선의 문항 순서가 다릅니다.")
                if name == "kiwi":
                    for ranked, old in ((hybrid, old_candidates), (top, old_final)):
                        if [source_key(page) for page, _ in ranked] != [
                            (row["book_id"], row["page"])
                            for row in old["retrieved_pages"]
                        ]:
                            raise ValueError(
                                "보관된 Kiwi 기준선 순위를 재현하지 못했습니다."
                            )
                evidence = references[question_id]
                result = score_row(question_id, hybrid, top, evidence)
                result["identifier_in_question"] = bool(
                    identifier_matches(item["question"])
                )
                result["bm25_top3_page_metrics"] = score_question(
                    question_id, sparse, evidence
                )["metrics"]
                result["bm25_top20_page_metrics"] = score_question(
                    question_id, sparse, evidence, top_k=CANDIDATES
                )["metrics"]
                records[split].append(result)
            log(f"{name} · {split} {len(rows)}문항 평가 완료")

        arms[name] = {
            "tokenizer": {**runtime, "index_build_seconds": index_seconds},
            "summary": summarize_groups(
                [row for rows in records.values() for row in rows]
            ),
            "identifier_only_bm25": score_identifier_probes(index, probes),
            "records": records,
        }
        del index, tokenizer

    result = {
        "dataset": DATASET,
        "run_at": run_at.isoformat(),
        "inputs": inputs,
        "page_baseline_run": PAGE_RUN.name,
        "search": {
            "dense_model": MODEL_ID,
            "dense_revision": MODEL_REVISION,
            "rrf_k": RRF_K,
            "weights": dict(zip(("bm25", "dense"), WEIGHTS, strict=True)),
            "candidates_per_retriever": CANDIDATES,
            "rerank_candidates": CANDIDATES,
            "final_top_k": TOP_K,
        },
        "reranker": {
            "model_id": KURE_ID,
            "revision": MODELS[KURE_ID],
            "device": torch.cuda.get_device_name(),
            "dtype": "float32",
            "batch_size": BATCH_SIZE,
            "max_pair_tokens": MAX_TOKENS,
        },
        "context": {"tokenizer": "o200k_base", "budget": CONTEXT_TOKENS},
        "corpus": {"pages": len(pages), **corpus},
        "arms": arms,
    }
    (run_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"비교 완료 · {run_dir.name}")


if __name__ == "__main__":
    main()
