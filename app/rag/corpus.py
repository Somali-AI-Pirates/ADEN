import json
import re
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.documents import Document

from app.rag.models import SourceRef, make_document
from app.rag.table_store import TableStore

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TableRagCorpus:
    documents: tuple[Document, ...]
    pages_by_source: dict[str, Document]
    vectors: np.ndarray
    table_store: TableStore
    embedding: dict[str, Any]
    page_count: int

    def close(self) -> None:
        _close_vector_map(self.vectors)


def _close_vector_map(vectors: Any) -> None:
    memory_map = getattr(vectors, "_mmap", None)
    if memory_map is not None and not getattr(memory_map, "closed", False):
        memory_map.close()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def _verify_hash(path: Path, expected: Any, name: str) -> None:
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    if _sha256(path) != expected:
        raise ValueError(f"Corpus fingerprint mismatch: {name}")


def load_table_documents(directory: str | Path):
    p = Path(directory) / "table-chunks.json"
    tables = json.loads(p.read_text(encoding="utf-8"))

    documents = []

    for table in tables:
        source = SourceRef(
            book_id=table["book_id"], file_name=table["file_name"], page=table["page"]
        )
        document = make_document(table["text"], source, table["chunk_id"])
        document.metadata["table_id"] = table["table_id"]
        documents.append(document)

    return documents


def load_page_documents(path: str | Path):
    pages = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(pages, list):
        raise ValueError("Page data must be a JSON list")

    documents = []
    seen_source_ids = set()

    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Each page entry must be a JSON object")

        file_name = page.get("file_name")
        text = page.get("text")
        if not isinstance(file_name, str) or not file_name:
            raise ValueError("Each page entry must have a non-empty file_name")
        if not isinstance(text, str):
            raise ValueError("Each page entry must have text as a string")

        book_id = page.get("book_id") or Path(file_name).stem
        source = SourceRef(
            book_id=book_id,
            file_name=file_name,
            page=page["page"],
        )
        if source.source_id in seen_source_ids:
            raise ValueError(f"Duplicate page source ID: {source.source_id}")
        seen_source_ids.add(source.source_id)

        documents.append(make_document(text, source, f"{source.source_id}:page"))

    return documents



