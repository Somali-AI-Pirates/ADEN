"""Build fresh original-PDF page and record units with explicit evidence-linked relations.

Scope: A preserves every extracted page as-is. B emits one HG role record per page,
IS table rows plus separate narrative units when boundaries are clear, and raw-page
fallbacks when they are not. C only links complete codes that a page explicitly
labels or places in a structured record field; it does not infer approvals, identity,
or applicability from code proximity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

NORMALIZED_ALNUM = re.compile(r"[^\w]", re.UNICODE)
HG_HEADER_RE = re.compile(
    r"^\s*(HG\d{4}-\d{5})\s*/\s*([^/]+?)\s*/\s*([A-Z0-9-]+)\s*/\s*(.+?)\s*$"
)
HG_CODE_RE = re.compile(r"(?<![A-Z0-9])(HG\d{4}-\d{5})(?![A-Z0-9])")
IS_PAGE_RE = re.compile(r"^IS\d{2}\s*\|")
IS_ROW_RE = re.compile(r"^\s*(IS\d{2}-\d{3}-\d{2})\b")
CORE_HEADER_RE = re.compile(r"^\s*([A-Z]{2}\d{2}-[A-Z]\d{3,})\s+(.+?)\s*$")
CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,5})(?![A-Z0-9])")
GR_CODE_RE = re.compile(r"(?<![A-Z0-9])(GR\d{2}(?:-[A-Z0-9]+){1,3})(?![A-Z0-9])")
PAGE_FOOTER_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


def normalize_code(code: str) -> str:
    """Remove punctuation and whitespace while preserving alphanumeric case."""
    return "".join(ch for ch in code if ch.isalnum())


def event_id_for(code: str) -> str:
    normalized = normalize_code(code)
    if not normalized:
        raise ValueError("event codes must contain at least one alphanumeric character")
    return f"event:{normalized}"


def _page_lines(page: dict[str, Any]) -> list[str]:
    lines = page.get("lines")
    if isinstance(lines, list):
        return [str(line) for line in lines]
    text = page.get("text")
    if isinstance(text, str):
        return text.splitlines()
    raise ValueError(f"page {page.get('source_id')} has no text or lines")


def _first_nonblank(lines: list[str]) -> str:
    return next((line.strip() for line in lines if line.strip()), "")


def _compress_indices(source_id: str, indices: Iterable[int]) -> list[dict[str, Any]]:
    """Convert sorted zero-based line indices into 1-based inclusive spans."""
    ordered = sorted(set(i for i in indices if i >= 0))
    if not ordered:
        return []
    spans: list[dict[str, Any]] = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index == previous + 1:
            previous = index
            continue
        spans.append({"source_id": source_id, "start": start + 1, "end": previous + 1})
        start = previous = index
    spans.append({"source_id": source_id, "start": start + 1, "end": previous + 1})
    return spans


def _make_unit(
    *,
    unit_id: str,
    corpus: str,
    lines: list[str],
    source_id: str,
    indices: Iterable[int],
    kind: str,
    title: str,
    text_override: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(set(i for i in indices if 0 <= i < len(lines)))
    text = (
        text_override
        if text_override is not None
        else "\n".join(lines[i] for i in ordered)
    )
    return {
        "unit_id": unit_id,
        "corpus": corpus,
        "text": text,
        "sources": [source_id],
        "kind": kind,
        "title": title,
        "event_ids": [],
        "line_spans": _compress_indices(source_id, ordered),
    }


def _looks_like_columns(line: str) -> bool:
    if not line.strip() or IS_ROW_RE.match(line):
        return False
    cells = [part for part in COLUMN_SPLIT_RE.split(line.strip()) if part]
    return len(cells) >= 3 and any(re.search(r"[가-힣A-Za-z]", cell) for cell in cells)


def _find_column_header(
    lines: list[str], before: int, max_lookback: int = 28
) -> int | None:
    lower = max(-1, before - max_lookback - 1)
    for index in range(before - 1, lower, -1):
        if _looks_like_columns(lines[index]):
            return index
    return None


def _generic_table_starts(lines: list[str]) -> list[tuple[int, str]]:
    """Find high-confidence ID-free row tables by repeated aligned row IDs."""
    candidates: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, line in enumerate(lines):
        match = CODE_RE.search(line)
        if not match or line[: match.start()].strip():
            continue
        code = match.group(1)
        # A code at the start of prose is not a table row. Require the original
        # layout to show multiple columns and a real gap after the first cell.
        if not re.match(r"\s{2,}\S", line[match.end() :]):
            continue
        if len(COLUMN_SPLIT_RE.split(line.strip())) < 3:
            continue
        if code.startswith(("IS", "HG")) or re.match(r"P\d{2}-", code):
            continue
        # A page/header code is not a row unless it repeats as a table key.
        prefix = code.split("-", 1)[0]
        candidates[prefix].append((index, code))

    ranked: list[tuple[int, int, str, list[tuple[int, str]]]] = []
    for prefix, starts in candidates.items():
        if len(starts) < 3:
            continue
        header_index = _find_column_header(lines, starts[0][0])
        if header_index is None:
            continue
        header_text = lines[header_index]
        # GR IDs are reservation keys only when the table header names that field.
        if prefix.startswith("GR") and not re.search(r"예약|대상", header_text):
            continue
        ranked.append((len(starts), header_index, prefix, starts))
    if not ranked:
        return []

    # Multiple repeated code families can be continuation cells. Keep a unique
    # primary family; if tied, prefer the one explicitly under a reservation field.
    best_count = max(item[0] for item in ranked)
    best = [item for item in ranked if item[0] == best_count]
    if len(best) > 1:
        reservation = [
            item
            for item in best
            if item[2].startswith("GR") and re.search(r"예약|대상", lines[item[1]])
        ]
        if len(reservation) == 1:
            best = reservation
        else:
            return []
    return sorted(best[0][3])


def _table_context(
    lines: list[str], first_row_index: int, header_index: int | None
) -> list[int]:
    indices: set[int] = set()
    if header_index is not None:
        indices.add(header_index)
        # Keep an immediately preceding table caption when it is visibly a heading.
        previous = next(
            (i for i in range(header_index - 1, -1, -1) if lines[i].strip()), None
        )
        if previous is not None:
            caption = lines[previous].strip()
            if (
                "—" in caption
                or "–" in caption
                or "원문" in caption
                or "묶음" in caption
            ):
                indices.add(previous)
    else:
        previous = next(
            (i for i in range(first_row_index - 1, -1, -1) if lines[i].strip()), None
        )
        if previous is not None:
            indices.add(previous)
    return sorted(indices)


def _page_context(
    lines: list[str], first_row_index: int | None
) -> tuple[list[int], str, str, str]:
    nonblank = [i for i, line in enumerate(lines) if line.strip()]
    before = [i for i in nonblank if first_row_index is None or i < first_row_index]
    # In the IS layout, the first four nonblank lines are the document, topic,
    # section, and same-page record-group title.
    top = before[:4] if before else nonblank[:4]
    title = (
        lines[top[-1]].strip()
        if len(top) >= 4
        else (lines[top[-1]].strip() if top else _first_nonblank(lines))
    )
    section = lines[top[-2]].strip() if len(top) >= 2 else title
    return top, title, section, (_first_nonblank(lines))


def _row_end(lines: list[str], start: int, starts: set[int]) -> list[int]:
    indices = [start]
    for index in range(start + 1, len(lines)):
        if index in starts or not lines[index].strip():
            break
        indices.append(index)
    return indices


def _paragraph_groups(lines: list[str], blocked: set[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        if index in blocked or not line.strip():
            if current:
                groups.append(current)
                current = []
            continue
        current.append(index)
    if current:
        groups.append(current)
    return groups


def _parse_is_page(
    lines: list[str], source_id: str, text_override: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    explicit_starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = IS_ROW_RE.match(line)
        if match:
            explicit_starts.append((index, match.group(1)))

    starts = explicit_starts or _generic_table_starts(lines)
    if not starts:
        all_indices = range(len(lines))
        return (
            [
                _make_unit(
                    unit_id=f"B:{source_id}:raw",
                    corpus="B",
                    lines=lines,
                    source_id=source_id,
                    indices=all_indices,
                    kind="raw_page_fallback",
                    title=_first_nonblank(lines),
                    text_override=text_override,
                )
            ],
            [],
            False,
        )

    starts_set = {index for index, _ in starts}
    first_row = starts[0][0]
    top_context, group_title, section_title, _ = _page_context(lines, first_row)
    header_index = _find_column_header(lines, first_row)
    table_context = _table_context(lines, first_row, header_index)
    footer_indices = [i for i, line in enumerate(lines) if PAGE_FOOTER_RE.match(line)]
    common_context = set(top_context) | set(table_context) | set(footer_indices)

    row_units: list[dict[str, Any]] = []
    row_info: list[dict[str, Any]] = []
    blocked: set[int] = set(common_context)
    for ordinal, (start, code) in enumerate(starts, 1):
        row_indices = _row_end(lines, start, starts_set)
        blocked.update(row_indices)
        selected = common_context | set(row_indices)
        unit_id = f"B:{source_id}:row:{normalize_code(code)}:{ordinal:03d}"
        unit = _make_unit(
            unit_id=unit_id,
            corpus="B",
            lines=lines,
            source_id=source_id,
            indices=selected,
            kind=lines[table_context[0]].strip() if table_context else "is_table_row",
            title=group_title or section_title,
        )
        row_units.append(unit)
        row_info.append(
            {
                "unit_id": unit_id,
                "row_code": code,
                "row_indices": row_indices,
                "header_indices": table_context,
                "row_start": start,
                "explicit_is_id": bool(IS_ROW_RE.match(lines[start])),
            }
        )

    # Every non-table paragraph stays as a separate source-backed unit. Page,
    # section, and footer context are repeated so each narrative remains legible.
    narrative_units: list[dict[str, Any]] = []
    narrative_ordinal = 0
    for group in _paragraph_groups(lines, blocked):
        narrative_ordinal += 1
        selected = set(top_context) | set(footer_indices) | set(group)
        unit_id = f"B:{source_id}:narrative:{narrative_ordinal:03d}"
        narrative_units.append(
            _make_unit(
                unit_id=unit_id,
                corpus="B",
                lines=lines,
                source_id=source_id,
                indices=selected,
                kind=section_title or "is_supporting_narrative",
                title=group_title or section_title,
            )
        )

    # Context-only title/header lines are present in row records. If there are no
    # narrative paragraphs, rows alone still cover the page's nonblank evidence.
    return row_units + narrative_units, row_info, True


def _classify_page(lines: list[str]) -> tuple[str, int | None, re.Match[str] | None]:
    first = _first_nonblank(lines)
    if IS_PAGE_RE.match(first):
        return "IS", None, None
    for index, line in enumerate(lines):
        match = HG_HEADER_RE.match(line)
        if match:
            return "HG", index, match
    for index, line in enumerate(lines):
        match = CORE_HEADER_RE.match(line)
        if match:
            return "core", index, match
    return "other", None, None


def _whole_page_unit(
    page: dict[str, Any],
    lines: list[str],
    corpus: str,
    unit_id: str,
    kind: str,
    title: str,
) -> dict[str, Any]:
    return _make_unit(
        unit_id=unit_id,
        corpus=corpus,
        lines=lines,
        source_id=str(page["source_id"]),
        indices=range(len(lines)),
        kind=kind,
        title=title,
        text_override=page.get("text") if isinstance(page.get("text"), str) else None,
    )


def _code_occurrences(line: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(1), match.start(1), match.end(1))
        for match in CODE_RE.finditer(line)
    ]


def _find_labeled_code(line: str, label: str, prefix: str | None = None) -> list[str]:
    result: list[str] = []
    for code, start, _ in _code_occurrences(line):
        if prefix and not code.startswith(prefix):
            continue
        before = line[:start]
        if re.search(label, before):
            result.append(code)
    return result


def _explicit_reservation_mentions(line: str) -> list[str]:
    found: list[str] = []
    for code, start, _ in _code_occurrences(line):
        if not (code.startswith(("HG", "GR", "SW", "HA", "DL"))):
            continue
        before = line[:start]
        if re.search(r"예약(?:번호|코드|키)?\s*[:：]?\s*$", before):
            found.append(code)
    return found


def _add_relation(
    events: dict[str, dict[str, Any]],
    code_to_events: dict[str, set[str]],
    seen_edges: set[tuple[Any, ...]],
    unit_event_ids: dict[str, set[str]],
    *,
    code: str,
    event_id: str,
    role: str,
    source_id: str,
    line_number: int,
    unit_id: str,
) -> None:
    normalized = normalize_code(code)
    if not normalized:
        return
    edge_key = (event_id, normalized, role, source_id, line_number, unit_id)
    if edge_key in seen_edges:
        return
    seen_edges.add(edge_key)
    event = events.setdefault(
        event_id,
        {"unit_ids": set(), "source_ids": set(), "line_spans": set(), "relations": []},
    )
    event["unit_ids"].add(unit_id)
    event["source_ids"].add(source_id)
    event["line_spans"].add((source_id, line_number, line_number))
    event["relations"].append(
        {
            "normalized_code": normalized,
            "code": code,
            "role": role,
            "source_id": source_id,
            "start": line_number,
            "end": line_number,
        }
    )
    code_to_events[normalized].add(event_id)
    unit_event_ids[unit_id].add(event_id)


def _relation_pass(
    page: dict[str, Any],
    lines: list[str],
    family: str,
    units: list[dict[str, Any]],
    row_info: list[dict[str, Any]],
    events: dict[str, dict[str, Any]],
    code_to_events: dict[str, set[str]],
    seen_edges: set[tuple[Any, ...]],
    unit_event_ids: dict[str, set[str]],
) -> None:
    source_id = str(page["source_id"])

    def add(code: str, anchor: str, role: str, line_index: int, unit_id: str) -> None:
        _add_relation(
            events,
            code_to_events,
            seen_edges,
            unit_event_ids,
            code=code,
            event_id=event_id_for(anchor),
            role=role,
            source_id=source_id,
            line_number=line_index + 1,
            unit_id=unit_id,
        )

    if family == "HG":
        header_index = next(
            (i for i, line in enumerate(lines) if HG_HEADER_RE.match(line)), None
        )
        if header_index is None:
            return
        header_match = HG_HEADER_RE.match(lines[header_index])
        assert header_match is not None
        anchor = header_match.group(1)
        unit = next((u for u in units if u["corpus"] == "B"), None)
        if unit is None:
            return
        add(anchor, anchor, "reservation_key", header_index, unit["unit_id"])

        # The structured HG header explicitly identifies the equipment column.
        equipment = header_match.group(3).strip()
        if equipment and equipment.startswith(("HL-", "EDU-")):
            add(equipment, anchor, "equipment", header_index, unit["unit_id"])

        for index, line in enumerate(lines):
            if "계약" in line:
                for code in _find_labeled_code(line, r"계약", prefix="CT-"):
                    add(code, anchor, "contract_reference", index, unit["unit_id"])
            if "실제" in line and any(
                word in line for word in ("양하", "반출", "반납")
            ):
                for code, _, _ in _code_occurrences(line):
                    if code.startswith("EV-"):
                        event_role = next(
                            (
                                word
                                for word in (
                                    "실제 양하",
                                    "실제 적입 장비 반출",
                                    "실제 지정 장치장 반납",
                                )
                                if word in line
                            ),
                            "event_evidence",
                        )
                        add(
                            code,
                            anchor,
                            f"evidence:{event_role}",
                            index,
                            unit["unit_id"],
                        )
            if "정산실 대조번호" in line:
                for code in _find_labeled_code(
                    line, r"정산실\s*대조번호", prefix="INV-"
                ):
                    add(code, anchor, "invoice_reference", index, unit["unit_id"])
            if "수납 원본" in line:
                for code in _find_labeled_code(line, r"수납\s*원본", prefix="RCPT-"):
                    add(code, anchor, "receipt_reference", index, unit["unit_id"])
            if "기록 번호" in line:
                for code in _find_labeled_code(line, r"기록\s*번호", prefix="AP-"):
                    # This links the cited record only; it does not assert approval
                    # applicability or effect.
                    add(code, anchor, "record_number", index, unit["unit_id"])
        return

    if family == "core":
        unit = next((u for u in units if u["corpus"] == "B"), None)
        if unit is None:
            return
        reservation_hits: list[tuple[str, int]] = []
        for index, line in enumerate(lines):
            reservation_hits.extend(
                (code, index) for code in _explicit_reservation_mentions(line)
            )
        unique = {
            normalize_code(code): (code, index) for code, index in reservation_hits
        }
        if len(unique) != 1:
            return
        anchor, anchor_index = next(iter(unique.values()))
        add(anchor, anchor, "reservation_key", anchor_index, unit["unit_id"])
        for index, line in enumerate(lines):
            for code in _find_labeled_code(line, r"장비", prefix=None):
                if code.startswith(("HL-", "EDU-")):
                    add(code, anchor, "equipment", index, unit["unit_id"])
            for code in _find_labeled_code(line, r"계약", prefix="CT-"):
                add(code, anchor, "contract_reference", index, unit["unit_id"])
            if "실제" in line and any(
                word in line for word in ("양하", "반출", "반납")
            ):
                for code, _, _ in _code_occurrences(line):
                    if code.startswith("EV-"):
                        add(code, anchor, "event_evidence", index, unit["unit_id"])
        return

    if family == "IS":
        # Row units preserve the row's exact source lines plus labeled headers.
        for info in row_info:
            unit_id = info["unit_id"]
            row_indices = info["row_indices"]
            row_lines = [(i, lines[i]) for i in row_indices]
            row_code = info["row_code"]
            gr_hits: list[tuple[str, int]] = []
            for index, line in row_lines:
                gr_hits.extend(
                    (code, index)
                    for code, _, _ in _code_occurrences(line)
                    if GR_CODE_RE.fullmatch(code)
                )
            gr_unique = {normalize_code(code): (code, index) for code, index in gr_hits}
            header_text = " ".join(lines[i] for i in info["header_indices"])
            has_subject_field = bool(re.search(r"예약|대상", header_text))
            primary_is_gr = bool(GR_CODE_RE.fullmatch(row_code))

            if primary_is_gr:
                anchor = row_code
                start = info["row_start"]
                add(anchor, anchor, "reservation_key", start, unit_id)
            elif len(gr_unique) == 1 and has_subject_field:
                anchor, _ = next(iter(gr_unique.values()))
                add(
                    anchor,
                    anchor,
                    "reservation_key",
                    next(
                        i
                        for code, i in gr_hits
                        if normalize_code(code) == normalize_code(anchor)
                    ),
                    unit_id,
                )
                add(row_code, anchor, "record_id", info["row_start"], unit_id)
            else:
                anchor = row_code
                role = "is_record_id" if info["explicit_is_id"] else "source_record_id"
                add(row_code, anchor, role, info["row_start"], unit_id)
                # A GR mention is linked only if the row itself labels it as a
                # reservation field, or its table header names that field.
                if len(gr_unique) == 1 and has_subject_field:
                    gr_code, gr_index = next(iter(gr_unique.values()))
                    add(gr_code, gr_code, "reservation_key", gr_index, unit_id)

        # In page/narrative fallbacks, only a directly labeled reservation code
        # can create an event edge; generic co-occurring codes are ignored.
        row_unit_ids = {info["unit_id"] for info in row_info}
        for unit in units:
            if unit["unit_id"] in row_unit_ids:
                continue
            for span in unit["line_spans"]:
                for line_index in range(span["start"] - 1, span["end"]):
                    line = lines[line_index]
                    for code in _explicit_reservation_mentions(line):
                        add(
                            code,
                            code,
                            "reservation_reference",
                            line_index,
                            unit["unit_id"],
                        )
                    # ID-free but visibly columnar GR reservation rows are safe
                    # anchors even when the whole page remains a fallback unit.
                    match = GR_CODE_RE.match(line)
                    header_index = _find_column_header(lines, line_index)
                    if (
                        match
                        and header_index is not None
                        and re.search(r"예약|대상", lines[header_index])
                    ):
                        add(
                            match.group(1),
                            match.group(1),
                            "reservation_key",
                            line_index,
                            unit["unit_id"],
                        )
        return

    # For an unclassified fallback page, do not infer any cross-code relations.


def build_artifacts(
    pages: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    units: list[dict[str, Any]] = []
    events: dict[str, dict[str, Any]] = {}
    code_to_events: dict[str, set[str]] = defaultdict(set)
    seen_edges: set[tuple[Any, ...]] = set()
    unit_event_ids: dict[str, set[str]] = defaultdict(set)
    stats: dict[str, Any] = {
        "pages": 0,
        "units_by_corpus": Counter(),
        "families": defaultdict(lambda: Counter()),
        "relation_edges": 0,
        "events": 0,
        "linked_codes": 0,
    }

    for page in pages:
        stats["pages"] += 1
        lines = _page_lines(page)
        source_id = str(page["source_id"])
        first = _first_nonblank(lines)
        all_indices = range(len(lines))
        a_unit = _make_unit(
            unit_id=f"A:{source_id}:raw",
            corpus="A",
            lines=lines,
            source_id=source_id,
            indices=all_indices,
            kind="raw_page",
            title=first,
            text_override=page.get("text")
            if isinstance(page.get("text"), str)
            else None,
        )
        units.append(a_unit)
        stats["units_by_corpus"]["A"] += 1

        family, header_index, header_match = _classify_page(lines)
        family_stats = stats["families"][family]
        family_stats["pages"] += 1
        row_info: list[dict[str, Any]] = []

        if family == "HG" and header_index is not None and header_match is not None:
            b_unit = _whole_page_unit(
                page,
                lines,
                "B",
                f"B:{source_id}:record",
                header_match.group(4).strip(),
                lines[header_index].strip(),
            )
            b_units = [b_unit]
            family_stats["structured_pages"] += 1
            family_stats["record_units"] += 1
        elif family == "core" and header_index is not None and header_match is not None:
            b_unit = _whole_page_unit(
                page,
                lines,
                "B",
                f"B:{source_id}:record",
                header_match.group(2).strip(),
                lines[header_index].strip(),
            )
            b_units = [b_unit]
            family_stats["structured_pages"] += 1
            family_stats["record_units"] += 1
        elif family == "IS":
            b_units, row_info, structured = _parse_is_page(
                lines,
                source_id,
                page.get("text") if isinstance(page.get("text"), str) else None,
            )
            if structured:
                family_stats["structured_pages"] += 1
                family_stats["row_units"] += len(row_info)
                family_stats["narrative_units"] += len(b_units) - len(row_info)
            else:
                family_stats["fallback_pages"] += 1
        else:
            b_unit = _whole_page_unit(
                page,
                lines,
                "B",
                f"B:{source_id}:raw",
                "raw_page_fallback",
                first,
            )
            b_units = [b_unit]
            family_stats["fallback_pages"] += 1

        for unit in b_units:
            units.append(unit)
            stats["units_by_corpus"]["B"] += 1
        _relation_pass(
            page,
            lines,
            family,
            b_units,
            row_info,
            events,
            code_to_events,
            seen_edges,
            unit_event_ids,
        )

    for unit in units:
        unit["event_ids"] = sorted(unit_event_ids.get(unit["unit_id"], set()))

    relation_index = {
        "normalization": "remove punctuation and whitespace; preserve alphanumeric case",
        "code_to_events": {
            code: sorted(event_ids)
            for code, event_ids in sorted(code_to_events.items())
        },
        "events": {},
    }
    for event_id, event in sorted(events.items()):
        relation_index["events"][event_id] = {
            "unit_ids": sorted(event["unit_ids"]),
            "source_ids": sorted(event["source_ids"]),
            "line_spans": [
                {"source_id": source_id, "start": start, "end": end}
                for source_id, start, end in sorted(event["line_spans"])
            ],
            "relations": sorted(
                event["relations"],
                key=lambda rel: (
                    rel["source_id"],
                    rel["start"],
                    rel["end"],
                    rel["normalized_code"],
                    rel["role"],
                ),
            ),
        }

    stats["units_by_corpus"] = dict(stats["units_by_corpus"])
    stats["families"] = {
        family: dict(counts) for family, counts in sorted(stats["families"].items())
    }
    stats["events"] = len(relation_index["events"])
    stats["linked_codes"] = len(relation_index["code_to_events"])
    stats["relation_edges"] = len(seen_edges)
    return units, relation_index, stats


def read_pages(path: str | Path) -> Iterator[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            page = json.loads(line)
            if not isinstance(page, dict) or "source_id" not in page:
                raise ValueError(f"invalid page record at line {line_number}")
            yield page


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_outputs(
    pages_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    pages_path = Path(pages_path)
    output_dir = Path(output_dir)
    units, relation_index, stats = build_artifacts(read_pages(pages_path))
    output_dir.mkdir(parents=True, exist_ok=True)
    units_path = output_dir / "units.jsonl"
    relation_path = output_dir / "relation_index.json"
    manifest_path = output_dir / "units-build-manifest.json"
    with units_path.open("w", encoding="utf-8", newline="\n") as stream:
        for unit in units:
            stream.write(
                json.dumps(unit, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    relation_path.write_text(
        json.dumps(relation_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    raw_manifest_path = pages_path.with_name("raw-manifest.json")
    source_manifest: dict[str, Any] = {}
    if raw_manifest_path.exists():
        raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
        source_manifest = {
            "path": raw_manifest_path.name,
            "sha256": _sha256(raw_manifest_path),
            "source_pdf_count": len(raw_manifest.get("files", [])),
            "source_page_count": raw_manifest.get("pages"),
        }
    manifest = {
        "builder": "experimental/u_10o0o/fresh_record_hybrid/units.py",
        "builder_sha256": _sha256(Path(__file__)),
        "input_pages": {"path": pages_path.name, "sha256": _sha256(pages_path)},
        "source_manifest": source_manifest,
        "outputs": {
            "units.jsonl": _sha256(units_path),
            "relation_index.json": _sha256(relation_path),
        },
        "stats": stats,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **stats,
        "units_path": str(units_path),
        "relation_index_path": str(relation_path),
        "build_manifest_path": str(manifest_path),
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    default_artifact = (
        repo_root / "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=Path, default=default_artifact / "pages.jsonl")
    parser.add_argument("--output-dir", type=Path, default=default_artifact)
    args = parser.parse_args()
    print(
        json.dumps(
            write_outputs(args.pages, args.output_dir), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
