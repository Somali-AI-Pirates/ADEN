import argparse
import json
from datetime import datetime
from time import perf_counter
from traceback import print_exc
from zoneinfo import ZoneInfo

import numpy as np
import torch
from sentence_transformers import MultiVectorEncoder, SentenceTransformer

from experimental.u_10o0o.embeding import (
    BATCH_SIZE,
    CPU_THREADS,
    MODEL_ID,
    MODEL_REVISION,
)
from experimental.u_10o0o.evaluate import RECORDS_DIR, evaluate, save_results
from experimental.u_10o0o.prepare import CACHE_PATH, DATASET
from experimental.u_10o0o.search import TOP_K, rank_pages, top_pages

GTE = "alibaba-nlp-community/gte-multilingual-base"
GTE_REVISION = "fdaec56c6a50d6ebcf691f616ee83a166346b76c"
MODELS = {
    MODEL_ID: MODEL_REVISION,
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
    "nlpai-lab/KURE-v1": "8b418a58414668e75532ed045c22d9ca018ae2b2",
    GTE: GTE_REVISION,
}
KURE_V2 = "nlpai-lab/KURE-v2"
GEMMA = "google/embeddinggemma-2"
KANANA = "kakaocorp/kanana-2-1.3b-embedding"
CANDIDATES = {
    MODEL_ID: MODEL_REVISION,
    GTE: GTE_REVISION,
    "hotchpotch/bekko-embedding-v1-a25m": "44f0b8af0f487acd0ccf1a7cb7ae7a29a6dfc09c",
    "nlpai-lab/KURE-v3": "73d5e710bc0b1f324a60746922cbad6d38f480d4",
    GEMMA: "f9972ac22e696a105c482202646979e33e4b5a1e",
    KANANA: "107b09b19ea3628acbd42615ee9ad59b82f604a3",
    KURE_V2: "3431f86d399d666083890dbb882aced6708873bc",
}


