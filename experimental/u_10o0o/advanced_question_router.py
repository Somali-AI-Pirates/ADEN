"""Question-only interpretation and guarded SQLite-first HG lookups."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.generation.context import build_context
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import ROLES, hg_ids
from experimental.u_10o0o.advanced_pair_context import (
    CANDIDATES,
    FINAL_K,
    pack_pair_context,
)
from experimental.u_10o0o.advanced_target_lookup import (
    DATE_FACTS,
    INVALID_CONSTRAINT,
    ROLE_CUES,
    _canonical_date,
    normalized,
)
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _source_key,
)
from shared.contracts import Chunk, ContextBundle, RetrievedChunk

POLARITIES = ("include", "exclude")
ROUTES = ("lookup", "documents", "mixed", "unresolved")
EXCLUSION_CUE = re.compile(r"(?:을|를|이|가|은|는)?\s*(?:제외|말고|빼고|아닌)")
NON_SIMPLE_CUES = re.compile(
    r"예외|이유|사유|적용|정정|변경|개정|추가|때문|왜|비교|대비|포함|"
    r"제외|말고|각각|양쪽|두\s*(?:건|사건)|그리고|및|함께|예정|실제|"
    r"날짜|일자|맞는지|원인|발생|전산|입력|기간|산출|청구액|금액|총액"
)


class InterpretationConstraint(BaseModel):
    """A literal value copied from the target's question span."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field: str = Field(min_length=1, max_length=64)
    raw: str = Field(min_length=1, max_length=160)
    polarity: Literal["include", "exclude"] = "include"


class InterpretedTarget(BaseModel):
    """One target and only the records requested for that target."""

    model_config = ConfigDict(extra="forbid", strict=True)

    anchor: str = Field(min_length=1, max_length=500)
    constraints: list[InterpretationConstraint] = Field(
        default_factory=list, max_length=8
    )
    roles: list[str] = Field(min_length=1, max_length=len(ROLES))


class QuestionInterpretation(BaseModel):
    """Strict structured output expected from the interpretation provider."""

    model_config = ConfigDict(extra="forbid", strict=True)

    route: Literal["lookup", "documents", "mixed", "unresolved"]
    targets: list[InterpretedTarget] = Field(default_factory=list, max_length=2)


class InterpretationProvider(Protocol):
    """Provider must enforce the supplied deadline at its network call."""

    def invoke(
        self, question: str, *, timeout_seconds: float, rules_version: str | None
    ) -> Any: ...


