"""Load validated HG source links and apply the tested lookup rules."""

import json
import re
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from unicodedata import normalize

from app.ingestion.curation import read_rows
from app.ingestion.layout import compact, identifiers
from experimental.u_10o0o.advanced_case_sources import ROLES, hg_ids
from experimental.u_10o0o.advanced_field_roles import _page_heading
from experimental.u_10o0o.context_evidence import _source_key as location

CASE_INDEX = Path("artifacts/u_10o0o/advanced-curation/hg-case-sources-20261109")
PROFILE_ROLES = (ROLES[1], ROLES[3])
PROFILE = re.compile(
    r"\b화주 (?P<shipper>[^,.]+), 화물 (?P<cargo>.+?), "
    r"장비 (?P<equipment>[A-Z0-9-]+) *\((?P<spec>[A-Z0-9]+)\), "
    r"계약 (?P<contract>CT-[A-Z0-9-]+)"
)
UNSUPPORTED = re.compile(
    r"(?<!\d)(?:20\d{2}(?:[-./]\d{1,2}|년)|\d{1,2}월\s*\d{1,2}일)"
    r"|제외|아닌|말고|빼고"
)
CASE_EXCLUSION = (
    r"\s*(?:(?:사건|건)\s*)?[은는이가을를]?\s*(?:제외|아닌|아니[라고며]|말고|빼고)"
)
PARTICLE = r"(?:에서|으로|에게|까지|부터|의|은|는|이|가|을|를|와|과|에|로|도|만)?"


def load_cases(path, pages, curation_manifest_sha256):
    page_by_source = {}
    for page in pages:
        book_id, number = location(page)
        source_id = f"advanced:{book_id}:p{number}"
        if source_id in page_by_source:
            raise ValueError("복원 페이지 출처가 중복됩니다.")
        page_by_source[source_id] = page

    index_path = path / "case_sources.jsonl"
    summary = json.loads((path / "summary.json").read_bytes())
    if (
        summary["curation_manifest_sha256"] != curation_manifest_sha256
        or summary["case_sources_sha256"] != sha256(index_path.read_bytes()).hexdigest()
    ):
        raise ValueError("사건 인덱스의 지문이나 원본 스냅샷이 다릅니다.")
    by_case = defaultdict(dict)
    for row in read_rows(index_path):
        case, role, source = row["case_id"], row["record_role"], row["source_id"]
        if (
            hg_ids(case) != {case}
            or role in by_case[case]
            or row["match_rule"] != "page_heading_case_id"
            or source not in page_by_source
        ):
            raise ValueError(
                "사건 인덱스에서 유효한 단일 역할·출처를 확인할 수 없습니다."
            )
        heading = _page_heading(page_by_source[source]["text"])
        if hg_ids(heading) != {case} or compact(role) not in compact(heading):
            raise ValueError("복원 페이지의 머리말이 사건 인덱스와 다릅니다.")
        by_case[case][role] = source
    for roles in by_case.values():
        if set(roles) != set(ROLES) or len(set(roles.values())) != len(ROLES):
            raise ValueError("한 사건에는 다섯 역할의 서로 다른 쪽이 필요합니다.")
    if (
        not by_case
        or len(by_case) != summary["indexed_cases"]
        or len(by_case) * len(ROLES) != summary["source_links"]
    ):
        raise ValueError("사건 인덱스와 요약의 수량이 다릅니다.")
    return {
        case: [page_by_source[roles[role]] for role in ROLES]
        for case, roles in by_case.items()
    }


def matched_case(question, cases):
    ids = hg_ids(question)
    if len(ids) == 1:
        case = next(iter(ids))
        if case in cases and not re.search(
            rf"(?<![A-Z0-9-]){re.escape(case)}{CASE_EXCLUSION}", question
        ):
            return case
    return None


def augment(order, linked):
    if not linked:
        return order
    seen = {location(page) for page in linked}
    ranked = [(page, 0.0) for page in linked]
    for page, score in order:
        source = location(page)
        if source not in seen:
            ranked.append((page, score))
            seen.add(source)
    return ranked


