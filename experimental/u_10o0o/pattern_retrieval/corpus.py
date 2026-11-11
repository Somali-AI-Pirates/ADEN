"""Build a fresh source-only SQLite corpus from the Advanced PDF originals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Callable

from app.ingestion import layout

from .common import CODE, codes_in, file_sha, normalize_code, write_json

SCHEMA_VERSION = 1
DATABASE_NAME = "corpus.sqlite3"
MANIFEST_NAME = "corpus-manifest.json"

_LABEL_BEFORE_CODE = re.compile(r"(?P<label>[^:：=|]{1,60})\s*[:：=]\s*$")
_CASE_FIELD_LABEL = re.compile(
    r"\b(?:case|incident|matter)\b|"
    r"(?:사건|케이스|사례)(?!유형|종류|분류)",
    re.IGNORECASE,
)
_CODE_FIELD_LABEL = re.compile(
    r"\b(?:code|id|no\.?|number|ref|reference|key)\b|"
    r"번호|코드|식별자|참조|키",
    re.IGNORECASE,
)
_NON_IDENTIFIER_LABEL = re.compile(
    r"\b(?:type|kind|class|category)\b|종류|유형",
    re.IGNORECASE,
)
_ALIAS_FIELD_LABEL = re.compile(
    r"\b(?:alias|synonym)\b|별칭|약칭|약어|축약|다른\s*이름",
    re.IGNORECASE,
)
_PARTY_CONTEXT_LABEL = re.compile(
    r"party|company|organization|업체|회사|당사자|화주|고객|선사|운송사|담당자|법인|성명|이름",
    re.IGNORECASE,
)
_PARTY_FIELD_LABEL = re.compile(
    r"\b(?:company|organization|org|party|customer|client|shipper|consignee|"
    r"carrier|vendor|supplier)\b|회사|업체|조직|법인|당사자|고객|화주|"
    r"송화인|수화인|운송사|선사|공급사",
    re.IGNORECASE,
)
_IDENTIFIER_SUFFIX_LABEL = re.compile(
    r"\b(?:code|id|no\.?|number|key|ref|reference)\b|"
    r"번호|코드|식별자|참조|키",
    re.IGNORECASE,
)
_SUPPORTED_CODE_ROLE = re.compile(
    r"\b(?:booking|reservation|contract|equipment|container|evidence|proof|"
    r"document|invoice|claim|billing|settlement)\b|"
    r"예약|부킹|계약|장비|컨테이너|증빙|근거|문서|자료|청구|송장|정산",
    re.IGNORECASE,
)
_BOOKING_LABEL = re.compile(r"\b(?:booking|reservation)\b|예약", re.IGNORECASE)
_COMMON_MARKER = "공통"
_RULE_CUES = ("규칙", "기준", "원칙", "예외", "적용", "조건", "절차", "조항")
_SCOPE_CUES = ("적용 범위", "적용범위", "범위", "대상", "scope")
_EFFECT_CUES = ("효력", "유효", "적용", "기준", "원칙", "효과")
_EXCEPTION_CUES = ("예외", "단서", "제외", "다만", "exception")
_RECORD_TITLE_ROLES = frozenset(
    (
        "현장 사건 수신대장",
        "수납 및 미결 인계",
        "비용 청구 명세",
        "비용 조건 약정",
        "계약 조건 대조 회신",
    )
)
_HG_RECORD_CASE_ID = re.compile(r"^HG20\d\d-")
_TYPED_DOCUMENT_CASE_ID = re.compile(r"^(?:HA28-|SW28-|DL28-)")
_RECORD_METADATA_TRIM = " \t:：=|/;,·"


class CorpusBuildError(RuntimeError):
    """Raised when a source PDF or corpus integrity check fails."""


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def _pdftotext_version() -> str:
    completed = subprocess.run(
        ["pdftotext", "-v"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    return next(
        (line.strip() for line in output.splitlines() if "version" in line.lower()),
        "unknown",
    )


def _extract_pages(path: Path) -> list[str]:
    completed = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise CorpusBuildError(
            f"pdftotext failed for {path.name} (exit {completed.returncode})"
        )
    try:
        extracted = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorpusBuildError(
            f"pdftotext returned non-UTF-8 text for {path.name}"
        ) from error
    pages = extracted.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def _all_identifiers(text: str) -> list[str]:
    return sorted(set(layout.identifiers(text)) | set(codes_in(text)))


def _label_type(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).casefold()
    return (
        re.sub(r"[^a-z0-9\uac00-\ud7a3]+", "_", normalized).strip("_")
        or "explicit_code"
    )


def _is_case_label(label: str) -> bool:
    return bool(
        _CASE_FIELD_LABEL.search(label) and not _NON_IDENTIFIER_LABEL.search(label)
    )


def _is_code_label(label: str) -> bool:
    return bool(
        _CODE_FIELD_LABEL.search(label)
        and not _NON_IDENTIFIER_LABEL.search(label)
        and not _is_case_label(label)
    )


def _is_role_only_label(label: str) -> bool:
    normalized = unicodedata.normalize("NFKC", label).casefold()
    matches = list(_SUPPORTED_CODE_ROLE.finditer(normalized))
    if not matches:
        return False
    remainder = []
    cursor = 0
    for match in matches:
        remainder.append(normalized[cursor : match.start()])
        cursor = match.end()
    remainder.append(normalized[cursor:])
    return not re.sub(r"[\s/|,;&+()\-\u00b7\u2022]+", "", "".join(remainder))


def _is_linkable_code_label(label: str) -> bool:
    if _is_case_label(label):
        return False
    if _NON_IDENTIFIER_LABEL.search(label) or _PARTY_FIELD_LABEL.search(label):
        return False
    normalized = unicodedata.normalize("NFKC", label).casefold()
    matches = [
        *_SUPPORTED_CODE_ROLE.finditer(normalized),
        *_IDENTIFIER_SUFFIX_LABEL.finditer(normalized),
    ]
    role_matches = list(_SUPPORTED_CODE_ROLE.finditer(normalized))
    if not role_matches:
        return False
    covered = [False] * len(normalized)
    for match in matches:
        for index in range(match.start(), match.end()):
            covered[index] = True
    remainder = "".join(
        char for index, char in enumerate(normalized) if not covered[index]
    )
    return not re.sub(r"[\s/|,;&+()\-·•_:：=]+", "", remainder)


def _paragraph_fields(text: str) -> list[tuple[str, str, str]]:
    fields = set()
    for line in text.splitlines():
        line_codes = set(codes_in(line))
        for match in CODE.finditer(line):
            code = normalize_code(match.group())
            if code not in line_codes:
                continue
            label_match = _LABEL_BEFORE_CODE.search(line[: match.start()])
            if not label_match:
                continue
            label = label_match.group("label").strip()
            booking_anchor = (
                _BOOKING_LABEL.search(label)
                and not _NON_IDENTIFIER_LABEL.search(label)
                and layout.CASE_ID.match(code)
            )
            if (
                _is_case_label(label)
                or _is_code_label(label)
                or _is_linkable_code_label(label)
                or booking_anchor
            ):
                fields.add((label, code, _label_type(label)))
    return sorted(fields)


def _explicit_alias_values(text: str) -> list[str]:
    values = []
    for line in text.splitlines():
        for separator in (":", "：", "="):
            if separator not in line:
                continue
            label, value = line.split(separator, 1)
            if _ALIAS_FIELD_LABEL.search(label.strip()) and value.strip():
                values.append(value.strip())
            break
    return values


def _case_id_for_record(fields: list[tuple[str, str]]) -> str | None:
    anchors = {code for label, code in fields if _is_case_label(label)}
    anchors.update(
        code
        for label, code in fields
        if _BOOKING_LABEL.search(label)
        and not _NON_IDENTIFIER_LABEL.search(label)
        and layout.CASE_ID.match(code)
    )
    return next(iter(anchors)) if len(anchors) == 1 else None


def _code_link_rows(
    fields: list[tuple[str, str, str]],
    case_id: str | None,
    source_id: str,
    line_start: int,
    line_end: int,
) -> list[tuple[str, str, str, str, int, int, str]]:
    if case_id is None:
        return []
    case_fields = [
        (label, code, code_type)
        for label, code, code_type in fields
        if _is_case_label(label)
    ]
    code_fields = [
        (label, code, code_type)
        for label, code, code_type in fields
        if _is_linkable_code_label(label)
    ]
    links = set()
    for _, code, code_type in case_fields:
        if code == case_id:
            links.add(
                (
                    code,
                    code_type,
                    case_id,
                    source_id,
                    line_start,
                    line_end,
                    "explicit_case_identifier_to_self",
                )
            )
    for label, code, code_type in fields:
        if (
            _BOOKING_LABEL.search(label)
            and not _NON_IDENTIFIER_LABEL.search(label)
            and layout.CASE_ID.match(code)
            and code == case_id
        ):
            links.add(
                (
                    code,
                    code_type,
                    case_id,
                    source_id,
                    line_start,
                    line_end,
                    "explicit_booking_key_to_self",
                )
            )
    for label, code, code_type in code_fields:
        relation = (
            "explicit_booking_key_to_self"
            if code == case_id and _BOOKING_LABEL.search(label)
            else "explicit_field_code_to_case_record"
        )
        links.add(
            (
                code,
                code_type,
                case_id,
                source_id,
                line_start,
                line_end,
                relation,
            )
        )
    return sorted(links)


def _raw_page_heading(
    page_text: str, body: list[dict], page: int, total: int
) -> str | None:
    body_lines = {
        number for block in body for number in range(block["start"], block["end"] + 1)
    }
    all_blocks = layout.blocks(page_text)
    footer_lines = set()
    if all_blocks:
        last = all_blocks[-1]
        footer = layout.FOOTER.fullmatch(last["lines"][-1])
        if footer and (int(footer[1]), int(footer[2])) == (page, total):
            footer_lines.update(range(last["start"], last["end"] + 1))
    title_lines = [
        line.rstrip("\r\n")
        for number, line in enumerate(page_text.splitlines(keepends=True), start=1)
        if number not in body_lines and number not in footer_lines and line.strip()
    ]
    title = "\n".join(title_lines)
    return title or None


def _body_blocks(
    page_text: str, book_id: str, page: int, total: int
) -> tuple[str | None, list[dict], bool]:
    try:
        _, body = layout.page_body(page_text, book_id, page, total)
        return _raw_page_heading(page_text, body, page, total), body, True
    except ValueError:
        parts = layout.blocks(page_text)
        if parts:
            footer = layout.FOOTER.fullmatch(parts[-1]["lines"][-1])
            if footer and (int(footer[1]), int(footer[2])) == (page, total):
                parts.pop()
        return None, parts, False


def _line_span(page_text: str, start: int, end: int) -> str:
    lines = page_text.splitlines(keepends=True)
    if start < 1 or end < start or end > len(lines):
        raise CorpusBuildError("Invalid extracted line range.")
    return "".join(lines[start - 1 : end])


def _table_section_title(
    page_text: str, body: list[dict], header_index: int
) -> str | None:
    if header_index == 0:
        return None
    previous = body[header_index - 1]
    if len(previous["lines"]) != 1:
        return None
    title = _line_span(page_text, previous["start"], previous["end"])
    clean = title.strip()
    if (
        not clean
        or len(clean) > 120
        or _all_identifiers(clean)
        or layout.header_cells(previous)
        or layout.looks_like_row(previous)
        or re.search(r"[.!?;:。！？]$", clean)
    ):
        return None
    return title.rstrip("\r\n")


def _context_title(page_heading: str | None, section_title: str | None) -> str:
    return "\n".join(value for value in (page_heading, section_title) if value)


def _formatted_unit(
    context_title: str,
    raw_text: str | None = None,
    fields: list[tuple[str, str]] | None = None,
) -> str:
    parts = [context_title] if context_title else []
    if fields:
        parts.extend(f"{label}: {value}" for label, value in fields)
    if raw_text is not None:
        parts.append(raw_text)
    return "\n".join(parts)


def _classify(
    raw_text: str, context_title: str, fields: list[tuple[str, str]]
) -> tuple[str, str | None]:
    case_id = _case_id_for_record(fields)
    if case_id:
        return "record", case_id
    context = context_title + "\n" + raw_text
    if _COMMON_MARKER in context and any(cue in context for cue in _RULE_CUES):
        return "common_rule", None
    return "raw", None


def _can_deduplicate_common(raw_text: str) -> bool:
    normalized = " ".join(raw_text.casefold().split())
    return (
        any(cue in normalized for cue in _SCOPE_CUES)
        and any(cue in normalized for cue in _EFFECT_CUES)
        and any(cue in normalized for cue in _EXCEPTION_CUES)
    )


def _table_anchor_fields(
    fields: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    result = []
    for label, value in fields:
        value_codes = codes_in(value)
        if _is_case_label(label):
            result.extend((label, code, _label_type(label)) for code in value_codes)
        elif _BOOKING_LABEL.search(label) and not _NON_IDENTIFIER_LABEL.search(label):
            canonical_codes = [
                code for code in value_codes if layout.CASE_ID.match(code)
            ]
            result.extend((label, code, _label_type(label)) for code in canonical_codes)
    return sorted(set(result))


def _table_link_fields(
    fields: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    return sorted(
        {
            (label, code, _label_type(label))
            for label, value in fields
            if _is_case_label(label) or _is_linkable_code_label(label)
            for code in codes_in(value)
        }
    )


def _structured_record_title(
    page_text: str, page_heading: str | None
) -> tuple[str, str, int, int, str] | None:
    if not page_heading:
        return None
    source_lines = page_text.splitlines()
    candidates = []

    for title_line in page_heading.splitlines():
        components = [part.strip() for part in title_line.split("/")]
        if len(components) != 4:
            continue
        case_codes = codes_in(components[0])
        secondary_codes = codes_in(components[2])
        role = " ".join(components[3].split())
        if (
            len(case_codes) != 1
            or components[0] != case_codes[0]
            or not _HG_RECORD_CASE_ID.match(case_codes[0])
            or len(secondary_codes) != 1
            or layout.CASE_ID.match(secondary_codes[0])
            or role not in _RECORD_TITLE_ROLES
        ):
            continue
        line_number = next(
            (
                number
                for number, source_line in enumerate(source_lines, start=1)
                if source_line.strip() == title_line.strip()
            ),
            None,
        )
        if line_number is not None:
            candidates.append(
                (case_codes[0], role, line_number, line_number, "slash_title")
            )

    blocks = layout.blocks(page_text)
    if len(blocks) >= 2:
        title_block, case_block = blocks[:2]
        typed_title = title_block["lines"][0].strip() if title_block["lines"] else ""
        case_line = case_block["lines"][0].strip() if case_block["lines"] else ""
        case_codes = codes_in(case_line)
        if (
            len(title_block["lines"]) == 1
            and typed_title
            and "|" not in typed_title
            and len(case_codes) == 1
            and case_line.startswith(case_codes[0])
            and _TYPED_DOCUMENT_CASE_ID.match(case_codes[0])
        ):
            candidates.append(
                (
                    case_codes[0],
                    typed_title,
                    case_block["start"],
                    case_block["start"],
                    "typed_document_heading",
                )
            )
    if len(candidates) != 1:
        return None
    return candidates[0]


def _record_role_label(prefix: str) -> str | None:
    segments = re.split(r"[:：=]", prefix)
    candidates = [
        (index, segment.strip(_RECORD_METADATA_TRIM))
        for index, segment in enumerate(segments)
        if _is_linkable_code_label(segment.strip(_RECORD_METADATA_TRIM))
    ]
    if len(candidates) != 1:
        return None
    index, label = candidates[0]
    preceding = " ".join(segments[:index])
    if _PARTY_FIELD_LABEL.search(preceding) or _NON_IDENTIFIER_LABEL.search(preceding):
        return None
    return label


def _record_metadata_fields(
    page_text: str, record_title: tuple[str, str, int, int, str]
) -> list[tuple[str, str, str]]:
    """Read explicit role-code fields after the title in its original source block."""
    title_line = record_title[2]
    title_block = next(
        (
            block
            for block in layout.blocks(page_text)
            if block["start"] <= title_line <= block["end"]
        ),
        None,
    )
    if title_block is None:
        return []

    first_metadata_index = title_line - title_block["start"] + 1
    metadata_lines = title_block["lines"][first_metadata_index:]
    results = set()
    pending_label = None

    for line in metadata_lines:
        matches = [
            match
            for match in CODE.finditer(line)
            if normalize_code(match.group()) in codes_in(line)
        ]
        if not matches:
            candidate = _record_role_label(line)
            if (
                candidate
                and not _NON_IDENTIFIER_LABEL.search(line)
                and not _PARTY_FIELD_LABEL.search(line)
            ):
                pending_label = candidate
            else:
                pending_label = None
            continue

        first = matches[0]
        prefix = line[: first.start()]
        line_label = _record_role_label(prefix)
        if line_label is not None:
            active_label = line_label
        elif not prefix.strip(_RECORD_METADATA_TRIM):
            active_label = pending_label
        else:
            active_label = None
        if not active_label or not _is_linkable_code_label(active_label):
            active_label = None

        for index, match in enumerate(matches):
            code = normalize_code(match.group())
            if code not in codes_in(line):
                continue
            label = active_label
            if index:
                between = line[matches[index - 1].end() : match.start()]
                if not re.fullmatch(r"[\s/|,;&+·\-]*", between):
                    label = None
            if label:
                results.add((label, code, _label_type(label)))
        pending_label = None

    return sorted(results)


def _body_anchor_codes(
    page_text: str,
    body: list[dict],
    record_title: tuple[str, str, int, int, str] | None = None,
) -> set[str]:
    anchors = set()
    if record_title is not None:
        anchors.update(
            code
            for label, code, _ in _record_metadata_fields(page_text, record_title)
            if _BOOKING_LABEL.search(label) and layout.CASE_ID.match(code)
        )
    index = 0
    while index < len(body):
        block = body[index]
        rows = []
        next_index = index + 1
        if layout.header_cells(block):
            while next_index < len(body) and layout.looks_like_row(body[next_index]):
                rows.append(body[next_index])
                next_index += 1
        parsed_rows = layout.parse_table(block, rows) if rows else None
        if parsed_rows is not None and len(parsed_rows) == len(rows):
            for row_fields in parsed_rows:
                anchor_fields = _table_anchor_fields(list(row_fields.items()))
                anchors.update(code for _, code, _ in anchor_fields)
            index = next_index
        else:
            raw_text = _line_span(page_text, block["start"], block["end"])
            fields = _paragraph_fields(raw_text)
            anchors.update(
                code
                for label, code, _ in fields
                if _is_case_label(label)
                or (
                    _BOOKING_LABEL.search(label)
                    and layout.CASE_ID.match(code)
                    and not _NON_IDENTIFIER_LABEL.search(label)
                )
            )
            index += 1
    return anchors


def _insert_record_page(
    connection: sqlite3.Connection,
    source_id: str,
    page_text: str,
    page_line_count: int,
    record_title: tuple[str, str, int, int, str],
    record_parts: list[str],
    common_units: dict[tuple[str, str], str],
    counts: Counter,
) -> None:
    case_id, role, title_line_start, title_line_end, grammar = record_title
    record_text = "\n\n".join(part for part in record_parts if part)
    if not record_text:
        raise CorpusBuildError("Structured record page contains no source text.")
    _insert_unit(
        connection,
        f"B:{source_id}:record",
        "record",
        record_text,
        f"record:{source_id}:{case_id}",
        source_id,
        1,
        page_line_count,
        case_id,
        role,
        _all_identifiers(page_text),
        common_units,
        counts,
        record_text,
    )
    _insert_links(
        connection,
        [
            (
                case_id,
                _label_type("structured record title case"),
                case_id,
                source_id,
                title_line_start,
                title_line_end,
                "explicit_structured_record_title_case_to_self",
            )
        ],
    )
    counts["record_pages"] += 1
    counts[f"record_scope_{grammar}"] += 1
    counts["record_blocks"] += 1


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE pages (
            source_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            page INTEGER NOT NULL CHECK (page > 0),
            text TEXT NOT NULL,
            heading TEXT,
            pdf_sha256 TEXT NOT NULL
        );

        CREATE TABLE units (
            unit_id TEXT PRIMARY KEY,
            arm TEXT NOT NULL CHECK (arm IN ('A', 'B')),
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            scope_key TEXT NOT NULL
        );

        CREATE TABLE occurrences (
            unit_id TEXT NOT NULL REFERENCES units(unit_id),
            source_id TEXT NOT NULL REFERENCES pages(source_id),
            line_start INTEGER NOT NULL CHECK (line_start > 0),
            line_end INTEGER NOT NULL CHECK (line_end >= line_start),
            case_id TEXT,
            role TEXT NOT NULL,
            identifiers_json TEXT NOT NULL,
            UNIQUE (unit_id, source_id, line_start, line_end, role)
        );

        CREATE TABLE code_links (
            code_norm TEXT NOT NULL,
            code_type TEXT NOT NULL,
            case_id TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES pages(source_id),
            line_start INTEGER NOT NULL CHECK (line_start > 0),
            line_end INTEGER NOT NULL CHECK (line_end >= line_start),
            relation TEXT NOT NULL,
            UNIQUE (
                code_norm, code_type, case_id, source_id, line_start, line_end, relation
            )
        );
        """
    )


