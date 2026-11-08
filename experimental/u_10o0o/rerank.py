import argparse
import json
import platform
from datetime import datetime
from importlib.metadata import version
from time import perf_counter
from zoneinfo import ZoneInfo

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from experimental.u_10o0o.bm25 import K1, B, BM25Index, load_tokenizer
from experimental.u_10o0o.embeding import (
    CPU_THREADS,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
)
from experimental.u_10o0o.evaluate import RECORDS_DIR, SPLITS, evaluate, save_results
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import DATA_DIR, DATASET
from experimental.u_10o0o.search import TOP_K, load_index, search

MODELS = {
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
    "nlpai-lab/KURE-Reranker-nano": "8514b9e113f3b27c441ed9c2b50f81d80388b2fd",
    "Horizon-Labs/multilingual-reranker-small": "23139fec6f1f448cdf89a5ac2d2c6bf5e8c7b398",
}
WEIGHTS = (0.6, 0.4)
MAX_TOKENS = 512
BATCH_SIZE = 8
KST = ZoneInfo("Asia/Seoul")


def latency(times):
    return {
        "p50_ms": float(np.median(times)),
        "p95_ms": float(np.percentile(times, 95)),
    }


def hit_changes(before, after):
    gained, lost = [], []
    for split in before["records"]:
        for old, new in zip(
            before["records"][split], after["records"][split], strict=True
        ):
            assert old["question_id"] == new["question_id"]
            if old["metrics"] is None:
                continue
            change = new["metrics"]["hit"] - old["metrics"]["hit"]
            if change:
                (gained if change > 0 else lost).append(new["question_id"])
    return {"gained": gained, "lost": lost}


