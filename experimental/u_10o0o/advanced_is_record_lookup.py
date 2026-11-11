"""Exact IS record lookup over the existing read-only structured snapshot.

The key identifies a record, not an HG case. Shared prefixes or equipment
identifiers do not create record relationships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from experimental.u_10o0o.advanced_structured_store import RecordRow, StructuredStore
from experimental.u_10o0o.advanced_target_lookup import EXCLUSION
from experimental.u_10o0o.bm25 import CODE_DASHES, NORMALIZED_BIGRAM_ID_PATTERN

RECORD_CODE = re.compile(r"IS[0-9]{2}-[0-9]{3}-[0-9]{2}")
# Observed record-key columns in parsed IS tables; never search arbitrary cells.
RECORD_COLUMNS = (
    "SI기록",
    "VGM 대조",
    "검산번호",
    "검토 기록번호",
    "계산 메모",
    "계획번호",
    "기록번호",
    "단위 대조",
    "당사자 등록",
    "대조번호",
    "등록번호",
    "문서 기록",
    "반입 기록",
    "상태 기록",
    "수정통지",
    "신청·승인 기록",
    "업무 기록번호",
    "운항 문의",
    "인계번호",
    "입금 기록",
    "접수 대조",
    "접수번호",
    "처리 기록",
    "확인번호",
)
ANNOTATED_COLUMNS = ("수정통지", "입금 기록")
LEGACY_COLUMNS = ("기록번호", "처리 기록")


def record_codes(question: str) -> tuple[str, ...]:
    """Keep complete record codes, with the BM25 spelling normalization."""
    codes = []
    for match in NORMALIZED_BIGRAM_ID_PATTERN.finditer(question):
        # Do not turn an embedded identifier or longer compound into a key.
        if match.start() and question[match.start() - 1] in "_-‐‑–—−－":
            continue
        if match.end() < len(question) and question[match.end()] in "_-‐‑–—−－":
            continue
        key = re.sub(
            r"[ \t]*-[ \t]*", "-", match.group().translate(CODE_DASHES)
        ).upper()
        if RECORD_CODE.fullmatch(key) and key not in codes:
            codes.append(key)
    return tuple(codes)


def source_record_key(column: str, value: str) -> str | None:
    """Extract a key only from an observed record-key cell shape."""
    if column not in RECORD_COLUMNS:
        return None
    if RECORD_CODE.fullmatch(value):
        return value
    if column in ANNOTATED_COLUMNS:
        key, separator, annotation = value.partition(" ")
        if (
            separator
            and annotation.strip()
            and RECORD_CODE.fullmatch(key)
            and record_codes(value) == (key,)
        ):
            return key
    return None


def lookup_record(
    store: StructuredStore, key: str, *, legacy_only: bool = False
) -> list[RecordRow]:
    """Return all exact matches, retaining ambiguity and original field values."""
    if RECORD_CODE.fullmatch(key) is None:
        raise ValueError("정규화된 완전 IS 기록 코드가 필요합니다.")
    columns = LEGACY_COLUMNS if legacy_only else RECORD_COLUMNS
    placeholders = ",".join("?" for _ in columns)
    rows = store._execute(
        "SELECT records.record_id, records.kind, records.source_id, "
        "records.line_start, records.line_end, pages.book_id, pages.page, "
        "documents.file_name, documents.file_sha256, fields.field_name, fields.raw_value "
        "FROM record_fields AS fields "
        "JOIN source_records AS records ON records.record_id = fields.record_id "
        "JOIN source_pages AS pages ON pages.source_id = records.source_id "
        "JOIN source_documents AS documents ON documents.book_id = pages.book_id "
        f"WHERE fields.field_name IN ({placeholders}) "
        "AND (fields.raw_value = ? OR (fields.field_name IN (?, ?) "
        "AND fields.raw_value GLOB ?)) "
        "AND records.kind = 'table_row' AND records.parse_status = 'parsed' "
        "AND pages.page_family = 'IS' "
        "ORDER BY pages.book_id, pages.page, records.line_start, records.record_id, "
        "fields.field_name",
        (*columns, key, *ANNOTATED_COLUMNS, key + " *"),
    ).fetchall()
    return [
        store._record_row(row, family="IS", case_id="", role=row["field_name"])
        for row in rows
        if source_record_key(row["field_name"], row["raw_value"]) == key
    ]


@dataclass(frozen=True)
class RecordResolution:
    status: str
    keys: tuple[str, ...]
    records: tuple[RecordRow, ...] = ()


def resolve_records(
    question: str, store: StructuredStore, *, legacy_only: bool = False
) -> RecordResolution:
    """Resolve up to two explicit records; abstain without changing search."""
    keys = record_codes(question)
    if not keys:
        return RecordResolution("no_supported_key", keys)
    if EXCLUSION.search(question):
        return RecordResolution("exclusion_unresolved", keys)
    if len(keys) > 2:
        return RecordResolution("too_many_records", keys)
    records = []
    for key in keys:
        matches = lookup_record(store, key, legacy_only=legacy_only)
        if not matches:
            return RecordResolution("not_found", keys)
        if len(matches) != 1:
            return RecordResolution("ambiguous", keys)
        records.append(matches[0])
    return RecordResolution("resolved", keys, tuple(records))
