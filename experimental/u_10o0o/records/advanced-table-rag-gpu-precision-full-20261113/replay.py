"""Verify the local FastAPI loader and replay FP32/BF16 on shared exact candidates."""

import argparse
import json
from hashlib import sha256
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import torch
from fastapi.testclient import TestClient
from sentence_transformers import CrossEncoder

from app.api.main import app
from app.rag.evidence import assemble_evidence
from app.rag.retrieval import TableRetriever, _rrf, rank_dense
from shared.table_rag_policy import (
    BM25_TOP_K,
    DENSE_TOP_K,
    RERANK_BATCH_SIZE,
    RERANK_MAX_LENGTH,
    RERANKER_ID,
    RERANKER_REVISION,
)

QUESTIONS = Path("artifacts/u_10o0o/advanced-table-rag-20261111/v5/questions.json")


def replay_row(retriever, runner, question, candidates):
    ranked, work = TableRetriever._rerank(runner, question, candidates)
    lookup = retriever.corpus.pages_by_source
    pages = [lookup[unit.metadata["source_id"]] for unit, _ in ranked]
    context = assemble_evidence(question, [pages], (), (), lookup.get)
    return {
        "rank": [unit.metadata["unit_id"] for unit, _ in ranked],
        "scores": [score for _, score in ranked],
        "final_sources": context.source_ids,
        "context_sha256": context.context_hash,
        "context_tokens": context.token_count,
        "work": work,
        "table_ids": retriever._table_ids(ranked, pages),
    }


def model_device_dtype(model):
    params = {
        (str(p.device), str(p.dtype))
        for p in model.parameters()
        if p.is_floating_point()
    }
    if len(params) != 1:
        raise ValueError("Unexpected mixed parameter placement")
    return next(iter(params))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run(output):
    output.mkdir(exist_ok=False)
    question_data = QUESTIONS.read_bytes()
    questions = json.loads(question_data)
    if len(questions) != 280 or len({q["id"] for q in questions}) != 280:
        raise ValueError("Expected the frozen 60+220 questions")
    with TestClient(app) as client:
        readiness = client.get("/health/ready")
        if readiness.status_code != 200:
            raise RuntimeError(f"Server not ready: {readiness.status_code}")
        service = app.state.table_service
        retriever = service._graph.dependencies.search.__self__
        settings = app.state.settings
        if settings.device != "cuda" or settings.vector_backend != "qdrant":
            raise ValueError("Expected local CUDA and Qdrant configuration")
        device, dtype = model_device_dtype(retriever.reranker)
        actual = retriever.manifest["retrieval"]["reranker"]
        if (
            device != "cuda:0"
            or dtype != "torch.bfloat16"
            or actual["dtype"] != "bfloat16"
        ):
            raise ValueError("Server did not load the BF16 CUDA reranker")
        smoke = retriever.search(questions[0]["question"])
        stages = smoke.diagnostics["stages"]
        if len(smoke.diagnostics["rrf_candidates"]) != 20 or stages["reranker_ms"] <= 0:
            raise ValueError("Server did not rerank 20 candidates")
        write_json(
            output / "server-check.json",
            {
                "http_readiness": readiness.status_code,
                "readiness": readiness.json(),
                "device": settings.device,
                "backend": settings.vector_backend,
                "gpu": torch.cuda.get_device_name(),
                "manifest": actual,
                "actual_reranker_parameter_device": device,
                "actual_reranker_parameter_dtype": dtype,
                "sample_id": questions[0]["id"],
                "sample_stages": stages,
                "reranked_candidates": len(smoke.diagnostics["rrf_candidates"]),
                "llm_calls": 0,
            },
        )
        print(
            "FastAPI readiness 200, CUDA BF16 reranker executed 20 candidates",
            flush=True,
        )
        fixed = []
        for index, row in enumerate(questions, 1):
            question = row["question"].strip()
            vector = retriever._query_vector(question)
            sparse = retriever._bm25.fielded_search(question, top_k=BM25_TOP_K)
            dense = rank_dense(retriever.corpus, vector, top_k=DENSE_TOP_K)
            candidates = _rrf(sparse, dense)
            if (
                len(candidates) != 20
                or len({unit.metadata["source_id"] for unit, _ in candidates}) != 20
            ):
                raise ValueError(f"Missing distinct candidates for {row['id']}")
            fixed.append(candidates)
            if index % 70 == 0:
                print(f"Frozen {index}/280 candidate sets", flush=True)

        fp32 = CrossEncoder(
            RERANKER_ID,
            revision=RERANKER_REVISION,
            device="cuda:0",
            max_length=RERANK_MAX_LENGTH,
            local_files_only=True,
            model_kwargs={"dtype": torch.float32, "attn_implementation": "sdpa"},
        )
        if model_device_dtype(fp32) != ("cuda:0", "torch.float32"):
            raise ValueError("Control reranker did not load in CUDA FP32")
        runners = {
            "fp32": SimpleNamespace(reranker=fp32, _reranker_lock=Lock()),
            "bf16": retriever,
        }
        rows = []
        for index, (row, candidates) in enumerate(
            zip(questions, fixed, strict=True), 1
        ):
            ordered = ("bf16", "fp32") if (index - 1) // 20 % 2 else ("fp32", "bf16")
            arms = {
                name: replay_row(
                    retriever, runners[name], row["question"].strip(), candidates
                )
                for name in ordered
            }
            if arms["fp32"]["work"] != arms["bf16"]["work"]:
                raise ValueError(f"Token inputs differ for {row['id']}")
            candidate_ids = [unit.metadata["unit_id"] for unit, _ in candidates]
            if any(set(arm["rank"]) != set(candidate_ids) for arm in arms.values()):
                raise ValueError(
                    f"Reranking changed candidate identity for {row['id']}"
                )
            rows.append({"id": row["id"], "candidates": candidate_ids, **arms})
            if index % 40 == 0:
                print(f"Reranked {index}/280 in both precisions", flush=True)
        write_json(
            output / "replay.json",
            {
                "questions_sha256": sha256(question_data).hexdigest(),
                "snapshot_sha256": service.manifest["snapshot_sha256"],
                "runtime_sha256": service.manifest["module_sha256"],
                "runtime_versions": service.manifest["runtime_versions"],
                "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "candidate_search": "local exact cosine + product BM25/RRF",
                "rerank_batch": RERANK_BATCH_SIZE,
                "rerank_max_length": RERANK_MAX_LENGTH,
                "references_opened": False,
                "rows": rows,
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output)
