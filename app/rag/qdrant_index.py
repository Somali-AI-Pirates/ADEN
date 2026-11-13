"""Cloud or explicitly selected local Qdrant for the pinned vector bundle."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
from langchain_core.documents import Document

from shared.table_rag_policy import (
    DIMENSIONS as _DIMENSIONS,
    EMBEDDING_ID as _EMBEDDING_ID,
    EMBEDDING_REVISION as _EMBEDDING_REVISION,
)
from qdrant_client import QdrantClient, models

DEFAULT_COLLECTION = "advanced_table_rag"
_COLLECTION_PATTERN = re.compile(r"^advanced_table_rag(?:_[A-Za-z0-9][A-Za-z0-9_-]*)?$")
_SCHEMA_VERSION = 1
_BATCH_SIZE = 128
_SCROLL_PAGE_SIZE = 512
_NORM_TOLERANCE = 2e-3
_VECTOR_TOLERANCE = 1e-6


class QdrantIndexError(RuntimeError):
    """A safe, credential-free error raised by the Qdrant index adapter."""


def _require_collection_name(collection: str) -> None:
    if not isinstance(collection, str) or not _COLLECTION_PATTERN.fullmatch(collection):
        raise ValueError(
            "Collection name must be 'advanced_table_rag' or start with "
            "'advanced_table_rag_' and contain only letters, digits, '_' or '-'."
        )


def _read_snapshot(directory: str | Path, corpus) -> tuple[dict[str, Any], int]:
    directory = Path(directory).resolve()
    manifest_path = directory / "corpus.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != _SCHEMA_VERSION
        or manifest.get("dataset") != "advanced"
    ):
        raise ValueError("Qdrant upload requires an Advanced TableRAG corpus schema v1.")

    embedding = manifest.get("embedding")
    counts = manifest.get("counts")
    files = manifest.get("files")
    if not isinstance(embedding, dict) or not isinstance(counts, dict) or not isinstance(files, dict):
        raise ValueError("Corpus manifest is missing embedding, counts, or files metadata.")
    if (
        embedding.get("model_id") != _EMBEDDING_ID
        or embedding.get("revision") != _EMBEDDING_REVISION
        or embedding.get("dimensions") != _DIMENSIONS
        or embedding.get("normalized") is not True
        or embedding.get("query_prefix") != "query: "
        or embedding.get("document_prefix") != "passage: "
    ):
        raise ValueError("Corpus embedding contract differs from the pinned KURE-v3 index.")
    for name in ("pages", "table_documents", "physical_tables"):
        value = counts.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Corpus count {name!r} must be a positive integer.")
    vectors_sha256 = files.get("vectors.npy")
    if (
        not isinstance(vectors_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", vectors_sha256) is None
    ):
        raise ValueError("Corpus manifest is missing the vectors.npy SHA-256.")

    embedding_json = json.dumps(embedding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    point_count = counts["pages"] + counts["table_documents"]
    metadata = {
        "aden_schema_version": _SCHEMA_VERSION,
        "aden_status": "building",
        "aden_dataset": "advanced",
        "aden_corpus_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "aden_vectors_sha256": vectors_sha256,
        "aden_embedding_contract": embedding_json,
        "aden_dimensions": _DIMENSIONS,
        "aden_point_count": point_count,
        "aden_page_count": counts["pages"],
        "aden_table_document_count": counts["table_documents"],
        "aden_physical_table_count": counts["physical_tables"],
        "aden_distance": "cosine",
        "aden_search_mode": "exact",
    }

    vectors = getattr(corpus, "vectors", None)
    documents = getattr(corpus, "documents", None)
    if not isinstance(documents, (tuple, list)) or len(documents) != point_count:
        raise ValueError("Corpus documents do not match the manifest point count.")
    if not isinstance(vectors, np.ndarray) or vectors.dtype != np.dtype(np.float32):
        raise ValueError("Corpus vectors must be a float32 NumPy array.")
    if vectors.shape != (point_count, _DIMENSIONS):
        raise ValueError("Corpus vector shape does not match the pinned KURE-v3 contract.")
    corpus_embedding = getattr(corpus, "embedding", None)
    if corpus_embedding != embedding:
        raise ValueError("Loaded corpus embedding metadata differs from corpus.json.")
    return metadata, point_count


def _validate_local_vectors(corpus, point_count: int) -> None:
    vectors = corpus.vectors
    for start in range(0, point_count, 1024):
        batch = np.asarray(vectors[start : start + 1024], dtype=np.float32)
        if not np.isfinite(batch).all():
            raise ValueError("Corpus contains non-finite vectors.")
        norms = np.linalg.norm(batch, axis=1)
        if not np.allclose(norms, 1.0, atol=_NORM_TOLERANCE, rtol=0.0):
            raise ValueError("Corpus contains vectors that are not normalized.")


def _point_payload(document: Document, row_index: int) -> dict[str, Any]:
    metadata = document.metadata
    payload = {
        "row_index": row_index,
        "unit_id": metadata.get("unit_id"),
        "source_id": metadata.get("source_id"),
        "book_id": metadata.get("book_id"),
        "file_name": metadata.get("file_name"),
        "page": metadata.get("page"),
    }
    if "table_id" in metadata:
        payload["table_id"] = metadata["table_id"]
    if any(not isinstance(payload[key], str) or not payload[key] for key in ("unit_id", "source_id", "book_id", "file_name")):
        raise ValueError("Search unit is missing required source or unit metadata.")
    if isinstance(payload["page"], bool) or not isinstance(payload["page"], int) or payload["page"] < 1:
        raise ValueError("Search unit page metadata must be a positive integer.")
    if "table_id" in payload and (not isinstance(payload["table_id"], str) or not payload["table_id"]):
        raise ValueError("Table search units must have a non-empty table_id.")
    return payload


def _connect(
    client,
    credentials=None,
    *,
    timeout: int = 60,
    check_compatibility: bool | None = None,
):
    if client is not None:
        return client, False
    if credentials is None:
        from app.rag.config import load_table_rag_settings

        credentials = load_table_rag_settings().credentials
    url = getattr(credentials, "qdrant_url", None)
    api_key = getattr(credentials, "qdrant_api_key", None)
    local = getattr(credentials, "qdrant_local", False)
    parsed_url = urlsplit(url or "")
    if local:
        if (
            parsed_url.scheme != "http"
            or parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
            or any(char.isspace() for char in (url or ""))
        ):
            raise QdrantIndexError(
                "Local Qdrant requires an HTTP URL using localhost or a loopback address."
            )
        if api_key:
            raise QdrantIndexError("Local Qdrant must not use a QDRANT_API_KEY.")
        client_options = {"url": url, "timeout": timeout}
        if check_compatibility is not None:
            client_options["check_compatibility"] = check_compatibility
        try:
            return QdrantClient(**client_options), True
        except Exception as error:
            raise QdrantIndexError(
                f"Qdrant connection failed ({type(error).__name__})."
            ) from None

    if (
        parsed_url.scheme != "https" or not parsed_url.hostname
        or parsed_url.username is not None or parsed_url.password is not None
        or parsed_url.query or parsed_url.fragment
        or any(char.isspace() for char in (url or ""))
    ):
        raise QdrantIndexError(
            "Advanced Qdrant Cloud requires an HTTPS QDRANT_URL without credentials or query parameters."
        )
    if not api_key:
        raise QdrantIndexError("QDRANT_API_KEY is required for Advanced Qdrant Cloud.")
    client_options = {"url": url, "api_key": api_key, "timeout": timeout}
    if check_compatibility is not None:
        client_options["check_compatibility"] = check_compatibility
    try:
        return QdrantClient(**client_options), True
    except Exception as error:
        raise QdrantIndexError(
            f"Qdrant connection failed ({type(error).__name__})."
        ) from None


def _safe_call(operation: str, callback):
    try:
        return callback()
    except QdrantIndexError:
        raise
    except Exception as error:
        raise QdrantIndexError(
            f"Qdrant {operation} failed ({type(error).__name__})."
        ) from None


def _close_owned(client, owned: bool) -> None:
    if owned:
        try:
            client.close()
        except Exception:
            pass


def _load_corpus(directory: str | Path):
    from app.rag.corpus import load_corpus

    return load_corpus(directory)


def _collection_info(client, collection: str):
    return _safe_call("collection lookup", lambda: client.get_collection(collection))


def _validate_vector_config(info) -> None:
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams):
        raise QdrantIndexError("Qdrant collection must use one unnamed dense vector.")
    if vectors.size != _DIMENSIONS or vectors.distance != models.Distance.COSINE:
        raise QdrantIndexError("Qdrant collection must use 2,048-dimensional cosine vectors.")
    if vectors.datatype not in (None, models.Datatype.FLOAT32):
        raise QdrantIndexError("Qdrant collection must use float32 vector storage.")
    if (
        getattr(info.config, "quantization_config", None) is not None
        or getattr(vectors, "quantization_config", None) is not None
    ):
        raise QdrantIndexError("Qdrant collection quantization is unsupported for exact parity.")


def _validate_metadata(info, expected: dict[str, Any], status: str) -> None:
    wanted = dict(expected)
    wanted["aden_status"] = status
    if getattr(info.config, "metadata", None) != wanted:
        raise QdrantIndexError("Qdrant collection metadata does not match this corpus snapshot.")


def _count(client, collection: str) -> int:
    result = _safe_call("point count", lambda: client.count(collection, exact=True))
    return int(result.count)


def _verify_points(client, collection: str, corpus, point_count: int, *, with_vectors: bool) -> None:
    seen = np.zeros(point_count, dtype=np.bool_)
    offset = None
    while True:
        records, next_offset = _safe_call(
            "point verification",
            lambda: client.scroll(
                collection,
                limit=_SCROLL_PAGE_SIZE,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            ),
        )
        for record in records:
            row_index = record.id
            if isinstance(row_index, bool) or not isinstance(row_index, int) or not 0 <= row_index < point_count:
                raise QdrantIndexError("Qdrant contains a point ID outside the corpus row range.")
            if seen[row_index]:
                raise QdrantIndexError("Qdrant contains a duplicate corpus point ID.")
            expected_payload = _point_payload(corpus.documents[row_index], row_index)
            if record.payload != expected_payload:
                raise QdrantIndexError("Qdrant point payload does not match its corpus row.")
            if with_vectors:
                actual = np.asarray(record.vector, dtype=np.float32)
                expected = np.asarray(corpus.vectors[row_index], dtype=np.float32)
                if (
                    actual.shape != (_DIMENSIONS,)
                    or not np.isfinite(actual).all()
                    or not np.allclose(actual, expected, atol=_VECTOR_TOLERANCE, rtol=0.0)
                ):
                    raise QdrantIndexError("Qdrant point vector does not match its corpus row.")
            seen[row_index] = True
        if next_offset is None:
            break
        if not records:
            raise QdrantIndexError("Qdrant point verification stopped before reaching the corpus end.")
        offset = next_offset
    if not bool(seen.all()):
        raise QdrantIndexError("Qdrant point IDs do not cover every corpus row.")


def upload_qdrant_index(
    directory: str | Path,
    *,
    collection: str = DEFAULT_COLLECTION,
    client=None,
    credentials=None,
) -> dict[str, Any]:
    """Upload the existing vectors.npy without embedding text or deleting collections."""
    _require_collection_name(collection)
    with closing(_load_corpus(directory)) as corpus:
        expected, point_count = _read_snapshot(directory, corpus)
        _validate_local_vectors(corpus, point_count)

        qdrant, owned = _connect(client, credentials)
        try:
            exists = _safe_call("collection lookup", lambda: qdrant.collection_exists(collection))
            if exists:
                info = _collection_info(qdrant, collection)
                _validate_vector_config(info)
                metadata = getattr(info.config, "metadata", None)
                if metadata == {**expected, "aden_status": "ready"}:
                    if _count(qdrant, collection) != point_count:
                        raise QdrantIndexError("Ready Qdrant collection has an unexpected point count.")
                    _verify_points(qdrant, collection, corpus, point_count, with_vectors=False)
                    return {"collection": collection, "points": point_count, "status": "ready", "action": "verified"}
                if metadata != {**expected, "aden_status": "building"}:
                    raise QdrantIndexError("Existing Qdrant collection has a different corpus or build state.")
                if _count(qdrant, collection) > point_count:
                    raise QdrantIndexError("Building Qdrant collection contains unexpected extra points.")
                action = "resumed"
            else:
                _safe_call(
                    "collection creation",
                    lambda: qdrant.create_collection(
                        collection,
                        vectors_config=models.VectorParams(
                            size=_DIMENSIONS,
                            distance=models.Distance.COSINE,
                            datatype=models.Datatype.FLOAT32,
                        ),
                        metadata={**expected, "aden_status": "building"},
                    ),
                )
                action = "uploaded"

            for start in range(0, point_count, _BATCH_SIZE):
                end = min(start + _BATCH_SIZE, point_count)
                points = [
                    models.PointStruct(
                        id=row_index,
                        vector=np.asarray(corpus.vectors[row_index], dtype=np.float32).tolist(),
                        payload=_point_payload(corpus.documents[row_index], row_index),
                    )
                    for row_index in range(start, end)
                ]
                _safe_call(
                    "vector upload",
                    lambda points=points: qdrant.upsert(collection, points=points, wait=True),
                )

            if _count(qdrant, collection) != point_count:
                raise QdrantIndexError("Qdrant upload did not produce the expected point count.")
            _verify_points(qdrant, collection, corpus, point_count, with_vectors=True)
            _safe_call(
                "collection finalization",
                lambda: qdrant.update_collection(
                    collection,
                    metadata={**expected, "aden_status": "ready"},
                ),
            )
            return {"collection": collection, "points": point_count, "status": "ready", "action": action}
        finally:
            _close_owned(qdrant, owned)


def check_qdrant_readiness(
    directory: str | Path,
    corpus,
    *,
    collection: str = DEFAULT_COLLECTION,
    client=None,
    credentials=None,
    timeout: int = 2,
) -> None:
    """Check a ready collection with one bounded, read-only metadata request."""
    _require_collection_name(collection)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("Qdrant readiness timeout must be a positive integer")
    expected, point_count = _read_snapshot(directory, corpus)
    qdrant, owned = _connect(
        client,
        credentials,
        timeout=timeout,
        check_compatibility=False,
    )
    try:
        info = _collection_info(qdrant, collection)
        _validate_vector_config(info)
        _validate_metadata(info, expected, "ready")
        if getattr(info, "status", None) != models.CollectionStatus.GREEN:
            raise QdrantIndexError("Qdrant collection is not ready.")
        if getattr(info, "optimizer_status", None) != models.OptimizersStatusOneOf.OK:
            raise QdrantIndexError("Qdrant collection optimizers are not ready.")
        points_count = getattr(info, "points_count", None)
        if (
            isinstance(points_count, bool)
            or not isinstance(points_count, int)
            or points_count != point_count
        ):
            raise QdrantIndexError("Qdrant collection has an unexpected point count.")
    finally:
        _close_owned(qdrant, owned)


def check_qdrant_index(
    directory: str | Path,
    *,
    collection: str = DEFAULT_COLLECTION,
    client=None,
    credentials=None,
    full: bool = False,
) -> dict[str, Any]:
    """Check a ready collection; full mode also checks every row and vector."""
    _require_collection_name(collection)
    with closing(_load_corpus(directory)) as corpus:
        expected, point_count = _read_snapshot(directory, corpus)
        qdrant, owned = _connect(client, credentials)
        try:
            exists = _safe_call("collection lookup", lambda: qdrant.collection_exists(collection))
            if not exists:
                raise QdrantIndexError("Qdrant collection does not exist.")
            info = _collection_info(qdrant, collection)
            _validate_vector_config(info)
            _validate_metadata(info, expected, "ready")
            if _count(qdrant, collection) != point_count:
                raise QdrantIndexError("Ready Qdrant collection has an unexpected point count.")
            if full:
                _verify_points(qdrant, collection, corpus, point_count, with_vectors=True)
            return {
                "collection": collection,
                "points": point_count,
                "status": "ready",
                "validation": "vectors-and-payload" if full else "metadata-and-count",
            }
        finally:
            _close_owned(qdrant, owned)


class QdrantDenseIndex:
    """Exact dense search over a verified collection aligned to local documents."""

    def __init__(self, client, corpus, collection: str, metadata: dict[str, Any], *, owns_client: bool):
        self._client = client
        self._corpus = corpus
        self._collection = collection
        self._owns_client = owns_client
        self._closed = False
        self._point_count = metadata["aden_point_count"]
        self.manifest = {
            "backend": "qdrant_cloud",
            "collection": collection,
            "points": self._point_count,
            "dimensions": _DIMENSIONS,
            "distance": "cosine",
            "search_mode": "exact",
            "corpus_sha256": metadata["aden_corpus_sha256"],
            "vectors_sha256": metadata["aden_vectors_sha256"],
            "embedding_contract": json.loads(metadata["aden_embedding_contract"]),
        }

    def search(self, query_vector, top_k: int = 50) -> list[tuple[Document, float]]:
        if self._closed:
            raise RuntimeError("Qdrant dense index is closed.")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer.")
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if vector.shape != (_DIMENSIONS,) or not np.isfinite(vector).all():
            raise ValueError("Query vector must be finite and match the corpus dimensions.")
        norm = float(np.linalg.norm(vector))
        if norm == 0 or not np.isclose(norm, 1.0, atol=1e-4, rtol=0.0):
            raise ValueError("Query vector must be non-zero and normalized.")

        limit = min(self._point_count, max(top_k + 1, 1))
        while True:
            response = _safe_call(
                "exact search",
                lambda limit=limit: self._client.query_points(
                    self._collection,
                    query=vector.tolist(),
                    search_params=models.SearchParams(exact=True),
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                ),
            )
            points = list(response.points)
            expected_length = min(limit, self._point_count)
            if len(points) != expected_length:
                raise QdrantIndexError("Exact Qdrant search returned an incomplete result set.")
            seen_ids = set()
            for point in points:
                self._validate_result(point, seen_ids)
            if (
                limit >= self._point_count
                or len(points) <= top_k
                or float(points[top_k - 1].score) != float(points[top_k].score)
            ):
                break
            limit = min(self._point_count, max(limit + 1, limit * 2))

        ordered = sorted(points, key=lambda point: (-float(point.score), int(point.id)))
        return [
            (self._corpus.documents[int(point.id)], float(point.score))
            for point in ordered[:top_k]
        ]

    def _validate_result(self, point, seen_ids: set[int]) -> None:
        row_index = point.id
        if isinstance(row_index, bool) or not isinstance(row_index, int) or not 0 <= row_index < self._point_count:
            raise QdrantIndexError("Qdrant search returned a point ID outside the corpus row range.")
        if row_index in seen_ids:
            raise QdrantIndexError("Qdrant search returned a duplicate point ID.")
        if not np.isfinite(float(point.score)):
            raise QdrantIndexError("Qdrant search returned a non-finite score.")
        if point.payload != _point_payload(self._corpus.documents[row_index], row_index):
            raise QdrantIndexError("Qdrant search point does not match its corpus unit/source mapping.")
        seen_ids.add(row_index)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_owned(self._client, self._owns_client)


def open_qdrant_index(
    directory: str | Path,
    corpus,
    *,
    collection: str = DEFAULT_COLLECTION,
    client=None,
    credentials=None,
) -> QdrantDenseIndex:
    """Open a ready exact-search collection aligned to a validated local corpus."""
    _require_collection_name(collection)
    expected, point_count = _read_snapshot(directory, corpus)
    qdrant, owned = _connect(client, credentials)
    try:
        exists = _safe_call("collection lookup", lambda: qdrant.collection_exists(collection))
        if not exists:
            raise QdrantIndexError("Qdrant collection does not exist.")
        info = _collection_info(qdrant, collection)
        _validate_vector_config(info)
        _validate_metadata(info, expected, "ready")
        if _count(qdrant, collection) != point_count:
            raise QdrantIndexError("Ready Qdrant collection has an unexpected point count.")
        return QdrantDenseIndex(qdrant, corpus, collection, expected, owns_client=owned)
    except Exception:
        _close_owned(qdrant, owned)
        raise
