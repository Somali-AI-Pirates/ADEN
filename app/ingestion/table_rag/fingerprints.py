"""Source and dependency fingerprints for staged corpus builds."""

import hashlib
import inspect
import json
from importlib.metadata import version as distribution_version
from pathlib import Path

from app.ingestion.curation import curation_environment


def callable_fingerprint(*functions, values=()):
    digest = hashlib.sha256()
    for function in functions:
        digest.update(function.__module__.encode())
        digest.update(function.__qualname__.encode())
        digest.update(inspect.getsource(function).encode())
    digest.update(
        json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            default=lambda value: (
                sorted(value) if isinstance(value, set) else repr(value)
            ),
        ).encode()
    )
    return digest.hexdigest()


def dependency_versions(names):
    return {name: distribution_version(name) for name in names}


def stage_fingerprints():
    """Fingerprint each build stage and the overall assembly implementation."""
    from app.indexing.keyword_store import (
        BM25_CODE_DASHES,
        BM25_CODE_PATTERN,
        TOKEN,
        tokenize,
        tokenize_for_bm25,
    )
    from shared.text_normalization import normalize_text

    from .common import atomic_dump, digest, dump
    from .constants import (
        CODE,
        DATE_FIELDS,
        HA_ROLES,
        HG_EVENT_FIELDS,
        IS_ANNOTATED_KEYS,
        IS_KEYS,
        ROLE_MAP,
        ROLE_ORDER,
    )
    from .sources import (
        _read_curated,
        extract_pages,
        family,
        page_id,
        page_references,
        typed,
    )
    from .stores import (
        build_physical_store,
        build_records_index,
        code_kind,
        normalize_code,
        norm,
    )
    from .vectors import (
        _encode_document_batch,
        _input_token_stats,
        build_vectors,
        dependency_versions,
        embedding_contract,
    )
    from app.ingestion.table_rag_builder import (
        _assert_work_dir_compatible,
        _build_bundle_locked,
        _output_build_lock,
        build_bundle,
    )

    curation_digest = hashlib.sha256()
    ingestion_dir = Path(__file__).resolve().parents[1]
    for name in ("curation.py", "curated_schema.py", "layout.py", "common_rules.py"):
        curation_digest.update(name.encode())
        curation_digest.update((ingestion_dir / name).read_bytes())
    curation_digest.update(
        json.dumps(curation_environment(), ensure_ascii=False, sort_keys=True).encode()
    )
    curation = curation_digest.hexdigest()
    pages = callable_fingerprint(
        extract_pages,
        dependency_versions,
        values=("pypdf-layout", dependency_versions(("pypdf",))),
    )
    physical = callable_fingerprint(
        build_physical_store,
        _read_curated,
        typed,
        family,
        page_id,
        norm,
        normalize_text,
        normalize_code,
        code_kind,
        dump,
        digest,
        values=(ROLE_MAP, DATE_FIELDS, HG_EVENT_FIELDS, ROLE_ORDER),
    )
    records = callable_fingerprint(
        build_records_index,
        page_references,
        page_id,
        family,
        norm,
        normalize_text,
        normalize_code,
        code_kind,
        dump,
        digest,
        tokenize_for_bm25,
        tokenize,
        values=(
            ROLE_MAP,
            HA_ROLES,
            DATE_FIELDS,
            HG_EVENT_FIELDS,
            IS_KEYS,
            IS_ANNOTATED_KEYS,
            ROLE_ORDER,
            CODE.pattern,
            TOKEN.pattern,
            BM25_CODE_PATTERN.pattern,
            tuple(sorted(BM25_CODE_DASHES.items())),
        ),
    )
    vectors = callable_fingerprint(
        build_vectors,
        _encode_document_batch,
        _input_token_stats,
        embedding_contract,
        dependency_versions,
        atomic_dump,
        digest,
        values=(embedding_contract("cuda:0"),),
    )
    orchestration = callable_fingerprint(
        build_bundle,
        _build_bundle_locked,
        _output_build_lock,
        _assert_work_dir_compatible,
        stage_fingerprints,
        atomic_dump,
    )
    stages = {
        "curation": curation,
        "pages": pages,
        "physical": physical,
        "records": records,
        "vectors": vectors,
        "orchestration": orchestration,
    }
    stages["overall"] = hashlib.sha256(
        json.dumps(stages, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return stages
