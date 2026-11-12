"""Experimental KURE-v2 MaxSim backend; shared TableRAG search/SQL/context flow."""

from pathlib import Path
from threading import Lock
from time import perf_counter

import numpy as np
import torch
from sentence_transformers import CrossEncoder
from torch.nn.utils.rnn import pad_sequence

from experimental.u_10o0o.embedding_ablation.__main__ import (
    DEFAULT_RUN,
    validate_assets,
)
from experimental.u_10o0o.embedding_ablation.kure_v2 import (
    load_model,
    MaxSimIndex,
    MODEL_ID,
    REVISION,
    TOKEN_DIM,
    PAGE_BLOCK,
)
from experimental.u_10o0o.embedding_ablation.kure_resolution import configure_query
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.retrieval import Retriever, KURE, RERANK_MODELS
from experimental.u_10o0o.table_rag.speed import V5, wait_gpu_available

QUERY_LIMIT = 128
RERANK_LIMIT = 1024
GPU_FRACTION = 0.65


def validate_page_input():
    frozen = read_json(DEFAULT_RUN / "frozen.json")
    item = next(
        r for r in frozen["baseline"]["inputs"] if ROOT / r["path"] == CACHE_PATH
    )
    if file_sha(CACHE_PATH) != item["sha256"]:
        raise ValueError("Original page text/order differs from KURE-v2 index input")
    assets = validate_assets(DEFAULT_RUN, "kure_v2")
    if (assets["model"], assets["revision"]) != (MODEL_ID, REVISION):
        raise ValueError("Unexpected KURE-v2 revision")
    return assets


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    assets = validate_page_input()
    chunks = read_json(V5 / "table-chunks.json")
    wait_gpu_available()
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(GPU_FRACTION, 0)
    model = load_model()
    lengths = []
    path = output / "table-tokens.f16"
    tick = perf_counter()
    with path.open("wb") as stream:
        for start in range(0, len(chunks), 64):
            rows = chunks[start : start + 64]
            vectors = model.encode_document(
                [r["text"] for r in rows], batch_size=8, show_progress_bar=False
            )
            for v in vectors:
                a = v.to(dtype=torch.float16, device="cpu").numpy()
                if (
                    a.shape[1:] != (TOKEN_DIM,)
                    or not len(a)
                    or not np.isfinite(a).all()
                ):
                    raise ValueError("Invalid table token embedding")
                lengths.append(len(a))
                stream.write(a.tobytes())
            if len(lengths) % 512 == 0 or len(lengths) == len(chunks):
                print(f"Table token vectors {len(lengths)}/{len(chunks)}", flush=True)
    if max(lengths) >= model.max_seq_length:
        raise ValueError("Table reached document encoder limit")
    length_path = output / "table-lengths.npy"
    np.save(length_path, np.array(lengths, dtype=np.int32), allow_pickle=False)
    if path.stat().st_size != sum(lengths) * TOKEN_DIM * 2:
        raise ValueError("Table vectors/offsets mismatch")
    write_json(
        output / "vectors-manifest.json",
        {
            "status": "complete",
            "model": MODEL_ID,
            "revision": REVISION,
            "input_sha256": file_sha(V5 / "table-chunks.json"),
            "unit_ids_sha256": object_sha([r["chunk_id"] for r in chunks]),
            "source_pages_sha256": file_sha(CACHE_PATH),
            "raw_page_manifest_sha256": file_sha(
                DEFAULT_RUN / "kure_v2/vectors-manifest.json"
            ),
            "raw_page_count": assets["document_count"],
            "table_count": len(lengths),
            "table_token_count": sum(lengths),
            "document_max_tokens": model.max_seq_length,
            "maximum_table_vectors": max(lengths),
            "table_vectors_sha256": file_sha(path),
            "lengths_sha256": file_sha(length_path),
            "table_vector_bytes": path.stat().st_size,
            "build_seconds": perf_counter() - tick,
            "model_dtype": str(model.dtype),
            "index_dtype": "float16",
            "batch_size": 8,
        },
    )


