import argparse
import json
from collections import defaultdict
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

import numpy as np

from experimental.u_10o0o.bm25 import K1, B, TOKENIZERS, BM25Index, load_tokenizer
from experimental.u_10o0o.embeding import (
    CPU_THREADS,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
)
from experimental.u_10o0o.evaluate import RECORDS_DIR, SPLITS, evaluate, save_results
from experimental.u_10o0o.prepare import DATA_DIR, DATASET
from experimental.u_10o0o.search import TOP_K, load_index, search

RATIOS = ((5, 5), (4, 6), (6, 4), (3, 7), (7, 3))
CANDIDATES = 20
RRF_K = 60


def weighted_rrf(
    bm25_pages, dense_pages, weights, *, top_k=TOP_K
) -> list[tuple[dict, float]]:
    scores = defaultdict(float)
    pages = {}
    for ranked, weight in zip((bm25_pages, dense_pages), weights, strict=True):
        for rank, (page, _) in enumerate(ranked, start=1):
            key = (page["file_name"], page["page"], page.get("chunk_id", ""))
            pages[key] = page
            scores[key] += weight / (RRF_K + rank)

    order = sorted(scores, key=lambda key: (-scores[key], key))
    return [(pages[key], scores[key]) for key in order[:top_k]]


def evaluate_ratio(index, ratio, warmup, output_dir, *, tokenizer_name, bm25_runtime):
    bm25_weight, dense_weight = (part / sum(ratio) for part in ratio)
    times = []

    def search_question(question):
        started = perf_counter()
        bm25_pages = index.search(question, top_k=CANDIDATES)
        dense_pages = search(question, top_k=CANDIDATES)
        ranked = weighted_rrf(bm25_pages, dense_pages, (bm25_weight, dense_weight))
        times.append((perf_counter() - started) * 1000)
        return ranked

    for item in warmup:
        search_question(item["question"])
    times.clear()

    ratio_label = f"{ratio[0]}:{ratio[1]}"
    label = f"BM25 ({tokenizer_name}):Snowflake-ko {ratio_label}"
    result = evaluate(
        label,
        VECTORS_PATH,
        search_question,
        similarity="weighted_rrf",
    )
    result["runtime"] = {
        "device": "cpu",
        "dense_cpu_threads": CPU_THREADS,
        "dense_model_id": MODEL_ID,
        "dense_revision": MODEL_REVISION,
        **bm25_runtime,
        "bm25_k1": K1,
        "bm25_b": B,
        "candidate_count_per_retriever": CANDIDATES,
        "rank_constant": RRF_K,
        "weights": {"bm25": bm25_weight, "dense": dense_weight},
        "search_latency": {
            "scope": "bm25_dense_and_weighted_rrf_top3",
            "warmup_queries": len(warmup),
            "query_count": len(times),
            "p50_ms": float(np.median(times)),
            "p95_ms": float(np.percentile(times, 95)),
        },
    }
    output_dir.mkdir(parents=True)
    save_results(result, output_dir)
    overall = result["summary"]["overall"]
    print(
        f"{label} | Hit@3 {overall['hit']:.2%} | "
        f"Recall@3 {overall['recall']:.2%} | Precision@3 {overall['precision']:.2%}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="페이지 단위 가중 RRF 검색 평가")
    parser.add_argument("--tokenizer", choices=TOKENIZERS, default="bigram")
    args = parser.parse_args()

    pages, _ = load_index()
    started = perf_counter()
    tokenizer, tokenizer_runtime = load_tokenizer(args.tokenizer)
    index = BM25Index(pages, tokenizer=tokenizer)
    bm25_runtime = {
        **{f"bm25_{key}": value for key, value in tokenizer_runtime.items()},
        "bm25_index_build_seconds": perf_counter() - started,
    }
    question_path = DATA_DIR / "evaluation" / f"{SPLITS[0]}_questions.json"
    warmup = json.loads(question_path.read_text(encoding="utf-8"))[:3]
    run_at = datetime.now(ZoneInfo("Asia/Seoul"))
    run_id = (
        f"{DATASET}-hybrid-rrf-{args.tokenizer}-page-top{TOP_K}"
        f"-{run_at:%Y%m%d-%H%M%S-%f}"
    )
    output_root = RECORDS_DIR / run_id
    for ratio in RATIOS:
        output_dir = output_root / f"bm25-{ratio[0]}-dense-{ratio[1]}"
        evaluate_ratio(
            index,
            ratio,
            warmup,
            output_dir,
            tokenizer_name=args.tokenizer,
            bm25_runtime=bm25_runtime,
        )
    print(f"저장 위치 {output_root}")


if __name__ == "__main__":
    main()
