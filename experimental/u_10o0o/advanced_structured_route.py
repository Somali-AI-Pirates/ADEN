"""Verify proposed Advanced lookups and assemble evidence entirely locally."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.generation.context import build_context
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import ROLES, hg_ids
from experimental.u_10o0o.advanced_format_extension import HA_ROLES, HANDBOOK, ha_ids
from experimental.u_10o0o.advanced_question_router import (
    EXCLUSION_CUE,
    _model_field_values,
    _uncovered_exclusion_cues,
)
from experimental.u_10o0o.advanced_search import DEFAULT_CONTEXT_TOKENS, FINAL_K
from experimental.u_10o0o.advanced_target_lookup import INVALID_CONSTRAINT, normalized
from experimental.u_10o0o.context_evidence import _count_tokens
from shared.contracts import Chunk, RetrievedChunk

FIELD_PATTERNS = {
    "actual_discharge_date": (1, r"실제\s*양하(?:\s*(?:날짜|일자|일))?|양하일"),
    "actual_gate_out_date": (
        1,
        r"실제\s*(?:적입\s*장비\s*)?반출(?:\s*(?:날짜|일자|일))?|반출일",
    ),
    "actual_designated_return_date": (
        1,
        r"(?:실제\s*(?:(?:지정\s*(?:장치장\s*)?)|(?:장비\s*))?|지정\s*장치장\s*)"
        r"반납(?:\s*(?:날짜|일자|일))?",
    ),
    "scheduled_return_date": (
        1,
        r"반납\s*(?:예약\s*)?예정(?:\s*(?:날짜|일자|일))?|예정\s*반납(?:일)?",
    ),
    "system_entry_date": (1, r"전산\s*입력(?:\s*(?:날짜|일자|일))?"),
    "approval_record": (2, r"승인\s*(?:상태|기록|여부)|서명\s*(?:상태|여부)"),
    "comparison_record_id": (2, r"대조\s*기록\s*번호|회신\s*기록\s*번호"),
    "recorded_amendment_effective_date": (
        2,
        r"(?:변경|정정|개정)\s*효력(?:\s*(?:날짜|일자|일))",
    ),
    "base_free_days": (
        0,
        r"(?:기본|약정|계약(?:상)?)\s*(?:무료\s*(?:기간|일수)|무상\s*일수)",
    ),
    "billed_amount": (3, r"(?:확정\s*)?청구\s*(?:금액|액)|청구(?:한)?\s*비용"),
    "billed_free_days": (3, r"청구(?:상|서(?:의)?)?\s*무료\s*(?:기간|일수)"),
    "billed_paid_days": (3, r"(?:청구(?:상|서(?:의)?)?\s*)?유료\s*일수"),
    "billed_included_days": (3, r"산입\s*일수"),
    "receipt_total": (4, r"거래\s*합계|수납\s*총액"),
    "received_amount": (4, r"수납\s*(?:금액|액)|입금액|받은\s*금액"),
    "refund_review_amount": (4, r"환급\s*(?:검토\s*)?(?:금액|액)"),
    "balance_amount": (4, r"미수\s*또는\s*환급|미수(?:금|액)|미결\s*(?:금액|잔액)"),
}
PATTERNS = {
    name: (ROLES[i], re.compile(pattern))
    for name, (i, pattern) in FIELD_PATTERNS.items()
}
COST_FIELDS = frozenset(
    {
        "base_free_days",
        "billed_amount",
        "billed_free_days",
        "billed_paid_days",
        "billed_included_days",
    }
)
MONEY_FIELDS = frozenset(
    {
        "billed_amount",
        "receipt_total",
        "received_amount",
        "refund_review_amount",
        "balance_amount",
    }
)
COSTS = {
    "storage": re.compile(r"보관료"),
    "late_return": re.compile(r"(?:장비\s*)?반납\s*지연료"),
}
IS_COLUMNS = {key: columns - {key} for columns, key in HANDBOOK.items()}
HA_COLUMNS = (
    "기재 내용",
    "무료기간",
    "첫 구간 단가",
    "첫 유료구간",
    "다음 구간",
    "기록",
    "날짜·접수값",
    "확인 상태",
    "금액",
    "처리 근거",
)
HA_QUALIFIERS = {
    HA_ROLES[0]: ("항목", "비용"),
    HA_ROLES[1]: ("확인 항목", "기록"),
    HA_ROLES[2]: ("항목",),
}
DOCUMENT_CUES = re.compile(
    r"예외|적용\s*(?:이유|사유|조건|기준|규정)|업무\s*(?:절차|규정)|"
    r"관련\s*(?:규정|절차)|근거\s*(?:규정|조항)|왜|어떤\s*규정"
)
QUESTION_WORDS = re.compile(
    r"예약번호|예약\s*번호|예약\s*건|예약|사건\s*번호|사건|건에서|건|에서|자료집의|"
    r"값(?:을|은|이)?|무엇(?:인가요|인가|인지)?|뭔가요|뭐야|언제(?:인가요|인지)?|"
    r"얼마(?:인가요|인지|야)?|며칠(?:인가요|인지)?|알려\s*(?:줘|주세요)|"
    r"확인해\s*(?:줘|주세요)|보여\s*(?:줘|주세요)|말해\s*줘|기재된|적힌|"
    r"어떻게\s*기록돼\s*있어|기록을|기록된|행에서|행의|"
    r"로\s*기록(?:됐어|되었나요|되어\s*있나요)|기록됐어|인가요|인가|인지|"
    r"각각|그리고|와|과|및|의|은|는|이|가|를|을|에|도|요|[\s,?.:()'\"·]+"
)
SAFE_STATES = frozenset(
    {
        "reported",
        "pending",
        "unconfirmed",
        "uncomputed",
        "blank",
        "not_applicable",
        "receipt_missing",
        "non_designated_yard_only",
    }
)


@dataclass(frozen=True)
class Target:
    family: str
    anchor: str
    constraints: tuple = ()
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fact:
    family: str
    key: str
    role: str | None
    field: str
    cost_kind: str | None
    value: str | int | None
    state: str
    unit: str | None
    currency: str | None
    source: object
    column: str
    qualifier: str | None = None
    value_type: str = "text"

    @property
    def location(self):
        return self.source.book_id, self.source.page


@dataclass(frozen=True)
class Decision:
    status: str
    facts: tuple[Fact, ...] = ()
    targets: tuple[tuple[str, tuple[str, ...]], ...] = ()
    queries: int = 0

    @property
    def skip_search(self):
        return self.status == "db_only"


def target_family(text):
    if hg_ids(text):
        return "HG"
    if ha_ids(text):
        return "HA"
    if any(key in text for key in IS_COLUMNS) and identifiers(text):
        return "IS"
    return "HG"


def rule_targets(question):
    # Splitting only on clear coordinators avoids assigning a field to a neighbor.
    parts = re.split(r"\s+(?:그리고|및)\s+|[,;]\s*", question)
    keyed = [part.strip() for part in parts if identifiers(part)]
    if len(keyed) == 2:
        return tuple(Target(target_family(part), part) for part in keyed)
    return (Target(target_family(question), question),)


def _hg_constraints(anchor, constraints, lookup):
    if lookup is None:
        return {}, {}, False
    parsed = lookup._matched_exact_fields(anchor)
    if INVALID_CONSTRAINT in parsed:
        return {}, {}, False
    positive = {field: set(values) for field, values in parsed.items()}
    excluded = {}
    for constraint in constraints:
        field, raw = constraint.field, constraint.raw
        if raw not in anchor:
            return {}, {}, False
        values = _model_field_values(field, raw, anchor, lookup)
        if field == "equipment_id":
            values = {normalized(value) for value in values}
        if len(values) != 1:
            return {}, {}, False
        if constraint.polarity == "exclude":
            if not re.search(re.escape(raw) + EXCLUSION_CUE.pattern, anchor):
                return {}, {}, False
            excluded.setdefault(field, set()).update(values)
            positive.get(field, set()).difference_update(values)
        else:
            positive.setdefault(field, set()).update(values)
    direct = hg_ids(anchor) - excluded.get("case_id", set())
    if direct:
        positive.setdefault("case_id", set()).update(direct)
    for raw in identifiers(anchor):
        if raw in direct or raw in excluded.get("case_id", ()):
            continue
        fields = lookup.id_fields.get(raw, set())
        if not fields and raw not in hg_ids(anchor):
            return {}, {}, False
        for field in fields - {"case_id"}:
            value = normalized(raw) if field == "equipment_id" else raw
            if value not in excluded.get(field, ()):
                positive.setdefault(field, set()).add(value)
    positive = {key: values for key, values in positive.items() if values}
    valid = all(len(values) == 1 for values in positive.values())
    return (
        {field: next(iter(values)) for field, values in positive.items()},
        excluded,
        valid,
    )


def _covered(span, requested, match):
    if not requested:
        return True
    return any(
        match.group() in phrase or phrase in match.group()
        for phrase in requested
        if phrase in span
    )


def _hg_fields(anchor, phrases):
    chosen = []
    for code, (role, pattern) in PATTERNS.items():
        match = pattern.search(anchor)
        if match and _covered(anchor, phrases, match):
            chosen.append((code, role, match.group()))
    # A scheduled phrase must not imply an actual return date.
    return chosen


def _field_grounded(anchor, phrases, column):
    if column not in anchor:
        return False
    if phrases:
        return any(column in phrase for phrase in phrases)
    quoted = r"[\"'‘’“”\x60]"
    return bool(
        re.search(
            quoted
            + re.escape(column)
            + quoted
            + r"|"
            + re.escape(column)
            + r"\s*(?:값|[은는을를이가](?:\s|[?!.]|$)|[?]|$)",
            anchor,
        )
    )


def _typed_cell(raw, column):
    raw = raw.strip()
    states = {
        "미결": "pending",
        "미확정": "unconfirmed",
        "미산출": "uncomputed",
        "해당 없음": "not_applicable",
        "해당없음": "not_applicable",
        "공란": "blank",
        "실제 인수증 미수신": "receipt_missing",
        "지정 외 야드 반입증만 있음": "non_designated_yard_only",
        "검토 필요": "needs_review",
    }
    if raw in states:
        return None, states[raw], None, None, "null"
    if not raw or raw in {"-", "—"}:
        return None, "needs_review", None, None, "null"
    if column == "금액":
        if match := re.fullmatch(
            r"(?:(USD|KRW|EUR)\s*)?(-?[\d,]+(?:\.\d+)?)(?:\s*(USD|KRW|EUR))?", raw
        ):
            before, number, after = match.groups()
            if before and after:
                return None, "needs_review", None, None, "null"
            if re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", number):
                return (
                    format(Decimal(number.replace(",", "")), "f"),
                    "reported",
                    None,
                    before or after,
                    "decimal",
                )
        return None, "needs_review", None, None, "null"
    if column == "무료기간" and (match := re.fullmatch(r"(?:기본\s*)?(\d+)\s*일", raw)):
        return int(match[1]), "reported", "day", None, "integer"
    if column == "날짜·접수값" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat(), "reported", None, None, "date"
        except ValueError:
            return None, "needs_review", None, None, "null"
    return raw, "reported", None, None, "text"


def _remove(text, terms):
    for term in sorted(set(terms), key=len, reverse=True):
        if term:
            text = text.replace(term, " ")
    return text


def _hg(anchor, phrases, constraints, lookup, store):
    wanted = _hg_fields(anchor, phrases)
    if not wanted:
        return "unresolved", [], None, 0, ()
    explicit_roles = {role for role in ROLES if role in anchor}
    if explicit_roles and any(role not in explicit_roles for _, role, _ in wanted):
        return "conflict", [], None, 0, ()
    positive, excluded, valid = _hg_constraints(anchor, constraints, lookup)
    if not valid:
        return "conflict", [], None, 0, ()
    if "case_id" not in positive and len(positive) < 2:
        return "unresolved", [], None, 0, ()
    cases = set(store.profile_candidates("HG", filters=positive))
    queries = 1
    for field, values in excluded.items():
        for value in values:
            cases.difference_update(
                store.profile_candidates("HG", filters={field: value})
            )
            queries += 1
    if len(cases) != 1:
        return ("ambiguous" if cases else "not_found"), [], None, queries, ()
    case = next(iter(cases))
    costs = [kind for kind, pattern in COSTS.items() if pattern.search(anchor)]
    result = []
    consumed = [matched for _, _, matched in wanted] + list(positive.values())
    consumed.extend(role for _, role, _ in wanted if role in anchor)
    for kind in costs:
        consumed.extend(match.group() for match in COSTS[kind].finditer(anchor))
    roles = tuple(dict.fromkeys(role for _, role, _ in wanted))
    status = "found"
    for code, role, _ in wanted:
        if code in COST_FIELDS and len(costs) != 1:
            status = "partial"
            continue
        cost = costs[0] if code in COST_FIELDS else None
        matches = store.get_hg_facts(case, fact_code=code, role=role, cost_kind=cost)
        queries += 1
        if len(matches) != 1 or matches[0].state not in SAFE_STATES:
            status = "partial"
            continue
        row = matches[0]
        if (
            row.state == "reported"
            and code in MONEY_FIELDS
            and (row.value_type != "decimal" or row.currency is None)
        ):
            status = "partial"
            continue
        value = row.value_int if row.value_int is not None else row.value_text
        if row.state == "reported" and value is None:
            status = "partial"
            continue
        result.append(
            Fact(
                "HG",
                case,
                role,
                code,
                cost,
                value,
                row.state,
                row.unit,
                row.currency,
                row.source,
                row.column_label,
                value_type=row.value_type if row.state == "reported" else "null",
            )
        )
    return status, result, (case, roles), queries, consumed


def _row_facts(family, key, role, row, columns, qualifier=None):
    facts = []
    for column in columns:
        value, state, unit, currency, value_type = _typed_cell(
            row.fields[column], column
        )
        if state in SAFE_STATES:
            facts.append(
                Fact(
                    family,
                    key,
                    role,
                    column,
                    None,
                    value,
                    state,
                    unit,
                    currency,
                    row.source,
                    column,
                    qualifier,
                    value_type,
                )
            )
    return facts


def _ha(anchor, phrases, store):
    keys = ha_ids(anchor)
    roles = [role for role in HA_ROLES if role in anchor]
    if len(keys) != 1 or set(identifiers(anchor)) != keys or len(roles) != 1:
        return "ambiguous" if len(keys) > 1 else "unresolved", [], 0, ()
    key, role = next(iter(keys)), roles[0]
    # Ignore a generic column word when it is part of a longer requested column.
    columns = [
        col
        for col in HA_COLUMNS
        if _field_grounded(anchor.replace(role, ""), phrases, col)
    ]
    columns = [
        col
        for col in columns
        if not any(col != other and col in other for other in columns)
    ]
    if not columns:
        return "unresolved", [], 0, ()
    remainder = _remove(anchor, (key, role, *columns))
    matches = {}
    for row in store.get_role_rows("HA", key, role):
        for column in HA_QUALIFIERS[role]:
            value = row.fields.get(column)
            if value and value in remainder and set(columns) <= row.fields.keys():
                matches[row.record_id] = row, value
    if len(matches) != 1:
        return ("ambiguous" if matches else "not_found"), [], 1, ()
    row, qualifier = next(iter(matches.values()))
    facts = _row_facts("HA", key, role, row, columns, qualifier)
    return (
        ("found" if len(facts) == len(columns) else "partial"),
        facts,
        1,
        (key, role, qualifier, *columns),
    )


def _is(anchor, phrases, store):
    keys = identifiers(anchor)
    labels = [label for label in IS_COLUMNS if label in anchor]
    if len(keys) != 1 or len(labels) != 1:
        return ("ambiguous" if len(keys) > 1 else "unresolved"), [], 0, ()
    key, label = keys[0], labels[0]
    columns = sorted(
        col for col in IS_COLUMNS[label] if _field_grounded(anchor, phrases, col)
    )
    if not columns:
        return "unresolved", [], 0, ()
    matches = store.lookup_handbook(label, key)
    if len(matches) != 1:
        return ("ambiguous" if matches else "not_found"), [], 1, ()
    facts = _row_facts("IS", key, None, matches[0], columns, label)
    return (
        ("found" if len(facts) == len(columns) else "partial"),
        facts,
        1,
        (key, label, *columns),
    )


def verify(question, lookup, store, *, proposal=None, rules=False):
    """The model may propose a lookup, while the server verifies every field and target."""
    if not rules and (proposal is None or proposal.route not in {"lookup", "mixed"}):
        return Decision("search")
    targets = rule_targets(question) if rules else proposal.targets
    if not targets or len(targets) > 2:
        return Decision("search")
    if EXCLUSION_CUE.search(question) and (
        rules
        or any(t.family != "HG" for t in targets)
        or _uncovered_exclusion_cues(question, targets)
    ):
        return Decision("conflict")
    occupied = set()
    for target in targets:
        anchor = target.anchor
        if (
            not anchor
            or question.count(anchor) != 1
            or any(not phrase or phrase not in anchor for phrase in target.fields)
        ):
            return Decision("invalid")
        start = question.index(anchor)
        span = set(range(start, start + len(anchor)))
        if occupied & span:
            return Decision("conflict")
        occupied.update(span)
        if target_family(anchor) != target.family:
            return Decision("invalid")
        if target.family in {"HA", "IS"}:
            key_field = "case_id" if target.family == "HA" else "record_id"
            if any(
                constraint.field != key_field
                or constraint.polarity != "include"
                or constraint.raw not in identifiers(anchor)
                for constraint in target.constraints
            ):
                return Decision("invalid")
    unbound = "".join(char for i, char in enumerate(question) if i not in occupied)
    if identifiers(unbound) or hg_ids(unbound) or ha_ids(unbound):
        return Decision("conflict")

    facts, verified, statuses, leftover = [], [], [], []
    queries = 0
    before_sql = getattr(store, "query_count", None)
    for target in targets:
        if target.family == "HG":
            status, found, identity, count, consumed = _hg(
                target.anchor, target.fields, target.constraints, lookup, store
            )
            if identity:
                verified.append(identity)
        else:
            read = _ha if target.family == "HA" else _is
            status, found, count, consumed = read(target.anchor, target.fields, store)
        statuses.append(status)
        facts.extend(found)
        queries += count
        leftover.append(_remove(target.anchor, consumed))

    if before_sql is not None:
        queries = store.query_count - before_sql

    if {"ambiguous", "conflict"} & set(statuses):
        return Decision(
            "conflict" if "conflict" in statuses else "ambiguous", queries=queries
        )
    unique = tuple(dict.fromkeys(facts))
    all_grounded = not QUESTION_WORDS.sub("", unbound + " ".join(leftover)).strip()
    direct = (
        unique
        and set(statuses) == {"found"}
        and all_grounded
        and not DOCUMENT_CUES.search(question)
        and (rules or proposal.route == "lookup")
        and len({fact.location for fact in unique}) <= FINAL_K
    )
    if direct:
        status = "db_only"
    elif unique:
        status = "mixed"
    elif "partial" in statuses:
        status = "insufficient_evidence"
    elif "not_found" in statuses:
        status = "not_found"
    else:
        status = "unresolved"
    return Decision(status, unique, tuple(verified), queries)


def _fact_text(fact):
    values = " ".join(
        str(v) for v in (fact.value, fact.unit, fact.currency) if v is not None
    )
    line = f"원본 줄 {fact.source.line_start}–{fact.source.line_end}"
    target = " · ".join(
        part
        for part in (fact.family, fact.key, fact.role, fact.cost_kind, fact.qualifier)
        if part
    )
    return (
        f"{target} | {fact.field} | {fact.state} | {values} | {line} | 열 {fact.column}"
    )


def assemble(decision, original=None):
    """Use one source slot per page and keep structured values separate from PDF text."""
    if not decision.facts and original is not None:
        return original, ()
    grouped = defaultdict(list)
    for fact in decision.facts:
        grouped[fact.location].append(fact)
    doc_chunks = (
        {(item.chunk.book_id, item.chunk.page): item for item in original.final_chunks}
        if original is not None
        else {}
    )
    candidates, structured_chunks, fact_only = [], {}, {}
    for i, (location, facts) in enumerate(grouped.items(), 1):
        first = facts[0].source
        chunk_id = f"structured:{first.book_id}:{first.page}"
        chunk = Chunk(
            chunk_id,
            first.book_id,
            first.file_name,
            first.page,
            "구조화 근거\n" + "\n".join(map(_fact_text, facts)),
        )
        fact_only[chunk_id] = RetrievedChunk(chunk, i, 0.0, "sqlite")
        candidates.append(fact_only[chunk_id])
        structured_chunks[chunk_id] = facts
        if doc := doc_chunks.pop(location, None):
            chunk = Chunk(
                chunk_id,
                first.book_id,
                first.file_name,
                first.page,
                chunk.text + "\n\n원본 문서 근거\n" + doc.chunk.text,
            )
            candidates[-1] = RetrievedChunk(chunk, i, doc.score, "sqlite+document")
    remaining_documents = list(doc_chunks.values())
    if remaining_documents and not any(
        c.retriever == "sqlite+document" for c in candidates
    ):
        # Give the highest-ranked original one slot before extra structured pages.
        candidates[1:1] = [remaining_documents.pop(0)]
    candidates.extend(remaining_documents)
    context = build_context(candidates, FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens)
    packed_ids = {chunk.chunk.chunk_id for chunk in context.final_chunks}
    if any(
        c.retriever == "sqlite+document" and c.chunk.chunk_id not in packed_ids
        for c in candidates
    ):
        # Full-page text may be too large even though the requested values fit.
        candidates = [
            fact_only[c.chunk.chunk_id]
            if c.retriever == "sqlite+document" and c.chunk.chunk_id not in packed_ids
            else c
            for c in candidates
        ]
        context = build_context(
            candidates, FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens
        )
    if (
        original is not None
        and original.final_chunks
        and all(c.retriever == "sqlite" for c in context.final_chunks)
    ):
        return original, ()
    packed = tuple(
        fact
        for chunk in context.final_chunks
        for fact in structured_chunks.get(chunk.chunk.chunk_id, ())
    )
    return context, packed