def append_tables(index, output):
    state = read_json(output / "vectors-manifest.json")
    for path, sha in (
        (output / "table-tokens.f16", state["table_vectors_sha256"]),
        (output / "table-lengths.npy", state["lengths_sha256"]),
        (V5 / "table-chunks.json", state["input_sha256"]),
    ):
        if file_sha(path) != sha:
            raise ValueError("Table index fingerprint changed")
    lengths = np.load(output / "table-lengths.npy", allow_pickle=False)
    if len(lengths) != state["table_count"] or np.any(lengths <= 0):
        raise ValueError("Invalid table offsets")
    offsets = np.concatenate(([0], lengths.cumsum(dtype=np.int64)))
    if offsets[-1] != state["table_token_count"]:
        raise ValueError("Table token count mismatch")
    tokens = np.memmap(
        output / "table-tokens.f16",
        mode="r",
        dtype=np.float16,
        shape=(int(offsets[-1]), TOKEN_DIM),
    )
    order = lengths.argsort(kind="stable")
    base = index.page_count
    for start in range(0, len(order), PAGE_BLOCK):
        ids = order[start : start + PAGE_BLOCK]
        arrays = [
            torch.from_numpy(tokens[offsets[i] : offsets[i + 1]].copy()) for i in ids
        ]
        docs = pad_sequence(arrays, batch_first=True).to("cuda")
        valid = (
            torch.arange(docs.shape[1], device="cuda")
            < torch.from_numpy(lengths[ids]).to("cuda")[:, None]
        )
        index.blocks.append((torch.from_numpy(ids + base).to("cuda"), docs, valid))
    index.page_count += len(lengths)


class KureV2Retriever(Retriever):
    def __init__(self, output):
        output = Path(output)
        state = read_json(output / "vectors-manifest.json")
        assets = validate_page_input()
        if state["status"] != "complete" or state[
            "raw_page_manifest_sha256"
        ] != file_sha(DEFAULT_RUN / "kure_v2/vectors-manifest.json"):
            raise ValueError("Frozen source index changed")
        self._prepare_inputs(V5)
        if (
            len(self.pages) != assets["document_count"]
            or object_sha([r["chunk_id"] for r in self.chunks])
            != state["unit_ids_sha256"]
        ):
            raise ValueError("Source row identity differs")
        torch.set_num_threads(2)
        torch.cuda.set_per_process_memory_fraction(GPU_FRACTION, 0)
        self.embedder = load_model()
        configure_query(self.embedder, QUERY_LIMIT)
        self.index = MaxSimIndex(DEFAULT_RUN)
        append_tables(self.index, output)
        if len(self.units) != self.index.page_count:
            raise ValueError("Search units do not align with token vectors")
        self.reranker_device = "cuda"
        self.reranker = CrossEncoder(
            KURE,
            revision=RERANK_MODELS[KURE],
            device="cuda",
            max_length=RERANK_LIMIT,
            local_files_only=True,
            model_kwargs={"dtype": torch.float32, "attn_implementation": "sdpa"},
        )
        self.lock = Lock()
        # Both models and MaxSim use the same GPU; protect its working memory.
        self.embedding_lock = self.reranking_lock = Lock()
        self.calls = 0

    def _encode_query(self, question):
        return self.embedder.encode_query(
            [question], batch_size=1, show_progress_bar=False
        )[0].to(dtype=torch.float16, device="cuda")

    def _dense_rank(self, query):
        with self.embedding_lock:
            scores = self.index.score(query.unsqueeze(0))[0].cpu().numpy()
        ids = np.argsort(-scores, kind="stable")[:50]
        return [(self.units[i], float(scores[i])) for i in ids]

    def search(self, question):
        started = perf_counter()
        with self.embedding_lock:
            prompt = self.embedder.prompts["query"]
            raw = len(
                self.embedder.tokenizer(prompt + question, truncation=False)[
                    "input_ids"
                ]
            )
        result = super().search(question)
        result["query_input"] = {
            "raw_tokens": raw,
            "max_tokens": QUERY_LIMIT,
            "expansion_floor": 64,
            "truncated": raw > QUERY_LIMIT,
        }
        result["seconds"] = perf_counter() - started
        return result