def normalized(text):
    return " ".join(normalize("NFKC", text).casefold().split())


def profile_attributes(text, fields):
    matches = list(PROFILE.finditer(text))
    if len(matches) != 1:
        raise ValueError("원문에서 단일 화주·화물·장비 정보를 확인할 수 없습니다.")
    profile = matches[0]
    if any(
        profile[key] != fields[label]
        for key, label in (
            ("shipper", "화주"),
            ("equipment", "장비"),
            ("contract", "계약"),
        )
    ):
        raise ValueError("원문과 정제된 화주·장비·계약 정보가 다릅니다.")
    return tuple(normalized(profile[key]) for key in ("shipper", "cargo", "spec"))


def load_profiles(curated, cases):
    expected = {}
    for case, pages in cases.items():
        for role in PROFILE_ROLES:
            page = pages[ROLES.index(role)]
            book, number = location(page)
            expected[f"advanced:{book}:p{number}"] = case, role, page["text"]

    found = set()
    profiles = {}
    for path in sorted((curated / "records").glob("*.jsonl")):
        for row in read_rows(path):
            source = row["source_id"]
            if row["kind"] != "case_context" or source not in expected:
                continue
            case, role, source_text = expected[source]
            fields = row["fields"]
            if (
                source in found
                or row.get("review_status") != "parsed"
                or row.get("case_ids") != [case]
                or fields.get("기록 종류") != role
            ):
                raise ValueError("현장·청구 기록의 사건 또는 역할이 다릅니다.")
            if compact(row["text"]) not in compact(source_text):
                raise ValueError("현장·청구 기록의 단서가 복원 원본 쪽과 다릅니다.")
            profile = profile_attributes(row["text"], fields)
            if profiles.setdefault(case, profile) != profile:
                raise ValueError("같은 사건의 현장·청구 원문에 적힌 단서가 다릅니다.")
            found.add(source)
    if found != set(expected):
        raise ValueError(
            "누락된 현장·청구 기록이 있어 사건의 유일성을 검증할 수 없습니다."
        )
    return profiles


class ProfileIndex:
    def __init__(self, profiles):
        self.cases = defaultdict(list)
        for case, attributes in profiles.items():
            self.cases[attributes].append(case)
        self.patterns = []
        for values in zip(*profiles.values(), strict=True):
            alternatives = "|".join(
                re.escape(v) for v in sorted(set(values), key=lambda v: (-len(v), v))
            )
            self.patterns.append(
                re.compile(rf"(?<!\w)({alternatives}){PARTICLE}(?!\w)")
            )

    def candidate(self, question):
        if hg_ids(question):
            return None, "explicit_hg"
        text = normalized(question)
        if identifiers(question) or UNSUPPORTED.search(text):
            return None, "unsupported_constraint"
        matches = [set(pattern.findall(text)) for pattern in self.patterns]
        if any(len(values) > 1 for values in matches):
            return None, "ambiguous_terms"
        if any(not values for values in matches):
            return None, "missing_terms"
        cases = self.cases.get(tuple(next(iter(values)) for values in matches), ())
        if not cases:
            return None, "no_profile"
        if len(cases) != 1:
            return None, "ambiguous_profile"
        return cases[0], "eligible"


def linked_pages(order, pages):
    sources = {location(page) for page in pages}
    return pages if any(location(page) in sources for page, _ in order) else None


def case_role_pages(cases, case_id, roles):
    provider = getattr(cases, "pages_for", None)
    if provider is not None:
        return provider(case_id, roles)
    pages = cases[case_id]
    return [pages[ROLES.index(role)] for role in roles]


def rank_with_hg_lookup(question, order, cases, profiles):
    """Prepend the five verified source pages when the question identifies a case."""
    if case := matched_case(question, cases):
        return augment(order, cases[case]), "explicit_hg"
    case, route = profiles.candidate(question)
    if case is None:
        return order, "unverified_hg" if route == "explicit_hg" else route
    if linked := linked_pages(order, cases[case]):
        return augment(order, linked), "indirect_hg"
    return order, "not_in_candidates"
