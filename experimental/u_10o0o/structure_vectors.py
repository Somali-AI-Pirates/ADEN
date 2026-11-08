"""Embed the Advanced structural chunks with the experiment's Snowflake-ko baseline."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from experimental.u_10o0o.embeding import (
    BATCH_SIZE,
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
    load_model,
)
from experimental.u_10o0o.prepare import CACHE_PATH

STRUCTURE_CHUNKS_PATH = CACHE_PATH.with_name("advanced_structure_chunks.json")
STRUCTURE_VECTORS_PATH = CACHE_PATH.with_name("advanced_structure_vectors.npy")
STRUCTURE_VECTOR_METADATA_PATH = CACHE_PATH.with_name(
    "advanced_structure_vectors_metadata.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_chunks() -> list[dict[str, object]]:
    if not STRUCTURE_CHUNKS_PATH.is_file():
        raise SystemExit(f"구조 청크 파일이 없습니다: {STRUCTURE_CHUNKS_PATH}")

    with STRUCTURE_CHUNKS_PATH.open(encoding="utf-8") as file:
        chunks = json.load(file)
    if not isinstance(chunks, list) or not chunks:
        raise SystemExit("구조 청크 파일이 비어 있거나 배열 형식이 아닙니다.")

    required_fields = {"text", "file_name", "page", "chunk_id"}
    identities: set[tuple[object, object, object]] = set()
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict) or not required_fields.issubset(chunk):
            raise ValueError(f"{index}번째 구조 청크의 필수 필드가 올바르지 않습니다.")
        if not isinstance(chunk["text"], str) or not chunk["text"].strip():
            raise ValueError(f"{index}번째 구조 청크의 text가 비어 있습니다.")
        if (
            not isinstance(chunk["file_name"], str)
            or not isinstance(chunk["page"], int)
            or isinstance(chunk["page"], bool)
            or not isinstance(chunk["chunk_id"], str)
            or not chunk["chunk_id"].strip()
        ):
            raise ValueError(f"{index}번째 구조 청크의 식별 정보가 올바르지 않습니다.")
        identity = (chunk["file_name"], chunk["page"], chunk["chunk_id"])
        if identity in identities:
            raise ValueError(f"{index}번째 구조 청크 ID가 중복됩니다.")
        identities.add(identity)
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advanced 구조 청크 Snowflake-ko 벡터 생성"
    )
    parser.parse_args()

    # The model and tokenizer must be fully local for this benchmark run.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    chunks = load_chunks()
    texts: list[str] = [chunk["text"] for chunk in chunks]
    source_sha256 = sha256_file(STRUCTURE_CHUNKS_PATH)
    print(f"Advanced 구조 청크 임베딩 시작: {len(chunks)}개 | batch size {BATCH_SIZE}")

    total_started = time.perf_counter()
    load_started = time.perf_counter()
    model = load_model("cuda")
    model_load_seconds = time.perf_counter() - load_started

    encode_started = time.perf_counter()
    vectors = model.encode_document(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    encode_seconds = time.perf_counter() - encode_started

    if not isinstance(vectors, np.ndarray):
        vectors = np.asarray(vectors)
    if vectors.shape != (len(chunks), EMBEDDING_DIM):
        raise ValueError(
            f"벡터 모양이 예상과 다릅니다: {vectors.shape} "
            f"(예상 {(len(chunks), EMBEDDING_DIM)})"
        )
    if vectors.dtype != np.float32:
        raise ValueError(f"벡터 dtype이 float32가 아닙니다: {vectors.dtype}")
    if not np.isfinite(vectors).all():
        raise ValueError("벡터에 유한하지 않은 값이 있습니다.")

    STRUCTURE_VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=STRUCTURE_VECTORS_PATH.parent) as temporary:
        temporary_dir = Path(temporary)
        staged_vectors_path = temporary_dir / STRUCTURE_VECTORS_PATH.name
        staged_metadata_path = temporary_dir / STRUCTURE_VECTOR_METADATA_PATH.name

        np.save(staged_vectors_path, vectors, allow_pickle=False)
        metadata = {
            "input_file": STRUCTURE_CHUNKS_PATH.name,
            "input_sha256": source_sha256,
            "vector_file": STRUCTURE_VECTORS_PATH.name,
            "vector_sha256": sha256_file(staged_vectors_path),
            "rows": len(chunks),
            "shape": list(vectors.shape),
            "dtype": str(vectors.dtype),
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "device": "cuda",
            "normalized": True,
            "batch_size": BATCH_SIZE,
            "model_load_seconds": round(model_load_seconds, 3),
            "encode_seconds": round(encode_seconds, 3),
            "total_seconds": round(time.perf_counter() - total_started, 3),
        }
        staged_metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        staged_vectors_path.replace(STRUCTURE_VECTORS_PATH)
        staged_metadata_path.replace(STRUCTURE_VECTOR_METADATA_PATH)

    print(
        f"완료: {vectors.shape} float32 | 모델 로딩 {model_load_seconds:.1f}초 | "
        f"임베딩 {encode_seconds:.1f}초 | 벡터 저장 {STRUCTURE_VECTORS_PATH}"
    )
    print(f"출처 해시 {source_sha256} | 메타데이터 {STRUCTURE_VECTOR_METADATA_PATH}")


if __name__ == "__main__":
    main()
