"""Build local, source-grounded Advanced page-vector and HG relation snapshots."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import (
    CURATED,
    ROLES,
    build_index,
    hg_ids,
)
from experimental.u_10o0o.embeding import (
    BATCH_SIZE,
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, PROJECT_ROOT

ARTIFACTS = PROJECT_ROOT / "artifacts/u_10o0o"
IS_SNAPSHOT = ARTIFACTS / "advanced-snapshots/is-snowflake-v1"
HG_SNAPSHOT = ARTIFACTS / "advanced-snapshots/hg-relations-v1"
SCHEMA_VERSION = 1
IS_TYPE = "is-search-vectors"
HG_TYPE = "hg-source-relations-sqlite"
SNAPSHOT_OUTPUTS = {
    IS_TYPE: ("source_ids.jsonl", "page_inputs.jsonl", "vectors.npy"),
    HG_TYPE: ("advanced.sqlite3",),
}
IS_HEADING = re.compile(r"IS\d+\s*\|")
HA_ROLE_NAMES = frozenset(("계약 조건 확인", "사건·증빙 접수 대조", "비용별 정산 확인"))
OTHER_ID_PREFIXES = ("SW28-", "DL28-")
PARAGRAPH = re.compile(r"\n\s*\n+")
PAGE_FOOTER = re.compile(r"(\d+)\s*/\s*(\d+)")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _sha_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _page_snapshot_sha(pages: Sequence[dict[str, Any]]) -> str:
    digest = sha256()
    for page in pages:
        book_id = Path(page["file_name"]).stem
        text = page["text"]
        if not isinstance(text, str):
            raise ValueError("페이지 본문은 문자열이어야 합니다.")
        identity = {
            "source_id": f"advanced:{book_id}:p{page['page']}",
            "text_sha256": _sha_bytes(text.encode("utf-8")),
        }
        digest.update(_json_bytes(identity))
    return digest.hexdigest()


def _read_pages_and_sources() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    curated_manifest = CURATED / "manifest.json"
    manifest = json.loads(curated_manifest.read_bytes())
    if manifest.get("identity", {}).get("dataset") != "advanced":
        raise ValueError("Advanced 정제 스냅샷이 필요합니다.")
    sources_path = CURATED / "sources.jsonl"
    if manifest.get("outputs", {}).get("sources.jsonl") != _sha_file(sources_path):
        raise ValueError("sources.jsonl이 정제 스냅샷 manifest와 다릅니다.")

    sources = list(read_rows(sources_path))
    source_by_id = {}
    for source in sources:
        source_id = source["source_id"]
        if source_id in source_by_id:
            raise ValueError(f"중복된 source_id: {source_id}")
        source_by_id[source_id] = source

    pages = json.loads(CACHE_PATH.read_bytes())
    if len(pages) != len(sources):
        raise ValueError("복원 페이지와 정제 출처의 페이지 수가 다릅니다.")
    for page in pages:
        book_id = Path(page["file_name"]).stem
        source_id = f"advanced:{book_id}:p{page['page']}"
        source = source_by_id.get(source_id)
        if (
            source is None
            or source["dataset"] != "advanced"
            or source["book_id"] != book_id
            or source["file_name"] != page["file_name"]
            or source["page"] != page["page"]
            or not isinstance(page.get("text"), str)
        ):
            raise ValueError("복원 페이지의 원본 source_id 대응을 확인할 수 없습니다.")
    if {
        f"advanced:{Path(page['file_name']).stem}:p{page['page']}" for page in pages
    } != source_by_id.keys():
        raise ValueError("정제 출처와 복원 페이지의 집합이 다릅니다.")
    return pages, source_by_id


def _input_identity(pages, source_by_id) -> dict[str, Any]:
    return {
        "curation_manifest_sha256": _sha_file(CURATED / "manifest.json"),
        "curated_sources_sha256": _sha_file(CURATED / "sources.jsonl"),
        "page_cache_sha256": _sha_file(CACHE_PATH),
        "page_snapshot_sha256": _page_snapshot_sha(pages),
        "source_page_count": len(source_by_id),
    }


def _published_snapshot(
    output_root: Path, input_identity: dict[str, Any], snapshot_type: str
) -> bool:
    if not output_root.exists():
        return False
    manifest_path = output_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileExistsError(f"완성된 스냅샷이 아닌 출력 경로입니다: {output_root}")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("inputs") != input_identity:
        raise FileExistsError(
            f"기존 스냅샷 입력 지문이 다릅니다. 기존 데이터를 보존합니다: {output_root}"
        )
    outputs = manifest.get("outputs", {})
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("snapshot_type") != snapshot_type
        or not isinstance(outputs, dict)
        or outputs.keys() != set(SNAPSHOT_OUTPUTS[snapshot_type])
        or manifest.get("snapshot_id")
        != _sha_bytes(_json_bytes({"inputs": input_identity, "outputs": outputs}))
    ):
        raise ValueError("기존 스냅샷의 종류, 출력 파일 또는 manifest 지문이 다릅니다.")
    for name, expected in outputs.items():
        path = output_root / name
        if not path.is_file() or _sha_file(path) != expected:
            raise ValueError(f"기존 스냅샷 파일의 지문이 다릅니다: {path}")
    return True


def _publish(
    output_root: Path, staging: Path, input_identity: dict[str, Any], snapshot_type: str
) -> Path:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.rename(output_root)
    except OSError:
        if not _published_snapshot(output_root, input_identity, snapshot_type):
            raise
    return output_root


def _write_snapshot_manifest(
    staging, *, snapshot_type, inputs, output_names, counts, config
):
    outputs = {name: _sha_file(staging / name) for name in output_names}
    content = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_type": snapshot_type,
        "snapshot_id": _sha_bytes(_json_bytes({"inputs": inputs, "outputs": outputs})),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "inputs": inputs,
        "outputs": outputs,
        "counts": counts,
        "config": config,
    }
    (staging / "manifest.json").write_bytes(_json_bytes(content))
    return content


def _is_page_texts(pages, source_by_id):
    is_pages_by_book: dict[str, list[tuple[dict[str, Any], list[str]]]] = defaultdict(
        list
    )
    page_counts = Counter(source["book_id"] for source in source_by_id.values())
    original_by_id = {}

    for page in pages:
        book_id = Path(page["file_name"]).stem
        source_id = f"advanced:{book_id}:p{page['page']}"
        original_by_id[source_id] = page["text"]
        if IS_HEADING.match(source_by_id[source_id]["heading"]):
            blocks = [
                part.strip()
                for part in PARAGRAPH.split(page["text"].strip())
                if part.strip()
            ]
            is_pages_by_book[book_id].append((page, blocks))

    if len(is_pages_by_book) != 50 or sum(map(len, is_pages_by_book.values())) != 3_750:
        raise ValueError(
            "정제본에서 기대한 50개 PDF·3,750개 IS 페이지를 확인하지 못했습니다."
        )

    common_prefixes = {}
    prefix_books_verified = 0
    for book_id, rows in is_pages_by_book.items():
        candidates = [tuple(blocks[:2]) for _, blocks in rows if len(blocks) >= 2]
        if len(candidates) == len(rows) and len(set(candidates)) == 1:
            common_prefixes[book_id] = candidates[0]
            prefix_books_verified += 1

    search_text_by_id = {}
    removed_by_id = {}
    reason_counts = Counter()
    for book_id, rows in is_pages_by_book.items():
        common = common_prefixes.get(book_id)
        for page, original_blocks in rows:
            prefix_matches = (
                common is not None
                and len(original_blocks) >= 2
                and tuple(original_blocks[:2]) == common
            )
            footer_index = len(original_blocks) - 1
            footer_match = (
                PAGE_FOOTER.fullmatch(original_blocks[footer_index])
                if original_blocks
                else None
            )
            footer_matches = (
                footer_match is not None
                and int(footer_match[1]) == page["page"]
                and int(footer_match[2]) == page_counts[book_id]
            )
            removed = []
            if not prefix_matches:
                reason_counts["preserved_missing_common_prefix"] += 1
            if not footer_matches:
                reason_counts["preserved_missing_exact_footer"] += 1

            if prefix_matches and footer_matches:
                blocks = original_blocks[2:-1]
                candidate = "\n\n".join(blocks).strip()
                if candidate:
                    removed = [0, 1, footer_index]
                    reason_counts["removed_verified_prefix_and_footer"] += 1
                else:
                    candidate = page["text"]
                    reason_counts["preserved_empty_result"] += 1
            else:
                # If either boundary is uncertain, retain the source bytes exactly.
                candidate = page["text"]

            source_id = f"advanced:{book_id}:p{page['page']}"
            search_text_by_id[source_id] = candidate
            removed_by_id[source_id] = removed

    page_inputs = []
    changed_ids = []
    for page in pages:
        book_id = Path(page["file_name"]).stem
        source_id = f"advanced:{book_id}:p{page['page']}"
        text = search_text_by_id.get(source_id, page["text"])
        page_inputs.append(
            {
                "source_id": source_id,
                "text": text,
                "input_sha256": _sha_bytes(text.encode("utf-8")),
                "removed_blocks": removed_by_id.get(source_id, []),
            }
        )
        if text != original_by_id[source_id]:
            changed_ids.append(source_id)
    return page_inputs, changed_ids, prefix_books_verified, dict(reason_counts)


def build_is(output_root: Path = IS_SNAPSHOT, *, device: str = "cuda") -> Path:
    """Build an immutable, source-ordered IS vector snapshot from local inputs."""
    pages, source_by_id = _read_pages_and_sources()
    inputs = _input_identity(pages, source_by_id)
    output_root = Path(output_root)
    base_vectors = np.load(VECTORS_PATH, mmap_mode="r", allow_pickle=False)
    if (
        base_vectors.shape != (len(pages), EMBEDDING_DIM)
        or base_vectors.dtype != np.float32
    ):
        raise ValueError("기존 Snowflake-ko 벡터의 행 수·차원·자료형이 맞지 않습니다.")
    if not np.isfinite(base_vectors).all():
        raise ValueError("기존 벡터에 유한하지 않은 값이 있습니다.")
    base_vector_content_sha = _sha_bytes(np.ascontiguousarray(base_vectors).tobytes())
    inputs.update(
        {
            "model": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "embedding_dimension": EMBEDDING_DIM,
            "base_vectors_file_sha256": _sha_file(VECTORS_PATH),
            "base_vectors_content_sha256": base_vector_content_sha,
        }
    )
    if _published_snapshot(output_root, inputs, IS_TYPE):
        return output_root

    page_inputs, changed_ids, prefix_books_verified, reason_counts = _is_page_texts(
        pages, source_by_id
    )
    page_ids = [
        f"advanced:{Path(page['file_name']).stem}:p{page['page']}" for page in pages
    ]
    input_text_by_id = {row["source_id"]: row["text"] for row in page_inputs}
    if len(input_text_by_id) != len(page_ids) or set(input_text_by_id) != set(page_ids):
        raise ValueError("검색 텍스트와 페이지 출처 순서가 일치하지 않습니다.")

    vectors = np.array(base_vectors, dtype=np.float32, copy=True)
    model_config = {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "dimension": EMBEDDING_DIM,
        "normalized": True,
        "batch_size": BATCH_SIZE,
        "device": device,
    }
    token_counts = {}
    if changed_ids:
        model = load_model(device)
        texts = [input_text_by_id[source_id] for source_id in changed_ids]
        token_lengths = [
            len(ids)
            for ids in model.tokenizer(texts, truncation=False, verbose=False)[
                "input_ids"
            ]
        ]
        if model.max_seq_length is None:
            raise ValueError("Snowflake-ko 모델의 입력 한도를 확인할 수 없습니다.")
        if any(not text.strip() for text in texts):
            raise ValueError("정제 뒤 본문이 빈 IS 페이지가 있습니다.")
        embeddings = model.encode_document(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        if embeddings.shape != (len(changed_ids), EMBEDDING_DIM):
            raise ValueError("새 IS 벡터의 행 수 또는 차원이 맞지 않습니다.")
        indices = {source_id: index for index, source_id in enumerate(page_ids)}
        for source_id, embedding in zip(changed_ids, embeddings, strict=True):
            vectors[indices[source_id]] = embedding
        token_counts = {
            "transformed_pages": len(token_lengths),
            "max_input_tokens": max(token_lengths, default=0),
            "over_model_limit": sum(
                length > model.max_seq_length for length in token_lengths
            ),
        }
        del model, embeddings

    norms = np.linalg.norm(vectors, axis=1)
    if not np.isfinite(vectors).all() or not np.allclose(norms, 1, atol=2e-3):
        raise ValueError("IS 벡터에 정규화되지 않았거나 유한하지 않은 행이 있습니다.")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_root.parent, prefix=f".{output_root.name}-"
    ) as temporary:
        staging = Path(temporary)
        _write_jsonl(
            staging / "source_ids.jsonl", [{"source_id": item} for item in page_ids]
        )
        _write_jsonl(staging / "page_inputs.jsonl", page_inputs)
        np.save(staging / "vectors.npy", vectors, allow_pickle=False)
        counts = {
            "pages": len(page_ids),
            "is_pages": len(changed_ids),
            "transformed_is_pages": len(changed_ids),
            "verified_is_header_books": prefix_books_verified,
            "is_transform_reasons": reason_counts,
            **token_counts,
        }
        config = {
            **model_config,
            "base_vectors_file_sha256": _sha_file(VECTORS_PATH),
            "base_vectors_content_sha256": base_vector_content_sha,
            "page_ids_sha256": _sha_file(staging / "source_ids.jsonl"),
            "page_inputs_sha256": _sha_file(staging / "page_inputs.jsonl"),
            "vectors_sha256": _sha_file(staging / "vectors.npy"),
            "page_order": "Advanced restored page cache order",
        }
        _write_snapshot_manifest(
            staging,
            snapshot_type=IS_TYPE,
            inputs=inputs,
            output_names=SNAPSHOT_OUTPUTS[IS_TYPE],
            counts=counts,
            config=config,
        )
        result = _publish(output_root, staging, inputs, IS_TYPE)
    return result


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(_json_bytes_compact(row))


def _json_bytes_compact(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def load_is(
    snapshot: Path,
    pages: Sequence[dict[str, Any]],
    base_vectors: np.ndarray,
) -> np.ndarray:
    """Load cleaned vectors after verifying page order and original-vector identity."""
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("snapshot_type") != "is-search-vectors"
    ):
        raise ValueError("IS 벡터 스냅샷 버전이 맞지 않습니다.")
    expected_outputs = {"source_ids.jsonl", "page_inputs.jsonl", "vectors.npy"}
    if set(manifest.get("outputs", {})) != expected_outputs:
        raise ValueError("IS 벡터 스냅샷 출력 파일 구성이 다릅니다.")
    if manifest.get("snapshot_id") != _sha_bytes(
        _json_bytes({"inputs": manifest.get("inputs"), "outputs": manifest["outputs"]})
    ):
        raise ValueError("IS 벡터 스냅샷 manifest 지문이 맞지 않습니다.")
    if (
        manifest["config"].get("model") != MODEL_ID
        or manifest["config"].get("revision") != MODEL_REVISION
        or manifest["config"].get("dimension") != EMBEDDING_DIM
    ):
        raise ValueError("IS 벡터 스냅샷의 임베딩 모델 설정이 다릅니다.")
    inputs = manifest["inputs"]
    if inputs["page_snapshot_sha256"] != _page_snapshot_sha(pages):
        raise ValueError("IS 벡터 스냅샷의 원본 페이지 지문이 다릅니다.")
    current_pages, current_sources = _read_pages_and_sources()
    expected_inputs = _input_identity(current_pages, current_sources)
    if (
        base_vectors.shape != (len(pages), EMBEDDING_DIM)
        or base_vectors.dtype != np.float32
    ):
        raise ValueError("현재 Snowflake-ko 기준 벡터의 모양 또는 자료형이 다릅니다.")
    current_base_sha = _sha_bytes(np.ascontiguousarray(base_vectors).tobytes())
    expected_inputs.update(
        {
            "model": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "embedding_dimension": EMBEDDING_DIM,
            "base_vectors_file_sha256": _sha_file(VECTORS_PATH),
            "base_vectors_content_sha256": current_base_sha,
        }
    )
    if inputs != expected_inputs:
        raise ValueError("IS 스냅샷의 정제본·페이지 입력 출처가 현재 자료와 다릅니다.")
    if manifest["config"]["base_vectors_content_sha256"] != current_base_sha:
        raise ValueError("IS 벡터 스냅샷과 기준 벡터 파일이 다릅니다.")

    for name, expected in manifest["outputs"].items():
        if _sha_file(snapshot / name) != expected:
            raise ValueError(f"IS 벡터 스냅샷 파일의 지문이 다릅니다: {name}")
    source_rows = list(_read_jsonl(snapshot / "source_ids.jsonl"))
    page_ids = [
        f"advanced:{Path(page['file_name']).stem}:p{page['page']}" for page in pages
    ]
    if [row["source_id"] for row in source_rows] != page_ids:
        raise ValueError("IS 벡터 스냅샷의 source_id 순서가 다릅니다.")
    page_inputs = list(_read_jsonl(snapshot / "page_inputs.jsonl"))
    if [row["source_id"] for row in page_inputs] != page_ids:
        raise ValueError("IS 검색 텍스트의 source_id 순서가 다릅니다.")
    for row in page_inputs:
        if row["input_sha256"] != _sha_bytes(row["text"].encode("utf-8")):
            raise ValueError(f"IS 검색 텍스트 지문이 다릅니다: {row['source_id']}")

    vectors = np.load(snapshot / "vectors.npy", allow_pickle=False)
    if vectors.shape != (len(pages), EMBEDDING_DIM) or vectors.dtype != np.float32:
        raise ValueError("IS 벡터 스냅샷의 행 수·차원·자료형이 맞지 않습니다.")
    if not np.isfinite(vectors).all() or not np.allclose(
        np.linalg.norm(vectors, axis=1), 1, atol=2e-3
    ):
        raise ValueError("IS 벡터 스냅샷의 수치 또는 정규화를 확인할 수 없습니다.")
    return vectors


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _page_family(heading: str) -> str:
    families = set()
    if hg_ids(heading):
        families.add("HG")
    if any(item.startswith("HA28-") for item in identifiers(heading)):
        families.add("HA")
    if IS_HEADING.match(heading):
        families.add("IS")
    if any(item.startswith(OTHER_ID_PREFIXES) for item in identifiers(heading)):
        families.add("other")
    if len(families) == 1:
        return families.pop()
    return "unknown"


def _validate_case_source_index(inputs: dict[str, Any]) -> list[dict[str, str]]:
    index_path = HG_SOURCES / "case_sources.jsonl"
    summary_path = HG_SOURCES / "summary.json"
    summary = json.loads(summary_path.read_bytes())
    if (
        summary["curation_manifest_sha256"] != inputs["curation_manifest_sha256"]
        or summary["case_sources_sha256"] != _sha_file(index_path)
        or summary["indexed_cases"] != 2_730
        or summary["source_links"] != 2_730 * len(ROLES)
        or summary["review_cases"] != 0
    ):
        raise ValueError("검증된 HG 사건·출처 인덱스의 지문 또는 건수가 맞지 않습니다.")
    links = list(_read_jsonl(index_path))
    independently_built, review, unmatched = build_index(CURATED)
    if review or unmatched or links != independently_built:
        raise ValueError("HG 사건·역할 연결을 원본 정제본에서 재현하지 못했습니다.")
    return links


HG_SOURCES = ARTIFACTS / "advanced-curation/hg-case-sources-20261109"


def _verify_pdfs(expected_documents):
    for file_name, document in expected_documents.items():
        if Path(file_name).name != file_name:
            raise ValueError("정제본의 원본 PDF 파일 이름이 유효하지 않습니다.")
        path = DATA_DIR / "documents" / file_name
        if not path.is_file() or _sha_file(path) != document["sha256"]:
            raise ValueError(f"원본 PDF 파일의 지문이 정제본과 다릅니다: {file_name}")


def build_hg(output_root: Path = HG_SNAPSHOT) -> Path:
    """Build a verified, read-only-use SQLite snapshot for HG source relations."""
    pages, source_by_id = _read_pages_and_sources()
    inputs = _input_identity(pages, source_by_id)
    output_root = Path(output_root)
    links = _validate_case_source_index(inputs)
    curation = json.loads((CURATED / "manifest.json").read_bytes())
    expected_documents = {
        row["file_name"]: row for row in curation["identity"]["documents"]
    }
    source_documents: dict[str, dict[str, Any]] = {}
    page_counts = Counter()
    source_pages = []
    page_by_source = {}

    for page in pages:
        book_id = Path(page["file_name"]).stem
        source_id = f"advanced:{book_id}:p{page['page']}"
        source = source_by_id[source_id]
        document = expected_documents.get(source["file_name"])
        if (
            document is None
            or source["file_sha256"] != document["sha256"]
            or source["page"] < 1
            or source["page"] > document["pages"]
        ):
            raise ValueError(
                f"복원 페이지의 PDF 지문 또는 쪽 번호가 다릅니다: {source_id}"
            )
        source_documents[book_id] = {
            "book_id": book_id,
            "file_name": source["file_name"],
            "file_sha256": source["file_sha256"],
            "page_count": document["pages"],
        }
        page_counts[book_id] += 1
        row = {
            "source_id": source_id,
            "book_id": book_id,
            "page": source["page"],
            "page_family": _page_family(source["heading"]),
            "heading": source["heading"],
        }
        source_pages.append(row)
        page_by_source[source_id] = row

    if len(source_pages) != 18_000 or any(
        page_counts[book_id] != doc["page_count"]
        for book_id, doc in source_documents.items()
    ):
        raise ValueError("PDF별 출처 페이지 수가 정제본과 일치하지 않습니다.")
    _verify_pdfs(expected_documents)
    for row in links:
        source = page_by_source.get(row["source_id"])
        if (
            source is None
            or source["page_family"] != "HG"
            or row["record_role"] not in ROLES
            or row["match_rule"] != "page_heading_case_id"
            or hg_ids(source["heading"]) != {row["case_id"]}
        ):
            raise ValueError("HG 사건 연결의 원문 페이지를 검증할 수 없습니다.")

    inputs.update(
        {
            "case_sources_sha256": _sha_file(HG_SOURCES / "case_sources.jsonl"),
            "case_sources_summary_sha256": _sha_file(HG_SOURCES / "summary.json"),
            "pdfs": {
                name: {
                    "sha256": document["sha256"],
                    "page_count": document["pages"],
                }
                for name, document in sorted(expected_documents.items())
            },
        }
    )
    if _published_snapshot(output_root, inputs, HG_TYPE):
        return output_root
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_root.parent, prefix=f".{output_root.name}-"
    ) as temporary:
        staging = Path(temporary)
        database_path = staging / "advanced.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            version = sqlite3.sqlite_version_info
            if version < (3, 37, 0):
                raise RuntimeError("HG 스냅샷에는 SQLite 3.37 이상이 필요합니다.")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE source_documents (
                    book_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL UNIQUE,
                    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
                    page_count INTEGER NOT NULL CHECK (page_count > 0)
                ) STRICT;
                CREATE TABLE source_pages (
                    source_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL REFERENCES source_documents(book_id),
                    page INTEGER NOT NULL CHECK (page > 0),
                    page_family TEXT NOT NULL CHECK (
                        page_family IN ('HG', 'HA', 'IS', 'other', 'unknown')
                    ),
                    heading TEXT NOT NULL,
                    UNIQUE (book_id, page)
                ) STRICT;
                CREATE TABLE cases (
                    family TEXT NOT NULL CHECK (family IN ('HG', 'HA')),
                    case_id TEXT NOT NULL,
                    PRIMARY KEY (family, case_id)
                ) STRICT;
                CREATE TABLE case_records (
                    family TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source_id TEXT NOT NULL UNIQUE REFERENCES source_pages(source_id),
                    match_rule TEXT NOT NULL,
                    PRIMARY KEY (family, case_id, role),
                    FOREIGN KEY (family, case_id)
                        REFERENCES cases(family, case_id)
                ) STRICT;
                CREATE INDEX source_pages_book_page
                    ON source_pages(book_id, page);
                PRAGMA user_version = 1;
                """
            )
            connection.executemany(
                "INSERT INTO source_documents VALUES (?, ?, ?, ?)",
                [
                    (d["book_id"], d["file_name"], d["file_sha256"], d["page_count"])
                    for d in sorted(
                        source_documents.values(), key=lambda row: row["book_id"]
                    )
                ],
            )
            connection.executemany(
                "INSERT INTO source_pages VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["source_id"],
                        row["book_id"],
                        row["page"],
                        row["page_family"],
                        row["heading"],
                    )
                    for row in source_pages
                ],
            )
            cases = sorted({("HG", row["case_id"]) for row in links})
            connection.executemany("INSERT INTO cases VALUES (?, ?)", cases)
            connection.executemany(
                "INSERT INTO case_records VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        "HG",
                        row["case_id"],
                        row["record_role"],
                        row["source_id"],
                        row["match_rule"],
                    )
                    for row in links
                ],
            )
            foreign_key_issues = list(connection.execute("PRAGMA foreign_key_check"))
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if foreign_key_issues or integrity != "ok":
                raise ValueError("SQLite 스냅샷 무결성 검사가 실패했습니다.")
            counts = {
                "source_documents": connection.execute(
                    "SELECT count(*) FROM source_documents"
                ).fetchone()[0],
                "source_pages": connection.execute(
                    "SELECT count(*) FROM source_pages"
                ).fetchone()[0],
                "hg_cases": connection.execute(
                    "SELECT count(*) FROM cases WHERE family = 'HG'"
                ).fetchone()[0],
                "hg_case_records": connection.execute(
                    "SELECT count(*) FROM case_records WHERE family = 'HG'"
                ).fetchone()[0],
                "page_families": dict(
                    connection.execute(
                        "SELECT page_family, count(*) FROM source_pages "
                        "GROUP BY page_family ORDER BY page_family"
                    ).fetchall()
                ),
                "links_by_role": dict(
                    connection.execute(
                        "SELECT role, count(*) FROM case_records "
                        "GROUP BY role ORDER BY role"
                    ).fetchall()
                ),
            }
            if (
                counts["source_documents"] != 120
                or counts["source_pages"] != 18_000
                or counts["hg_cases"] != 2_730
                or counts["hg_case_records"] != 13_650
                or set(counts["links_by_role"].values()) != {2_730}
            ):
                raise ValueError("HG SQLite 스냅샷의 건수가 예상과 다릅니다.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        _write_snapshot_manifest(
            staging,
            snapshot_type=HG_TYPE,
            inputs=inputs,
            output_names=SNAPSHOT_OUTPUTS[HG_TYPE],
            counts=counts,
            config={
                "sqlite_version": sqlite3.sqlite_version,
                "role_order": list(ROLES),
                "database_read_only": True,
            },
        )
        result = _publish(output_root, staging, inputs, HG_TYPE)
    return result


