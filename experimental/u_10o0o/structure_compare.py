import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from experimental.u_10o0o.bm25 import K1, B, BM25Index, load_tokenizer
from experimental.u_10o0o.context_evidence import (
    audit_evidence_matchability,
    score_context,
)
from experimental.u_10o0o.embeding import (
    CPU_THREADS,
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
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
from experimental.u_10o0o.search import TOP_K, rank_pages
from experimental.u_10o0o.structure_chunks import METADATA_PATH
from experimental.u_10o0o.structure_vectors import (
    STRUCTURE_CHUNKS_PATH,
    STRUCTURE_VECTOR_METADATA_PATH,
    STRUCTURE_VECTORS_PATH,
)

PAGE_RUN = RECORDS_DIR / "advanced-reranking-20261108-201548-983299"
KURE_ID = "nlpai-lab/KURE-Reranker-nano"
CONTEXT_TOKENS = 4000


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_key(page):
    return Path(page["file_name"]).stem, page["page"]


def saved_ranking(record, pages_by_source):
    return [
        (pages_by_source[item["book_id"], item["page"]], item["score"])
        for item in record["retrieved_pages"]
    ]


def trace_ranking(ranked):
    return [
        {
            "rank": rank,
            "book_id": source_key(page)[0],
            "page": page["page"],
            "chunk_id": page.get("chunk_id"),
            "score": score,
        }
        for rank, (page, score) in enumerate(ranked, start=1)
    ]


def rank_chunks(question, chunks, vectors, index, encoder, reranker):
    sparse = index.search(question, top_k=CANDIDATES)
    question_vector = encoder.encode_query(question, normalize_embeddings=True)
    dense = rank_pages(question_vector, vectors, chunks, top_k=CANDIDATES)
    candidates = weighted_rrf(sparse, dense, WEIGHTS, top_k=CANDIDATES)
    top, over_limit = rerank_candidates(question, candidates, reranker)
    return candidates, top, over_limit


def rerank_candidates(question, candidates, reranker):
    texts = [chunk["text"] for chunk, _ in candidates]
    scores = reranker.predict(
        [(question, text) for text in texts],
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
    )
    ordered = sorted(zip(candidates, scores, strict=True), key=lambda row: -row[1])
    top = [(chunk, float(score)) for (chunk, _), score in ordered[:TOP_K]]
    token_ids = reranker.tokenizer(
        [question] * len(texts), texts, truncation=False, verbose=False
    )["input_ids"]
    return top, sum(len(ids) > MAX_TOKENS for ids in token_ids)


def score_row(question_id, candidates, top, evidence_groups):
    return {
        "question_id": question_id,
        "candidates": trace_ranking(candidates),
        "top3": trace_ranking(top),
        "candidate_page_metrics": score_question(
            question_id, candidates, evidence_groups, top_k=CANDIDATES
        )["metrics"],
        "top3_page_metrics": score_question(question_id, top, evidence_groups)[
            "metrics"
        ],
        "context": score_context(top, evidence_groups, token_budget=CONTEXT_TOKENS),
    }


def summarize_rows(rows):
    contexts = [row["context"] for row in rows]
    scored = [row for row in contexts if row["expected_quotes"]]
    expected = sum(row["expected_quotes"] for row in scored)
    found = sum(row["found_quotes"] for row in scored)
    return {
        "candidate_pages": summarize(
            [{"metrics": row["candidate_page_metrics"]} for row in rows]
        ),
        "top3_pages": summarize(
            [{"metrics": row["top3_page_metrics"]} for row in rows]
        ),
        "mean_unique_top3_pages": mean(
            len({(page["book_id"], page["page"]) for page in row["top3"]})
            for row in rows
        ),
        "context": {
            "mean_tokens": mean(row["context_tokens"] for row in contexts),
            "max_tokens": max(row["context_tokens"] for row in contexts),
            "questions_with_under_three_items": sum(
                row["context_chunks"] < TOP_K for row in contexts
            ),
            "questions_with_any_quote": sum(row["found_quotes"] > 0 for row in scored),
            "scored_questions": len(scored),
            "found_quotes": found,
            "expected_quotes": expected,
            "macro_quote_recall": mean(
                row["found_quotes"] / row["expected_quotes"] for row in scored
            ),
            "micro_quote_recall": found / expected,
            "groups_with_any_found_quote": sum(
                row["groups_with_any_found_quote"] for row in scored
            ),
            "evidence_groups": sum(row["evidence_groups"] for row in scored),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advanced 페이지와 소제목·문단 청크 비교"
    )
    parser.parse_args()
    if DATASET != "advanced" or not torch.cuda.is_available():
        raise SystemExit("Advanced 페이지·청크 캐시와 CUDA가 필요합니다.")

    torch.set_num_threads(CPU_THREADS)
    torch.set_num_interop_threads(1)
    candidate_path = PAGE_RUN / "candidates/results.json"
    top3_path = PAGE_RUN / KURE_ID.replace("/", "-") / "results.json"
    config_path = PAGE_RUN / "config.json"
    frozen = load_json(candidate_path)
    page_top3 = load_json(top3_path)
    page_config = load_json(config_path)
    weights = dict(zip(("bm25", "dense"), WEIGHTS, strict=True))
    fixed_config = {
        "dense": {"model": MODEL_ID, "revision": MODEL_REVISION},
        "rrf": {"k": RRF_K, "weights": weights},
        "candidates_per_retriever": CANDIDATES,
        "rerank_candidates": CANDIDATES,
        "final_top_k": TOP_K,
        "rerank_token_limit": MAX_TOKENS,
        "rerank_batch_size": BATCH_SIZE,
    }
    if any(page_config[key] != value for key, value in fixed_config.items()):
        raise ValueError(
            "보관된 페이지 실행의 검색·리랭킹 설정이 현재 설정과 다릅니다."
        )
    pages_input = fingerprint(CACHE_PATH)
    chunks_input = fingerprint(STRUCTURE_CHUNKS_PATH)
    vectors_input = fingerprint(STRUCTURE_VECTORS_PATH)
    chunk_metadata = load_json(METADATA_PATH)
    vector_metadata = load_json(STRUCTURE_VECTOR_METADATA_PATH)
    if (
        frozen["inputs"] != page_top3["inputs"]
        or pages_input != frozen["inputs"]["pages"]
    ):
        raise ValueError("페이지 캐시와 보관된 기준선의 입력이 다릅니다.")
    if page_top3["revision"] != MODELS[KURE_ID]:
        raise ValueError("보관된 페이지 기준선의 리랭커 버전이 다릅니다.")
    if (
        chunk_metadata["chunk_sha256"] != chunks_input["sha256"]
        or chunk_metadata["source_page_cache_sha256"] != pages_input["sha256"]
    ):
        raise ValueError("구조 청크의 원본 페이지 또는 청크 해시가 다릅니다.")
    if (
        vector_metadata["input_sha256"] != chunks_input["sha256"]
        or vector_metadata["vector_sha256"] != vectors_input["sha256"]
        or vector_metadata["model_revision"] != MODEL_REVISION
        or vector_metadata["model_id"] != MODEL_ID
        or vector_metadata["normalized"] is not True
    ):
        raise ValueError("청크와 벡터의 입력 해시 또는 모델 버전이 다릅니다.")
    pages = load_json(CACHE_PATH)
    pages_by_source = {source_key(page): page for page in pages}
    chunks = load_json(STRUCTURE_CHUNKS_PATH)
    chunks_by_source = defaultdict(list)
    for chunk in chunks:
        chunks_by_source[source_key(chunk)].append(chunk)
    vectors = np.load(STRUCTURE_VECTORS_PATH, allow_pickle=False)
    if vectors.shape != (len(chunks), EMBEDDING_DIM) or vectors.dtype != np.float32:
        raise ValueError("청크 벡터의 모양 또는 자료형이 예상과 다릅니다.")

    run_at = datetime.now(KST)
    run_dir = RECORDS_DIR / f"advanced-structure-chunking-{run_at:%Y%m%d-%H%M%S-%f}"
    run_dir.mkdir(parents=True)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (run_dir / "run.log").open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")

    log(f"페이지 {len(pages):,}쪽 · 구조 청크 {len(chunks):,}개")
    started = perf_counter()
    tokenizer, tokenizer_runtime = load_tokenizer("kiwi")
    bm25_config = {**tokenizer_runtime, "k1": K1, "b": B}
    if any(page_config["bm25"][key] != value for key, value in bm25_config.items()):
        raise ValueError("보관된 페이지 실행의 Kiwi BM25 설정이 현재 설정과 다릅니다.")
    index = BM25Index(chunks, tokenizer=tokenizer)
    log(f"구조 청크 Kiwi 색인 완료 · {perf_counter() - started:.2f}초")
    encoder = load_model()
    reranker = CrossEncoder(
        KURE_ID,
        revision=MODELS[KURE_ID],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float32},
    )
    if reranker.max_seq_length != MAX_TOKENS:
        raise ValueError("리랭커 입력 토큰 상한이 기준선과 다릅니다.")

    records = {"page": {}, "structure": {}}
    audit = {"page": [], "structure": []}
    over_limit = {"page": 0, "structure": 0}
    for split in SPLITS:
        question_path = DATA_DIR / "evaluation" / f"{split}_questions.json"
        reference_path = DATA_DIR / "evaluation" / f"{split}.json"
        if frozen["inputs"]["evaluation"][split] != {
            "questions": fingerprint(question_path),
            "references": fingerprint(reference_path),
        }:
            raise ValueError(f"기준선의 {split} 평가 입력과 현재 파일이 다릅니다.")
        questions = load_json(question_path)
        page_candidates = [
            saved_ranking(row, pages_by_source) for row in frozen["records"][split]
        ]
        page_ranks = [
            rerank_candidates(item["question"], candidates, reranker)
            for item, candidates in zip(questions, page_candidates, strict=True)
        ]
        ranks = [
            rank_chunks(item["question"], chunks, vectors, index, encoder, reranker)
            for item in questions
        ]
        references = {
            row["id"]: row["evidence_groups"] for row in load_json(reference_path)
        }
        records["page"][split] = []
        records["structure"][split] = []
        for item, frozen_row, top3_row, candidates, page_rank, structure_rank in zip(
            questions,
            frozen["records"][split],
            page_top3["records"][split],
            page_candidates,
            page_ranks,
            ranks,
            strict=True,
        ):
            top_pages, page_over = page_rank
            candidate_chunks, top_chunks, structure_over = structure_rank
            question_id = item["id"]
            if (
                question_id != frozen_row["question_id"]
                or question_id != top3_row["question_id"]
            ):
                raise ValueError("기준선 문항 순서가 평가 입력과 다릅니다.")
            if [source_key(page) for page, _ in top_pages] != [
                (row["book_id"], row["page"]) for row in top3_row["retrieved_pages"]
            ]:
                raise ValueError(
                    "GPU에서 보관된 페이지 기준선 Top 3 순위를 재현하지 못했습니다."
                )
            evidence = references[question_id]
            evidence_pages = {
                (item["book_id"], item["page"]) for group in evidence for item in group
            }
            audit["page"].append(
                audit_evidence_matchability(
                    [pages_by_source[key] for key in evidence_pages], evidence
                )
            )
            audit["structure"].append(
                audit_evidence_matchability(
                    [
                        chunk
                        for key in evidence_pages
                        for chunk in chunks_by_source[key]
                    ],
                    evidence,
                )
            )
            records["page"][split].append(
                score_row(
                    question_id,
                    candidates,
                    top_pages,
                    evidence,
                )
            )
            records["structure"][split].append(
                score_row(question_id, candidate_chunks, top_chunks, evidence)
            )
            over_limit["page"] += page_over
            over_limit["structure"] += structure_over
        log(f"{split} · {len(questions)}문항 평가 완료")

    summary = {
        name: {
            "overall": summarize_rows(
                [row for rows in splits.values() for row in rows]
            ),
            **{split: summarize_rows(rows) for split, rows in splits.items()},
        }
        for name, splits in records.items()
    }
    if summary["page"]["overall"]["top3_pages"] != page_top3["summary"]["overall"]:
        raise ValueError("보관된 페이지 기준선의 Top 3 수치를 재현하지 못했습니다.")

    result = {
        "dataset": DATASET,
        "run_at": run_at.isoformat(),
        "inputs": {
            "pages": pages_input,
            "chunks": chunks_input,
            "chunk_metadata": fingerprint(METADATA_PATH),
            "vectors": vectors_input,
            "evaluation": frozen["inputs"]["evaluation"],
            "page_baseline": {
                "config": fingerprint(config_path),
                "candidates": fingerprint(candidate_path),
                "top3": fingerprint(top3_path),
            },
        },
        "page_baseline_run": PAGE_RUN.name,
        "search": {
            "bm25": bm25_config,
            "dense_model": MODEL_ID,
            "dense_revision": MODEL_REVISION,
            "rrf_k": RRF_K,
            "weights": weights,
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
            "page_pairs_over_limit": over_limit["page"],
            "structure_pairs_over_limit": over_limit["structure"],
        },
        "context": {"tokenizer": "o200k_base", "budget": CONTEXT_TOKENS},
        "corpus": {
            "pages": len(pages),
            "structure_chunks": len(chunks),
            "chunks_with_heading": sum(bool(chunk.get("heading")) for chunk in chunks),
            "structure_preparation": chunk_metadata,
            "structure_embedding": vector_metadata,
        },
        "evidence_matchability": {
            arm: {key: sum(row[key] for row in rows) for key in rows[0]}
            for arm, rows in audit.items()
        },
        "summary": summary,
        "records": records,
    }
    (run_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"비교 완료 · {run_dir.name}")


if __name__ == "__main__":
    main()
