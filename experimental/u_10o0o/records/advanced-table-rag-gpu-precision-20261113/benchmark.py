"""Replay frozen product candidates with KURE nano in FP32 and BF16 on CUDA."""

import argparse
import gc
import json
import os
import subprocess
import threading
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from app.rag.corpus import load_corpus
from app.rag.evidence import assemble_evidence
from app.rag.retrieval import TableRetriever
from shared.table_rag_policy import (
    RERANK_BATCH_SIZE,
    RERANK_MAX_LENGTH,
    RERANKER_ID,
    RERANKER_REVISION,
)

BUNDLE = Path("artifacts/advanced-table-rag")
FROZEN = Path("artifacts/u_10o0o/table-rag-cpu-threads-20261112/frozen.json")
ARM_ORDER = ("float32", "bfloat16", "bfloat16", "float32", "float32", "bfloat16")
MIB = 1024**2


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def gpu_status():
    command = [
        "nvidia-smi",
        "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
        "-i",
        "0",
    ]
    output = subprocess.check_output(command, text=True, timeout=5)
    used, util, temp = (int(value.strip()) for value in output.strip().split(","))
    return {"used_mib": used, "util_percent": util, "temperature_c": temp}


def load_frozen_inputs():
    frozen = json.loads(FROZEN.read_bytes())
    if digest(BUNDLE / "corpus.json") != frozen["inputs"][str(BUNDLE / "corpus.json")]:
        raise ValueError("Current corpus differs from the frozen candidate bundle.")
    policy = {
        "model_id": RERANKER_ID,
        "revision": RERANKER_REVISION,
        "max_length": RERANK_MAX_LENGTH,
        "batch_size": RERANK_BATCH_SIZE,
        "dtype": "float32",
    }
    if frozen["retrieval"]["reranker"] != policy:
        raise ValueError("Reranker policy differs from the frozen candidate bundle.")
    return frozen["rows"]


def resolve_candidates(row, documents):
    candidates = []
    for saved in row["candidates"]:
        document = documents[saved["unit_id"]]
        fields = ("source_id", "file_name", "page", "table_id")
        if any(document.metadata.get(key) != saved[key] for key in fields):
            raise ValueError(f"Frozen candidate metadata differs for {row['id']}.")
        candidates.append((document, saved["score"]))
    if (
        len(candidates) != 20
        or len({doc.metadata["source_id"] for doc, _ in candidates}) != 20
    ):
        raise ValueError(f"Expected 20 distinct source pages for {row['id']}.")
    return row["id"], row["question"], candidates


def measure_arm(dtype, rows, sweep_orders):
    gpu_before = gpu_status()
    model = CrossEncoder(
        RERANKER_ID,
        revision=RERANKER_REVISION,
        device="cuda:0",
        max_length=RERANK_MAX_LENGTH,
        local_files_only=True,
        model_kwargs={"dtype": getattr(torch, dtype), "attn_implementation": "sdpa"},
    )
    actual_dtypes = {str(p.dtype) for p in model.parameters() if p.is_floating_point()}
    if actual_dtypes != {f"torch.{dtype}"}:
        raise ValueError(f"Expected {dtype}, got {actual_dtypes}.")

    runner = SimpleNamespace(reranker=model, _reranker_lock=threading.Lock())
    longest = max(rows, key=lambda row: sum(len(doc.page_content) for doc, _ in row[2]))
    warmup = (longest, rows[0], rows[-1])
    for _, question, candidates in warmup:
        TableRetriever._rerank(runner, question, candidates)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    samples = []
    for order in sweep_orders:
        for index in order:
            question_id, question, candidates = rows[index]
            torch.cuda.synchronize()
            started = perf_counter()
            ranked, work = TableRetriever._rerank(runner, question, candidates)
            torch.cuda.synchronize()
            samples.append(
                {
                    "id": question_id,
                    "ms": (perf_counter() - started) * 1000,
                    "order": [doc.metadata["unit_id"] for doc, _ in ranked],
                    "scores": [score for _, score in ranked],
                    "work": work,
                }
            )

    return {
        "dtype": dtype,
        "gpu_before": gpu_before,
        "gpu_after": gpu_status(),
        "peak_allocated_mib": torch.cuda.max_memory_allocated() / MIB,
        "peak_reserved_mib": torch.cuda.max_memory_reserved() / MIB,
        "samples": samples,
    }


def add_contexts(sessions, corpus, rows, documents):
    questions = {question_id: question for question_id, question, _ in rows}
    cached = {}
    for session in sessions:
        for sample in session["samples"]:
            key = (sample["id"], tuple(sample["order"]))
            if key not in cached:
                pages = [
                    corpus.pages_by_source[documents[unit].metadata["source_id"]]
                    for unit in sample["order"]
                ]
                evidence = assemble_evidence(
                    questions[sample["id"]], [pages], (), (), corpus.pages_by_source.get
                )
                cached[key] = {
                    "page_order": [page.metadata["source_id"] for page in pages],
                    "final_pages": evidence.source_ids,
                    "context_tokens": evidence.token_count,
                    "context_sha256": evidence.context_hash,
                }
            sample.update(cached[key])


def main(output):
    if output.exists():
        raise FileExistsError(output)
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(
        including_emulation=False
    ):
        raise RuntimeError("A CUDA GPU with native BF16 support is required.")
    torch.set_num_threads(2)
    torch.backends.cuda.matmul.fp32_precision = "ieee"
    torch.backends.cudnn.conv.fp32_precision = "ieee"
    torch.set_float32_matmul_precision("highest")
    sources = [
        FROZEN,
        BUNDLE / "corpus.json",
        Path("app/rag/retrieval.py"),
        Path("app/rag/evidence.py"),
        Path("shared/table_rag_policy.py"),
        Path(__file__).resolve().relative_to(Path.cwd()),
    ]
    fingerprints = {str(path): digest(path) for path in sources}
    frozen = load_frozen_inputs()
    with closing(load_corpus(BUNDLE)) as corpus:
        documents = {doc.metadata["unit_id"]: doc for doc in corpus.documents}
        rows = [resolve_candidates(row, documents) for row in frozen]
        sessions = []
        for session_index, dtype in enumerate(ARM_ORDER):
            indexes = np.roll(np.arange(len(rows)), 5 * (session_index // 2)).tolist()
            sessions.append(measure_arm(dtype, rows, (indexes, indexes[::-1])))
            print(
                f"{session_index + 1}/{len(ARM_ORDER)} {dtype}: {len(indexes) * 2} samples",
                flush=True,
            )
            gc.collect()
            torch.cuda.empty_cache()
        add_contexts(sessions, corpus, rows, documents)
    if any(digest(path) != fingerprints[str(path)] for path in sources):
        raise ValueError("An experiment input changed during replay.")
    result = {
        "environment": {
            "gpu": torch.cuda.get_device_name(0),
            "gpu_mib": torch.cuda.get_device_properties(0).total_memory / MIB,
            "cuda": torch.version.cuda,
            "dependencies": {
                name: version(name)
                for name in ("torch", "transformers", "sentence-transformers")
            },
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "torch_threads": torch.get_num_threads(),
            "tf32": False,
            "allocator": os.environ.get("PYTORCH_CUDA_ALLOC_CONF"),
        },
        "fingerprints": fingerprints,
        "reranker": {
            "id": RERANKER_ID,
            "revision": RERANKER_REVISION,
            "attention": "sdpa",
            "batch": RERANK_BATCH_SIZE,
            "max_length": RERANK_MAX_LENGTH,
            "candidates": 20,
        },
        "sessions": sessions,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args().output)