class HGStore:
    """Read validated HG source relations and return original page objects."""

    def __init__(self, snapshot: Path, pages: Sequence[dict[str, Any]]):
        self.snapshot = Path(snapshot)
        self.database = self.snapshot / "advanced.sqlite3"
        self._page_snapshot_sha = _page_snapshot_sha(pages)
        self._pages = {}
        for page in pages:
            key = (Path(page["file_name"]).stem, page["page"])
            if key in self._pages:
                raise ValueError(f"복원 페이지 위치가 중복됩니다: {key}")
            self._pages[key] = page
        self._connection: sqlite3.Connection | None = None
        self._manifest: dict[str, Any] | None = None
        self._manifest_sha256: str | None = None

    def __enter__(self) -> HGStore:
        if self._connection is not None:
            raise RuntimeError("HGStore가 이미 열려 있습니다.")
        manifest_path = self.snapshot / "manifest.json"
        self._manifest = json.loads(manifest_path.read_bytes())
        if (
            self._manifest.get("schema_version") != SCHEMA_VERSION
            or self._manifest.get("snapshot_type") != "hg-source-relations-sqlite"
            or self._manifest.get("inputs", {}).get("page_snapshot_sha256")
            != self._page_snapshot_sha
        ):
            raise ValueError("HG SQLite 스냅샷 버전 또는 페이지 지문이 다릅니다.")
        if set(self._manifest.get("outputs", {})) != {"advanced.sqlite3"}:
            raise ValueError("HG SQLite 스냅샷 출력 파일 구성이 다릅니다.")
        if self._manifest.get("snapshot_id") != _sha_bytes(
            _json_bytes(
                {
                    "inputs": self._manifest.get("inputs"),
                    "outputs": self._manifest["outputs"],
                }
            )
        ):
            raise ValueError("HG SQLite 스냅샷 manifest 지문이 맞지 않습니다.")
        current_pages, current_sources = _read_pages_and_sources()
        expected_inputs = _input_identity(current_pages, current_sources)
        curation = json.loads((CURATED / "manifest.json").read_bytes())
        expected_documents = {
            row["file_name"]: row for row in curation["identity"]["documents"]
        }
        expected_inputs.update(
            {
                "case_sources_sha256": _sha_file(HG_SOURCES / "case_sources.jsonl"),
                "case_sources_summary_sha256": _sha_file(HG_SOURCES / "summary.json"),
                "pdfs": {
                    name: {
                        "sha256": document["sha256"],
                        "page_count": document["pages"],
                    }
                    for name, document in sorted(expected_documents.items())
                },
            }
        )
        if self._manifest["inputs"] != expected_inputs:
            raise ValueError(
                "HG 스냅샷의 정제본·사건 연결 출처가 현재 자료와 다릅니다."
            )
        _verify_pdfs(expected_documents)
        expected = self._manifest["outputs"]["advanced.sqlite3"]
        if _sha_file(self.database) != expected:
            raise ValueError("HG SQLite 데이터베이스 지문이 다릅니다.")
        uri = f"{self.database.resolve().as_uri()}?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.execute("PRAGMA query_only = ON")
        if self._connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            self._connection.close()
            self._connection = None
            raise ValueError("HG SQLite 스냅샷의 무결성 검사가 실패했습니다.")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            self._connection.close()
            self._connection = None
            raise ValueError("HG SQLite 스키마 버전이 다릅니다.")
        self._manifest_sha256 = _sha_file(manifest_path)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def provenance(self) -> dict[str, Any]:
        if self._manifest is None or self._manifest_sha256 is None:
            raise RuntimeError("HGStore를 열기 전에는 출처 정보를 읽을 수 없습니다.")
        return {
            "snapshot_id": self._manifest["snapshot_id"],
            "manifest_sha256": self._manifest_sha256,
            "inputs": self._manifest["inputs"],
            "database_sha256": self._manifest["outputs"]["advanced.sqlite3"],
        }

    def pages_for(
        self, case_id: str, roles: Sequence[str] = ROLES
    ) -> list[dict[str, Any]]:
        if self._connection is None:
            raise RuntimeError("pages_for()를 호출하려면 HGStore를 열어야 합니다.")
        wanted = tuple(roles)
        if len(wanted) != len(set(wanted)) or any(role not in ROLES for role in wanted):
            raise ValueError("요청 역할은 중복 없이 기존 HG 역할에서 골라야 합니다.")
        if not wanted:
            return []

        placeholders = ", ".join("?" for _ in ROLES)
        parameters = (
            case_id,
            *wanted,
            *(None for _ in range(len(ROLES) - len(wanted))),
        )
        rows = self._connection.execute(
            "SELECT records.role, records.source_id, pages.book_id, pages.page "
            "FROM case_records AS records "
            "JOIN source_pages AS pages ON pages.source_id = records.source_id "
            f"WHERE records.family = 'HG' AND records.case_id = ? "
            f"AND records.role IN ({placeholders})",
            parameters,
        ).fetchall()
        by_role = {
            role: (source_id, book_id, page_number)
            for role, source_id, book_id, page_number in rows
        }
        if len(by_role) != len(rows):
            raise ValueError("HG 사건의 역할-원본 페이지 연결이 중복되었습니다.")
        if not set(wanted) <= by_role.keys():
            return []

        pages = []
        for role in wanted:
            source_id, book_id, page_number = by_role[role]
            page = self._pages.get((book_id, page_number))
            if page is None:
                raise ValueError(
                    f"HG 스냅샷 원본 페이지가 현재 캐시에 없습니다: {source_id}"
                )
            pages.append(page)
        return pages
