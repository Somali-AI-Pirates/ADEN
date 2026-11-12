"""Pinned embedding model helpers and resumable vector construction."""

import json
import os
import shutil
from pathlib import Path
from time import perf_counter

import numpy as np
from shared.table_rag_policy import (
    DIMENSIONS,
    BUILD_CPU_THREADS,
    DOCUMENT_PREFIX,
    EMBEDDING_BUILD_BATCH_SIZE,
    EMBEDDING_ID,
    EMBEDDING_REVISION,
    PAGE_MAX_LENGTH,
    QUERY_MAX_LENGTH,
    QUERY_PREFIX,
    RERANKER_ID,
    RERANKER_REVISION,
    TABLE_MAX_LENGTH,
    VECTOR_CHUNK_SIZE,
)

from .common import atomic_dump as _atomic_dump
from .common import digest
from .fingerprints import callable_fingerprint, dependency_versions

VECTOR_DEPENDENCIES = (
    "numpy",
    "sentence-transformers",
    "torch",
    "transformers",
    "tokenizers",
    "huggingface-hub",
)


def prefetch_models():
    from huggingface_hub import snapshot_download

    return {
        model: snapshot_download(repo_id=model, revision=revision)
        for model, revision in (
            (EMBEDDING_ID, EMBEDDING_REVISION),
            (RERANKER_ID, RERANKER_REVISION),
        )
    }


def embedding_contract(device):
    encoder_sha256 = callable_fingerprint(
        _encode_document_batch,
        _input_token_stats,
        values=(
            DIMENSIONS,
            PAGE_MAX_LENGTH,
            TABLE_MAX_LENGTH,
            "bfloat16",
            "float32",
            EMBEDDING_BUILD_BATCH_SIZE,
            VECTOR_CHUNK_SIZE,
            BUILD_CPU_THREADS,
        ),
    )
    return {
        "model_id": EMBEDDING_ID,
        "revision": EMBEDDING_REVISION,
        "dimensions": DIMENSIONS,
        "query_max_length": QUERY_MAX_LENGTH,
        "page_max_length": PAGE_MAX_LENGTH,
        "table_max_length": TABLE_MAX_LENGTH,
        "query_prefix": QUERY_PREFIX,
        "document_prefix": DOCUMENT_PREFIX,
        "normalized": True,
        "model_dtype": "bfloat16",
        "output_dtype": "float32",
        "encoder_sha256": encoder_sha256,
        "encoder_dependencies": dependency_versions(VECTOR_DEPENDENCIES),
        "build_device": "cuda:0" if device == "cuda" else device,
    }


def _input_token_stats(model, texts, maximum, *, batch_size=VECTOR_CHUNK_SIZE):
    prompt = model.prompts.get("document", "")
    total = 0
    longest = 0
    truncated = 0
    for start in range(0, len(texts), batch_size):
        tokenized = model.tokenizer(
            [prompt + text for text in texts[start : start + batch_size]],
            truncation=False,
            padding=False,
            verbose=False,
        )
        lengths = [len(input_ids) for input_ids in tokenized["input_ids"]]
        total += sum(lengths)
        longest = max(longest, max(lengths, default=0))
        truncated += sum(length > maximum for length in lengths)
    return {
        "count": len(texts),
        "raw_tokens": total,
        "max_raw_tokens": longest,
        "truncated_inputs": truncated,
        "max_seq_length": maximum,
        "prompt": prompt,
    }


def _encode_document_batch(model, texts, *, batch_size=EMBEDDING_BUILD_BATCH_SIZE):
    import torch

    with torch.inference_mode():
        vectors = model.encode_document(
            texts,
            batch_size=batch_size,
            normalize_embeddings=False,
            show_progress_bar=False,
            convert_to_tensor=True,
        )
    normalized = torch.nn.functional.normalize(vectors.float(), p=2, dim=1)
    result = normalized.cpu().numpy().astype(np.float32, copy=False)
    if (
        result.ndim != 2
        or result.shape[1] != DIMENSIONS
        or not np.isfinite(result).all()
        or not np.allclose(np.linalg.norm(result, axis=1), 1.0, atol=1e-4)
    ):
        raise ValueError("Invalid normalized document embeddings")
    return result


