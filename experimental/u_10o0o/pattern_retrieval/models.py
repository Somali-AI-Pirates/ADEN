"""Pinned local models, exact vectors and resumable *new* input embedding."""

import os
import sqlite3
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
from huggingface_hub import constants as hf_constants
from sentence_transformers import CrossEncoder, SentenceTransformer
from threadpoolctl import threadpool_limits

from .common import (
    CONFIG,
    CPU_THREADS,
    EMBED_BATCH,
    EMBED_MODEL,
    EMBED_REVISION,
    MAX_INPUT,
    RERANK_BATCH,
    RERANK_MODEL,
    RERANK_REVISION,
    file_sha,
    object_sha,
    read_json,
    write_json,
)


def model_assets(name, revision):
    snapshot = (
        Path(hf_constants.HF_HUB_CACHE)
        / ("models--" + name.replace("/", "--"))
        / "snapshots"
        / revision
    )
    files = sorted(p for p in snapshot.rglob("*") if p.is_file())
    if not files or not (snapshot / "config.json").is_file():
        raise ValueError("Pinned local model assets are missing.")
    return [
        {"path": str(p.relative_to(snapshot)), "sha256": file_sha(p)} for p in files
    ]


def setup_device(device):
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("GPU quality execution requires CUDA.")
    threadpool_limits(limits=CPU_THREADS)
    torch.set_num_threads(CPU_THREADS)
    torch.set_float32_matmul_precision("highest")
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False


def embedder(device):
    setup_device(device)
    model = SentenceTransformer(
        EMBED_MODEL,
        revision=EMBED_REVISION,
        device=device,
        local_files_only=True,
        model_kwargs={"dtype": torch.float32},
    )
    model.max_seq_length = MAX_INPUT
    return model


def reranker(device):
    setup_device(device)
    return CrossEncoder(
        RERANK_MODEL,
        revision=RERANK_REVISION,
        device=device,
        local_files_only=True,
        max_length=MAX_INPUT,
        model_kwargs={"dtype": torch.float32},
    )


def synchronize(device):
    if device == "cuda":
        torch.cuda.synchronize()


def token_lengths(tokenizer, texts):
    return [
        len(ids)
        for ids in tokenizer(
            texts, truncation=False, add_special_tokens=True, padding=False
        )["input_ids"]
    ]