def _assert_integrity(connection: sqlite3.Connection) -> dict[str, int | str]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise CorpusBuildError("SQLite integrity_check failed.")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise CorpusBuildError("SQLite foreign_key_check failed.")

    missing_occurrences = connection.execute(
        """
        SELECT count(*) FROM units AS u
        LEFT JOIN occurrences AS o ON o.unit_id = u.unit_id
        WHERE o.unit_id IS NULL
        """
    ).fetchone()[0]
    if missing_occurrences:
        raise CorpusBuildError("Corpus contains units without source occurrences.")

    missing_pages = connection.execute(
        """
        SELECT count(*) FROM pages AS p
        LEFT JOIN units AS u ON u.unit_id = 'A:' || p.source_id AND u.arm = 'A'
        LEFT JOIN occurrences AS o ON o.unit_id = u.unit_id AND o.source_id = p.source_id
        WHERE u.unit_id IS NULL OR o.unit_id IS NULL
        """
    ).fetchone()[0]
    if missing_pages:
        raise CorpusBuildError("Corpus contains pages without an A unit.")

    unverified_links = connection.execute(
        """
        SELECT count(*) FROM code_links AS l
        LEFT JOIN occurrences AS o
          ON o.source_id = l.source_id
         AND o.case_id = l.case_id
         AND o.line_start <= l.line_start
         AND o.line_end >= l.line_end
         AND EXISTS (
             SELECT 1 FROM units AS linked_unit
             WHERE linked_unit.unit_id = o.unit_id
               AND linked_unit.arm = 'B'
               AND linked_unit.kind = 'record'
         )
        WHERE o.unit_id IS NULL
        """
    ).fetchone()[0]
    if unverified_links:
        raise CorpusBuildError("Corpus contains an unverified code link.")

    return {
        "sqlite": integrity,
        "pages": connection.execute("SELECT count(*) FROM pages").fetchone()[0],
        "units": connection.execute("SELECT count(*) FROM units").fetchone()[0],
        "occurrences": connection.execute(
            "SELECT count(*) FROM occurrences"
        ).fetchone()[0],
        "code_links": connection.execute("SELECT count(*) FROM code_links").fetchone()[
            0
        ],
        "unverified_links": unverified_links,
    }


