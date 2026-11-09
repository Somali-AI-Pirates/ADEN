"""Bind requests to two verified HG cases by their local question clauses."""

import re

from app.ingestion.layout import IDENTIFIER
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_pair_context import OTHER_COUNT, unique_case
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    _canonical_date,
    normalized,
)

IDENTIFIER_FOLDED = re.compile(IDENTIFIER.pattern, re.I)
SHARED = re.compile(r"각각|건별|각\s*(?:건|사건)|두\s*(?:건|사건)\s*(?:다|모두)|양쪽")
COORDINATED = re.compile(r"(?:[과와]|랑|하고|및|그리고|[,;/])\s*$")
GATE_PREFIX = re.compile(
    r"(?:실제\s*)?(?:장비\s*)?(?:반출(?:\s*(?:일자?|날짜))?|나간\s*날(?:짜)?)"
    r"\s*(?:은|는|이|가|:)?\s*$"
)
GATE_SUFFIX = re.compile(
    r"\s*(?:에|자로|부터)?\s*(?:실제(?:로)?\s*)?(?:장비\s*)?"
    r"(?:반출(?:\s*(?:된|한|됐던|시킨))?|(?:밖으로\s*)?(?:나간|나왔던|빠져나간))"
    r"(?:\s*(?:건|분|사건|예약|장비|컨테이너))?"
)
GATE_MODIFIER = re.compile(r"예정|요청|신청|전산|입력")
ROLE_TERMS = tuple(
    re.compile(pattern)
    for pattern in (
        r"약정|요율|단가|(?:무료|무상|프리)\s*(?:기간|일수|보관|타임)|"
        r"(?:무료|무상)로.{0,8}(?:며칠|몇\s*일|보관)|(?:계약|보관료|비용)\s*(?:상|조건|기준)",
        r"(?:실제|현장).{0,8}(?:반출|양하|반납|기록|사건)|"
        r"(?:반출|양하|반납)\s*(?:일자?|날짜|시각|시간)|"
        r"(?:반출|양하|반납).{0,7}(?:언제|며칠)|(?:나간|나왔|돌려준|돌려놓은)\s*날|"
        r"(?:반출|양하|반납)\s*(?:예정|실적)|전산\s*입력",
        r"승인|반려|정정|개정|변경|(?:대조|검토)\s*(?:기록|결과|회신)|"
        r"(?:요청|회신)\s*(?:번호|일자|날짜|이력|상태)",
        r"청구|산출|계산|확정\s*(?:금액|보관료)|(?:금액|보관료)\s*(?:확정|얼마)|"
        r"(?:실제|최종)\s*보관료|보관료.{0,8}(?:붙었|나왔|나온)",
        r"수납|미수|환급|입금|납부|잔액|미결|(?:회계\s*)?인계|"
        r"(?:받은|낸)\s*(?:금액|돈)|(?:돈|금액)이?\s*(?:들어왔|남았)",
    )
)


def roles_in(text):
    return tuple(
        role for role, cue in zip(ROLES, ROLE_TERMS, strict=True) if cue.search(text)
    )


def gate_descriptor(text, match):
    """Find the part of the clause that uses the date to describe an event."""
    prefix = GATE_PREFIX.search(text[: match.start()])
    if prefix and not GATE_MODIFIER.search(
        text[max(0, prefix.start() - 5) : prefix.start()]
    ):
        return match.end()
    suffix = GATE_SUFFIX.match(text, match.end())
    if suffix and not GATE_MODIFIER.match(text[suffix.end() :].lstrip()):
        return suffix.end()
    return None


def resolve_clause(text, anchor, start, lookup, shared_date=None):
    if anchor.re is IDENTIFIER_FOLDED:
        key = anchor.group().upper()
        matches = lookup.id_to_cases.get(key, ())
        if len(matches) != 1 or not {"case_id", "equipment_id"} & lookup.id_fields[key]:
            return None, ""
        case = next(iter(matches))
        names = lookup._matched_profile_values(text)["shipper"]
        if names - {normalized(lookup.profiles[case][0])}:
            return None, ""
        return case, text[anchor.end() - start :]

    if shared_date is None:
        dates = [(day, gate_descriptor(text, day)) for day in DATE.finditer(text)]
        descriptions = [(day, end) for day, end in dates if end is not None]
        if len(descriptions) != 1:
            return None, ""
        day, descriptor_end = descriptions[0]
        shared_date = _canonical_date(day.group())
    else:
        descriptor_end = len(text)
    profile = lookup._matched_profile_values(text[:descriptor_end])
    if not shared_date or len(profile["cargo"]) != 1 or len(profile["spec"]) > 1:
        return None, ""
    fields = {
        "shipper": anchor.group(1),
        "cargo": next(iter(profile["cargo"])),
        "actual_gate_out_date": shared_date,
    }
    if profile["spec"]:
        fields["spec"] = next(iter(profile["spec"]))
    return unique_case(lookup, fields), text[descriptor_end:]


def clause_requirements(question, lookup):
    """Return each recognized target and its own roles, possibly no roles yet."""
    text = normalized(question)
    ids = list(IDENTIFIER_FOLDED.finditer(text))
    if EXCLUSION.search(text) or OTHER_COUNT.search(IDENTIFIER_FOLDED.sub("", text)):
        return {}
    names = list(lookup.profile_patterns[0].finditer(text))
    anchors = ids if len(ids) == 2 else sorted([*ids, *names], key=lambda m: m.start())
    if len(ids) > 2 or len(anchors) != 2:
        return {}
    left, right = anchors
    before = text[: left.start()]
    between = text[left.start() : right.start()]
    after = text[right.start() :]

    # A coordinated date list identifies the two preceding profiles in order.
    shared_roles = ()
    shared_dates = (None, None)
    dates = list(DATE.finditer(after))
    if not ids and not DATE.search(between) and len(dates) == 2:
        first, second = dates
        end = gate_descriptor(after, second)
        if (
            end is not None
            and SHARED.search(after[: first.start()])
            and re.fullmatch(
                r"\s*(?:,|와|과|및)\s*", after[first.end() : second.start()]
            )
        ):
            shared_dates = tuple(_canonical_date(day.group()) for day in dates)
            if not all(shared_dates):
                return {}
            shared_roles = roles_in(after[end:])
            after = after[: first.start()]

    first, left_request = resolve_clause(
        between, left, left.start(), lookup, shared_dates[0]
    )
    second, right_request = resolve_clause(
        after, right, right.start(), lookup, shared_dates[1]
    )
    if not first or not second or first == second:
        return {}

    shared = list(SHARED.finditer(right_request))
    if shared and (common := roles_in(right_request[shared[-1].end() :])):
        shared_roles = common
        right_request = right_request[: shared[-1].start()]
    left_roles, right_roles = roles_in(left_request), roles_in(right_request)
    if not left_roles and not shared_roles and COORDINATED.search(between):
        shared_roles, right_roles = right_roles or roles_in(before), ()
    return {
        first: tuple(
            role for role in ROLES if role in left_roles or role in shared_roles
        ),
        second: tuple(
            role for role in ROLES if role in right_roles or role in shared_roles
        ),
    }
