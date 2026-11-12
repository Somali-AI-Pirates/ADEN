"""Build and verify the self-contained Advanced TableRAG bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from app.ingestion.curation import curate_dataset, validate_curated
from app.rag.corpus import load_corpus
from app.rag.records import RecordIndex
from shared.table_rag_policy import (
    DIMENSIONS,
    RERANKER_ID,
    RERANKER_REVISION,
)

from .table_rag.common import atomic_dump as _atomic_dump
from .table_rag.common import digest, dump
from .table_rag.fingerprints import stage_fingerprints
from .table_rag.sources import extract_pages, typed
from .table_rag.stores import build_physical_store, build_records_index
from .table_rag.vectors import build_vectors, embedding_contract, prefetch_models

BUNDLE_FILES = (
    "pages.json",
    "vectors.npy",
    "store.json",
    "catalog.json",
    "tables.sqlite3",
    "table-chunks.json",
    "records.json",
    "records.sqlite3",
    "corpus.json",
)


def check_database(path):
    with sqlite3.connect(path) as db:
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError(f"SQLite integrity failure: {path}")


def check_bundle(directory):
    directory = Path(directory).resolve()
    corpus = load_corpus(directory)
    index = None
    try:
        index = RecordIndex(directory)
        for name in ("tables.sqlite3", "records.sqlite3"):
            check_database(directory / name)
        return {
            "path": str(directory),
            "pages": len(corpus.pages_by_source),
            "documents": len(corpus.documents),
            "vectors": list(corpus.vectors.shape),
            "tables": len(corpus.table_store.catalog),
            "record_counts": index.manifest["counts"],
            "families": index.manifest["families"],
        }
    finally:
        if index is not None:
            index.close()
        corpus.close()


def _work_dir_mismatch(work: Path, detail: str) -> ValueError:
    return ValueError(
        f"Build staging directory {work} contains artifacts from a different or "
        f"unverifiable TableRAG pipeline ({detail}). The artifacts were preserved. "
        "Choose a new --work-dir, or resume with the code version that created them."
    )


def _assert_work_dir_compatible(
    work: Path,
    stage_codes: dict[str, str],
    source_identity: list[dict[str, str]],
    source_fingerprint: str,
) -> None:
    marker = work / ".table-rag-pipeline.json"
    expected = {"stages": stage_codes, "source_pdfs_sha256": source_fingerprint}
    if marker.exists():
        try:
            actual = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _work_dir_mismatch(work, "pipeline marker is unreadable") from error
        if actual != expected:
            raise _work_dir_mismatch(work, "pipeline fingerprint changed")
        return

    stage = work / "bundle"
    if not stage.exists():
        return
    artifact_groups = (
        (
            "pages",
            ("pages.json", "pages-input.json"),
            "pages-input.json",
            lambda value: value.get("builder_sha256"),
        ),
        (
            "physical",
            ("store.json", "catalog.json", "tables.sqlite3", "table-chunks.json"),
            "store.json",
            lambda value: value.get("inputs", {}).get("builder_sha256"),
        ),
        (
            "records",
            ("records.json", "records.sqlite3"),
            "records.json",
            lambda value: value.get("builder_sha256"),
        ),
        (
            "vectors",
            ("vectors.npy", "vectors-input.json", "vectors-manifest.json"),
            "vectors-input.json",
            lambda value: value.get("builder_sha256"),
        ),
    )
    for stage_name, artifacts, sidecar, read_fingerprint in artifact_groups:
        if not any((stage / name).exists() for name in artifacts):
            continue
        sidecar_path = stage / sidecar
        if not sidecar_path.is_file():
            raise _work_dir_mismatch(work, f"{stage_name} stage has no identity marker")
        try:
            identity = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _work_dir_mismatch(
                work, f"{stage_name} identity is unreadable"
            ) from error
        actual = read_fingerprint(identity)
        if actual != stage_codes[stage_name]:
            raise _work_dir_mismatch(work, f"{stage_name} stage fingerprint changed")
        if stage_name == "pages" and identity.get("inputs") != source_identity:
            raise _work_dir_mismatch(work, "source PDF input fingerprint changed")
        if (
            stage_name == "physical"
            and identity.get("inputs", {}).get("source_fingerprint")
            != source_fingerprint
        ):
            raise _work_dir_mismatch(work, "source PDF input fingerprint changed")

    vector_manifest = stage / "vectors-manifest.json"
    if vector_manifest.exists():
        try:
            identity = json.loads(vector_manifest.read_text(encoding="utf-8"))
            actual = identity.get("inputs", {}).get("builder_sha256")
        except (OSError, json.JSONDecodeError) as error:
            raise _work_dir_mismatch(work, "vector manifest is unreadable") from error
        if actual != stage_codes["vectors"]:
            raise _work_dir_mismatch(work, "vector stage fingerprint changed")

    corpus_path = stage / "corpus.json"
    if corpus_path.exists():
        try:
            corpus_identity = json.loads(corpus_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _work_dir_mismatch(
                work, "staged corpus manifest is unreadable"
            ) from error
        staged_input = corpus_identity.get("inputs", {})
        if corpus_identity.get("pipeline_sha256") != stage_codes["overall"]:
            raise _work_dir_mismatch(work, "staged corpus pipeline fingerprint changed")
        if staged_input.get("source_pdfs_sha256") != source_fingerprint:
            raise _work_dir_mismatch(work, "staged corpus source fingerprint changed")

    for progress in stage.glob(".vectors-*"):
        if not progress.is_dir():
            continue
        state_path = progress / "build.json"
        has_artifacts = any(progress.iterdir())
        if not state_path.exists():
            if has_artifacts:
                raise _work_dir_mismatch(work, "vector progress has no build identity")
            continue
        try:
            identity = json.loads(state_path.read_text(encoding="utf-8")).get(
                "inputs", {}
            )
        except (OSError, json.JSONDecodeError) as error:
            raise _work_dir_mismatch(
                work, "vector progress identity is unreadable"
            ) from error
        if identity.get("builder_sha256") != stage_codes["vectors"]:
            raise _work_dir_mismatch(
                work, "saved vector progress belongs to another encoder pipeline"
            )


def _write_work_pipeline_marker(
    work: Path, stage_codes: dict[str, str], source_fingerprint: str
) -> None:
    marker = work / ".table-rag-pipeline.json"
    expected = {"stages": stage_codes, "source_pdfs_sha256": source_fingerprint}
    if not marker.exists():
        _atomic_dump(marker, expected)


@contextmanager
def _output_build_lock(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.build.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            detail = f"pid={owner.get('pid', 'unknown')}"
        except (OSError, json.JSONDecodeError):
            detail = "owner unknown"
        raise FileExistsError(
            f"Another build holds {lock_path} ({detail}). If that process stopped, "
            "verify it is no longer running and remove the stale lock before retrying."
        ) from error

    try:
        os.write(
            descriptor,
            json.dumps({"pid": os.getpid(), "output": str(output)}).encode(),
        )
        yield
    finally:
        owner_stat = os.fstat(descriptor)
        os.close(descriptor)
        try:
            current_stat = lock_path.stat()
        except FileNotFoundError:
            current_stat = None
        if current_stat is not None and (
            current_stat.st_dev,
            current_stat.st_ino,
        ) == (owner_stat.st_dev, owner_stat.st_ino):
            lock_path.unlink(missing_ok=True)


def build_bundle(documents, output, device="cpu", work_dir=None, cache_bundle=None):
    output = Path(output).resolve()
    with _output_build_lock(output):
        return _build_bundle_locked(
            documents,
            output,
            device=device,
            work_dir=work_dir,
            cache_bundle=cache_bundle,
        )


def _build_bundle_locked(
    documents, output, device="cpu", work_dir=None, cache_bundle=None
):
    documents = Path(documents).resolve()
    output = Path(output).resolve()
    if not documents.is_dir():
        raise FileNotFoundError(documents)
    pdfs = sorted(documents.glob("*.pdf"))
    source_identity = [{"file_name": p.name, "sha256": digest(p)} for p in pdfs]
    if not source_identity:
        raise ValueError(f"No PDFs found under {documents}")
    source_fingerprint = hashlib.sha256(
        json.dumps(source_identity, separators=(",", ":")).encode()
    ).hexdigest()
    stage_codes = stage_fingerprints()
    existing_corpus_path = output / "corpus.json"
    publish_state_path = output / ".build-state.json"
    publication_identity = {
        "source_pdfs_sha256": source_fingerprint,
        "pipeline_sha256": stage_codes["overall"],
    }
    if output.is_file():
        raise FileExistsError(f"Output path is a file: {output}")
    if publish_state_path.exists():
        try:
            publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Incomplete publication state is unreadable: {publish_state_path}"
            ) from error
        if publish_state != publication_identity:
            raise ValueError(
                "Incomplete output belongs to different inputs or pipeline; choose a new output directory"
            )
    existing_corpus = None
    if existing_corpus_path.exists():
        existing_corpus = json.loads(existing_corpus_path.read_text(encoding="utf-8"))
        if (
            existing_corpus.get("inputs", {}).get("source_pdfs_sha256")
            != source_fingerprint
        ):
            raise ValueError(
                "Existing bundle was built from different source PDFs; choose a new output directory"
            )
        if existing_corpus.get("pipeline_sha256") != stage_codes["overall"]:
            raise ValueError(
                "Existing bundle uses a different pipeline fingerprint; choose a new output directory"
            )
        try:
            check_bundle(output)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            pass
        else:
            publish_state_path.unlink(missing_ok=True)
            return output
    elif any((output / name).exists() for name in BUNDLE_FILES):
        raise FileExistsError(
            "Output contains bundle files without a matching build-state marker; "
            "choose a new output directory or move those files aside"
        )
    work = Path(work_dir or output.parent / f".{output.name}-build").resolve()
    _assert_work_dir_compatible(work, stage_codes, source_identity, source_fingerprint)
    work.mkdir(parents=True, exist_ok=True)
    _write_work_pipeline_marker(work, stage_codes, source_fingerprint)
    output.mkdir(parents=True, exist_ok=True)
    curated_root = (
        work / f"curated-{source_fingerprint[:12]}-{stage_codes['curation'][:12]}"
    )
    cache_root = work / "layout-cache"
    curated, curation_report = curate_dataset(
        "advanced",
        documents.parent.parent,
        curated_root,
        cache_root,
        documents_directory=documents,
    )
    validate_curated(curated)
    curation_manifest = json.loads(
        (curated / "manifest.json").read_text(encoding="utf-8")
    )
    curated_pdfs = [
        {"file_name": item["file_name"], "sha256": item["sha256"]}
        for item in curation_manifest["identity"]["documents"]
    ]
    if curated_pdfs != source_identity:
        raise ValueError("Curation inputs do not match the requested PDF directory")
    stage = work / "bundle"
    stage.mkdir(exist_ok=True)
    pages_path = stage / "pages.json"
    page_identity = stage / "pages-input.json"
    expected_page_identity = {
        "inputs": source_identity,
        "builder_sha256": stage_codes["pages"],
    }
    try:
        existing_page_identity = json.loads(page_identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing_page_identity = None
    pages_valid = (
        pages_path.exists()
        and isinstance(existing_page_identity, dict)
        and all(
            existing_page_identity.get(key) == value
            for key, value in expected_page_identity.items()
        )
        and existing_page_identity.get("pages_sha256") == digest(pages_path)
    )
    if not pages_valid:
        dump(pages_path, extract_pages(documents))
        dump(
            page_identity,
            {**expected_page_identity, "pages_sha256": digest(pages_path)},
        )
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    if len(pdfs) == 120 and len(pages) != 18000:
        raise ValueError(f"Expected 18000 pages, got {len(pages)}")
    curation_manifest_sha256 = digest(curated / "manifest.json")
    expected_store_inputs = {
        "source_fingerprint": source_fingerprint,
        "curation_manifest_sha256": curation_manifest_sha256,
        "builder_sha256": stage_codes["physical"],
    }
    try:
        store_state = json.loads((stage / "store.json").read_text(encoding="utf-8"))
        store_valid = (
            store_state.get("inputs") == expected_store_inputs
            and {"catalog.json", "tables.sqlite3", "table-chunks.json"}
            <= set(store_state.get("files", {}))
            and all(
                (stage / name).exists() and digest(stage / name) == value
                for name, value in store_state.get("files", {}).items()
            )
        )
    except (OSError, json.JSONDecodeError):
        store_valid = False
    if not store_valid:
        build_physical_store(
            curated,
            pages,
            stage,
            source_fingerprint,
            cache_root / "advanced",
            builder_sha256=stage_codes["physical"],
            curation_manifest_sha256=curation_manifest_sha256,
        )
    pages_hash = digest(pages_path)
    catalog_hash = digest(stage / "catalog.json")
    records_manifest_path = stage / "records.json"
    try:
        record_state = json.loads(records_manifest_path.read_text(encoding="utf-8"))
        records_valid = (
            record_state.get("inputs")
            == {"pages_sha256": pages_hash, "catalog_sha256": catalog_hash}
            and record_state.get("database_sha256") == digest(stage / "records.sqlite3")
            and record_state.get("builder_sha256") == stage_codes["records"]
            and record_state.get("curation_manifest_sha256")
            == digest(curated / "manifest.json")
        )
    except (OSError, json.JSONDecodeError):
        records_valid = False
    if not records_valid:
        build_records_index(
            curated, pages_path, stage, builder_sha256=stage_codes["records"]
        )
    vectors = stage / "vectors.npy"
    identity = stage / "vectors-input.json"
    vector_manifest = stage / "vectors-manifest.json"
    vector_input = {
        "pages_sha256": pages_hash,
        "table_chunks_sha256": digest(stage / "table-chunks.json"),
        "builder_sha256": stage_codes["vectors"],
        "embedding": embedding_contract(device),
    }
    try:
        vector_state = json.loads(vector_manifest.read_text(encoding="utf-8"))
        vectors_valid = (
            vectors.exists()
            and identity.exists()
            and json.loads(identity.read_text(encoding="utf-8")) == vector_input
            and vector_state.get("inputs") == vector_input
            and vector_state.get("vectors_sha256") == digest(vectors)
        )
    except (OSError, json.JSONDecodeError):
        vectors_valid = False
    if not vectors_valid:
        execution = build_vectors(
            pages_path,
            stage / "table-chunks.json",
            vectors,
            device=device,
            cache_bundle=cache_bundle,
            progress_dir=stage / f".vectors-{stage_codes['vectors'][:12]}",
            input_identity=vector_input,
        )
        dump(identity, vector_input)
        dump(
            vector_manifest,
            {
                "inputs": vector_input,
                "vectors_sha256": digest(vectors),
                "execution": execution,
            },
        )
    else:
        execution = vector_state.get("execution", {})
    chunks = json.loads((stage / "table-chunks.json").read_text(encoding="utf-8"))
    matrix = np.load(vectors, mmap_mode="r", allow_pickle=False)
    if (
        matrix.shape != (len(pages) + len(chunks), DIMENSIONS)
        or matrix.dtype != np.float32
        or not np.isfinite(matrix).all()
        or not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=2e-3)
    ):
        raise ValueError("Vector matrix failed validation")
    del matrix
    store = json.loads((stage / "store.json").read_text(encoding="utf-8"))
    records_manifest = json.loads((stage / "records.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "dataset": "advanced",
        "pipeline_sha256": stage_codes["overall"],
        "pipeline_stages": stage_codes,
        "embedding": vector_input["embedding"],
        "reranker": {"model_id": RERANKER_ID, "revision": RERANKER_REVISION},
        "counts": {
            "pages": len(pages),
            "table_documents": len(chunks),
            "physical_tables": store["tables"],
        },
        "files": {
            name: digest(stage / name)
            for name in (
                "pages.json",
                "vectors.npy",
                "store.json",
                "catalog.json",
                "tables.sqlite3",
                "table-chunks.json",
                "records.json",
                "records.sqlite3",
            )
        },
        "ingestion": {
            "curation": {
                "documents": curation_report["documents"],
                "pages": curation_report["pages"],
                "records": curation_report["records"],
                "covered_blocks": curation_report["covered_blocks"],
                "table_rows": curation_report["table_rows"],
                "source_coverage": curation_report["quality_gate"]["source_coverage"],
            },
            "table_store": {
                "tables": store["tables"],
                "raw_rows_roundtrip_verified": store["raw_rows_roundtrip_verified"],
            },
            "records": records_manifest["counts"],
            "family_tables": store["family_tables"],
            "vector_build": execution,
        },
        "inputs": {
            "curation_manifest_sha256": curation_manifest_sha256,
            "page_source_sha256": pages_hash,
            "table_chunks_sha256": digest(stage / "table-chunks.json"),
            "source_pdfs_sha256": source_fingerprint,
        },
    }
    dump(stage / "corpus.json", manifest)
    check_bundle(stage)
    names = BUNDLE_FILES
    if publish_state_path.exists():
        current_state = json.loads(publish_state_path.read_text(encoding="utf-8"))
        if current_state != publication_identity:
            raise ValueError(
                "Refusing to continue a publication with a different input or pipeline fingerprint"
            )
    else:
        _atomic_dump(publish_state_path, publication_identity)
    with tempfile.TemporaryDirectory(dir=output, prefix=".publishing-") as temporary:
        temp = Path(temporary)
        for name in names:
            shutil.copyfile(stage / name, temp / name)
        for name in names[:-1]:
            (temp / name).replace(output / name)
        (temp / "corpus.json").replace(output / "corpus.json")
    check_bundle(output)
    publish_state_path.unlink(missing_ok=True)
    return output


__all__ = (
    "build_bundle",
    "build_physical_store",
    "build_records_index",
    "build_vectors",
    "check_bundle",
    "check_database",
    "digest",
    "embedding_contract",
    "prefetch_models",
    "stage_fingerprints",
    "typed",
    "_output_build_lock",
)
