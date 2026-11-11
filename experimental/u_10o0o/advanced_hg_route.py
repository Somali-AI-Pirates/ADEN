"""Verify Luna's literal HG lookup arguments against the local case relation store."""

from dataclasses import dataclass
import re

from app.ingestion.layout import IDENTIFIER, identifiers
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    DATE_CUES,
    EXCLUSION,
    ROLE_CUES,
    SPEC_ALIASES,
    _canonical_date,
    normalized,
)

HG_CODE = re.compile(r"^(?:HG20\d{2}-|HL-|CT-|EV-|HS-|AP-|INV-|RCPT-|RC-|CR-)")
EQUIPMENT_SPEC = re.compile(r"(?<![A-Z0-9_])\d{2}(?:GP|HC|HQ|RF|OT|FR)(?![A-Z0-9_])")
GENERAL_RULE = re.compile(
    r"(?:일반|공통|관련|별도)\s*(?:업무\s*)?(?:규정|지침|절차)|"
    r"(?:규정|지침)\s*(?:상|도|까지|도 함께)"
)
CASE_REQUEST = re.compile(
    r"(?:해당|그|이)\s*(?:건|예약)|(?:청구|수납)(?:액|\s*명세|\s*기록)"
)
DATE_TYPES = {
    "actual": {
        "actual_discharge_date",
        "actual_gate_out_date",
        "actual_designated_return_date",
    },
    "scheduled": {"scheduled_return_date"},
    "system_entry": {"system_entry_date"},
    "effective": {"recorded_amendment_effective_date"},
}
DATE_PATTERNS = {
    **DATE_CUES,
    "recorded_amendment_effective_date": re.compile(
        r"(?:개정|변경|정정)\s*효력(?:일|\s*날짜)"
    ),
}
AS_OF = re.compile(r"^\s+(?:[01]\d|2[0-3]):[0-5]\d\s+(?:KST|한국시간)\s*기준", re.I)
DISCHARGE_DATE = re.compile(r"^\s*(?:실제\s*)?양하(?=\s|한|된|일)(?!\s*(?:예정|예약))")


@dataclass(frozen=True)
class CaseRequest:
    case_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    path: str
    reason: str
    cases: tuple[CaseRequest, ...] = ()
    candidate_count: int = 0
    cue_types: tuple[str, ...] = ()


def _code_matches(text, relations):
    return {
        code: candidates
        for code in identifiers(text.upper())
        if (candidates := relations.code_candidates(code)) or HG_CODE.match(code)
    }


def _profile(text, lookup):
    non_code_text = IDENTIFIER.sub(" ", text.upper())
    profile = {
        key: values
        for key, values in lookup._matched_profile_values(
            normalized(non_code_text)
        ).items()
        if values
    }
    explicit = {normalized(value) for value in EQUIPMENT_SPEC.findall(non_code_text)}
    if known := explicit.intersection(SPEC_ALIASES.values()):
        profile.setdefault("spec", set()).update(known)
    return profile


def _identity_dates(text, *, leading=True):
    return [
        match
        for match in DATE.finditer(text)
        if not (
            leading
            and not text[: match.start()].strip()
            and AS_OF.match(text[match.end() :])
            and _canonical_date(match.group())
        )
    ]


def _date_candidates(anchor, date_type, relations, *, leading):
    dates = _identity_dates(anchor, leading=leading)
    if not dates:
        return []
    matched = []
    for date in dates:
        value = _canonical_date(date.group())
        before = anchor[max(0, date.start() - 24) : date.start()]
        after = anchor[date.end() : date.end() + 18]
        if DISCHARGE_DATE.match(after) and not re.search(r"(?:예정|예약)\s*$", before):
            types = {"actual_discharge_date"}
        else:
            types = {
                name
                for name, cue in DATE_PATTERNS.items()
                if cue.search(before + after)
            }
        if (
            not value
            or len(types) != 1
            or (date_type in DATE_TYPES and not types <= DATE_TYPES[date_type])
        ):
            return None
        matched.append(relations.identities_for(next(iter(types)), value))
    return matched


def _roles(anchor, requested=()):
    # Use model role selections only where the anchor also has a local role cue.
    detected = {
        role
        for role, pattern in ROLE_CUES.items()
        if role in anchor or pattern.search(anchor)
    }
    stated = detected.intersection(requested)
    return tuple(role for role in ROLES if role in (stated or detected))


def _fallback(question, lookup, relations, reason):
    codes = _code_matches(question, relations)
    profile = _profile(question, lookup)
    has_case_cues = bool(profile or identifiers(question.upper()))
    hg = (
        bool(codes)
        or {"shipper", "cargo"} <= profile.keys()
        or (has_case_cues and CASE_REQUEST.search(question))
    )
    return Decision("hg_search" if hg else "general", reason)