def load_corpus(directory: str | Path) -> TableRagCorpus:
    """Load a validated Advanced TableRAG bundle without loading an embedding model."""
    directory = Path(directory).resolve()
    manifest = _read_json(directory / "corpus.json")
    if not isinstance(manifest, dict):
        raise ValueError("corpus.json must be a JSON object")
    if (
        manifest.get("schema_version") != 1
        or type(manifest.get("schema_version")) is not int
    ):
        raise ValueError("Unsupported corpus schema_version")
    if manifest.get("dataset") != "advanced":
        raise ValueError("Corpus dataset must be 'advanced'")

    embedding = manifest.get("embedding")
    counts = manifest.get("counts")
    files = manifest.get("files")

    if (
        not isinstance(embedding, dict)
        or not isinstance(counts, dict)
        or not isinstance(files, dict)
    ):
        raise ValueError("corpus.json requires embedding, counts, and files objects")
    for name in ("model_id", "revision", "query_prefix", "document_prefix"):
        if not isinstance(embedding.get(name), str):
            raise ValueError(f"embedding.{name} must be a string")
    for name in (
        "dimensions",
        "query_max_length",
        "page_max_length",
        "table_max_length",
    ):
        _require_positive_int(embedding.get(name), f"embedding.{name}")
    if embedding.get("normalized") is not True:
        raise ValueError("embedding.normalized must be true")

    for name in ("pages", "table_documents", "physical_tables"):
        _require_positive_int(counts.get(name), f"counts.{name}")
    for name in (
        "pages.json",
        "vectors.npy",
        "store.json",
        "records.json",
        "records.sqlite3",
    ):
        if name not in files:
            raise ValueError(f"Corpus is missing the {name} fingerprint")
        _verify_hash(directory / name, files.get(name), name)

    records_manifest = _read_json(directory / "records.json")
    if (
        not isinstance(records_manifest, dict)
        or records_manifest.get("schema_version") != 1
        or records_manifest.get("database_sha256") != files["records.sqlite3"]
    ):
        raise ValueError("Records manifest does not match the corpus snapshot")
    record_inputs = records_manifest.get("inputs")
    if (
        not isinstance(record_inputs, dict)
        or record_inputs.get("pages_sha256") != files["pages.json"]
        or record_inputs.get("catalog_sha256") != _sha256(directory / "catalog.json")
    ):
        raise ValueError("Records index does not match the page/table sources")

    store_manifest = _read_json(directory / "store.json")
    if not isinstance(store_manifest, dict) or not isinstance(
        store_manifest.get("files"), dict
    ):
        raise ValueError("store.json requires a files object")
    store_hashes = store_manifest["files"]
    for name in ("catalog.json", "tables.sqlite3", "table-chunks.json"):
        if name not in store_hashes:
            raise ValueError(f"store.json is missing the {name} hash")
    # TableStore verifies the catalog, database, and table-chunk hashes before
    # any table chunks are read.
    table_store = TableStore(directory)

    page_documents = load_page_documents(directory / "pages.json")
    table_documents = load_table_documents(directory)
    if len(page_documents) != counts["pages"]:
        raise ValueError("Page count does not match corpus.json")
    if len(table_documents) != counts["table_documents"]:
        raise ValueError("Table document count does not match corpus.json")
    if len(table_store.catalog) != counts["physical_tables"]:
        raise ValueError("Physical table count does not match corpus.json")

    pages_by_source = {doc.metadata["source_id"]: doc for doc in page_documents}
    unit_ids = {doc.metadata["unit_id"] for doc in page_documents}
    if len(unit_ids) != len(page_documents):
        raise ValueError("Page documents contain duplicate unit IDs")

    for catalog in table_store.catalog.values():
        source_id = f"{catalog['book_id']}:p{catalog['page']:04d}"
        if source_id not in pages_by_source:
            raise ValueError(f"Physical table refers to a missing page: {source_id}")

    for document in table_documents:
        metadata = document.metadata
        source_id = metadata["source_id"]
        unit_id = metadata["unit_id"]
        table_id = metadata["table_id"]
        book_id = metadata["book_id"]
        page = metadata["page"]
        page_document = pages_by_source.get(source_id)
        if page_document is None:
            raise ValueError(f"Table document refers to a missing page: {source_id}")
        if metadata["file_name"] != page_document.metadata["file_name"]:
            raise ValueError(f"Table/page file mismatch: {source_id}")
        if table_id not in table_store.catalog:
            raise ValueError(
                f"Table document refers to a missing physical table: {table_id}"
            )
        if table_id not in table_store.by_page.get((book_id, page), []):
            raise ValueError(f"Table/page relation mismatch: {table_id}")
        if unit_id in unit_ids:
            raise ValueError(f"Duplicate document unit ID: {unit_id}")
        unit_ids.add(unit_id)

    vectors = np.load(directory / "vectors.npy", mmap_mode="r", allow_pickle=False)
    try:
        if not isinstance(vectors, np.ndarray):
            raise ValueError("vectors.npy must contain a NumPy array")

        if vectors.dtype != np.dtype(np.float32):
            raise ValueError("vectors.npy must use float32 values")

        expected_shape = (
            counts["pages"] + counts["table_documents"],
            embedding["dimensions"],
        )
        if vectors.shape != expected_shape:
            raise ValueError(
                f"vectors.npy shape mismatch: expected {expected_shape}, got {vectors.shape}"
            )

        if vectors.flags.writeable:
            vectors.setflags(write=False)
    except BaseException:
        _close_vector_map(vectors)
        raise

    return TableRagCorpus(
        documents=tuple(page_documents + table_documents),
        pages_by_source=pages_by_source,
        vectors=vectors,
        table_store=table_store,
        embedding=dict(embedding),
        page_count=len(page_documents),
    )
