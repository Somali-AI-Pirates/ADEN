"""Find verified source pages for two separately identified HG targets."""

import re
from itertools import zip_longest

from app.ingestion.layout import IDENTIFIER
from experimental.u_10o0o.advanced_clause_binding import roles_in
from experimental.u_10o0o.advanced_hg_lookup import case_role_pages
from experimental.u_10o0o.advanced_pair_context import OTHER_COUNT, unique_case
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    SPEC_ALIASES,
    _exact_phrase_pattern,
    normalized,
)

IDENTIFIERS = re.compile(IDENTIFIER.pattern, re.I)
FIRST = re.compile(
    r"(?<!\w)(?:첫\s*번째|첫째|1번(?:째)?)\s*(?:건|사건|대상)?\s*(?:은|는|이|가)?"
)
SECOND = re.compile(
    r"(?<!\w)(?:두\s*번째|둘째|2번(?:째)?)\s*(?:건|사건|대상)?\s*(?:은|는|이|가)?"
)
SHARED = re.compile(r"각각|건별|양쪽|둘\s*다|두\s*(?:건|사건)(?:의|은|는|을|에|\s)")
REQUEST_SPLIT = re.compile(r"(?<=[.!?;])\s+|\s+(?=두\s*(?:건|사건)의|양쪽의|둘\s*다)")
SPEC_PATTERNS = tuple(_exact_phrase_pattern(alias) for alias in SPEC_ALIASES)


def resolve_target(text, lookup):
    """Resolve one clause and return its target and local record request."""
    ids = list(IDENTIFIERS.finditer(text))
    fields = lookup._matched_profile_values(text)
    if ids:
        if len(ids) != 1 or any(fields.values()):
            return None, ()
        key = ids[0].group().upper()
        cases = lookup.id_to_cases.get(key, ())
        if len(cases) != 1 or not {"case_id", "equipment_id"} & lookup.id_fields[key]:
            return None, ()
        return next(iter(cases)), roles_in(
            text[ids[0].end() :], getattr(lookup, "rules", None)
        )

    if not all(len(values) == 1 for values in fields.values()):
        return None, ()
    case = unique_case(
        lookup, {field: next(iter(values)) for field, values in fields.items()}
    )
    end = max(
        match.end()
        for pattern in (*lookup.profile_patterns, *SPEC_PATTERNS)
        for match in pattern.finditer(text)
    )
    return case, roles_in(text[end:], getattr(lookup, "rules", None))


def pair_source_lookup(question, lookup):
    """Resolve two ordinal clauses, with local roles or an explicit shared request."""
    text = normalized(question)
    if (
        EXCLUSION.search(text)
        or DATE.search(text)
        or OTHER_COUNT.search(IDENTIFIERS.sub("", text))
    ):
        return (), ()
    first, second = list(FIRST.finditer(text)), list(SECOND.finditer(text))
    if len(first) != 1 or len(second) != 1 or first[0].end() >= second[0].start():
        return (), ()
    left, right = text[first[0].end() : second[0].start()], text[second[0].end() :]
    if (
        IDENTIFIERS.search(text[: first[0].start()])
        or len(list(IDENTIFIERS.finditer(text))) > 1
    ):
        return (), ()

    # A shared request comes after the two identities. The marker can follow
    # the record list (for example, an instruction to check each separately).
    parts = REQUEST_SPLIT.split(right, maxsplit=1)
    shared = len(parts) == 2 and SHARED.search(parts[1])
    first_case, first_roles = resolve_target(left, lookup)
    second_case, second_roles = resolve_target(parts[0] if shared else right, lookup)
    if not first_case or not second_case or first_case == second_case:
        return (), ()
    if shared:
        if first_roles or second_roles:
            return (), ()
        first_roles = second_roles = roles_in(parts[1], getattr(lookup, "rules", None))
    if not first_roles or not second_roles:
        return (), ()

    targets = (first_case, second_case)
    pages = [
        case_role_pages(lookup.cases, case, roles)
        for case, roles in zip(targets, (first_roles, second_roles), strict=True)
    ]
    return targets, tuple(
        page for group in zip_longest(*pages) for page in group if page is not None
    )