def evaluate_model(model_id, revision, pages, artifact_dir, record_dir):
    print(f"CPU 평가 시작 | {model_id}", flush=True)
    multivector = model_id == KURE_V2
    model_class = MultiVectorEncoder if multivector else SentenceTransformer
    model_kwargs: dict[str, str | torch.dtype] = {"dtype": torch.float32}
    config_kwargs = {}
    if model_id == KANANA:
        model_kwargs["attn_implementation"] = "eager"
        # The current Qwen3 forward builds its mask from the config.
        config_kwargs = {"is_causal": False, "use_cache": False}
    elif model_id == GEMMA:
        config_kwargs = {"vision_config": None, "audio_config": None}
    model = model_class(
        model_id,
        revision=revision,
        device="cpu",
        local_files_only=True,
        trust_remote_code=model_id == KANANA,
        model_kwargs=model_kwargs,
        config_kwargs=config_kwargs,
        processor_kwargs={"model_max_length": 8192} if model_id == GEMMA else None,
    )
    query_prompt_name = "QuestionAnswering" if model_id == GEMMA else "query"
    query_prompt = model.prompts.get(query_prompt_name) or ""
    document_prompt = model.prompts.get("document") or ""
    texts: list[str] = [page["text"] for page in pages]
    if model_id == GEMMA:
        features = model.preprocess(texts, prompt=document_prompt, truncation=False)
        max_page_tokens = int(features["attention_mask"].sum(dim=1).max())
    else:
        input_ids = model.tokenizer(
            [document_prompt + item for item in texts], truncation=False, verbose=False
        )["input_ids"]
        max_page_tokens = max(map(len, input_ids))
    token_limit = model.max_seq_length
    if token_limit is None:
        raise ValueError(f"{model_id}의 입력 토큰 한도를 확인할 수 없습니다.")
    if max_page_tokens > token_limit:
        raise ValueError(f"{model_id}의 입력 한도를 넘는 페이지가 있습니다.")

    model.encode_document(
        texts[:BATCH_SIZE], normalize_embeddings=True, show_progress_bar=False
    )
    started = perf_counter()
    vectors = model.encode_document(
        texts, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=True
    )
    document_seconds = perf_counter() - started

    artifact_dir.mkdir(parents=True)
    if isinstance(vectors, list):
        vectors_path = artifact_dir / "document_token_vectors.npz"
        offsets = np.concatenate(([0], np.cumsum([len(page) for page in vectors])))
        token_vectors = torch.cat(vectors).numpy()
        np.savez(vectors_path, vectors=token_vectors, page_offsets=offsets)
        vector_shape = list(token_vectors.shape)
    else:
        vectors_path = artifact_dir / "document_vectors.npy"
        np.save(vectors_path, vectors, allow_pickle=False)
        vector_shape = list(vectors.shape)
    query_times = []
    search_times = []

    def search_question(question):
        query_started = perf_counter()
        vector = model.encode_query(
            question,
            prompt=query_prompt,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        query_times.append((perf_counter() - query_started) * 1000)
        if isinstance(model, MultiVectorEncoder):
            scores = model.similarity(vector, vectors, length_normalize=False)[0]
            ranked = top_pages(scores.numpy(), pages)
        else:
            ranked = rank_pages(vector, vectors, pages)
        search_times.append((perf_counter() - query_started) * 1000)
        return ranked

    result = evaluate(
        model_id,
        vectors_path,
        search_question,
        similarity="maxsim" if multivector else "cosine",
    )
    result["revision"] = revision
    result["runtime"] = {
        "device": str(model.device),
        "dtype": str(next(model.parameters()).dtype),
        "cpu_threads": torch.get_num_threads(),
        "batch_size": BATCH_SIZE,
        "vector_shape": vector_shape,
        "vector_file_bytes": vectors_path.stat().st_size,
        "document_prompt": document_prompt,
        "query_prompt": query_prompt,
        "max_page_tokens": max_page_tokens,
        "token_limit": token_limit,
        "document_encoding_seconds": document_seconds,
        "query_count": len(query_times),
        "query_encoding_p50_ms": float(np.median(query_times)),
        "query_encoding_p95_ms": float(np.percentile(query_times, 95)),
        "search_p50_ms": float(np.median(search_times)),
        "search_p95_ms": float(np.percentile(search_times, 95)),
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    save_results(result, record_dir)
    print(
        f"CPU 평가 완료 | {model_id} | 문서 임베딩 {document_seconds:.1f}초", flush=True
    )
    return result


def comparison_report(results):
    lines = [
        f"# {DATASET.capitalize()} 임베딩 모델 CPU 비교",
        "",
        f"동일 문서와 일반 평가 문항 · 페이지 검색 · 상위 {TOP_K}쪽",
        f"CPU {CPU_THREADS}스레드 · float32 · 문서 배치 {BATCH_SIZE} · 실행 1회",
        f"실험 기준 모델 `{MODEL_ID}`",
        "",
    ]
    lines.extend(
        comparison_table([row for row in results if row[1]["similarity"] == "cosine"])
    )
    late_interaction = [row for row in results if row[1]["similarity"] == "maxsim"]
    if late_interaction:
        lines.extend(["", "## 토큰 벡터 검색", ""])
        lines.extend(comparison_table(late_interaction))
        runtime = late_interaction[0][1]["runtime"]
        lines.extend(
            [
                "",
                "KURE-v2는 페이지마다 토큰 벡터를 만들고 전체 페이지를 MaxSim으로 검색했다.",
                (
                    f"질문 임베딩과 순위 계산을 합친 p50은 {runtime['search_p50_ms']:.1f}ms, "
                    f"p95는 {runtime['search_p95_ms']:.1f}ms다."
                ),
            ]
        )
    lines.extend(
        [
            "",
            "시간은 모델을 불러오고 예열한 뒤 임베딩에 걸린 시간이다.",
            "문서 시간은 전체 페이지, 질문 시간은 한 문항씩 임베딩한 시간의 분포다.",
            "근거 페이지가 없는 문항은 검색에 포함하고 점수 평균에서 제외했다.",
            "모델별 기록 폴더에 묶음별 보고서, 문항별 순위와 입력 해시를 저장했다.",
            "문서 벡터 파일은 로컬 artifacts 폴더에 저장했다.",
            "",
        ]
    )
    return "\n".join(lines)


def comparison_table(results):
    lines = [
        f"| 모델 | 채점 | Hit@{TOP_K} | Recall@{TOP_K} | Precision@{TOP_K} | 문서 임베딩 | 질문 p50 | 질문 p95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for folder, result in results:
        scores = result["summary"]["overall"]
        runtime = result["runtime"]
        lines.append(
            f"| [{result['model_id']}]({folder}/report.md) | {scores['scored']} | "
            f"{scores['hit']:.2%} | {scores['recall']:.2%} | {scores['precision']:.2%} | "
            f"{runtime['document_encoding_seconds']:.1f}초 | "
            f"{runtime['query_encoding_p50_ms']:.1f}ms | {runtime['query_encoding_p95_ms']:.1f}ms |"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"{DATASET.capitalize()} 페이지 임베딩 CPU 비교"
    )
    parser.add_argument(
        "--candidates", action="store_true", help="기준 모델과 후속 후보 비교"
    )
    args = parser.parse_args()
    torch.set_num_threads(CPU_THREADS)
    torch.set_num_interop_threads(1)
    pages = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    run_at = datetime.now(ZoneInfo("Asia/Seoul"))
    run_id = f"{DATASET}-cpu-comparison-{run_at:%Y%m%d-%H%M%S-%f}"
    artifact_root = CACHE_PATH.parent / "retrieval_evaluations" / run_id
    record_root = RECORDS_DIR / run_id
    artifact_root.mkdir(parents=True)
    record_root.mkdir(parents=True)

    results = []
    failed = []
    for model_id, revision in (CANDIDATES if args.candidates else MODELS).items():
        folder = model_id.replace("/", "-")
        try:
            result = evaluate_model(
                model_id,
                revision,
                pages,
                artifact_root / folder,
                record_root / folder,
            )
        except Exception:  # noqa: BLE001 - keep the other model runs after a failure.
            print_exc()
            failed.append(model_id)
            continue
        results.append((folder, result))

    summary = comparison_report(results)
    if failed:
        summary += f"\n실패한 모델 {', '.join(failed)}\n"
    (record_root / "report.md").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"기록 위치 {record_root}")
    print(f"벡터 위치 {artifact_root}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
