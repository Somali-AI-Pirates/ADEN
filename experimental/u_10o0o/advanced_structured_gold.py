"""Build a private, PDF-derived typed-fact pilot for Advanced DB/RAG routing.

Scope contract: create 30 independent questions (10 each for HG, HA, and IS;
per family 6 direct, 2 mixed, 2 fallback) and gold facts by extracting original
PDFs at build time. No SQLite database, curated JSONL, role-facts export,
existing evaluation answers, or network model is read. The public contract is
two existing-evaluation-compatible files (``questions.json`` and
``references.json``) under the ignored ``artifacts/`` tree. IS and HA labels
remain literal source columns unless a narrow parser can verify a type.

The checker re-extracts the selected source pages from PDF and verifies values,
location, PDF hashes, and evidence quotes. It prints aggregate counts only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Mapping, Sequence

from app.ingestion.curation import LayoutDocument, split_page
from app.ingestion.layout import identifiers, page_body
from experimental.u_10o0o.context_evidence import _normalize
from experimental.u_10o0o.prepare import DATA_DIR, PROJECT_ROOT

DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts/u_10o0o/advanced-structured-gold-20261109"
PLAN_PATH = Path(__file__).with_name("structured_gold_selection.json")
DATASET_VERSION = "advanced-structured-gold-v1"
EXPECTED_PDFS = 120
EXPECTED_PAGES_PER_PDF = 150
FAMILIES = ("HG", "HA", "IS")
ROUTE_COUNTS = {"direct": 6, "mixed": 2, "fallback": 2}

HG_ROLES = (
    "비용 조건 약정",
    "현장 사건 수신대장",
    "계약 조건 대조 회신",
    "비용 청구 명세",
    "수납 및 미결 인계",
)
HA_ROLES = (
    "계약 조건 확인",
    "사건·증빙 접수 대조",
    "비용별 정산 확인",
)
HG_COSTS = {"보관료": "storage", "반납지연료": "late_return", "장비반납지연료": "late_return"}
HG_EVENTS = {
    "실제 양하": "actual_discharge_date",
    "실제 적입 장비 반출": "actual_gate_out_date",
    "실제 지정 장치장 반납": "actual_designated_return_date",
    "반납 예약 예정": "scheduled_return_date",
    "전산 입력일": "system_entry_date",
}
HA_KEY_COLUMNS = {
    frozenset(
        ("기존·요청 근거", "대상", "상태·기록일", "요청·회신 내용", "처리 기록")
    ): "처리 기록",
    frozenset(
        ("기록번호", "대상", "범위·상태", "업무·의제", "확인·회신 내용")
    ): "기록번호",
}

HG_DIRECT_SPECS = (
    ("actual_discharge_date", "none"),
    ("actual_gate_out_date", "none"),
    ("scheduled_return_date", "none"),
    ("system_entry_date", "none"),
    ("billed_amount", "storage"),
    ("received_amount", "none"),
)
HA_DIRECT_SPECS = (
    ("계약 조건 확인", "무료기간"),
    ("계약 조건 확인", "첫 유료구간"),
    ("사건·증빙 접수 대조", "날짜·접수값"),
    ("사건·증빙 접수 대조", "날짜·접수값"),
    ("비용별 정산 확인", "금액"),
    ("계약 조건 확인", "다음 구간"),
)
IS_DIRECT_SPECS = (
    ("처리 기록", "상태·기록일"),
    ("처리 기록", "요청·회신 내용"),
    ("처리 기록", "기존·요청 근거"),
    ("기록번호", "범위·상태"),
    ("기록번호", "업무·의제"),
    ("기록번호", "확인·회신 내용"),
)

HG_LABELS = {
    "actual_discharge_date": "실제 양하일",
    "actual_gate_out_date": "실제 적입 장비 반출일",
    "actual_designated_return_date": "실제 지정 장소 반납일",
    "scheduled_return_date": "반납 예약 예정일",
    "system_entry_date": "전산 입력일",
    "base_free_days": "기본 무료 일수",
    "billed_amount": "확정 금액",
    "billed_included_days": "산입 일수",
    "billed_free_days": "무료 일수",
    "billed_paid_days": "유료 일수",
    "receipt_total": "거래 합계",
    "received_amount": "수납액",
    "balance_amount": "미수 또는 환급 잔액",
    "refund_review_amount": "환급 검토액",
    "approval_record": "승인 대조 기록",
    "comparison_record_id": "대조 기록 번호",
}

NARRATIVE_CUES = re.compile(r"사유|예외|정정|변경|승인|조건|근거|보류|반영|확인|적용")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DAY_COUNT = re.compile(r"(?:기본\s*)?(\d+)\s*일")


@dataclass(frozen=True)
class ParsedSourceRow:
    family: str
    key: str | None
    role: str | None
    source: dict[str, Any]
    row: Any
    page_text: str


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extractor_version() -> str:
    if shutil.which("pdftotext") is None:
        raise RuntimeError("원본 PDF 검증에는 Poppler pdftotext가 필요합니다.")
    result = subprocess.run(
        ["pdftotext", "-v"], capture_output=True, check=True, text=True
    )
    return (result.stderr or result.stdout).splitlines()[0].strip()


def _direct_document(pdf: Path) -> LayoutDocument:
    raw = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, check=True
    ).stdout
    pages = raw.decode("utf-8").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != EXPECTED_PAGES_PER_PDF or any(not page.strip() for page in pages):
        raise ValueError(f"PDF page count or extraction is invalid: {pdf.name}")
    return LayoutDocument(
        book_id=pdf.stem,
        file_name=pdf.name,
        file_sha256=_file_sha(pdf),
        pages=pages,
    )


def _classify_heading(heading: str) -> tuple[str, str | None, str | None] | None:
    found = identifiers(heading)
    hg_ids = [value for value in found if re.fullmatch(r"HG\d{4}-\d{5}", value)]
    if len(hg_ids) == 1:
        role = heading.rsplit(" / ", 1)[-1].strip()
        if role in HG_ROLES:
            return "HG", hg_ids[0], role
    ha_ids = [value for value in found if re.fullmatch(r"HA\d{2}-[CH]\d{3}", value)]
    if len(ha_ids) == 1:
        tail = heading.rsplit(" > ", 1)[-1]
        _, _, role = tail.partition(" ")
        if role in HA_ROLES:
            return "HA", ha_ids[0], role
    if re.match(r"IS\d+\s*\|", heading):
        return "IS", None, None
    return None


def _scan_original_pdfs(
    *, selected_files: set[str] | None = None, page_filter: set[tuple[str, int]] | None = None
) -> tuple[list[ParsedSourceRow], dict[str, str]]:
    pdfs = sorted((DATA_DIR / "documents").glob("*.pdf"))
    if selected_files is None and len(pdfs) != EXPECTED_PDFS:
        raise ValueError(f"Expected {EXPECTED_PDFS} Advanced original PDFs.")
    if selected_files is not None:
        missing = selected_files - {path.name for path in pdfs}
        if missing:
            raise ValueError(f"Selected original PDFs are missing: {len(missing)}")
        pdfs = [path for path in pdfs if path.name in selected_files]

    rows: list[ParsedSourceRow] = []
    hashes: dict[str, str] = {}
    for pdf in pdfs:
        document = _direct_document(pdf)
        hashes[pdf.name] = document.file_sha256
        rule_cache: dict[str, Any] = {}
        for page_number, page_text in enumerate(document.pages, start=1):
            if page_filter is not None and (pdf.stem, page_number) not in page_filter:
                continue
            heading, _ = page_body(
                page_text, document.book_id, page_number, len(document.pages)
            )
            if _classify_heading(heading) is None:
                continue
            source, page_rows, _ = split_page("advanced", document, page_number, rule_cache)
            classification = _classify_heading(source.heading)
            if classification is None:
                continue
            family, key, role = classification
            source_ref = {
                "book_id": source.book_id,
                "file_name": source.file_name,
                "pdf_sha256": source.file_sha256,
                "page": source.page,
            }
            rows.extend(
                ParsedSourceRow(family, key, role, source_ref, record, page_text)
                for record in page_rows
            )
    return rows, hashes


def _source_with_row(item: ParsedSourceRow) -> dict[str, Any]:
    row = item.row
    return {
        **item.source,
        "record_id": row.record_id,
        "line_start": row.line_start,
        "line_end": row.line_end,
    }


def _source_quote(item: ParsedSourceRow) -> str:
    lines = item.page_text.splitlines()[item.row.line_start - 1 : item.row.line_end]
    quote = " ".join(line.strip() for line in lines if line.strip())
    if not quote:
        raise ValueError("Selected PDF source row has no extracted text.")
    return quote


def _valid_date(value: str) -> str | None:
    if not ISO_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _money(value: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"(?:(USD|KRW|EUR)\s*)?(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
        r"(?:\s*(USD|KRW|EUR))?",
        value.strip(),
    )
    if not match:
        return None
    before, raw_number, after = match.groups()
    if bool(before) == bool(after):
        return None
    try:
        number = Decimal(raw_number.replace(",", ""))
    except InvalidOperation:
        return None
    return format(number, "f"), before or after


def _days(value: str) -> int | None:
    match = DAY_COUNT.fullmatch(value.strip())
    return int(match.group(1)) if match else None


def _typed_fact(
    item: ParsedSourceRow,
    *,
    field: str,
    column: str,
    qualifier: str | None,
    value: str | int,
    value_type: str,
    cost_kind: str = "none",
    unit: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    return {
        "family": item.family,
        "key": item.key,
        "role": item.role,
        "field": field,
        "cost_kind": cost_kind,
        "value": value,
        "value_type": value_type,
        "state": "reported",
        "unit": unit,
        "currency": currency,
        "source": _source_with_row(item),
        "column": column,
        "qualifier": qualifier,
    }


def _hg_facts(item: ParsedSourceRow) -> list[dict[str, Any]]:
    if item.family != "HG" or item.key is None:
        return []
    row = item.row
    if row.kind != "table_row" or row.review_status != "parsed":
        return []
    fields = row.fields
    result: list[dict[str, Any]] = []
    event = HG_EVENTS.get(fields.get("항목", ""))
    raw_date = fields.get("날짜 또는 상태", "")
    parsed_date = _valid_date(raw_date)
    if event and parsed_date:
        result.append(
            _typed_fact(
                item,
                field=event,
                column="날짜 또는 상태",
                qualifier=fields.get("항목"),
                value=parsed_date,
                value_type="date",
            )
        )

    cost_kind = HG_COSTS.get(fields.get("비용", ""), "none")
    if item.role == "비용 조건 약정":
        value = fields.get("기본 무료", "")
        days = _days(value)
        if days is not None and cost_kind != "none":
            result.append(
                _typed_fact(
                    item,
                    field="base_free_days",
                    column="기본 무료",
                    qualifier=fields.get("비용"),
                    value=days,
                    value_type="integer",
                    cost_kind=cost_kind,
                    unit="day",
                )
            )

    if item.role == "비용 청구 명세":
        breakdown = fields.get("산입/무료/유료일", "")
        match = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", breakdown)
        if match and cost_kind != "none":
            for field_name, value in zip(
                ("billed_included_days", "billed_free_days", "billed_paid_days"),
                map(int, match.groups()),
                strict=True,
            ):
                result.append(
                    _typed_fact(
                        item,
                        field=field_name,
                        column="산입/무료/유료일",
                        qualifier=fields.get("비용"),
                        value=value,
                        value_type="integer",
                        cost_kind=cost_kind,
                        unit="day",
                    )
                )
        amount = _money(fields.get("확정 금액", ""))
        if amount and cost_kind != "none":
            result.append(
                _typed_fact(
                    item,
                    field="billed_amount",
                    column="확정 금액",
                    qualifier=None,
                    value=amount[0],
                    value_type="decimal",
                    cost_kind=cost_kind,
                    currency=amount[1],
                )
            )

    if item.role == "계약 조건 대조 회신":
        label = fields.get("항목", "")
        raw = fields.get("확인 기록", "")
        text_fields = {
            "승인 대조": "approval_record",
            "관련 계약": "comparison_contract_id",
            "대상 장비": "comparison_equipment",
            "대조 사항": "comparison_subject",
            "기록 번호": "comparison_record_id",
        }
        if label in text_fields and raw.strip():
            result.append(
                _typed_fact(
                    item,
                    field=text_fields[label],
                    column="확인 기록",
                    qualifier=None,
                    value=raw.strip(),
                    value_type="text",
                )
            )
        if label == "개정 효력일":
            effective_date = _valid_date(raw)
            if effective_date:
                result.append(
                    _typed_fact(
                        item,
                        field="recorded_amendment_effective_date",
                        column="확인 기록",
                        qualifier=None,
                        value=effective_date,
                        value_type="date",
                    )
                )
    if item.role == "수납 및 미결 인계":
        label = fields.get("구분", "")
        field_code = {
            "거래 합계": "receipt_total",
            "수납": "received_amount",
            "미수 또는 환급": "balance_amount",
        }.get(label)
        raw_amount = fields.get("금액/상태", "")
        if field_code and not (label == "미수 또는 환급" and raw_amount.startswith("환급 검토 ")):
            amount = _money(raw_amount)
            if amount:
                result.append(
                    _typed_fact(
                        item,
                        field=field_code,
                        column="금액/상태",
                        qualifier=label,
                        value=amount[0],
                        value_type="decimal",
                        currency=amount[1],
                    )
                )
        if label == "미수 또는 환급" and raw_amount.startswith("환급 검토 "):
            amount = _money(raw_amount.removeprefix("환급 검토 "))
            if amount:
                result.append(
                    _typed_fact(
                        item,
                        field="refund_review_amount",
                        column="금액/상태",
                        qualifier=label,
                        value=amount[0],
                        value_type="decimal",
                        currency=amount[1],
                    )
                )
    return result


def _ha_facts(item: ParsedSourceRow) -> list[dict[str, Any]]:
    if item.family != "HA" or item.key is None:
        return []
    row = item.row
    if row.kind != "table_row" or row.review_status != "parsed":
        return []
    fields = row.fields
    if item.role == "계약 조건 확인":
        label = fields.get("항목") or fields.get("비용")
    elif item.role == "사건·증빙 접수 대조":
        label = fields.get("확인 항목") or fields.get("기록")
    else:
        label = fields.get("항목")
    cost_kind = HG_COSTS.get(fields.get("비용", ""), "none")
    result: list[dict[str, Any]] = []
    for column, raw in fields.items():
        if column == "무료기간":
            days = _days(raw)
            if days is not None:
                result.append(
                    _typed_fact(item, field=column, column=column, qualifier=label, value=days, value_type="integer", cost_kind=cost_kind, unit="day")
                )
        elif column == "날짜·접수값":
            # This accepts an explicitly printed ISO date only. The source row's
            # category/verification fields remain in the row quote and qualifier.
            parsed_date = _valid_date(raw)
            if parsed_date:
                result.append(
                    _typed_fact(item, field=column, column=column, qualifier=label, value=parsed_date, value_type="date", cost_kind=cost_kind)
                )
        elif column == "금액":
            parsed_amount = _money(raw)
            if parsed_amount:
                result.append(
                    _typed_fact(item, field=column, column=column, qualifier=label, value=parsed_amount[0], value_type="decimal", cost_kind=cost_kind, currency=parsed_amount[1])
                )
        elif column in {"첫 구간 단가", "다음 구간", "첫 유료구간", "기재 내용", "기록", "성격", "확인 상태", "처리 근거"}:
            if isinstance(raw, str) and raw.strip():
                result.append(
                    _typed_fact(item, field=column, column=column, qualifier=label, value=raw.strip(), value_type="text", cost_kind=cost_kind)
                )
    return result


def _is_facts(item: ParsedSourceRow) -> list[dict[str, Any]]:
    if item.family != "IS":
        return []
    row = item.row
    if row.kind != "table_row" or row.review_status != "parsed":
        return []
    fields = row.fields
    key_column = HA_KEY_COLUMNS.get(frozenset(fields))
    if key_column is None:
        return []
    key = fields.get(key_column, "").strip()
    if not key or len(identifiers(key)) != 1:
        return []
    result = []
    for column, raw in fields.items():
        if column == key_column or not isinstance(raw, str) or not raw.strip():
            continue
        result.append(
            {
                **_typed_fact(
                    item,
                    field=column,
                    column=column,
                    qualifier=key_column,
                    value=raw.strip(),
                    value_type="text",
                ),
                "key": key,
            }
        )
    return result


def _facts_for(rows: Sequence[ParsedSourceRow]) -> list[dict[str, Any]]:
    facts = []
    for row in rows:
        if row.family == "HG":
            facts.extend(_hg_facts(row))
        elif row.family == "HA":
            facts.extend(_ha_facts(row))
        elif row.family == "IS":
            facts.extend(_is_facts(row))
    return facts


def _fact_identity(fact: Mapping[str, Any]) -> tuple[Any, ...]:
    family = fact.get("family")
    if family == "HG":
        return (
            family,
            fact.get("key"),
            fact.get("role"),
            fact.get("field"),
            fact.get("cost_kind") or "none",
        )
    return (
        family,
        fact.get("key"),
        fact.get("role"),
        fact.get("field"),
        fact.get("cost_kind", "none"),
        fact.get("column"),
        fact.get("qualifier"),
    )


def _source_identity(source: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    if not isinstance(source, Mapping):
        return None
    return tuple(source.get(key) for key in ("file_name", "page", "record_id", "line_start", "line_end"))


def score_typed_facts(
    expected_facts: Sequence[Mapping[str, Any]],
    returned_facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score typed DB output without exposing values or source text.

    Identity measures whether the requested field/qualifier was returned. Value
    accuracy additionally compares scalar type, state, unit, and currency. Source
    accuracy separately checks the exact PDF row pointer. Empty predictions have
    zero recall and undefined precision/value accuracy, not fabricated errors.
    """
    if isinstance(expected_facts, (str, bytes)) or isinstance(returned_facts, (str, bytes)):
        raise TypeError("facts must be sequences of objects")
    expected = [row for row in expected_facts if isinstance(row, Mapping)]
    returned = [row for row in returned_facts if isinstance(row, Mapping)]
    indexed: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in returned:
        indexed[_fact_identity(row)].append(row)

    matched = 0
    value_correct = 0
    source_correct = 0
    used: Counter[tuple[Any, ...]] = Counter()
    for gold in expected:
        identity = _fact_identity(gold)
        candidates = indexed.get(identity, ())
        offset = used[identity]
        if offset >= len(candidates):
            continue
        prediction = candidates[offset]
        used[identity] += 1
        matched += 1
        value_match = (
            type(prediction.get("value")) is type(gold.get("value"))
            and prediction.get("value") == gold.get("value")
            and prediction.get("value_type") == gold.get("value_type")
            and prediction.get("state") == gold.get("state")
            and prediction.get("unit") == gold.get("unit")
            and prediction.get("currency") == gold.get("currency")
        )
        value_correct += int(value_match)
        source_correct += int(
            _source_identity(prediction.get("source"))
            == _source_identity(gold.get("source"))
        )
    false_positive = len(returned) - matched
    false_negative = len(expected) - matched
    precision = matched / len(returned) if returned else None
    recall = matched / len(expected) if expected else None
    value_precision = value_correct / matched if matched else None
    value_recall = value_correct / len(expected) if expected else None
    source_recall = source_correct / len(expected) if expected else None
    return {
        "required": len(expected),
        "returned": len(returned),
        "identity": {
            "true_positive": matched,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
        },
        "value": {
            "correct": value_correct,
            "accuracy_on_matched": value_precision,
            "recall_of_required": value_recall,
        },
        "source": {
            "correct": source_correct,
            "recall_of_required": source_recall,
        },
        "exact": bool(
            len(expected) == len(returned)
            and matched == len(expected)
            and value_correct == len(expected)
            and source_correct == len(expected)
        ),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_order(items: Iterable[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: sha256(
            f"{DATASET_VERSION}:{label}:{item['source']['file_name']}:{item['source']['page']}:{item['source']['record_id']}".encode()
        ).hexdigest(),
    )


def _citation(item: ParsedSourceRow) -> dict[str, Any]:
    return {
        "book_id": item.source["book_id"],
        "file_name": item.source["file_name"],
        "page": item.source["page"],
        "quote": _source_quote(item),
    }


def _evidence_requirement(fact: Mapping[str, Any], citation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": fact.get("role") or fact.get("family"),
        "ask": fact.get("field"),
        "fact": f"typed:{fact.get('field')}",
        "primary": [dict(citation)],
        "alternatives": [],
    }


def _reference_for(
    qid: str,
    family: str,
    route_class: str,
    facts: Sequence[dict[str, Any]],
    rag_rows: Sequence[ParsedSourceRow],
    rows: Sequence[ParsedSourceRow],
) -> dict[str, Any]:
    source_rows: dict[tuple[str, int, str], ParsedSourceRow] = {}
    fact_citations = []
    requirements = []
    for fact in facts:
        key = (fact["source"]["file_name"], fact["source"]["page"], fact["source"]["record_id"])
        item = _find_row_for_fact(rows, fact)
        source_rows[key] = item
        citation = _citation(item)
        fact_citations.append(citation)
        requirements.append(_evidence_requirement(fact, citation))
    rag_citations = []
    for item in rag_rows:
        citation = _citation(item)
        rag_citations.append(citation)
        requirements.append(
            {
                "role": item.role or family,
                "ask": "narrative_context",
                "fact": "narrative_context",
                "primary": [citation],
                "alternatives": [],
            }
        )
        source_rows[(item.source["file_name"], item.source["page"], item.row.record_id)] = item
    unique_citations = {}
    for citation in [*fact_citations, *rag_citations]:
        unique_citations[(citation["book_id"], citation["page"], citation["quote"])] = citation
    evidence_groups = [[dict(citation)] for citation in unique_citations.values()]
    expected_tool_use = {
        "direct": "structured_only",
        "mixed": "structured_and_rag",
        "fallback": "rag_only",
    }[route_class]
    return {
        "id": qid,
        "family": family,
        "route_class": route_class,
        "expected_action": route_class,
        "expected_tool_use": expected_tool_use,
        "expected_facts": list(facts),
        "expected_rag_sources": [
            {**item.source, "record_id": item.row.record_id, "line_start": item.row.line_start, "line_end": item.row.line_end}
            for item in rag_rows
        ],
        "expected_sources": [
            {**item.source, "record_id": item.row.record_id, "line_start": item.row.line_start, "line_end": item.row.line_end}
            for item in source_rows.values()
        ],
        "evidence_groups": evidence_groups,
        "evidence_requirements": requirements,
    }


def _make_direct_question(family: str, facts: Sequence[dict[str, Any]], ordinal: int) -> str:
    if family == "HG":
        case = facts[0]["key"]
        labels = []
        for fact in facts:
            label = HG_LABELS.get(fact["field"], fact["field"])
            if fact["field"] == "billed_amount":
                label = "확정 청구 금액"
            if fact.get("cost_kind") == "storage":
                label = f"보관료 {label}"
            elif fact.get("cost_kind") == "late_return":
                label = f"장비반납지연료 {label}"
            labels.append(label)
        roles = list(dict.fromkeys(fact["role"] for fact in facts if fact.get("role")))
        if len(labels) == 1:
            return f"{case} 건 {roles[0]}에서 {labels[0]} 기록을 확인해줘."
        return f"{case} 건 {', '.join(roles)}에 기록된 {'·'.join(labels)}을 각각 알려줘."
    if family == "HA":
        fact = facts[0]
        cost_label = {"storage": "보관료", "late_return": "장비반납지연료"}.get(fact.get("cost_kind"), "")
        cost_prefix = f"{cost_label}의 " if cost_label else ""
        return (
            f"{fact['key']} 건 {fact['role']}에서 {cost_prefix}'{fact['qualifier']}' 행의 "
            f"'{fact['column']}' 값이 어떻게 기록돼 있어?"
        )
    fact = facts[0]
    key_kind = fact["qualifier"]
    return f"자료집의 {key_kind} '{fact['key']}'에서 '{fact['column']}' 값이 뭐야?"


def _find_row_for_fact(rows: Sequence[ParsedSourceRow], fact: Mapping[str, Any]) -> ParsedSourceRow:
    source = fact["source"]
    for row in rows:
        if (
            row.source["file_name"] == source["file_name"]
            and row.source["page"] == source["page"]
            and row.row.record_id == source["record_id"]
        ):
            return row
    raise ValueError("Gold fact source does not point to an original-PDF row.")


def _make_mixed_question(family: str, fact: Mapping[str, Any]) -> str:
    if family == "HG":
        cost_label = {"storage": "보관료", "late_return": "장비반납지연료"}.get(fact.get("cost_kind"), "")
        field_label = HG_LABELS.get(fact["field"], fact["field"])
        if fact["field"] == "billed_amount":
            field_label = "확정 청구 금액"
        if cost_label:
            field_label = f"{cost_label} {field_label}"
        return (
            f"{fact['key']} 건 {fact['role']}의 {field_label} 기록을 확인하고, "
            "관련 회신에 적힌 적용 사유와 예외도 원문에서 확인해줘."
        )
    if family == "HA":
        return (
            f"{fact['key']} 건 {fact['role']}의 '{fact['qualifier']}' 행에서 "
            f"'{fact['column']}' 값과, 관련 검토 메모에 적힌 적용 근거를 같이 확인해줘."
        )
    return (
        f"자료집의 {fact['qualifier']} '{fact['key']}'에서 '{fact['column']}' 값을 확인하고, "
        "같은 처리 건의 검토 근거와 후속 내용도 함께 알려줘."
    )


def _make_fallback_question(family: str, key: str | None, topic: str) -> str:
    if family == "HG":
        return f"{key} 건에서 기록된 변경이나 비용 조정의 사유와 적용 예외를 설명해줘."
    if family == "HA":
        return f"{key} 건의 비용 조정이 어떤 조건과 검토 근거를 거쳐 반영됐는지 설명해줘."
    return f"업무 자료에서 {topic}와 관련해 확인해야 할 근거와 후속 절차를 설명해줘."


def _case_rows(rows: Sequence[ParsedSourceRow], family: str) -> dict[str, list[ParsedSourceRow]]:
    result: dict[str, list[ParsedSourceRow]] = defaultdict(list)
    for row in rows:
        if row.family == family and row.key:
            result[row.key].append(row)
    return result


def _narrative_for_case(
    rows_by_case: Mapping[str, Sequence[ParsedSourceRow]],
    key: str,
    *,
    preferred_roles: Sequence[str] = (),
) -> ParsedSourceRow | None:
    candidates = []
    for item in rows_by_case.get(key, ()):
        if item.row.kind not in {"narrative", "external_quotation"}:
            continue
        text = item.row.text
        if not isinstance(text, str) or not NARRATIVE_CUES.search(text):
            continue
        if preferred_roles and item.role not in preferred_roles:
            continue
        candidates.append(item)
    return _stable_order(
        [
            {"source": _source_with_row(item), "item": item}
            for item in candidates
        ],
        f"narrative:{key}",
    )[0]["item"] if candidates else None


def _direct_fact_candidates(facts: Sequence[dict[str, Any]], family: str, field: str, cost_kind: str | None = None) -> list[dict[str, Any]]:
    return [
        fact
        for fact in facts
        if fact["family"] == family
        and fact["field"] == field
        and (cost_kind is None or fact.get("cost_kind") == cost_kind)
    ]


def _choose_distinct(
    candidates: Sequence[dict[str, Any]], label: str, used_keys: set[str]
) -> dict[str, Any]:
    ordered = _stable_order(candidates, label)
    for candidate in ordered:
        key = str(candidate.get("key"))
        if key not in used_keys:
            used_keys.add(key)
            return candidate
    raise ValueError(f"Not enough distinct original-PDF candidates for {label}.")


def _ha_direct_candidates(
    facts: Sequence[dict[str, Any]],
    role: str,
    column: str,
    rows: Sequence[ParsedSourceRow],
) -> list[dict[str, Any]]:
    candidates = [
        fact
        for fact in facts
        if fact["family"] == "HA"
        and fact.get("role") == role
        and fact.get("column") == column
    ]
    if column == "날짜·접수값":
        candidates = [
            fact
            for fact in candidates
            if fact.get("qualifier")
            and any(
                row.row.fields.get("확인 상태") == "확인 완료"
                and row.row.fields.get("성격") in {"실제", "실제 증빙"}
                for row in rows
                if row.row.record_id == fact["source"]["record_id"]
            )
        ]
    return candidates


def _is_direct_candidates(facts: Sequence[dict[str, Any]], key_column: str, column: str) -> list[dict[str, Any]]:
    records_by_key: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if fact["family"] == "IS" and fact["qualifier"] in {"처리 기록", "기록번호"}:
            records_by_key[fact["key"]].add(fact["source"]["record_id"])
    return [
        fact
        for fact in facts
        if fact["family"] == "IS"
        and fact["qualifier"] == key_column
        and fact["column"] == column
        and len(records_by_key[fact["key"]]) == 1
    ]


def _cache_source_pages() -> dict[tuple[str, int], str]:
    path = PROJECT_ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
    if not path.is_file():
        return {}
    pages = json.loads(path.read_text(encoding="utf-8"))
    return {
        (Path(row["file_name"]).stem, row["page"]): row["text"]
        for row in pages
        if isinstance(row, Mapping)
        and isinstance(row.get("file_name"), str)
        and isinstance(row.get("page"), int)
        and isinstance(row.get("text"), str)
    }


def _validate_quote_coverage(references: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    restored = _cache_source_pages()
    if not restored:
        return 0, 0
    expected = found = 0
    for reference in references:
        for group in reference.get("evidence_groups", ()):
            for evidence in group:
                expected += 1
                page_text = restored.get((evidence["book_id"], evidence["page"]), "")
                found += int(_normalize(evidence["quote"]) in _normalize(page_text))
    return found, expected


def _narratives_by_page(
    rows: Sequence[ParsedSourceRow], family: str
) -> dict[tuple[str, int], list[ParsedSourceRow]]:
    grouped: dict[tuple[str, int], list[ParsedSourceRow]] = defaultdict(list)
    for item in rows:
        if (
            item.family == family
            and item.row.kind in {"narrative", "external_quotation"}
            and isinstance(item.row.text, str)
            and NARRATIVE_CUES.search(item.row.text)
        ):
            grouped[(item.source["file_name"], item.source["page"])].append(item)
    return grouped


def _is_narrative_near(
    narrative_index: Mapping[tuple[str, int], Sequence[ParsedSourceRow]],
    fact: Mapping[str, Any],
) -> ParsedSourceRow | None:
    source = fact["source"]
    start = source["line_start"]
    nearby = [
        item
        for item in narrative_index.get((source["file_name"], source["page"]), ())
        if min(
            abs(item.row.line_start - start), abs(item.row.line_end - start)
        ) <= 24
    ]
    ordered = _stable_order(
        [{"source": _source_with_row(item), "item": item} for item in nearby],
        f"is-narrative:{fact['key']}:{fact['source']['record_id']}",
    )
    return ordered[0]["item"] if ordered else None


def _is_topic(text: str) -> str:
    if re.search(r"\bVGM\b", text, re.IGNORECASE):
        return "VGM 확인 기록"
    if re.search(r"\bSI\b|선적지시서", text, re.IGNORECASE):
        return "SI 제출·정정 기록"
    if "예약" in text or "양하" in text or "반출" in text:
        return "예약과 현장 처리 기록"
    if "요청" in text or "회신" in text:
        return "요청·회신 처리 기록"
    return "업무 처리 기록"


def _direct_questions(
    facts: Sequence[dict[str, Any]], rows: Sequence[ParsedSourceRow]
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    used = {family: set() for family in FAMILIES}
    result: list[dict[str, Any]] = []

    for ordinal, (field, cost_kind) in enumerate(HG_DIRECT_SPECS, start=1):
        candidates = _direct_fact_candidates(facts, "HG", field, cost_kind)
        fact = _choose_distinct(candidates, f"hg-direct-{ordinal}", used["HG"])
        qid = f"sg-hg-direct-{ordinal:02d}"
        question = _make_direct_question("HG", [fact], ordinal)
        result.append(
            {
                "question": {"id": qid, "question": question},
                "reference": _reference_for(qid, "HG", "direct", [fact], (), rows)
                | {"expected_case_keys": [fact["key"]], "expected_record_keys": []},
            }
        )

    for ordinal, (role, column) in enumerate(HA_DIRECT_SPECS, start=1):
        candidates = _ha_direct_candidates(facts, role, column, rows)
        fact = _choose_distinct(candidates, f"ha-direct-{ordinal}", used["HA"])
        qid = f"sg-ha-direct-{ordinal:02d}"
        result.append(
            {
                "question": {"id": qid, "question": _make_direct_question("HA", [fact], ordinal)},
                "reference": _reference_for(qid, "HA", "direct", [fact], (), rows)
                | {"expected_case_keys": [fact["key"]], "expected_record_keys": []},
            }
        )

    for ordinal, (key_column, column) in enumerate(IS_DIRECT_SPECS, start=1):
        candidates = _is_direct_candidates(facts, key_column, column)
        fact = _choose_distinct(candidates, f"is-direct-{ordinal}", used["IS"])
        qid = f"sg-is-direct-{ordinal:02d}"
        result.append(
            {
                "question": {"id": qid, "question": _make_direct_question("IS", [fact], ordinal)},
                "reference": _reference_for(qid, "IS", "direct", [fact], (), rows)
                | {"expected_case_keys": [], "expected_record_keys": [fact["key"]]},
            }
        )
    return result, used


def _mixed_questions(
    facts: Sequence[dict[str, Any]],
    rows: Sequence[ParsedSourceRow],
    used: dict[str, set[str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    case_rows = {family: _case_rows(rows, family) for family in ("HG", "HA")}

    hg_candidates = []
    for fact in _direct_fact_candidates(facts, "HG", "billed_amount"):
        narrative = _narrative_for_case(
            case_rows["HG"],
            fact["key"],
            preferred_roles=("계약 조건 대조 회신",),
        )
        if narrative is not None:
            hg_candidates.append({"source": fact["source"], "fact": fact, "narrative": narrative})
    ha_candidates = []
    for fact in facts:
        if fact["family"] != "HA" or fact["field"] not in {"금액", "무료기간", "날짜·접수값"}:
            continue
        narrative = _narrative_for_case(case_rows["HA"], fact["key"])
        if narrative is not None:
            ha_candidates.append({"source": fact["source"], "fact": fact, "narrative": narrative})
    is_candidates = []
    for fact in facts:
        if fact["family"] != "IS":
            continue
        narrative = _is_narrative_near(rows, fact)
        if narrative is not None:
            is_candidates.append({"source": fact["source"], "fact": fact, "narrative": narrative})

    for family, candidates in (
        ("HG", hg_candidates),
        ("HA", ha_candidates),
        ("IS", is_candidates),
    ):
        for ordinal in range(1, ROUTE_COUNTS["mixed"] + 1):
            chosen = None
            for candidate in _stable_order(candidates, f"{family}-mixed-{ordinal}"):
                fact = candidate["fact"]
                key = str(fact["key"])
                if key not in used[family]:
                    chosen = candidate
                    used[family].add(key)
                    break
            if chosen is None:
                raise ValueError(f"Not enough independent {family} mixed-query sources.")
            fact = chosen["fact"]
            narrative = chosen["narrative"]
            qid = f"sg-{family.lower()}-mixed-{ordinal:02d}"
            result.append(
                {
                    "question": {"id": qid, "question": _make_mixed_question(family, fact)},
                    "reference": _reference_for(qid, family, "mixed", [fact], [narrative], rows)
                    | {
                        "expected_case_keys": [fact["key"]] if family != "IS" else [],
                        "expected_record_keys": [fact["key"]] if family == "IS" else [],
                    },
                }
            )
    return result


def _fallback_questions(
    rows: Sequence[ParsedSourceRow], used: dict[str, set[str]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    case_rows = {family: _case_rows(rows, family) for family in ("HG", "HA")}

    for family in ("HG", "HA"):
        eligible = []
        for key in sorted(case_rows[family]):
            if key in used[family]:
                continue
            preferred = (
                ("계약 조건 대조 회신",)
                if family == "HG"
                else ("비용별 정산 확인", "계약 조건 확인")
            )
            narrative = _narrative_for_case(case_rows[family], key, preferred_roles=preferred)
            if narrative is None:
                narrative = _narrative_for_case(case_rows[family], key)
            if narrative is not None:
                eligible.append({"key": key, "narrative": narrative, "source": _source_with_row(narrative)})
        selected = []
        for candidate in _stable_order(eligible, f"{family}-fallback"):
            if candidate["key"] not in used[family]:
                selected.append(candidate)
                used[family].add(candidate["key"])
            if len(selected) == ROUTE_COUNTS["fallback"]:
                break
        if len(selected) != ROUTE_COUNTS["fallback"]:
            raise ValueError(f"Not enough independent {family} fallback sources.")
        for ordinal, candidate in enumerate(selected, start=1):
            key, narrative = candidate["key"], candidate["narrative"]
            qid = f"sg-{family.lower()}-fallback-{ordinal:02d}"
            result.append(
                {
                    "question": {"id": qid, "question": _make_fallback_question(family, key, "")},
                    "reference": _reference_for(qid, family, "fallback", [], [narrative], rows)
                    | {"expected_case_keys": [key], "expected_record_keys": []},
                }
            )

    is_narratives = [
        item
        for page_rows in _narratives_by_page(rows, "IS").values()
        for item in page_rows
    ]
    unique = {}
    for item in is_narratives:
        page_key = (item.source["file_name"], item.source["page"])
        unique.setdefault(page_key, item)
    eligible_is = []
    for item in unique.values():
        topic = _is_topic(item.row.text)
        if topic == "업무 처리 기록":
            continue
        eligible_is.append({"source": _source_with_row(item), "item": item, "topic": topic})
    chosen_is = _stable_order(eligible_is, "IS-fallback")[: ROUTE_COUNTS["fallback"]]
    if len(chosen_is) != ROUTE_COUNTS["fallback"]:
        raise ValueError("Not enough independent IS fallback pages.")
    for ordinal, candidate in enumerate(chosen_is, start=1):
        item, topic = candidate["item"], candidate["topic"]
        qid = f"sg-is-fallback-{ordinal:02d}"
        result.append(
            {
                "question": {"id": qid, "question": _make_fallback_question("IS", None, topic)},
                "reference": _reference_for(qid, "IS", "fallback", [], [item], rows)
                | {"expected_case_keys": [], "expected_record_keys": []},
            }
        )
    return result


def _build_records(rows: Sequence[ParsedSourceRow]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts = _facts_for(rows)
    direct, used = _direct_questions(facts, rows)
    mixed = _mixed_questions(facts, rows, used)
    fallback = _fallback_questions(rows, used)
    items = [*direct, *mixed, *fallback]
    questions = [item["question"] for item in items]
    references = [item["reference"] for item in items]
    expected_counts = Counter((row["family"], row["route_class"]) for row in references)
    if len(questions) != 30 or len({row["id"] for row in questions}) != 30:
        raise ValueError("The private typed-gold set must contain 30 unique questions.")
    for family in FAMILIES:
        for route, expected_count in ROUTE_COUNTS.items():
            if expected_counts[family, route] != expected_count:
                raise ValueError(f"Typed-gold count mismatch for {family}/{route}.")
    return questions, references


def _validate_dataset(
    output: Path,
    questions: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    question_ids = [row.get("id") for row in questions]
    reference_ids = [row.get("id") for row in references]
    if len(question_ids) != 30 or len(set(question_ids)) != 30 or question_ids != reference_ids:
        raise ValueError("Private typed-gold question/reference IDs do not match.")
    counts = Counter((row["family"], row["route_class"]) for row in references)
    for family in FAMILIES:
        if any(counts[family, route] != count for route, count in ROUTE_COUNTS.items()):
            raise ValueError(f"Typed-gold split is unbalanced for {family}.")
    if any(not isinstance(row.get("question"), str) or not row["question"].strip() for row in questions):
        raise ValueError("A question is empty.")

    source_keys = set()
    for reference in references:
        if reference["route_class"] == "direct" and not reference["expected_facts"]:
            raise ValueError("Direct questions need at least one typed gold fact.")
        if reference["route_class"] == "mixed" and (not reference["expected_facts"] or not reference["expected_rag_sources"]):
            raise ValueError("Mixed questions need typed facts and RAG sources.")
        if reference["route_class"] == "fallback" and (reference["expected_facts"] or not reference["expected_rag_sources"]):
            raise ValueError("Fallback questions need RAG sources and no typed fact prediction.")
        for fact in reference["expected_facts"]:
            source = fact["source"]
            if source.get("pdf_sha256") is None or source.get("line_start", 0) < 1 or source.get("line_end", 0) < source.get("line_start", 1):
                raise ValueError("Typed fact is missing a verified original-PDF pointer.")
            source_keys.add((source["file_name"], source["page"]))
        for source in reference["expected_sources"]:
            source_keys.add((source["file_name"], source["page"]))
    return {
        "questions": len(questions),
        "references": len(references),
        "by_family_route": {
            f"{family}/{route}": counts[family, route]
            for family in FAMILIES
            for route in ROUTE_COUNTS
        },
        "questions_have_no_source_values": True,
        "facts": sum(len(row["expected_facts"]) for row in references),
        "expected_rag_source_rows": sum(len(row["expected_rag_sources"]) for row in references),
        "unique_original_pages": len(source_keys),
        "source_extraction": "pdftotext -layout original PDFs; app.ingestion.curation.split_page",
        "database_or_structured_exports_read": False,
        "api_calls": 0,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output = output.resolve()
    artifacts_root = (PROJECT_ROOT / "artifacts").resolve()
    if not output.is_relative_to(artifacts_root):
        raise ValueError("Private question and gold data must be written under ignored artifacts/.")
    if output.exists():
        raise FileExistsError(f"Existing private data is preserved: {output}")
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="aden-structured-gold-"):
        rows, pdf_hashes = _scan_original_pdfs()
    questions, references = _build_records(rows)
    summary = _validate_dataset(output, questions, references)
    quote_found, quote_expected = _validate_quote_coverage(references)
    if quote_expected and quote_found != quote_expected:
        raise ValueError("Some original-PDF evidence quotes do not match the restored page cache.")
    output.mkdir(parents=True)
    _write_json(output / "questions.json", questions)
    _write_json(output / "references.json", references)
    manifest = {
        "dataset_version": DATASET_VERSION,
        "selection_plan_sha256": _file_sha(PLAN_PATH),
        "extractor": _extractor_version(),
        "pdf_sha256": pdf_hashes,
        "question_sha256": _file_sha(output / "questions.json"),
        "reference_sha256": _file_sha(output / "references.json"),
        "summary": summary,
        "restored_page_quote_check": {"matched": quote_found, "expected": quote_expected},
        "selection": plan["selection"],
    }
    _write_json(output / "manifest.json", manifest)
    validation = validate(output, write_report=False)
    _write_json(output / "validation.json", validation)
    return validation


def validate(output: Path = DEFAULT_OUTPUT, *, write_report: bool = True) -> dict[str, Any]:
    output = output.resolve()
    questions = json.loads((output / "questions.json").read_text(encoding="utf-8"))
    references = json.loads((output / "references.json").read_text(encoding="utf-8"))
    json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    summary = _validate_dataset(output, questions, references)

    selected_files: set[str] = set()
    page_filter: set[tuple[str, int]] = set()
    expected_pdfs: dict[str, str] = {}
    for reference in references:
        for fact in reference["expected_facts"]:
            source = fact["source"]
            selected_files.add(source["file_name"])
            page_filter.add((source["book_id"], source["page"]))
            expected_pdfs[source["file_name"]] = source["pdf_sha256"]
        for source in reference["expected_sources"]:
            selected_files.add(source["file_name"])
            page_filter.add((source["book_id"], source["page"]))
            expected_pdfs[source["file_name"]] = source["pdf_sha256"]
    original_rows, actual_pdf_hashes = _scan_original_pdfs(
        selected_files=selected_files, page_filter=page_filter
    )
    if any(actual_pdf_hashes.get(name) != digest for name, digest in expected_pdfs.items()):
        raise ValueError("Selected original PDF hash changed since gold authoring.")
    original_facts = _facts_for(original_rows)
    original_fact_map: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for fact in original_facts:
        original_fact_map[_fact_identity(fact)].append(fact)
    checked_facts = 0
    for reference in references:
        for expected in reference["expected_facts"]:
            candidates = original_fact_map.get(_fact_identity(expected), ())
            exact_source = [
                fact for fact in candidates
                if _source_identity(fact.get("source")) == _source_identity(expected.get("source"))
            ]
            if len(exact_source) != 1:
                raise ValueError("Expected typed fact is not reproduced by its selected original-PDF row.")
            scored = score_typed_facts([expected], exact_source)
            if not scored["exact"]:
                raise ValueError("Original-PDF typed fact does not match its private gold label.")
            checked_facts += 1

    cache = _cache_source_pages()
    quote_count = cache_match = 0
    for reference in references:
        for group in reference.get("evidence_groups", ()):
            for evidence in group:
                quote_count += 1
                page = cache.get((Path(evidence["file_name"]).stem, evidence["page"]), "")
                cache_match += int(bool(page) and _normalize(evidence["quote"]) in _normalize(page))
    report = {
        **summary,
        "original_pdf_facts_rechecked": checked_facts,
        "original_pdf_hashes_rechecked": len(actual_pdf_hashes),
        "restored_page_quote_match": {"matched": cache_match, "expected": quote_count},
        "all_gold_values_rederived_from_original_pdf": True,
        "all_selected_pdf_hashes_match": True,
    }
    if write_report:
        _write_json(output / "validation.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="원본 PDF에서 비공개 질문·정답을 생성")
    build_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    validate_parser = subparsers.add_parser("validate", help="원본 PDF와 비공개 정답을 재검증")
    validate_parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output) if args.command == "build" else validate(args.input)
    print(
        json.dumps(
            {
                "questions": result["questions"],
                "facts": result["facts"],
                "by_family_route": result["by_family_route"],
                "original_pdf_facts_rechecked": result.get("original_pdf_facts_rechecked", 0),
                "restored_page_quote_match": result.get("restored_page_quote_match"),
                "api_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