class RuleMatcher(Protocol):
    version_id: str
    content_sha256: str

    def match_target_roles(self, text: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class RouteTarget:
    case_id: str
    roles: tuple[str, ...]
    pages: tuple[dict[str, Any], ...]
    anchor: str
    matched_fields: tuple[str, ...]


@dataclass(frozen=True)
class RouteDecision:
    route: str
    status: str
    targets: tuple[RouteTarget, ...] = ()
    candidate_case_ids: tuple[str, ...] = ()
    query_required: bool = True
    provider_called: bool = False
    reason: str | None = None
    rule_version: str | None = None
    rule_sha256: str | None = None

    @property
    def targets_by_case(self) -> dict[str, tuple[str, ...]]:
        roles_by_case: dict[str, set[str]] = {}
        for target in self.targets:
            roles_by_case.setdefault(target.case_id, set()).update(target.roles)
        return {
            case_id: tuple(role for role in ROLES if role in roles)
            for case_id, roles in roles_by_case.items()
        }


@dataclass(frozen=True)
class FastLookupResult:
    status: str
    order: tuple[tuple[dict[str, Any], float], ...] | None = None
    context: ContextBundle | None = None
    case_id: str | None = None
    roles: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def search_skipped(self) -> bool:
        return (
            self.status == "ready"
            and self.order is not None
            and self.context is not None
        )


@dataclass(frozen=True)
class _TargetMatch:
    status: Literal["verified", "ambiguous", "unresolved", "conflict"]
    target: RouteTarget | None = None
    candidates: tuple[str, ...] = ()
    matched_fields: tuple[str, ...] = ()


def _active_rules(lookup: Any, rules: RuleMatcher | None) -> RuleMatcher | None:
    return rules if rules is not None else getattr(lookup, "rules", None)


def _explicit_roles(
    question: str, lookup: Any, rules: RuleMatcher | None
) -> tuple[str, ...]:
    active_rules = _active_rules(lookup, rules)
    if active_rules is not None:
        matched = active_rules.match_target_roles(question)
    else:
        matched = tuple(role for role in ROLES if ROLE_CUES[role].search(question))
    return tuple(role for role in ROLES if role in matched)


def _model_field_values(field: str, raw: str, anchor: str, lookup: Any) -> set[str]:
    if field == "case_id":
        values = set(identifiers(raw))
        return {
            value
            for value in values
            if value in lookup.id_to_cases
            and "case_id" in lookup.id_fields.get(value, set())
            and raw == value
        }
    if field not in lookup.field_to_cases:
        return set()
    if field in DATE_FACTS:
        value = _canonical_date(raw)
        parsed = lookup._matched_exact_fields(anchor)
        if INVALID_CONSTRAINT in parsed:
            return set()
        typed_fields = {
            date_field
            for date_field in DATE_FACTS
            if value and value in parsed.get(date_field, set())
        }
        return {value} if typed_fields == {field} else set()

    profile_values = lookup._matched_profile_values(anchor)
    if field in profile_values:
        value = normalized(raw)
        return {value} if value in profile_values[field] else set()

    identifier_values = set(identifiers(raw))
    if len(identifier_values) != 1:
        return set()
    value = next(iter(identifier_values))
    fields = lookup.id_fields.get(value, set())
    if field not in fields:
        return set()
    return {value}


def _cases_for_constraint(field: str, value: str, lookup: Any) -> set[str]:
    if field == "case_id":
        return set(lookup.id_to_cases[value])
    return set(lookup.field_to_cases[field][value])


def _case_sets(
    target: InterpretedTarget, lookup: Any
) -> tuple[dict[str, set[str]], set[str], str | None]:
    if target.anchor != target.anchor.strip():
        return {}, set(), "invalid_anchor"
    positives: dict[str, set[str]] = {}
    positive_values: dict[str, set[str]] = {}
    exclusions: set[str] = set()
    for constraint in target.constraints:
        if constraint.raw not in target.anchor:
            return {}, set(), "constraint_outside_anchor"
        if (
            constraint.field not in lookup.field_to_cases
            and constraint.field != "case_id"
        ):
            return {}, set(), "unsupported_constraint"
        values = _model_field_values(
            constraint.field, constraint.raw, target.anchor, lookup
        )
        if len(values) != 1:
            reason = (
                "unverified_exclusion"
                if constraint.polarity == "exclude"
                else "constraint_not_source_grounded"
            )
            return {}, set(), reason
        value = next(iter(values))
        if constraint.polarity == "exclude":
            if not re.search(
                re.escape(constraint.raw) + EXCLUSION_CUE.pattern,
                target.anchor,
                re.IGNORECASE,
            ):
                return {}, set(), "unverified_exclusion"
            exclusions.update(_cases_for_constraint(constraint.field, value, lookup))
        else:
            positive_values.setdefault(constraint.field, set()).add(value)
    if any(len(values) > 1 for values in positive_values.values()):
        return {}, set(), "conflicting_positive_constraints"
    positives = {
        field: _cases_for_constraint(field, next(iter(values)), lookup)
        for field, values in positive_values.items()
    }
    excluded_ids = {
        value
        for constraint in target.constraints
        if constraint.field == "case_id" and constraint.polarity == "exclude"
        for value in identifiers(constraint.raw)
    }
    direct_ids = []
    for case_id in hg_ids(target.anchor.upper()):
        if case_id in excluded_ids:
            continue
        if case_id in lookup.id_to_cases and "case_id" in lookup.id_fields[case_id]:
            direct_ids.append(case_id)
    if direct_ids:
        if len(set(direct_ids)) != 1:
            return {}, set(), "ambiguous_identifier"
        direct_id = direct_ids[0]
        if positive_values.get("case_id", {direct_id}) != {direct_id}:
            return {}, set(), "conflicting_positive_constraints"
        positives["case_id"] = set(lookup.id_to_cases[direct_id])
    if not positives:
        return {}, set(), "missing_positive_constraint"
    if "case_id" not in positives and len(positives) < 2:
        return {}, set(), "insufficient_composite_constraints"
    if any(not cases for cases in positives.values()):
        return {}, set(), "constraint_has_no_candidates"
    if set.intersection(*positives.values()) == set():
        return {}, set(), "positive_constraints_disagree"
    return positives, exclusions, None


def _uncovered_exclusion_cues(
    question: str,
    targets: Sequence[InterpretedTarget],
) -> set[tuple[int, int]]:
    cues = {match.span() for match in EXCLUSION_CUE.finditer(question)}
    covered = set()
    for target in targets:
        anchor_start = question.find(target.anchor)
        if anchor_start < 0:
            continue
        for constraint in target.constraints:
            if constraint.polarity != "exclude":
                continue
            pattern = re.compile(
                re.escape(constraint.raw) + f"(?P<cue>{EXCLUSION_CUE.pattern})",
                re.IGNORECASE,
            )
            for match in pattern.finditer(target.anchor):
                start, end = match.span("cue")
                covered.add((anchor_start + start, anchor_start + end))
    return cues - covered


def _resolve_target(
    target: InterpretedTarget,
    lookup: Any,
    rules: RuleMatcher | None,
) -> _TargetMatch:
    grounded_roles = _explicit_roles(target.anchor, lookup, rules)
    requested_roles = tuple(dict.fromkeys(target.roles))
    if not requested_roles or any(
        role not in grounded_roles for role in requested_roles
    ):
        return _TargetMatch(
            status="unresolved",
            matched_fields=("role_not_grounded_in_anchor",),
        )
    positives, exclusions, reason = _case_sets(target, lookup)
    if reason:
        known_ids = tuple(
            sorted(
                case_id
                for case_id in hg_ids(target.anchor.upper())
                if case_id in lookup.cases
            )
        )
        status = (
            "conflict"
            if reason
            in {
                "constraint_outside_anchor",
                "unverified_exclusion",
                "conflicting_positive_constraints",
                "positive_constraints_disagree",
            }
            else "unresolved"
        )
        return _TargetMatch(
            status=status, candidates=known_ids, matched_fields=(reason,)
        )

    candidate_sets = list(positives.values())
    candidates = set.intersection(*candidate_sets) - exclusions
    if not candidates:
        return _TargetMatch(
            status="unresolved",
            matched_fields=tuple(sorted(positives)),
        )
    if len(candidates) > 1:
        return _TargetMatch(
            status="ambiguous",
            candidates=tuple(sorted(candidates)),
            matched_fields=tuple(sorted(positives)),
        )

    case_id = next(iter(candidates))
    roles = requested_roles
    if any(role not in ROLES for role in roles):
        return _TargetMatch(
            status="conflict",
            candidates=(case_id,),
            matched_fields=("unsupported_role",),
        )
    ordered_roles = tuple(role for role in ROLES if role in roles)
    pages = lookup.cases[case_id]
    if len(pages) != len(ROLES):
        return _TargetMatch(
            status="unresolved",
            candidates=(case_id,),
            matched_fields=("incomplete_role_sources",),
        )
    role_pages = tuple(pages[ROLES.index(role)] for role in ordered_roles)
    return _TargetMatch(
        status="verified",
        target=RouteTarget(
            case_id=case_id,
            roles=ordered_roles,
            pages=role_pages,
            anchor=target.anchor,
            matched_fields=tuple(sorted(positives)),
        ),
        matched_fields=tuple(sorted(positives)),
    )


def _decision(
    route: str,
    status: str,
    *,
    targets: Sequence[RouteTarget] = (),
    candidates: Sequence[str] = (),
    query_required: bool = True,
    provider_called: bool = False,
    reason: str | None = None,
    rules: RuleMatcher | None = None,
) -> RouteDecision:
    return RouteDecision(
        route=route,
        status=status,
        targets=tuple(targets),
        candidate_case_ids=tuple(sorted(set(candidates))),
        query_required=query_required,
        provider_called=provider_called,
        reason=reason,
        rule_version=getattr(rules, "version_id", None),
        rule_sha256=getattr(rules, "content_sha256", None),
    )


def _simple_lookup(
    question: str, lookup: Any, rules: RuleMatcher | None
) -> RouteDecision | None:
    identifiers_in_question = identifiers(question)
    cases_in_question = hg_ids(question)
    if len(cases_in_question) != 1 or len(identifiers_in_question) != 1:
        return None
    case_id = next(iter(cases_in_question))
    if case_id not in lookup.cases or "case_id" not in lookup.id_fields.get(
        case_id, set()
    ):
        return None
    plan = lookup.plan(question)
    if plan.status != "single_identifier":
        return None
    roles = _explicit_roles(question, lookup, rules)
    if len(roles) != 1:
        return None
    if NON_SIMPLE_CUES.search(question.replace(roles[0], "")):
        return None
    target = RouteTarget(
        case_id=case_id,
        roles=roles,
        pages=(lookup.cases[case_id][ROLES.index(roles[0])],),
        anchor=question,
        matched_fields=("case_id",),
    )
    return _decision(
        "lookup",
        "verified",
        targets=(target,),
        query_required=False,
        provider_called=False,
        rules=rules,
    )


def route_question(
    question: str,
    lookup: Any,
    interpreter: InterpretationProvider | None = None,
    *,
    rules: RuleMatcher | None = None,
    timeout_seconds: float = 10.0,
) -> RouteDecision:
    """Resolve a question from verified input only, with one optional structured call."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question은 비어 있지 않은 문자열이어야 합니다.")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds는 양수여야 합니다.")
    active_rules = _active_rules(lookup, rules)
    if direct := _simple_lookup(question, lookup, active_rules):
        return direct
    if interpreter is None:
        return _decision(
            "unresolved",
            "unresolved",
            reason="interpreter_not_configured",
            rules=active_rules,
        )

    try:
        raw_response = interpreter.invoke(
            question,
            timeout_seconds=timeout_seconds,
            rules_version=getattr(active_rules, "version_id", None),
        )
    except TimeoutError:
        return _decision(
            "unresolved",
            "timeout",
            provider_called=True,
            reason="interpretation_timeout",
            rules=active_rules,
        )
    except Exception:
        return _decision(
            "unresolved",
            "interpretation_failure",
            provider_called=True,
            reason="provider_error",
            rules=active_rules,
        )

    try:
        interpretation = QuestionInterpretation.model_validate(raw_response)
    except Exception:
        return _decision(
            "unresolved",
            "interpretation_failure",
            provider_called=True,
            reason="invalid_structure",
            rules=active_rules,
        )

    if _uncovered_exclusion_cues(question, interpretation.targets):
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="unverified_exclusion_cue",
            rules=active_rules,
        )

    if interpretation.route == "documents":
        if interpretation.targets or hg_ids(question):
            return _decision(
                "unresolved",
                "conflict",
                provider_called=True,
                reason="known_case_omitted",
                rules=active_rules,
            )
        return _decision(
            "documents",
            "verified",
            provider_called=True,
            rules=active_rules,
        )
    if interpretation.route == "unresolved":
        return _decision(
            "unresolved",
            "unresolved",
            provider_called=True,
            reason="model_unresolved",
            rules=active_rules,
        )
    if interpretation.route == "lookup" and not interpretation.targets:
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="lookup_without_target",
            rules=active_rules,
        )
    if interpretation.route == "mixed" and not interpretation.targets:
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="mixed_without_target",
            rules=active_rules,
        )
    anchors = [target.anchor for target in interpretation.targets]
    if len(set(anchors)) != len(anchors):
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="duplicate_anchor",
            rules=active_rules,
        )
    spans = []
    for anchor in anchors:
        if question.count(anchor) != 1:
            return _decision(
                "unresolved",
                "conflict",
                provider_called=True,
                reason="anchor_not_unique",
                rules=active_rules,
            )
        start = question.find(anchor)
        spans.append((start, start + len(anchor)))
    spans.sort()
    if any(start < 0 for start, _ in spans) or any(
        left_end > right_start
        for (_, left_end), (right_start, _) in zip(spans, spans[1:], strict=False)
    ):
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="anchor_not_in_question",
            rules=active_rules,
        )
    known_ids = hg_ids(question.upper())
    targeted_ids = set().union(*(hg_ids(anchor.upper()) for anchor in anchors))
    if known_ids - targeted_ids:
        return _decision(
            "unresolved",
            "conflict",
            provider_called=True,
            reason="known_case_omitted",
            rules=active_rules,
        )

    targets, candidates = [], set()
    partial = False
    ambiguous_match = False
    conflict = False
    target_reasons = []
    for target_input in interpretation.targets:
        match = _resolve_target(target_input, lookup, active_rules)
        candidates.update(match.candidates)
        if match.target is not None:
            targets.append(match.target)
            candidates.add(match.target.case_id)
        if match.status != "verified":
            partial = True
            target_reasons.extend(match.matched_fields)
        if match.status == "ambiguous":
            ambiguous_match = True
        if match.status == "conflict":
            conflict = True
    if conflict:
        status = "conflict"
    elif ambiguous_match:
        status = "ambiguous"
    elif partial and targets:
        status = "partial"
    elif partial:
        status = "unresolved"
    else:
        status = "verified"
    return _decision(
        interpretation.route,
        status,
        targets=targets,
        candidates=candidates,
        query_required=interpretation.route != "lookup" or partial,
        provider_called=True,
        reason=(
            target_reasons[0]
            if conflict and target_reasons
            else "multiple_candidates"
            if ambiguous_match
            else "partial_target_resolution"
            if partial and targets
            else "no_source"
            if partial
            else None
        ),
        rules=active_rules,
    )


def _pack_ranked(
    ranked: Sequence[tuple[Mapping[str, Any], float]], context_tokens: int
) -> ContextBundle:
    return build_context(
        _context_candidates(ranked),
        FINAL_K,
        context_tokens,
        _count_tokens,
    )


def pack_decision(
    question: str,
    ranked: Sequence[tuple[Mapping[str, Any], float]],
    lookup: Any,
    decision: RouteDecision,
    *,
    context_tokens: int = 8000,
) -> tuple[list[tuple[Mapping[str, Any], float]], ContextBundle, str]:
    """Pack verified targets into the shared context, preserving a ranked baseline."""
    if context_tokens <= 0:
        raise ValueError("context_tokens는 양수여야 합니다.")
    baseline = _pack_ranked(ranked, context_tokens)
    if not decision.targets or decision.status not in {
        "verified",
        "ambiguous",
        "partial",
    }:
        return list(ranked), baseline, decision.reason or decision.status
    scores = {}
    for page, score in ranked:
        source = _source_key(page)
        scores[source] = float(score)
    target_order = []
    for target in decision.targets:
        for page in target.pages:
            source = _source_key(page)
            target_order.append((page, scores.get(source, 0.0)))
    target_sources = {_source_key(page) for page, _ in target_order}
    augmented = []
    seen = set()
    for page, score in (
        *target_order,
        *(
            (page, float(score))
            for page, score in ranked
            if _source_key(page) not in target_sources
        ),
    ):
        source = _source_key(page)
        if source in seen:
            continue
        seen.add(source)
        augmented.append((page, score))
        if len(augmented) == CANDIDATES:
            break
    packed = pack_pair_context(
        question,
        augmented,
        lookup,
        baseline,
        targets=decision.targets_by_case,
        context_tokens=context_tokens,
        reserve_general=decision.route == "mixed",
    )
    if decision.status in {"ambiguous", "partial"}:
        return packed[0], packed[1], f"{packed[2]}_partial_target_resolution"
    return packed


def _retrieved_pages(
    pages: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], float], ...]:
    return tuple((dict(page), 1.0) for page in pages)


def _build_exact_context(
    ranked: Sequence[tuple[Mapping[str, Any], float]], context_tokens: int
) -> ContextBundle:
    candidates = []
    for index, (page, score) in enumerate(ranked):
        book_id, page_number = _source_key(page)
        file_name = page.get("file_name")
        text = page.get("text")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("HG 원본 쪽에 file_name이 없습니다.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("HG 원본 쪽 본문이 비어 있습니다.")
        candidates.append(
            RetrievedChunk(
                chunk=Chunk(
                    chunk_id=f"advanced:{book_id}:p{page_number}",
                    book_id=book_id,
                    file_name=file_name,
                    page=page_number,
                    text=text,
                ),
                rank=index + 1,
                score=float(score),
                retriever="hg-sqlite-fast-lookup",
            )
        )
    return build_context(candidates, FINAL_K, context_tokens, _count_tokens)


def fast_lookup(
    question: str,
    lookup: Any,
    page_lookup: Callable[[str, tuple[str, ...]], Sequence[Mapping[str, Any]]],
    *,
    context_tokens: int = 8000,
    rules: RuleMatcher | None = None,
) -> FastLookupResult:
    """Use a strict single-key/single-role path only when its source page fits."""
    if context_tokens <= 0:
        raise ValueError("context_tokens는 양수여야 합니다.")
    decision = _simple_lookup(question, lookup, _active_rules(lookup, rules))
    if decision is None or len(decision.targets) != 1:
        return FastLookupResult(status="ineligible", reason="request_needs_search")
    target = decision.targets[0]
    try:
        pages = tuple(page_lookup(target.case_id, target.roles))
    except Exception:
        return FastLookupResult(status="storage_error", reason="source_lookup_failed")
    expected_pages = dict(zip(target.roles, target.pages, strict=True))
    expected = {(target.case_id, role) for role in target.roles}
    actual = {lookup.page_roles.get(_source_key(page)) for page in pages}
    if not pages or actual != expected or len(pages) != len(expected):
        return FastLookupResult(status="source_missing", reason="source_role_mismatch")
    for page in pages:
        role_link = lookup.page_roles.get(_source_key(page))
        if role_link is None:
            return FastLookupResult(
                status="source_missing", reason="unknown_source_page"
            )
        role = role_link[1]
        source_page = expected_pages[role]
        if any(
            page.get(field) != source_page.get(field)
            for field in ("book_id", "file_name", "page", "text")
        ):
            return FastLookupResult(
                status="source_mismatch", reason="source_content_mismatch"
            )
    order = _retrieved_pages(pages)
    try:
        context = _build_exact_context(order, context_tokens)
    except (TypeError, ValueError):
        return FastLookupResult(
            status="packing_failed", reason="source_context_invalid"
        )
    packed = {
        lookup.page_roles.get((item.chunk.book_id, item.chunk.page))
        for item in context.final_chunks
    }
    packed_texts = {
        (item.chunk.book_id, item.chunk.page): item.chunk.text
        for item in context.final_chunks
    }
    if packed != expected or len(context.final_chunks) != len(expected):
        return FastLookupResult(status="packing_failed", reason="source_did_not_fit")
    if any(
        packed_texts.get(_source_key(source_page)) != source_page["text"]
        for source_page in expected_pages.values()
    ):
        return FastLookupResult(status="packing_failed", reason="source_was_truncated")
    return FastLookupResult(
        status="ready",
        order=order,
        context=context,
        case_id=target.case_id,
        roles=target.roles,
    )