def build_vectors(
    pages_path,
    chunks_path,
    output,
    device="cpu",
    cache_bundle=None,
    progress_dir=None,
    input_identity=None,
):
    page_data = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    if cache_bundle:
        cache = Path(cache_bundle)
        manifest_path = cache / "corpus.json"
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            old_store = json.loads((cache / "store.json").read_text(encoding="utf-8"))
            old_pages = json.loads((cache / "pages.json").read_text(encoding="utf-8"))
            old_chunks = json.loads(
                (cache / "table-chunks.json").read_text(encoding="utf-8")
            )
            declared_files = old["files"]
            store_files = old_store["files"]
            required_files = {
                "pages.json",
                "vectors.npy",
                "store.json",
                "catalog.json",
                "tables.sqlite3",
                "table-chunks.json",
                "records.json",
                "records.sqlite3",
            }
            missing_hashes = required_files - set(declared_files)
            if missing_hashes:
                raise ValueError(
                    "Cached bundle is missing required declared SHA values: "
                    + ", ".join(sorted(missing_hashes))
                )
            for name in required_files:
                if (
                    not (cache / name).is_file()
                    or digest(cache / name) != declared_files[name]
                ):
                    raise ValueError(f"Cached bundle declared SHA mismatch: {name}")
            for name in ("catalog.json", "tables.sqlite3", "table-chunks.json"):
                if (
                    name not in store_files
                    or not (cache / name).is_file()
                    or digest(cache / name) != store_files[name]
                ):
                    raise ValueError(f"Cached store declared SHA mismatch: {name}")
        except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Cached bundle is missing a valid declared file hash"
            ) from error
        expected_identity = input_identity or {}
        expected_contract = expected_identity.get("embedding") or embedding_contract(
            device
        )
        same = (
            old.get("embedding") == expected_contract
            and old.get("pipeline_stages", {}).get("vectors")
            == expected_identity.get("builder_sha256")
            and expected_identity.get("builder_sha256") is not None
            and old_pages == page_data
            and old_chunks == chunks
            and old.get("counts", {}).get("pages") == len(page_data)
            and old.get("counts", {}).get("table_documents") == len(chunks)
        )
        if same:
            vectors = np.load(cache / "vectors.npy", mmap_mode="r", allow_pickle=False)
            if (
                vectors.shape == (len(page_data) + len(chunks), DIMENSIONS)
                and vectors.dtype == np.float32
                and np.isfinite(vectors).all()
                and np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)
            ):
                shutil.copyfile(cache / "vectors.npy", output)
                return {
                    "reused": True,
                    "reused_from": str(cache.resolve()),
                    "vectors_sha256": digest(output),
                }
        raise ValueError(
            "Cached vectors lack a complete compatible embedding contract, "
            "verified file hashes, or identical page/table inputs; omit "
            "--reuse-vectors-from to encode current inputs"
        )

    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(BUILD_CPU_THREADS)
    model = SentenceTransformer(
        EMBEDDING_ID,
        revision=EMBEDDING_REVISION,
        device=device,
        trust_remote_code=True,
        model_kwargs={"dtype": torch.bfloat16},
    )
    if model.get_sentence_embedding_dimension() != DIMENSIONS:
        raise ValueError("Unexpected embedding dimension")
    if model.dtype != torch.bfloat16:
        raise ValueError("Pinned embedding model must load in bfloat16")
    if (
        model.prompts.get("document", "") != DOCUMENT_PREFIX
        or model.prompts.get("query", "") != QUERY_PREFIX
    ):
        raise ValueError("Pinned embedding prompts differ from the corpus contract")

    progress = Path(progress_dir or Path(output).parent / ".vector-progress")
    progress.mkdir(parents=True, exist_ok=True)
    state_path = progress / "build.json"
    identity = input_identity or {
        "pages_sha256": digest(pages_path),
        "table_chunks_sha256": digest(chunks_path),
        "embedding": embedding_contract(device),
    }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {
            "status": "building",
            "inputs": identity,
            "groups": {"pages": [], "tables": []},
            "model": {
                "id": EMBEDDING_ID,
                "revision": EMBEDDING_REVISION,
                "dtype": str(model.dtype),
                "device": str(model.device),
                "normalized_output_dtype": "float32",
                "batch_size": EMBEDDING_BUILD_BATCH_SIZE,
                "chunk_size": VECTOR_CHUNK_SIZE,
            },
        }
        _atomic_dump(state_path, state)
    except json.JSONDecodeError as error:
        raise ValueError("Vector progress manifest is not valid JSON") from error
    if state.get("inputs") != identity:
        raise ValueError(
            "Vector progress belongs to different inputs or encoder code; choose a new work directory"
        )
    groups = {"pages": page_data, "tables": chunks}
    group_vectors = {}
    run_started = perf_counter()
    reused_chunks = 0
    for group_name, items in groups.items():
        texts = [item["text"] for item in items]
        maximum = PAGE_MAX_LENGTH if group_name == "pages" else TABLE_MAX_LENGTH
        model.max_seq_length = maximum
        manifest_entries = state["groups"].setdefault(group_name, [])
        vectors_for_group = []
        for chunk_index, start in enumerate(range(0, len(texts), VECTOR_CHUNK_SIZE)):
            end = min(start + VECTOR_CHUNK_SIZE, len(texts))
            chunk_path = progress / f"{group_name}-{start:05d}-{end:05d}.npy"
            if chunk_index < len(manifest_entries):
                entry = manifest_entries[chunk_index]
                if (
                    entry.get("start") != start
                    or entry.get("count") != end - start
                    or entry.get("file") != chunk_path.name
                    or not chunk_path.exists()
                    or entry.get("sha256") != digest(chunk_path)
                ):
                    raise ValueError(
                        f"Saved vector chunk is corrupt: {chunk_path.name}"
                    )
                values = np.load(chunk_path, mmap_mode="r", allow_pickle=False)
                if (
                    values.shape != (end - start, DIMENSIONS)
                    or values.dtype != np.float32
                ):
                    raise ValueError(
                        f"Saved vector chunk has invalid shape: {chunk_path.name}"
                    )
                reused_chunks += 1
            else:
                batch_texts = texts[start:end]
                stats = _input_token_stats(model, batch_texts, maximum)
                started = perf_counter()
                values = _encode_document_batch(
                    model, batch_texts, batch_size=EMBEDDING_BUILD_BATCH_SIZE
                )
                elapsed = perf_counter() - started
                temporary = chunk_path.with_suffix(".npy.tmp")
                with temporary.open("wb") as stream:
                    np.save(stream, values, allow_pickle=False)
                os.replace(temporary, chunk_path)
                entry = {
                    "start": start,
                    "count": end - start,
                    "file": chunk_path.name,
                    "sha256": digest(chunk_path),
                    "seconds": elapsed,
                    "input_stats": stats,
                }
                manifest_entries.append(entry)
                state["groups"][group_name] = manifest_entries
                _atomic_dump(state_path, state)
                print(
                    f"Embedded {group_name}: {end}/{len(texts)} items",
                    flush=True,
                )
            vectors_for_group.append(values)
        group_vectors[group_name] = vectors_for_group
        stats_entries = [
            entry["input_stats"] for entry in manifest_entries if "input_stats" in entry
        ]
        state.setdefault("input_stats", {})[group_name] = {
            "count": sum(item["count"] for item in stats_entries),
            "raw_tokens": sum(item["raw_tokens"] for item in stats_entries),
            "max_raw_tokens": max(
                (item["max_raw_tokens"] for item in stats_entries), default=0
            ),
            "truncated_inputs": sum(item["truncated_inputs"] for item in stats_entries),
            "max_seq_length": maximum,
            "prompt": model.prompts.get("document", ""),
        }
        _atomic_dump(state_path, state)

    output = Path(output)
    temporary_output = output.with_suffix(".npy.tmp")
    matrix = np.lib.format.open_memmap(
        temporary_output,
        mode="w+",
        dtype=np.float32,
        shape=(len(page_data) + len(chunks), DIMENSIONS),
    )
    offset = 0
    for group_name in ("pages", "tables"):
        for vectors in group_vectors[group_name]:
            matrix[offset : offset + len(vectors)] = vectors
            offset += len(vectors)
    matrix.flush()
    del matrix
    os.replace(temporary_output, output)
    final = np.load(output, mmap_mode="r", allow_pickle=False)
    if (
        final.shape != (len(page_data) + len(chunks), DIMENSIONS)
        or final.dtype != np.float32
        or not np.isfinite(final).all()
        or not np.allclose(np.linalg.norm(final, axis=1), 1.0, atol=1e-4)
    ):
        raise ValueError("Invalid vector matrix")
    del final
    state.update(
        status="complete",
        vectors_sha256=digest(output),
        encoding_seconds=perf_counter() - run_started,
        reused_chunks=reused_chunks,
    )
    _atomic_dump(state_path, state)
    del model
    return {
        "reused": False,
        "device": device,
        "dtype": state["model"]["dtype"],
        "batch_size": EMBEDDING_BUILD_BATCH_SIZE,
        "chunk_size": VECTOR_CHUNK_SIZE,
        "input_stats": state["input_stats"],
        "encoding_seconds": state["encoding_seconds"],
        "reused_chunks": reused_chunks,
        "vectors_sha256": state["vectors_sha256"],
    }
