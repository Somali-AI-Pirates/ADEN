"""Conservative, local-only fact annotations for Advanced retrieval experiments.

This adapter does not decide whether an approval or contract is legally effective.
It can expose exact parsed row fields from an already retrieved source page and
perform one deliberately narrow tariff calculation when the question supplies
the contract ID and the number of chargeable days explicitly.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.ingestion.curated_schema import CuratedRecord, CuratedSource
from app.ingestion.curation import read_rows, validate_curated

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_ISO_DATE_TEXT = r"\d{4}-\d{2}-\d{2}"
_ID = re.compile(r"(?<![A-Za-z0-9-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9-])")
_PERIOD_LABEL = re.compile(r"(?:적용|효력|유효)\s*기간|기간\s*(?:적용|효력|유효)")
_STATUS_LABEL = re.compile(
    r"상태|승인\s*(?:여부|상태)|처리\s*(?:결과|상태)|후속|확정\s*여부"
)
_TARGET_LABEL = re.compile(r"대상|계약|화주|장비|규격|항로|비용|터미널|품명|컨테이너")
_RATE_CURRENCY = re.compile(r"\bUSD\b|\$|달러", re.IGNORECASE)
_COST_CATEGORIES = ("보관료", "반납지연료", "장비반납지연료")
_PERIOD_START_LABEL = re.compile(r"^(?:적용|효력|유효)(?:기간)?시작일$")
_PERIOD_END_LABEL = re.compile(r"^(?:적용|효력|유효)(?:기간)?종료일$")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _contains_identifier(text: str, identifier: str) -> bool:
    normalized = _normalize(text)
    token = re.escape(_normalize(identifier))
    return bool(re.search(rf"(?<![a-z0-9-]){token}(?![a-z0-9-])", normalized))


def _source_key(page: Mapping[str, Any]) -> tuple[str, int]:
    book_id = page.get("book_id")
    if not isinstance(book_id, str) or not book_id.strip():
        file_name = page.get("file_name")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("ranked pages require book_id or file_name")
        book_id = Path(file_name).stem
    number = page.get("page")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("ranked pages require a positive integer page")
    return book_id, number


def _explicit_ids(question: str, records: Sequence[CuratedRecord]) -> set[str]:
    found: set[str] = set()
    for record in records:
        for identifier in record.identifiers:
            if _contains_identifier(question, identifier):
                found.add(identifier)
    return found


def _record_matches_question(record: CuratedRecord, explicit_ids: set[str]) -> bool:
    return bool(explicit_ids.intersection(record.identifiers))


def _requested(question: str) -> dict[str, bool]:
    normalized = _normalize(question)
    return {
        "period": bool(
            re.search(
                r"유효|적용\s*기간|효력|언제부터|언제까지|기준일|시점", normalized
            )
        ),
        "status": bool(
            re.search(r"현재\s*상태|승인|확정|효력|처리\s*상태", normalized)
        ),
        "target": bool(
            re.search(
                r"대상|어떤\s*(?:계약|장비|항로)|어느\s*(?:계약|장비)", normalized
            )
        ),
        "calculation": bool(
            re.search(r"계산|산출|금액|요율|얼마", normalized)
            or (
                "청구" in normalized
                and any(category in normalized for category in _COST_CATEGORIES)
            )
        ),
    }


def _field_groups(record: CuratedRecord) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {
        "period": [],
        "status": [],
        "target": [],
    }
    for label, raw_value in record.fields.items():
        value = str(raw_value).strip()
        if not label.strip() or not value:
            continue
        normalized_label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label))
        if (
            (_PERIOD_LABEL.search(label) and len(_DATE.findall(value)) == 2)
            or _PERIOD_START_LABEL.fullmatch(normalized_label)
            or _PERIOD_END_LABEL.fullmatch(normalized_label)
        ):
            groups["period"].append((label, value))
        if _STATUS_LABEL.search(label):
            groups["status"].append((label, value))
        if _TARGET_LABEL.search(label):
            groups["target"].append((label, value))
    return groups


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _chargeable_days(question: str) -> int | None:
    """Accept only an explicitly labelled number of already-chargeable days."""
    normalized = unicodedata.normalize("NFKC", question)
    patterns = (
        r"(?:청구\s*대상|청구|과금|유료)\s*일수\s*(?:는|이|:|=)?\s*(\d{1,4})\s*일",
        r"(\d{1,4})\s*일\s*(?:을|를)?\s*(?:청구\s*대상|과금|유료)\s*일수",
    )
    values = {
        int(match) for pattern in patterns for match in re.findall(pattern, normalized)
    }
    return next(iter(values)) if len(values) == 1 else None


def _as_of_date(question: str) -> str | None:
    """Return a uniquely labelled, date-granularity as-of date."""
    normalized = unicodedata.normalize("NFKC", question)
    patterns = (
        rf"({_ISO_DATE_TEXT})\s*기준(?:일|시점)?",
        rf"기준(?:일|시점)?\s*(?:은|이|:|=)?\s*({_ISO_DATE_TEXT})",
        rf"as\s+of\s+({_ISO_DATE_TEXT})",
    )
    values = {
        match
        for pattern in patterns
        for match in re.findall(pattern, normalized, re.IGNORECASE)
    }
    if len(values) != 1:
        return None
    date_value = next(iter(values))
    try:
        date.fromisoformat(date_value)
    except ValueError:
        return None
    # A timestamp can change same-day validity and is outside the narrow
    # date-only comparison supported by this experiment.
    if re.search(re.escape(date_value) + r"\s+\d{1,2}:\d{2}", normalized):
        return None
    return date_value


def _effective_period(record: CuratedRecord) -> tuple[str, str] | None:
    """Accept explicit effective start/end fields or one labelled date range."""
    starts: list[str] = []
    ends: list[str] = []
    ranges: list[tuple[str, str]] = []
    for label, raw_value in record.fields.items():
        normalized_label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label))
        value = str(raw_value).strip()
        dates = _DATE.findall(value)
        between_dates = ""
        if len(dates) == 2:
            first_end = value.find(dates[0]) + len(dates[0])
            second_start = value.find(dates[1], first_end)
            between_dates = value[first_end:second_start]
        if (
            re.fullmatch(r"(?:적용|효력|유효)기간", normalized_label)
            and len(dates) == 2
            and re.search(r"부터|까지|[~～∼]", between_dates)
        ):
            ranges.append((dates[0], dates[1]))
        if _PERIOD_START_LABEL.fullmatch(normalized_label) and _DATE.fullmatch(value):
            starts.append(value)
        if _PERIOD_END_LABEL.fullmatch(normalized_label) and _DATE.fullmatch(value):
            ends.append(value)
    if ranges and not starts and not ends and len(ranges) == 1:
        start, end = ranges[0]
    elif not ranges and len(starts) == 1 and len(ends) == 1:
        start, end = starts[0], ends[0]
    else:
        return None
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        return None
    if start_date > end_date:
        return None
    return start, end


def _compare_effective_period(
    record: CuratedRecord, as_of: str | None
) -> tuple[str | None, str]:
    if as_of is None:
        return None, "missing_unambiguous_date_only_as_of"
    period = _effective_period(record)
    if period is None:
        return None, "missing_or_ambiguous_explicit_period_bounds"
    start, end = period
    if as_of == start or as_of == end:
        finding = "경계일이며 산입 규칙 확인이 필요"
        status = "boundary"
    elif start < as_of < end:
        finding = "명시된 시작일과 종료일 사이"
        status = "interior"
    else:
        finding = "명시된 날짜 범위 밖"
        status = "outside"
    note = (
        "[날짜 단위 효력기간 대조 — 원문 인용과 구분되는 실험 주석]\n"
        f"같은 행의 명시 날짜 범위는 {start}부터 {end}까지다. 기준일 {as_of}은 {finding}.\n"
        "구간 끝 날짜의 포함 여부는 원문에 산입 규칙이 명시된 경우만 확정한다. 승인 서명, 비용·거래별 적용 대상, "
        "시각·시간대, 예외는 판정하지 않았다."
    )
    return note, f"compared_{status}"


def _question_cost(question: str) -> str | None:
    normalized = _normalize(question)
    matches = [category for category in _COST_CATEGORIES if category in normalized]
    return matches[0] if len(matches) == 1 else None


def _contract_ids(record: CuratedRecord) -> set[str]:
    """Use IDs explicitly labelled as contract values, not case/link IDs."""
    result: set[str] = set()
    for label, value in record.fields.items():
        if "계약" in label:
            result.update(_ID.findall(value.upper()))
    # A single contract identifier in the section is accepted only when the
    # source row itself has no separate identifier column. It must still occur
    # verbatim in the question before any computation is attempted.
    if not result:
        result.update(
            identifier
            for identifier in record.identifiers
            if identifier.startswith("CT-")
        )
    return result


def _tariff_currency_is_explicit(record: CuratedRecord) -> bool:
    first = [value for label, value in record.fields.items() if "첫" in label]
    following = [value for label, value in record.fields.items() if "다음" in label]
    return (
        len(first) == 1
        and len(following) == 1
        and all(_RATE_CURRENCY.search(value) for value in (first[0], following[0]))
    )


def _calculate_cost(record: CuratedRecord, chargeable_days: int) -> Decimal | None:
    """Apply only contiguous, explicit daily tiers to already-chargeable days."""
    terms = record.cost_terms
    if not terms or chargeable_days <= 0 or not _tariff_currency_is_explicit(record):
        return None
    if any(re.search(r"상한|한도|최대", value) for value in record.fields.values()):
        return None
    tiers = terms.get("rate_tiers")
    if not isinstance(tiers, list) or not tiers:
        return None
    try:
        normalized: list[tuple[int, int | None, Decimal]] = []
        for tier in tiers:
            start = tier["paid_day_from"]
            end = tier["paid_day_to"]
            rate = Decimal(str(tier["daily_rate"]))
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or start < 1
                or (
                    end is not None
                    and (
                        isinstance(end, bool) or not isinstance(end, int) or end < start
                    )
                )
                or not rate.is_finite()
                or rate < 0
            ):
                return None
            normalized.append((start, end, rate))
    except (KeyError, TypeError, InvalidOperation):
        return None
    normalized.sort(key=lambda item: item[0])

    total = Decimal("0")
    next_day = 1
    for start, end, rate in normalized:
        if start != next_day:
            return None
        tier_end = chargeable_days if end is None else min(chargeable_days, end)
        if tier_end >= start:
            total += Decimal(tier_end - start + 1) * rate
        next_day = (end if end is not None else chargeable_days) + 1
        if next_day > chargeable_days:
            break
    return total if next_day > chargeable_days else None


def _render_calculation(
    question: str,
    ranked_records: Sequence[tuple[CuratedRecord, Mapping[str, Any]]],
) -> tuple[str | None, str]:
    days = _chargeable_days(question)
    category = _question_cost(question)
    if days is None:
        return None, "missing_explicit_chargeable_days"
    if category is None:
        return None, "missing_or_ambiguous_cost_category"

    candidates: list[tuple[CuratedRecord, str]] = []
    for record, _ in ranked_records:
        if not record.cost_terms or record.cost_terms.get("cost") != category:
            continue
        for identifier in _contract_ids(record):
            if _contains_identifier(question, identifier):
                candidates.append((record, identifier))
    if len(candidates) != 1:
        return None, "missing_or_ambiguous_contract_row"

    record, contract_id = candidates[0]
    amount = _calculate_cost(record, days)
    if amount is None:
        return None, "unsupported_or_incomplete_tariff"
    text = (
        "[조건부 요율 계산 — PDF 원문 근거와 구분되는 실험 주석]\n"
        f"계약 식별자 {contract_id}의 같은 표 행에 추출된 {category} 요율에 질문이 명시한 유료일수 {days}일을 적용: "
        f"USD {_format_decimal(amount)}.\n"
        "이 계산은 무료기간을 이미 제외한 유료일수를 입력으로 사용했다. 계약의 현재 적용 여부, 승인, 날짜 산입, "
        "다른 비용 항목, 상한·예외는 판정하지 않았다."
    )
    return text, "calculated"


class ValidityEvidence:
    """Look up parsed facts for ranked source pages, without reading labels/gold."""

    def __init__(self, curated_dir: str | Path):
        self.curated_dir = Path(curated_dir).resolve()
        validate_curated(self.curated_dir)

        self._sources: dict[str, CuratedSource] = {}
        for row in read_rows(self.curated_dir / "sources.jsonl"):
            source = CuratedSource.model_validate(row)
            if source.dataset != "advanced":
                raise ValueError(
                    "ValidityEvidence requires the Advanced curated dataset"
                )
            self._sources[source.source_id] = source

        self._records_by_page: dict[tuple[str, int], list[CuratedRecord]] = defaultdict(
            list
        )
        for path in sorted((self.curated_dir / "records").glob("*.jsonl")):
            for row in read_rows(path):
                record = CuratedRecord.model_validate(row)
                source = self._sources[record.source_id]
                self._records_by_page[(source.book_id, source.page)].append(record)

    def render(
        self,
        question: str,
        ranked: Sequence[tuple[Mapping[str, Any], float]],
    ) -> tuple[list[tuple[dict[str, Any], float]], dict[str, int]]:
        """Append tightly scoped notes while retaining each original page citation.

        Only source pages already present in ``ranked`` are inspected. If the
        question does not name an identifier, a row is usable only when that
        source page has exactly one curated record. Multi-row pages require an
        exact question-to-row identifier match.
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")

        requested = _requested(question)
        diagnostics: Counter[str] = Counter(
            input_pages=len(ranked),
            annotated_pages=0,
            selected_records=0,
            ambiguous_pages=0,
            incomplete_period=0,
            incomplete_status=0,
            incomplete_target=0,
            calculation_attempts=int(requested["calculation"]),
            calculation_successes=0,
            calculation_abstentions=0,
            period_comparison_attempts=int(requested["period"]),
            period_comparison_successes=0,
            period_comparison_abstentions=0,
            period_boundary_cases=0,
        )
        result: list[tuple[dict[str, Any], float]] = []
        selected_for_calculation: list[tuple[CuratedRecord, Mapping[str, Any]]] = []
        selected_for_period: list[CuratedRecord] = []
        seen_page_keys: set[tuple[str, int]] = set()
        annotated_page_keys: set[tuple[str, int]] = set()

        def append_note(index: int, note: str) -> None:
            page, score = result[index]
            copied = dict(page)
            copied["text"] = str(page.get("text", "")).rstrip() + "\n\n" + note
            result[index] = (copied, score)
            page_key = _source_key(page)
            if page_key not in annotated_page_keys:
                diagnostics["annotated_pages"] += 1
                annotated_page_keys.add(page_key)

        for page, score in ranked:
            key = _source_key(page)
            if key in seen_page_keys:
                # Duplicate ranking entries are retained, but never duplicate
                # the same source annotation in context.
                result.append((dict(page), score))
                continue
            seen_page_keys.add(key)
            page_records = self._records_by_page.get(key, [])
            explicit_ids = _explicit_ids(question, page_records)
            if explicit_ids:
                matching = [
                    record
                    for record in page_records
                    if record.review_status == "parsed"
                    and _record_matches_question(record, explicit_ids)
                ]
                # A broad identifier appearing in multiple parsed rows does
                # not identify a unique contract/case row.
                selected = matching if len(matching) == 1 else []
            elif len(page_records) == 1 and page_records[0].review_status == "parsed":
                selected = [page_records[0]]
            else:
                selected = []
                if len(page_records) > 1:
                    diagnostics["ambiguous_pages"] += 1

            page_notes: list[str] = []
            page_calculation_records: list[tuple[CuratedRecord, Mapping[str, Any]]] = []
            found_by_topic = {"period": False, "status": False, "target": False}
            if selected and any(requested.values()):
                for record in selected:
                    if requested["calculation"]:
                        page_calculation_records.append((record, page))
                    if requested["period"]:
                        selected_for_period.append(record)
                    groups = _field_groups(record)
                    lines: list[str] = []
                    for topic in ("period", "status", "target"):
                        if requested[topic]:
                            for label, value in groups[topic]:
                                lines.append(f"- {label}: {value}")
                                found_by_topic[topic] = True
                    if lines:
                        page_notes.append(
                            "[구조 추출 사실 · 원문 행 묶음 유지]\n"
                            + "\n".join(lines)
                            + "\n이 값은 해당 원문 행에서 구조 추출한 표기다. 승인·현재 효력·다른 행과의 연결을 뜻하지 않는다."
                        )
                        diagnostics["selected_records"] += 1

            for topic in ("period", "status", "target"):
                if requested[topic] and not found_by_topic[topic]:
                    diagnostics[f"incomplete_{topic}"] += 1
                    label = {
                        "period": "적용·효력 기간이 명시된 날짜 범위",
                        "status": "현재 승인·확정 상태",
                        "target": "요청에서 묻는 대상 범위",
                    }[topic]
                    page_notes.append(
                        f"[실험용 추출 점검 — PDF 근거 문구가 아님] 이 후보 페이지에서 {label}을(를) "
                        "고유하게 구조 추출하지 못했다. 이는 원문 전체에 정보가 없다는 뜻이 아니며, 원문과 연결 문서를 확인해야 한다."
                    )

            if page_notes:
                copied = dict(page)
                original_text = page.get("text")
                if not isinstance(original_text, str):
                    raise TypeError("ranked pages require text")
                copied["text"] = (
                    original_text.rstrip() + "\n\n" + "\n\n".join(page_notes)
                )
                annotated_page_keys.add(key)
                diagnostics["annotated_pages"] += 1
                result.append((copied, score))
            else:
                result.append((dict(page), score))
            selected_for_calculation.extend(page_calculation_records)

        if requested["calculation"]:
            calculation, status = _render_calculation(
                question, selected_for_calculation
            )
            if calculation is not None:
                diagnostics["calculation_successes"] = 1
                # Attach the explicitly non-source note to the unique matching
                # contract page, keeping file/page citation metadata unchanged.
                contract_record = next(
                    record
                    for record, _ in selected_for_calculation
                    if record.cost_terms.get("cost") == _question_cost(question)
                    and any(
                        _contains_identifier(question, item)
                        for item in _contract_ids(record)
                    )
                )
                source = self._sources[contract_record.source_id]
                for index, (page, score) in enumerate(result):
                    if _source_key(page) == (source.book_id, source.page):
                        copied = dict(page)
                        copied["text"] = (
                            str(page.get("text", "")).rstrip() + "\n\n" + calculation
                        )
                        result[index] = (copied, score)
                        if _source_key(page) not in annotated_page_keys:
                            diagnostics["annotated_pages"] += 1
                            annotated_page_keys.add(_source_key(page))
                        break
            else:
                diagnostics["calculation_abstentions"] = 1
                if result:
                    # A scoped, non-citable note tells downstream generation to
                    # abstain; it never claims the whole corpus lacks evidence.
                    reason = {
                        "missing_explicit_chargeable_days": "질문에 이미 확정된 유료일수",
                        "missing_or_ambiguous_cost_category": "하나로 특정된 비용 항목",
                        "missing_or_ambiguous_contract_row": "질문 식별자와 유일하게 연결되는 후보 계약 행",
                        "unsupported_or_incomplete_tariff": "통화 단위가 확인되고 구간이 연속된 요율표",
                    }[status]
                    note = (
                        "\n\n[실험용 계산 보류 — PDF 근거 문구가 아님] "
                        f"계산에 필요한 {reason}이 없다. 이 주석은 원문 부재나 금액 0을 뜻하지 않는다."
                    )
                    append_note(0, note)

        if requested["period"]:
            comparison_note: str | None = None
            comparison_status = "missing_or_ambiguous_source_row"
            if len(selected_for_period) == 1:
                comparison_note, comparison_status = _compare_effective_period(
                    selected_for_period[0], _as_of_date(question)
                )
            if comparison_note is not None:
                diagnostics["period_comparison_successes"] = 1
                if comparison_status == "compared_boundary":
                    diagnostics["period_boundary_cases"] = 1
                source = self._sources[selected_for_period[0].source_id]
                for index, (page, score) in enumerate(result):
                    if _source_key(page) == (source.book_id, source.page):
                        append_note(index, comparison_note)
                        break
            else:
                diagnostics["period_comparison_abstentions"] = 1
                if result:
                    reason = {
                        "missing_unambiguous_date_only_as_of": "질문에 명시된 단일 날짜 단위 기준일",
                        "missing_or_ambiguous_explicit_period_bounds": "같은 행의 명시적 적용 시작일·종료일 쌍",
                        "missing_or_ambiguous_source_row": "질문 식별자로 고유하게 연결된 구조 추출 행",
                    }.get(comparison_status, "고유하게 연결된 기간 근거")
                    note = (
                        "\n\n[실험용 기간 대조 보류 — PDF 근거 문구가 아님] "
                        f"{reason}이 확인되지 않았다. 현재 효력 여부를 추정하지 않는다."
                    )
                    append_note(0, note)

        return result, dict(diagnostics)