def evaluate_model(model_id, revision, frozen, warmup, search_times, baseline, log):
    model = CrossEncoder(
        model_id,
        revision=revision,
        device="cpu",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float32},
    )
    assert model.max_seq_length == MAX_TOKENS
    times = []
    lengths = []

    def rank(question):
        started = perf_counter()
        candidates = frozen[question]
        texts = [page["text"] for page, _ in candidates]
        scores = model.predict(
            [(question, text) for text in texts],
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
        )
        ordered = sorted(zip(candidates, scores, strict=True), key=lambda row: -row[1])
        result = [(page, float(score)) for (page, _), score in ordered[:TOP_K]]
        times.append((perf_counter() - started) * 1000)
        tokens = model.tokenizer(
            [question] * len(texts), texts, truncation=False, verbose=False
        )["input_ids"]
        lengths.extend(map(len, tokens))
        if len(times) % 10 == 0:
            log(f"{model_id} · {len(times)}문항 완료")
        return result

    for question in warmup:
        rank(question)
    times.clear()
    lengths.clear()
    result = evaluate(model_id, VECTORS_PATH, rank, similarity="cross_encoder")
    result["revision"] = revision
    result["runtime"] = {
        "rerank_latency": latency(times),
        "search_plus_rerank_estimate": latency(
            [
                search_ms + rerank_ms
                for search_ms, rerank_ms in zip(search_times, times, strict=True)
            ]
        ),
        "rerank_ms_by_question": times,
        "candidate_pairs": len(lengths),
        "pairs_over_token_limit": sum(length > MAX_TOKENS for length in lengths),
        "longest_pair_tokens": max(lengths),
    }
    result["hit_changes_vs_rrf"] = hit_changes(baseline, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="고정 RRF 후보의 CPU 리랭커 비교")
    parser.parse_args()
    if DATASET != "advanced":
        raise SystemExit("이 실험은 Advanced 페이지와 문항으로 실행하세요.")

    torch.set_num_threads(CPU_THREADS)
    torch.set_num_interop_threads(1)
    run_at = datetime.now(KST)
    run_dir = RECORDS_DIR / f"advanced-reranking-{run_at:%Y%m%d-%H%M%S-%f}"
    run_dir.mkdir(parents=True)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (run_dir / "run.log").open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    log(f"CPU 비교 시작 · {run_dir.name}")
    pages, _ = load_index()
    started = perf_counter()
    tokenizer, tokenizer_runtime = load_tokenizer("kiwi")
    index = BM25Index(pages, tokenizer=tokenizer)
    index_seconds = perf_counter() - started
    log(f"Kiwi BM25 색인 완료 · {len(pages):,}쪽 · {index_seconds:.2f}초")
    warmup_path = DATA_DIR / "evaluation" / f"{SPLITS[0]}_questions.json"
    warmup = [
        item["question"]
        for item in json.loads(warmup_path.read_text(encoding="utf-8"))[:3]
    ]

    frozen, search_times, bm25_times = {}, [], []

    def freeze(question):
        started = perf_counter()
        bm25_pages = index.search(question, top_k=CANDIDATES)
        dense_pages = search(question, top_k=CANDIDATES)
        ranked = weighted_rrf(bm25_pages, dense_pages, WEIGHTS, top_k=CANDIDATES)
        search_times.append((perf_counter() - started) * 1000)
        frozen[question] = ranked
        return ranked

    def kiwi_only(question):
        started = perf_counter()
        ranked = index.search(question)
        bm25_times.append((perf_counter() - started) * 1000)
        return ranked

    for question in warmup:
        freeze(question)
        kiwi_only(question)
    frozen.clear()
    search_times.clear()
    bm25_times.clear()
    label = "Kiwi BM25 + Snowflake-ko 6:4"
    candidates = evaluate(
        label, VECTORS_PATH, freeze, similarity="weighted_rrf", top_k=CANDIDATES
    )
    baseline = evaluate(
        label, VECTORS_PATH, frozen.__getitem__, similarity="weighted_rrf"
    )
    kiwi = evaluate("Kiwi BM25 단독", None, kiwi_only, similarity="bm25")
    candidates["runtime"] = {
        "search_latency": latency(search_times),
        "search_ms_by_question": search_times,
    }
    baseline["runtime"] = {"search_latency": latency(search_times)}
    kiwi["runtime"] = {
        "search_latency": latency(bm25_times),
        "search_ms_by_question": bm25_times,
    }

    config = {
        "dataset": DATASET,
        "run_at": run_at.isoformat(),
        "device": "cpu",
        "platform": platform.platform(),
        "versions": {
            name: version(name)
            for name in ("torch", "transformers", "sentence-transformers", "numpy")
        },
        "cpu_threads": CPU_THREADS,
        "interop_threads": torch.get_num_interop_threads(),
        "dtype": "float32",
        "backend": "torch",
        "warmup_questions": len(warmup),
        "bm25": {**tokenizer_runtime, "k1": K1, "b": B, "index_seconds": index_seconds},
        "dense": {"model": MODEL_ID, "revision": MODEL_REVISION},
        "rrf": {
            "k": RRF_K,
            "weights": dict(zip(("bm25", "dense"), WEIGHTS, strict=True)),
        },
        "candidates_per_retriever": CANDIDATES,
        "rerank_candidates": CANDIDATES,
        "final_top_k": TOP_K,
        "rerank_token_limit": MAX_TOKENS,
        "rerank_batch_size": BATCH_SIZE,
        "rerank_models": MODELS,
    }
    (run_dir / "config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )

    for name, result in (("candidates", candidates), ("rrf", baseline), ("kiwi", kiwi)):
        output = run_dir / name
        output.mkdir()
        save_results(result, output)
    log(f"RRF 후보 고정 · {len(search_times)}문항 · 문항당 최대 {CANDIDATES}쪽")

    for model_id, revision in MODELS.items():
        log(f"{model_id} · 평가 시작")
        result = evaluate_model(
            model_id, revision, frozen, warmup, search_times, baseline, log
        )
        output = run_dir / model_id.replace("/", "-")
        output.mkdir()
        save_results(result, output)
        overall = result["summary"]["overall"]
        log(
            f"{model_id} · Hit@3 {overall['hit']:.2%} · Recall@3 {overall['recall']:.2%} · Precision@3 {overall['precision']:.2%}"
        )
    log("리랭커 비교 완료")


if __name__ == "__main__":
    main()
