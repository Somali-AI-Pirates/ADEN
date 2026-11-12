"""Keep the selected KURE-v3 + enhanced HG date baseline inside TableRAG.

The source-derived resolver is copied unchanged from the existing HG ablation,
rather than silently falling back to the older common HG helper.
"""

import re
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_clause_binding import gate_descriptor
from experimental.u_10o0o.advanced_pair_context import unique_case
from experimental.u_10o0o.advanced_source_lookup import apply_sources, complete_sources
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    _canonical_date,
    normalized,
)

AS_OF = re.compile(
    r"\s*(?:(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:kst\s*)?)?"
    r"(?:을|를)?\s*기준(?:으로)?(?=\s|[,.;:]|$)"
)

DISCHARGE = re.compile(r"\s*(?:에\s*)?(?:실제\s*)?양하(?:된|한)?(?=\s|[,.;]|$)")

DISCHARGE_UNVERIFIED = re.compile(r"\s*(?:예정|신청|요청|전산|입력)")

MULTIPLE_CASES = re.compile(
    r"각각|건별|양쪽|비교|대비|(?:두|2)\s*(?:건|사건|예약|계약)"
)


def date_target(question, lookup):
    """Resolve one explicitly dated event using as-of and event dates separately."""
    text = normalized(question)
    dates = list(DATE.finditer(text))
    if len(dates) != 2:
        return None, "date_count"
    if (
        identifiers(text.upper())
        or EXCLUSION.search(text)
        or MULTIPLE_CASES.search(text)
    ):
        return None, "complex_target"
    first, event = dates
    as_of = AS_OF.match(text, first.end())
    if as_of is None or as_of.end() > event.start():
        return None, "as_of_not_identified"
    date_limit, day = (_canonical_date(m.group()) for m in dates)
    if date_limit is None or day is None or day > date_limit:
        return None, "invalid_dates"
    tail = text[as_of.end() :]
    local_event = DATE.search(tail)
    if local_event is None:
        return None, "event_role_unknown"
    discharge = DISCHARGE.match(tail, local_event.end())
    gate = gate_descriptor(tail, local_event)
    if discharge and not DISCHARGE_UNVERIFIED.match(tail, discharge.end()):
        if gate is not None:
            return None, "event_role_ambiguous"
        field = "actual_discharge_date"
    elif gate is not None and discharge is None:
        field = "actual_gate_out_date"
    else:
        return None, "event_role_unknown"
    matches = lookup._matched_profile_values(text)
    if (
        any(len(values) > 1 for values in matches.values())
        or not matches["shipper"]
        or not matches["cargo"]
    ):
        return None, "profile_unverified"
    fields = {key: next(iter(values)) for key, values in matches.items() if values}
    fields[field] = day
    case = unique_case(lookup, fields)
    return (case, field) if case else (None, "no_unique_event")


def complete_sources_with_dates(question, ranked, lookup, storage, context_tokens):
    linked, chosen, route = complete_sources(
        question, ranked, lookup, storage, context_tokens
    )
    current, context, native_cases, source_route, selection = chosen
    case, date_route = date_target(question, lookup)
    if (
        case
        and source_route == "fallback"
        and not native_cases
        and route not in {"explicit_hg", "indirect_hg"}
    ):
        current, context, selection = apply_sources(
            question,
            current,
            lookup,
            context_tokens,
            lookup.cases[case],
            (current, context, selection),
        )
        return (
            current,
            (current, context, native_cases, source_route, selection),
            "date/" + date_route,
        )
    return linked, chosen, route
