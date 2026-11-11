"""Link verified Advanced source pages and use the existing context selector."""

import re

from app.generation.context import build_context
from app.ingestion.layout import identifiers
from experimental.u_10o0o import advanced_pair_context
from experimental.u_10o0o.advanced_clause_binding import gate_descriptor, roles_in
from experimental.u_10o0o.advanced_hg_lookup import (
    augment,
    case_role_pages,
    location,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.advanced_pair_context import CANDIDATES, FINAL_K, unique_case
from experimental.u_10o0o.advanced_pair_source_lookup import pair_source_lookup
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    MULTI_TARGET,
    _canonical_date,
    normalized,
)
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens

LOADED_GATE = re.compile(r"\s*(?:에\s*)?화물을\s*싣고\s*밖으로\s*나간")


def unique_lookup(question, lookup):
    """Resolve a single indirect event using verified indexed fields."""
    text = normalized(question)
    if identifiers(text.upper()) or EXCLUSION.search(text) or MULTI_TARGET.search(text):
        return (), ()
    matches = lookup._matched_profile_values(text)
    if any(len(values) > 1 for values in matches.values()):
        return (), ()
    fields = {key: next(iter(values)) for key, values in matches.items() if values}
    if not {"shipper", "spec"} <= fields.keys():
        return (), ()
    dates = list(DATE.finditer(text))
    request = text
    if dates:
        if len(dates) != 1 or not (day := _canonical_date(dates[0].group())):
            return (), ()
        end = gate_descriptor(text, dates[0])
        if end is None and (gate := LOADED_GATE.match(text, dates[0].end())):
            end = gate.end()
        if end is None:
            return (), ()
        fields["actual_gate_out_date"] = day
        request = text[end:]
    elif "cargo" not in fields:
        return (), ()
    case = unique_case(lookup, fields)
    roles = roles_in(request, getattr(lookup, "rules", None))
    if case is None or not roles:
        return (), ()
    return (case,), tuple(case_role_pages(lookup.cases, case, roles))


def ambiguous_lookup(question, storage):
    cases = storage.candidates(question)
    if len(cases) != 2:
        return (), ()
    pages = storage.evidence_pages(cases)
    if len(set(cases)) != 2 or len({location(page) for page in pages}) != 4:
        raise ValueError(
            "모호 사건 조회에는 두 사건의 서로 다른 근거 네 쪽이 필요합니다."
        )
    return cases, pages


def choose_sources(matches):
    active = [(name, cases, pages) for name, (cases, pages) in matches.items() if cases]
    if len(active) == 1:
        return active[0]
    return ("conflict" if active else "fallback"), (), ()


def select_context(question, ranked, lookup, context_tokens):
    initial = build_context(
        _context_candidates(ranked[:FINAL_K]), FINAL_K, context_tokens, _count_tokens
    )
    return advanced_pair_context.pack_pair_context(
        question, ranked, lookup, initial, context_tokens=context_tokens
    )


def apply_sources(question, ranked, lookup, context_tokens, linked, baseline):
    selected = {
        (item.chunk.book_id, item.chunk.page) for item in baseline[1].final_chunks
    }
    if all(location(page) in selected for page in linked):
        return baseline
    candidates = augment(ranked, linked)[:CANDIDATES]
    return select_context(question, candidates, lookup, context_tokens)


def baseline_context(question, ranked, lookup, storage, context_tokens):
    """Apply the single and ambiguous source lookups used in earlier runs."""
    baseline = select_context(question, ranked, lookup, context_tokens)
    route, cases, linked = choose_sources(
        {
            "unique": unique_lookup(question, lookup),
            "ambiguous": ambiguous_lookup(question, storage),
        }
    )
    ordered, context, selection = apply_sources(
        question, ranked, lookup, context_tokens, linked, baseline
    )
    return ordered, context, cases, route, selection


def selected_context(question, ranked, lookup, storage, context_tokens):
    """Apply the current Advanced source lookups to the post-HG candidates."""
    baseline = baseline_context(question, ranked, lookup, storage, context_tokens)
    cases, linked = pair_source_lookup(question, lookup)
    if not cases:
        return baseline
    ordered, context, _, _, selection = baseline
    ordered, context, selection = apply_sources(
        question, ordered, lookup, context_tokens, linked, (ordered, context, selection)
    )
    return ordered, context, cases, "pair", selection


def complete_sources(question, ranked, lookup, storage, context_tokens):
    linked, route = rank_with_hg_lookup(
        question, ranked, lookup.cases, lookup.profile_index
    )
    linked = linked[:CANDIDATES]
    return (
        linked,
        selected_context(question, linked, lookup, storage, context_tokens),
        route,
    )