def _insert_unit(
    connection: sqlite3.Connection,
    unit_id: str,
    unit_kind: str,
    unit_text: str,
    scope_key: str,
    source_id: str,
    line_start: int,
    line_end: int,
    case_id: str | None,
    role: str,
    identifiers: list[str],
    common_units: dict[tuple[str, str], str],
    counts: Counter,
    raw_common_text: str,
) -> str:
    deduplicate = unit_kind == "common_rule" and _can_deduplicate_common(
        raw_common_text
    )
    common_key = (role, unit_text)
    if deduplicate and common_key in common_units:
        unit_id = common_units[common_key]
        counts["common_deduplicated_occurrences"] += 1
    else:
        if deduplicate:
            digest = hashlib.sha256(
                (role + "\0" + unit_text).encode("utf-8")
            ).hexdigest()
            unit_id = f"B:common:{digest}"
            scope_key = f"common:{digest}"
            common_units[common_key] = unit_id
        connection.execute(
            """
            INSERT INTO units (unit_id, arm, kind, text, scope_key)
            VALUES (?, 'B', ?, ?, ?)
            """,
            (unit_id, unit_kind, unit_text, scope_key),
        )
        counts["unique_b_units"] += 1
    connection.execute(
        """
        INSERT INTO occurrences
            (unit_id, source_id, line_start, line_end, case_id, role,
             identifiers_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit_id,
            source_id,
            line_start,
            line_end,
            case_id,
            role,
            json.dumps(identifiers, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return unit_id


def _insert_links(
    connection: sqlite3.Connection,
    rows: list[tuple[str, str, str, str, int, int, str]],
) -> None:
    if rows:
        connection.executemany(
            """
            INSERT OR IGNORE INTO code_links
                (code_norm, code_type, case_id, source_id, line_start, line_end, relation)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _add_b_block(
    connection: sqlite3.Connection,
    page_text: str,
    source_id: str,
    block: dict,
    page_heading: str | None,
    common_units: dict[tuple[str, str], str],
    counts: Counter,
    record_case_id: str | None = None,
    record_parts: list[str] | None = None,
    record_metadata_fields: list[tuple[str, str, str]] | None = None,
) -> str | None:
    raw_text = _line_span(page_text, block["start"], block["end"])
    alias_values = _explicit_alias_values(raw_text)
    counts["source_alias_paragraphs"] += bool(alias_values)
    counts["source_alias_cells"] += len(alias_values)
    counts["source_alias_values_complete_code"] += sum(
        bool(codes_in(value)) for value in alias_values
    )
    context = _context_title(page_heading, None)
    fields = _paragraph_fields(raw_text)
    if record_metadata_fields:
        fields = sorted(set(fields + record_metadata_fields))
        counts["record_metadata_typed_fields"] += len(record_metadata_fields)
    field_pairs = [(label, code) for label, code, _ in fields]
    if record_parts is not None:
        record_parts.append(raw_text)
        _insert_links(
            connection,
            _code_link_rows(
                fields,
                record_case_id,
                source_id,
                block["start"],
                block["end"],
            ),
        )
        counts["body_blocks"] += 1
        counts["record_page_source_blocks"] += 1
        return record_case_id

    kind, case_id = _classify(raw_text, context, field_pairs)
    unit_text = _formatted_unit(context, raw_text)
    unit_id = f"B:{source_id}:l{block['start']:04d}-{block['end']:04d}"
    _insert_unit(
        connection,
        unit_id,
        kind,
        unit_text,
        f"source:{source_id}:lines:{block['start']}-{block['end']}",
        source_id,
        block["start"],
        block["end"],
        case_id,
        context,
        _all_identifiers(raw_text),
        common_units,
        counts,
        raw_text,
    )
    _insert_links(
        connection,
        _code_link_rows(fields, case_id, source_id, block["start"], block["end"]),
    )
    counts["body_blocks"] += 1
    counts[f"{kind}_blocks"] += 1
    return case_id


def _add_table(
    connection: sqlite3.Connection,
    page_text: str,
    source_id: str,
    body: list[dict],
    header_index: int,
    rows: list[dict],
    parsed_rows: list[dict[str, str]],
    page_heading: str | None,
    common_units: dict[tuple[str, str], str],
    counts: Counter,
    record_case_id: str | None = None,
    record_parts: list[str] | None = None,
) -> set[str]:
    header = body[header_index]
    section_title = _table_section_title(page_text, body, header_index)
    context = _context_title(page_heading, section_title)
    header_labels = [label for _, _, label in (layout.header_cells(header) or [])]
    alias_labels = [
        label for label in header_labels if _ALIAS_FIELD_LABEL.search(label)
    ]
    if alias_labels:
        counts["source_alias_tables"] += 1
        if any(
            _PARTY_CONTEXT_LABEL.search(label)
            for label in [*header_labels, section_title or ""]
        ):
            counts["source_alias_party_tables"] += 1
    header_raw = _line_span(page_text, header["start"], header["end"])
    if record_parts is not None:
        record_parts.append(header_raw)
    else:
        header_text = _formatted_unit(context, header_raw)
        header_unit_id = f"B:{source_id}:l{header['start']:04d}-{header['end']:04d}"
        _insert_unit(
            connection,
            header_unit_id,
            "raw",
            header_text,
            f"source:{source_id}:lines:{header['start']}-{header['end']}",
            source_id,
            header["start"],
            header["end"],
            None,
            context,
            _all_identifiers(header_raw),
            common_units,
            counts,
            header_raw,
        )
    counts["table_headers"] += 1
    counts["body_blocks"] += 1
    if record_parts is not None:
        counts["record_page_source_blocks"] += 1
    source_lines = page_text.splitlines(keepends=True)
    case_ids = set()
    for row, row_fields in zip(rows, parsed_rows, strict=True):
        row_start = row["start"]
        row_end = row["end"]
        row_raw = _line_span(page_text, row_start, row_end)
        fields = list(row_fields.items())
        anchor_fields = _table_anchor_fields(fields)
        field_pairs = [(label, code) for label, code, _ in anchor_fields]
        row_case_id = _case_id_for_record(field_pairs)
        link_fields = _table_link_fields(fields)
        field_codes = sorted(set(anchor_fields + link_fields))
        if row_case_id:
            case_ids.add(row_case_id)
        kind, row_case_id = _classify(row_raw, context, field_pairs)
        row_text = _formatted_unit(context, fields=fields)
        if record_parts is not None:
            record_parts.append(_formatted_unit("", fields=fields))
            link_case_id = record_case_id
        else:
            row_unit_id = f"B:{source_id}:l{row_start:04d}-{row_end:04d}"
            _insert_unit(
                connection,
                row_unit_id,
                kind,
                row_text,
                f"source:{source_id}:lines:{row_start}-{row_end}",
                source_id,
                row_start,
                row_end,
                row_case_id,
                context,
                _all_identifiers(
                    row_raw + "\n" + "\n".join(value for _, value in fields)
                ),
                common_units,
                counts,
                row_raw,
            )
            link_case_id = row_case_id
        _insert_links(
            connection,
            _code_link_rows(field_codes, link_case_id, source_id, row_start, row_end),
        )
        for label, value in row_fields.items():
            if _ALIAS_FIELD_LABEL.search(label):
                counts["source_alias_cells"] += 1
                counts["source_alias_values_complete_code"] += bool(codes_in(value))
            if f"{label}: {value}" not in row_text:
                raise CorpusBuildError("A parsed table cell value was changed.")
            counts["table_cells"] += 1
        if "".join(source_lines[row_start - 1 : row_end]) != row_raw:
            raise CorpusBuildError("A table row span changed from its source page.")
        counts["table_cell_values_preserved"] += len(row_fields)
        counts["raw_table_rows_preserved"] += 1
        counts["table_rows"] += 1
        counts["body_blocks"] += 1
        if record_parts is not None:
            counts["record_page_source_blocks"] += 1
        else:
            counts[f"{kind}_blocks"] += 1
    return case_ids


def build_corpus(
    source_dir: str | Path,
    output_dir: str | Path,
    log: Callable[[str], None] | None,
) -> dict:
    """Extract source PDFs with pdftotext and write a new corpus and manifest."""
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    pdfs = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not pdfs:
        raise ValueError(f"No PDF sources found in {source_dir}.")
    output_dir.mkdir(parents=True, exist_ok=True)

    database_path = output_dir / DATABASE_NAME
    manifest_path = output_dir / MANIFEST_NAME
    if database_path.exists() or manifest_path.exists():
        raise FileExistsError(
            "Corpus output already exists; use a new output directory."
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".corpus-", suffix=".sqlite3", dir=output_dir
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)

    counts: Counter = Counter()
    documents = []
    common_units: dict[tuple[str, str], str] = {}
    connection = None
    try:
        connection = sqlite3.connect(temporary_path)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        _create_schema(connection)
        connection.execute("BEGIN")

        for pdf_index, pdf_path in enumerate(pdfs, start=1):
            _emit(log, f"Extracting source {pdf_index}/{len(pdfs)}: {pdf_path.name}")
            pdf_sha256 = file_sha(pdf_path)
            page_texts = _extract_pages(pdf_path)
            document_counts: Counter = Counter()
            for page_number, page_text in enumerate(page_texts, start=1):
                source_id = f"{pdf_path.stem}:p{page_number:04d}"
                page_line_count = max(1, len(page_text.splitlines()))
                page_heading, body, parsed_layout = _body_blocks(
                    page_text, pdf_path.stem, page_number, len(page_texts)
                )
                if not parsed_layout:
                    document_counts["raw_layout_pages"] += 1

                connection.execute(
                    """
                    INSERT INTO pages
                        (source_id, book_id, file_name, page, text, heading, pdf_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        pdf_path.stem,
                        pdf_path.name,
                        page_number,
                        page_text,
                        page_heading,
                        pdf_sha256,
                    ),
                )
                page_unit_id = f"A:{source_id}"
                connection.execute(
                    """
                    INSERT INTO units (unit_id, arm, kind, text, scope_key)
                    VALUES (?, 'A', 'page', ?, ?)
                    """,
                    (page_unit_id, page_text, f"page:{source_id}"),
                )
                connection.execute(
                    """
                    INSERT INTO occurrences
                        (unit_id, source_id, line_start, line_end, case_id, role,
                         identifiers_json)
                    VALUES (?, ?, 1, ?, ?, 'page', ?)
                    """,
                    (
                        page_unit_id,
                        source_id,
                        page_line_count,
                        None,
                        json.dumps(
                            _all_identifiers(page_text),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
                document_counts["pages"] += 1
                record_title = _structured_record_title(page_text, page_heading)
                if record_title is not None:
                    record_case_id = record_title[0]
                    page_case_codes = {
                        code
                        for code in codes_in(page_text)
                        if layout.CASE_ID.match(code)
                    }
                    body_anchors = _body_anchor_codes(page_text, body, record_title)
                    if page_case_codes != {record_case_id} or any(
                        code != record_case_id for code in body_anchors
                    ):
                        document_counts["record_scope_conflicts"] += 1
                        record_title = None
                record_case_id = record_title[0] if record_title else None
                record_parts = [page_heading] if record_title and page_heading else None
                metadata_fields = (
                    _record_metadata_fields(page_text, record_title)
                    if record_title
                    else []
                )
                line_coverage: Counter = Counter()
                page_case_ids = {record_case_id} if record_case_id else set()
                source_lines = page_text.splitlines()
                index = 0
                while index < len(body):
                    header = body[index]
                    rows = []
                    next_index = index + 1
                    if layout.header_cells(header):
                        while next_index < len(body) and layout.looks_like_row(
                            body[next_index]
                        ):
                            rows.append(body[next_index])
                            next_index += 1
                    parsed_rows = layout.parse_table(header, rows) if rows else None
                    if parsed_rows is not None and len(parsed_rows) == len(rows):
                        table_case_ids = _add_table(
                            connection,
                            page_text,
                            source_id,
                            body,
                            index,
                            rows,
                            parsed_rows,
                            page_heading,
                            common_units,
                            document_counts,
                            record_case_id,
                            record_parts,
                        )
                        document_counts["tables"] += 1
                        document_counts["table_body_blocks"] += 1 + len(rows)
                        document_counts["table_linked_case_rows"] += len(table_case_ids)
                        page_case_ids.update(table_case_ids)
                        for covered_block in [header, *rows]:
                            for line in range(
                                covered_block["start"], covered_block["end"] + 1
                            ):
                                line_coverage[line] += 1
                        index = next_index
                    else:
                        block = body[index]
                        case_id = _add_b_block(
                            connection,
                            page_text,
                            source_id,
                            block,
                            page_heading,
                            common_units,
                            document_counts,
                            record_case_id,
                            record_parts,
                            metadata_fields
                            if record_title and block["start"] == record_title[2] + 1
                            else None,
                        )
                        if case_id:
                            if record_title is None:
                                document_counts["record_case_blocks"] += 1
                            page_case_ids.add(case_id)
                        for line in range(block["start"], block["end"] + 1):
                            line_coverage[line] += 1
                        index += 1

                body_lines = {
                    line
                    for block in body
                    for line in range(block["start"], block["end"] + 1)
                    if source_lines[line - 1].strip()
                }
                if any(line_coverage[line] != 1 for line in body_lines):
                    raise CorpusBuildError(
                        f"Body line coverage failed for {source_id}."
                    )
                if record_title is not None:
                    _insert_record_page(
                        connection,
                        source_id,
                        page_text,
                        page_line_count,
                        record_title,
                        record_parts or [],
                        common_units,
                        document_counts,
                    )
                page_case = (
                    next(iter(page_case_ids)) if len(page_case_ids) == 1 else None
                )
                connection.execute(
                    "UPDATE occurrences SET case_id = ? WHERE unit_id = ?",
                    (page_case, page_unit_id),
                )
                document_counts["case_hint_pages"] += page_case is not None
                document_counts["body_lines"] += len(body_lines)
                document_counts["body_block_coverage_checks"] += len(body)
                document_counts["body_block_coverage_ok"] += sum(
                    all(
                        line_coverage[line] == 1
                        for line in range(block["start"], block["end"] + 1)
                    )
                    for block in body
                )

            if file_sha(pdf_path) != pdf_sha256:
                raise CorpusBuildError(
                    f"Source PDF changed during extraction: {pdf_path.name}"
                )
            counts.update(document_counts)
            documents.append(
                {
                    "book_id": pdf_path.stem,
                    "file_name": pdf_path.name,
                    "pdf_sha256": pdf_sha256,
                    **dict(document_counts),
                }
            )

        connection.execute(
            "CREATE INDEX occurrences_source_lines "
            "ON occurrences(source_id, line_start, line_end)"
        )
        connection.execute(
            "CREATE INDEX occurrences_case_source ON occurrences(case_id, source_id)"
        )
        connection.execute(
            "CREATE INDEX code_links_code_case ON code_links(code_norm, case_id)"
        )
        connection.execute(
            "CREATE INDEX code_links_source_lines ON code_links(source_id, line_start)"
        )
        integrity = _assert_integrity(connection)
        connection.commit()
        connection.close()
        connection = None
        temporary_path.replace(database_path)

        with sqlite3.connect(database_path) as check_connection:
            common_occurrences = Counter(
                unit_id
                for (unit_id,) in check_connection.execute(
                    """
                    SELECT o.unit_id FROM occurrences AS o
                    JOIN units AS u ON u.unit_id = o.unit_id
                    WHERE u.kind = 'common_rule'
                    """
                )
            )
        manifest_counts = {
            **dict(counts),
            "pages": integrity["pages"],
            "units": integrity["units"],
            "occurrences": integrity["occurrences"],
            "code_links": integrity["code_links"],
            "common_deduplicated_unit_groups": sum(
                count > 1 for count in common_occurrences.values()
            ),
            "common_deduplicated_occurrences": sum(
                count - 1 for count in common_occurrences.values() if count > 1
            ),
        }
        manifest = {
            "status": "complete",
            "schema_version": SCHEMA_VERSION,
            "database": DATABASE_NAME,
            "corpus_sha256": file_sha(database_path),
            "extraction": {"command": ["pdftotext", "-layout"], "encoding": "UTF-8"},
            "runtime": {
                "parser_sha256": file_sha(Path(__file__)),
                "layout_sha256": file_sha(Path(layout.__file__)),
                "pdftotext_version": _pdftotext_version(),
            },
            "code_link_policy": {
                "case_anchor": {
                    "explicit_case_label_regex": _CASE_FIELD_LABEL.pattern,
                    "booking_label_regex": _BOOKING_LABEL.pattern,
                    "canonical_case_id_regex": layout.CASE_ID.pattern,
                    "rule": (
                        "One distinct complete code in an explicit case field, "
                        "or a canonical case ID in an explicit booking/reservation "
                        "field, within one B record or table row."
                    ),
                    "structured_record_titles": {
                        "slash_title_component_count": 4,
                        "slash_case_id_family_regex": _HG_RECORD_CASE_ID.pattern,
                        "slash_case_code_position": 1,
                        "slash_secondary_code_position": 3,
                        "slash_secondary_code_is_case_key": False,
                        "slash_terminal_role_match": "exact allowlist of five source roles",
                        "typed_document_case_id_family_regex": (
                            _TYPED_DOCUMENT_CASE_ID.pattern
                        ),
                        "typed_document_heading_case_position": (
                            "first code token on the first line of the second source block"
                        ),
                        "pipe_title_supported": False,
                        "typed_document_heading_role": (
                            "full first source block preserved verbatim in the local occurrence role"
                        ),
                        "requires_one_distinct_page_case_code": True,
                        "requires_no_competing_body_anchor": True,
                        "title_metadata_fields": {
                            "scope": "same original layout block after the title line only",
                            "supported_role_regex": _SUPPORTED_CODE_ROLE.pattern,
                            "allow_colon_whitespace_or_line_break": True,
                            "generic_party_and_type_labels_excluded": True,
                            "unlabelled_title_secondary_code_linked": False,
                        },
                    },
                    "page_cooccurrence_is_anchor": False,
                },
                "linked_identifier_labels": {
                    "supported_role_regex": _SUPPORTED_CODE_ROLE.pattern,
                    "identifier_suffix_regex": _IDENTIFIER_SUFFIX_LABEL.pattern,
                    "pure_role_label_allowed": True,
                    "substring_matches_are_sufficient": False,
                    "pure_role_label_rule": (
                        "Allow only a normalized label composed entirely of supported roles "
                        "and separators, or a supported role plus an explicit identifier suffix."
                    ),
                    "requires_same_record_anchor": True,
                    "supported_roles": [
                        "booking/reservation",
                        "contract",
                        "unique equipment/container",
                        "evidence/document/proof",
                        "claim/invoice/billing/settlement",
                    ],
                },
                "excluded_identifier_labels": {
                    "party_organization_regex": _PARTY_FIELD_LABEL.pattern,
                    "type_category_regex": _NON_IDENTIFIER_LABEL.pattern,
                    "generic_number_or_reference_without_supported_role": True,
                    "party_aliases_registered_as_case_codes": False,
                },
                "relationship_scope": (
                    "Explicit code fields inside a title-scoped B record, one B paragraph, "
                    "or one parsed table row; page co-occurrence alone does not create a link."
                ),
            },
            "source_count": len(pdfs),
            "documents": documents,
            "counts": manifest_counts,
            "alias_handling": {
                "status": (
                    "preserved_unlinked_party_aliases"
                    if counts["source_alias_tables"]
                    and counts["source_alias_party_tables"]
                    == counts["source_alias_tables"]
                    else "unregistered"
                ),
                "source_alias_tables": counts["source_alias_tables"],
                "source_alias_cells": counts["source_alias_cells"],
                "source_alias_paragraphs": counts["source_alias_paragraphs"],
                "party_context_tables": counts["source_alias_party_tables"],
                "alias_values_matching_complete_code": counts[
                    "source_alias_values_complete_code"
                ],
                "registered_case_alias_links": 0,
                "reason": (
                    "Source alias fields appear in party or organization tables. "
                    "Their values are preserved as source field text and are not "
                    "registered as case codes."
                ),
            },
            "integrity": {
                "sqlite": integrity["sqlite"],
                "foreign_keys": "ok",
                "page_units": True,
                "body_block_coverage": (
                    counts["body_block_coverage_checks"]
                    == counts["body_block_coverage_ok"]
                ),
                "source_pdf_hashes": "verified_before_after",
                "table_cell_values": (
                    counts["table_cell_values_preserved"] == counts["table_cells"]
                ),
                "raw_table_rows": (
                    counts["raw_table_rows_preserved"] == counts["table_rows"]
                ),
                "code_links": integrity["unverified_links"] == 0,
            },
        }
        write_json(manifest_path, manifest)
        _emit(
            log,
            "Corpus complete: "
            f"pdfs={len(pdfs)} pages={integrity['pages']} "
            f"units={integrity['units']} code_links={integrity['code_links']} "
            f"dedup_common={manifest_counts['common_deduplicated_occurrences']}",
        )
        return manifest
    except Exception:
        if connection is not None:
            connection.rollback()
            connection.close()
        temporary_path.unlink(missing_ok=True)
        raise