def build_vectors(index_dir, log):
    """Resume only when the freshly built corpus and fixed model inputs match."""
    index_dir = Path(index_dir)
    database = index_dir / "corpus.sqlite3"
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT unit_id,text FROM units ORDER BY unit_id"
        ).fetchall()
    if not rows:
        raise ValueError("No corpus units.")
    identity = {
        "corpus_sha256": file_sha(database),
        "config_sha256": object_sha(CONFIG),
        "unit_ids_sha256": object_sha([r[0] for r in rows]),
        "unit_count": len(rows),
        "embedding_runtime": {
            name: file_sha(Path(__file__).with_name(name))
            for name in ("models.py", "common.py")
        },
        "embedding_assets": model_assets(EMBED_MODEL, EMBED_REVISION),
    }
    final_manifest = index_dir / "vectors-manifest.json"
    if final_manifest.exists():
        manifest = read_json(final_manifest)
        if manifest["identity"] != identity:
            raise ValueError("Completed vectors have different inputs.")
        if file_sha(index_dir / "vectors.npy") != manifest["vectors_sha256"]:
            raise ValueError("Vector file hash mismatch.")
        log("Existing completed fresh vectors verified.")
        return manifest
    if (index_dir / "vectors.npy").exists():
        raise ValueError(
            "Unpublished final vectors exist; preserve this directory and use a new output."
        )
    checkpoint = index_dir / "vectors-checkpoint.json"
    partial = index_dir / "vectors.partial.npy"
    if checkpoint.exists():
        progress = read_json(checkpoint)
        if progress["identity"] != identity:
            raise ValueError("Cannot resume vectors with different inputs.")
    else:
        if partial.exists():
            raise ValueError(
                "Partial vectors without checkpoint; use a new output directory."
            )
        progress = {
            "identity": identity,
            "completed": 0,
            "embedding_seconds": 0.0,
            "raw_tokens": 0,
            "input_tokens": 0,
            "truncated_units": 0,
            "encoded_units": 0,
            "encoded_input_tokens": 0,
            "encoded_raw_tokens": 0,
            "reused_identical_texts": 0,
        }
    model = embedder("cuda")
    dimension = model.get_sentence_embedding_dimension()
    start = progress["completed"]
    vectors = np.lib.format.open_memmap(
        partial,
        mode="r+" if partial.exists() else "w+",
        dtype=np.float32,
        shape=(len(rows), dimension),
    )
    if not checkpoint.exists():
        vectors.flush()
        write_json(checkpoint, progress)
    seen = {}
    for i in range(start):
        seen.setdefault(rows[i][1], i)
    log(f"Embedding fresh corpus: {start}/{len(rows)} units, dimension={dimension}.")
    for begin in range(start, len(rows), 256):
        end = min(begin + 256, len(rows))
        texts = [rows[i][1] for i in range(begin, end)]
        lengths = token_lengths(model.tokenizer, texts)
        progress["raw_tokens"] += sum(lengths)
        progress["input_tokens"] += sum(min(n, MAX_INPUT) for n in lengths)
        progress["truncated_units"] += sum(n > MAX_INPUT for n in lengths)
        missing = []
        for i in range(begin, end):
            text = rows[i][1]
            if text not in seen:
                seen[text] = i
                missing.append(i)
        begun = perf_counter()
        if missing:
            encoded = model.encode_document(
                [rows[i][1] for i in missing],
                batch_size=EMBED_BATCH,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vectors[missing] = np.asarray(encoded, dtype=np.float32)
            synchronize("cuda")
        progress["embedding_seconds"] += perf_counter() - begun
        for i in range(begin, end):
            source = seen[rows[i][1]]
            if source != i:
                vectors[i] = vectors[source]
        progress["encoded_units"] += len(missing)
        progress["encoded_raw_tokens"] += sum(lengths[i - begin] for i in missing)
        progress["encoded_input_tokens"] += sum(
            min(lengths[i - begin], MAX_INPUT) for i in missing
        )
        progress["reused_identical_texts"] += end - begin - len(missing)
        progress["completed"] = end
        vectors.flush()
        write_json(checkpoint, progress)
        if end % 1024 == 0 or end == len(rows):
            log(f"Fresh embeddings saved {end}/{len(rows)} units.")
    if not np.isfinite(vectors).all():
        raise ValueError("Nonfinite embeddings.")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise ValueError("Document vectors must be unit normalized.")
    del vectors
    os.replace(partial, index_dir / "vectors.npy")
    manifest = {
        "status": "complete",
        "identity": identity,
        "config": CONFIG,
        "dimension": dimension,
        "vectors_sha256": file_sha(index_dir / "vectors.npy"),
        "statistics": progress,
        "device": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
    }
    write_json(final_manifest, manifest)
    checkpoint.unlink()
    log("Fresh vector snapshot complete.")
    return manifest


def predict_scores(model, question, texts, device):
    lengths = [
        len(ids)
        for ids in model.tokenizer(
            [question] * len(texts),
            texts,
            padding=False,
            truncation=False,
            add_special_tokens=True,
        )["input_ids"]
    ]
    values = model.predict(
        [(question, text) for text in texts],
        batch_size=RERANK_BATCH,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    synchronize(device)
    return np.asarray(values).reshape(-1), {
        "rerank_pairs": len(texts),
        "rerank_raw_tokens": sum(lengths),
        "rerank_tokens": sum(min(n, MAX_INPUT) for n in lengths),
        "rerank_truncated": sum(n > MAX_INPUT for n in lengths),
    }