def decide(question, lookup, relations, proposal=None, *, tool_requested=False):
    """A provider proposal can select a source only after local verification."""
    if (
        not tool_requested
        or proposal is None
        or proposal.route not in {"lookup", "mixed"}
    ):
        return _fallback(question, lookup, relations, "no_verified_tool_proposal")
    if not proposal.targets or len(proposal.targets) > 2 or EXCLUSION.search(question):
        return _fallback(question, lookup, relations, "unresolved_scope")
    spans = []
    for target in proposal.targets:
        anchor = target.anchor
        identifiers_in_anchor = set(identifiers(anchor.upper()))
        if (
            not anchor
            or question.count(anchor) != 1
            or not target.cues
            or any(
                not cue.raw
                or cue.raw not in anchor
                or (
                    cue.kind == "identifier"
                    and cue.raw.upper() not in identifiers_in_anchor
                )
                for cue in target.cues
            )
        ):
            return _fallback(question, lookup, relations, "ungrounded_proposal")
        start = question.index(anchor)
        spans.append((start, start + len(anchor), target))
    spans.sort(key=lambda span: span[0])
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        return _fallback(question, lookup, relations, "overlapping_anchors")
    bound = [target.anchor for _, _, target in spans]
    codes = set(identifiers(question.upper()))
    if codes - set().union(*(identifiers(anchor.upper()) for anchor in bound)):
        return _fallback(question, lookup, relations, "unbound_identifier")
    profiles = _profile(question, lookup)
    profiles_by_anchor = [_profile(anchor, lookup) for anchor in bound]
    if any(
        values
        - set().union(*(anchor.get(field, set()) for anchor in profiles_by_anchor))
        for field, values in profiles.items()
        if field != "spec"
    ):
        return _fallback(question, lookup, relations, "unbound_profile")
    if any(
        not any(start <= date.start() and date.end() <= end for start, end, _ in spans)
        for date in _identity_dates(question)
    ):
        return _fallback(question, lookup, relations, "unbound_date")

    requests, cue_types = [], set()
    for (start, _, target), profile in zip(spans, profiles_by_anchor, strict=True):
        anchor = target.anchor
        exact = _code_matches(anchor, relations)
        # An unregistered token cannot be silently promoted into a prefix alias.
        if set(identifiers(anchor.upper())) - exact.keys() or any(
            not matches for matches in exact.values()
        ):
            return Decision("hg_search", "identifier_not_indexed")
        if any(len(values) > 1 for values in profile.values()):
            return Decision("hg_search", "unresolved_profile_scope")
        explicit_specs = {
            normalized(value)
            for value in EQUIPMENT_SPEC.findall(IDENTIFIER.sub(" ", anchor.upper()))
        }
        if explicit_specs - profile.get("spec", set()):
            return _fallback(question, lookup, relations, "unverified_equipment_class")
        code_sets = list(exact.values())
        if spec := profile.get("spec"):
            for code in exact:
                if not code.startswith("HL-"):
                    continue
                paired = {
                    row.case_id
                    for row in relations.identify(code)
                    if row.related_code_type == "equipment_class"
                    and row.related_code is not None
                    and normalized(row.related_code) in spec
                }
                if not paired:
                    return Decision("hg_search", "unverified_equipment_class_pair")
                code_sets.append(paired)
        profile_sets = [
            relations.identities_for(field, next(iter(values)))
            for field, values in profile.items()
        ]
        date_sets = _date_candidates(
            anchor, target.date_type, relations, leading=not question[:start].strip()
        )
        if date_sets is None:
            return _fallback(question, lookup, relations, "unverified_date_type")
        filters = [*code_sets, *profile_sets, *date_sets]
        if not filters:
            return _fallback(question, lookup, relations, "no_verified_identity")
        candidates = set.intersection(*filters)
        if len(candidates) > 1:
            return Decision(
                "clarify", "multiple_matching_cases", candidate_count=len(candidates)
            )
        if not candidates:
            return Decision("hg_search", "conflicting_or_missing_case_evidence")
        if not code_sets and len(profile_sets) + bool(date_sets) < 2:
            return Decision("hg_search", "insufficient_independent_clues")
        cue_types.update(profile)
        if code_sets:
            cue_types.add("identifier")
        if date_sets:
            cue_types.add("date")
        requests.append(
            CaseRequest(next(iter(candidates)), _roles(anchor, target.roles))
        )

    if len({request.case_id for request in requests}) != len(requests):
        return Decision("hg_search", "duplicate_resolved_case")
    mixed = proposal.route == "mixed" or GENERAL_RULE.search(question)
    return Decision(
        "mixed" if mixed else "lookup",
        "unique_verified_cases",
        tuple(requests),
        len(requests),
        tuple(sorted(cue_types)),
    )


def pilot_for(questions, lookup, relations):
    """Choose an explicit, locally connected case from question inputs."""
    for row in questions:
        question = row["question"]
        codes = _code_matches(question, relations)
        profile = _profile(question, lookup)
        if (
            not any(code.startswith("HG20") for code in codes)
            or set(identifiers(question.upper())) != codes.keys()
            or _identity_dates(question)
            or EXCLUSION.search(question)
            or any(len(values) != 1 for values in profile.values())
        ):
            continue
        profile_sets = [
            relations.identities_for(field, next(iter(values)))
            for field, values in profile.items()
        ]
        if len(set.intersection(*codes.values(), *profile_sets)) == 1:
            return row
    raise ValueError(
        "명시 HG 사건에 연결되는 공식 문항에서 도구를 먼저 검증해야 합니다."
    )
