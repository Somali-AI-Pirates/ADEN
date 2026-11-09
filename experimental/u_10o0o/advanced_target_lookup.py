"""Plan source-grounded HG target lookup from question fields only."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES, hg_ids
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    load_cases,
    load_profiles,
    normalized,
)
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.context_evidence import _source_key
from experimental.u_10o0o.prepare import CACHE_PATH

DATE = re.compile(r"(?<!\d)(20\d{2})[-./년 ]\s*(\d{1,2})[-./월 ]\s*(\d{1,2})일?(?!\d)")
EXCLUSION = re.compile(r"제외|아닌|아니[라고며]|말고|빼고")
MULTI_TARGET = re.compile(
    r"각각|각 건|모두|양쪽|양자|비교|대비|전체|그리고|및|두 (?:건|사건|예약|계약)|all|both",
    re.I,
)
MAX_SCOPED_CASES = 12
MAX_NAMED_SCOPE_CASES = 120
SPEC_ALIASES = {
    "20피트 일반형 컨테이너": "20gp",
    "40피트 일반형 컨테이너": "40gp",
}

FACT_FIELDS = {
    "comparison_contract_id": "contract_id",
    "comparison_equipment": "equipment_id",
    "comparison_record_id": "record_id",
}
DATE_FACTS = {
    "actual_gate_out_date",
    "actual_discharge_date",
    "actual_designated_return_date",
    "scheduled_return_date",
    "system_entry_date",
}
DATE_CUES = {
    "actual_gate_out_date": re.compile(r"실제.{0,8}반출|반출.{0,8}실제|장비.{0,8}반출"),
    "actual_discharge_date": re.compile(r"실제.{0,8}양하|양하.{0,8}실제"),
    "actual_designated_return_date": re.compile(
        r"실제.{0,8}지정.{0,8}반납|지정.{0,8}반납"
    ),
    "scheduled_return_date": re.compile(r"예정.{0,8}반납|반납.{0,8}예정"),
    "system_entry_date": re.compile(r"전산.{0,8}입력|입력.{0,8}일"),
}
INVALID_CONSTRAINT = "__invalid_constraint__"
ROLE_CUES = {
    "비용 조건 약정": re.compile(
        r"계약.{0,8}(?:조건|약정|요율|무료)|요율|무료기간|보관료.{0,8}(?:조건|기준)"
    ),
    "현장 사건 수신대장": re.compile(
        r"실제.{0,8}(?:반출|양하|반납)|현장.{0,8}(?:기록|사건)|반출일|양하일|반납일"
    ),
    "계약 조건 대조 회신": re.compile(r"승인|대조|회신|정정|변경|개정|요청|반려"),
    "비용 청구 명세": re.compile(r"청구|청구액|산출|계산|확정 금액|청구 명세"),
    "수납 및 미결 인계": re.compile(r"수납|미수|환급|입금|회계 인계|받은 금액"),
}


@dataclass(frozen=True)
class CaseTarget:
    case_id: str
    pages: tuple[dict, ...]
    matched_fields: tuple[str, ...]


@dataclass(frozen=True)
class CandidateQuery:
    text: str
    pages: tuple[dict, ...]
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class TargetPlan:
    status: str
    targets: tuple[CaseTarget, ...] = ()
    candidate_queries: tuple[CandidateQuery, ...] = ()


def _exact_phrase_pattern(value: str) -> re.Pattern:
    value = normalized(value)
    return re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])")


def _canonical_date(text: str) -> str | None:
    match = DATE.fullmatch(text.strip())
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


class TargetLookup:
    """Resolve exact supported identifiers or unique source-field composites."""

    def __init__(
        self,
        pages: list[dict],
        cases: dict[str, list[dict]],
        profiles: dict,
        *,
        curated: Path = CURATED,
        case_index: Path = CASE_INDEX,
        facts: Path = FACTS,
    ):
        self.cases = cases
        self.profiles = profiles
        self.page_roles = {
            _source_key(page): (case_id, role)
            for case_id, linked in cases.items()
            for role, page in zip(ROLES, linked, strict=True)
        }
        self.id_to_cases: dict[str, set[str]] = defaultdict(set)
        self.id_fields: dict[str, set[str]] = defaultdict(set)
        self.field_to_cases: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self.profile_index = ProfileIndex(profiles)
        self._index_profiles(curated)
        self._index_role_facts(curated, case_index, facts)
        self.profile_patterns = tuple(self.profile_index.patterns)
        self.text_field_patterns = {
            field: self._alternation_pattern(values)
            for field, values in self.field_to_cases.items()
            if field == "equipment_id" and values
        }

    @staticmethod
    def _alternation_pattern(values) -> re.Pattern:
        alternatives = "|".join(
            re.escape(value)
            for value in sorted(values, key=lambda item: (-len(item), item))
        )
        return re.compile(rf"(?<!\w)({alternatives})(?!\w)")

    def _index_profiles(self, curated: Path) -> None:
        for case_id, (shipper, cargo, spec) in self.profiles.items():
            for field, value in zip(
                ("shipper", "cargo", "spec"),
                (shipper, cargo, spec),
                strict=True,
            ):
                self.field_to_cases[field][normalized(value)].add(case_id)
            self.id_to_cases[case_id].add(case_id)
            self.id_fields[case_id].add("case_id")
        contracts_by_case = defaultdict(set)
        for path in sorted(curated.joinpath("records").glob("*.jsonl")):
            for row in read_rows(path):
                if row.get("kind") != "case_context":
                    continue
                cases = row.get("case_ids", [])
                if len(cases) != 1 or cases[0] not in self.cases:
                    continue
                case_id = cases[0]
                fields = row.get("fields", {})
                value = fields.get("계약", "")
                if (
                    row.get("review_status") == "parsed"
                    and fields.get("예약") == case_id
                    and value.startswith("CT-")
                    and identifiers(value) == [value]
                ):
                    contracts_by_case[case_id].add(value)
        if any(len(contracts_by_case[case]) != 1 for case in self.cases):
            raise ValueError("HG 사건에서 단일 계약 키를 확인할 수 없습니다.")
        for case_id, values in contracts_by_case.items():
            for value in values:
                self.id_to_cases[value].add(case_id)
                self.id_fields[value].add("contract_id")
                self.field_to_cases["contract_id"][value].add(case_id)

    def _index_role_facts(self, curated: Path, case_index: Path, facts: Path) -> None:
        summary_path = facts / "summary.json"
        facts_path = facts / "role_facts.jsonl"
        summary = json.loads(summary_path.read_bytes())
        manifest_sha = sha256((curated / "manifest.json").read_bytes()).hexdigest()
        links_sha = sha256((case_index / "case_sources.jsonl").read_bytes()).hexdigest()
        facts_sha = sha256(facts_path.read_bytes()).hexdigest()
        if (
            summary["curation_manifest_sha256"] != manifest_sha
            or summary["case_sources_sha256"] != links_sha
            or summary["role_facts_sha256"] != facts_sha
            or summary["cases"] != len(self.cases)
        ):
            raise ValueError("HG 역할 필드와 원본 인덱스의 지문이 다릅니다.")

        seen_facts = set()
        for row in read_rows(facts_path):
            source = row["source"]
            page_key = (Path(source["file_name"]).stem, source["page"])
            linked = self.page_roles.get(page_key)
            if linked != (row["case_id"], row["record_role"]):
                raise ValueError(
                    "정규화 필드의 사건, 역할 또는 페이지 연결이 다릅니다."
                )
            if source["source_id"] != f"advanced:{page_key[0]}:p{page_key[1]}":
                raise ValueError("정규화 필드의 원본 쪽 ID가 다릅니다.")
            if row["fact"] in DATE_FACTS | FACT_FIELDS.keys():
                fact_key = (
                    row["case_id"],
                    row["record_role"],
                    source["record_id"],
                    row["fact"],
                )
                if fact_key in seen_facts:
                    raise ValueError(
                        "검색 대상 역할 필드에 중복된 원본 사실이 있습니다."
                    )
                seen_facts.add(fact_key)
            if row["state"] != "reported":
                continue
            if row["fact"] in DATE_FACTS:
                value = _canonical_date(str(row["value"]))
                if value is None:
                    raise ValueError("확인된 날짜 필드가 ISO 날짜가 아닙니다.")
                self.field_to_cases[row["fact"]][value].add(row["case_id"])
            elif row["fact"] in FACT_FIELDS:
                field = FACT_FIELDS[row["fact"]]
                keys = identifiers(str(row["raw_value"]))
                if len(keys) != 1:
                    continue
                key = keys[0]
                field_key = normalized(key) if field == "equipment_id" else key
                self.field_to_cases[field][field_key].add(row["case_id"])
                self.id_to_cases[key].add(row["case_id"])
                self.id_fields[key].add(field)

    def _requested_roles(self, question: str) -> tuple[str, ...]:
        matched = tuple(role for role, cue in ROLE_CUES.items() if cue.search(question))
        return matched or ROLES

    def _pages_for_cases(
        self, case_ids: tuple[str, ...] | list[str], question: str
    ) -> tuple[CaseTarget, ...]:
        roles = self._requested_roles(question)
        return tuple(
            CaseTarget(
                case_id=case_id,
                pages=tuple(self.cases[case_id][ROLES.index(role)] for role in roles),
                matched_fields=(),
            )
            for case_id in sorted(set(case_ids))
        )

    def _matched_profile_values(self, text: str) -> dict[str, set[str]]:
        fields: dict[str, set[str]] = {"shipper": set(), "cargo": set(), "spec": set()}
        for field, pattern in zip(
            ("shipper", "cargo", "spec"), self.profile_patterns, strict=True
        ):
            fields[field].update(pattern.findall(text))
        for alias, spec in SPEC_ALIASES.items():
            if _exact_phrase_pattern(alias).search(text):
                fields["spec"].add(spec)
        return fields

    def _matched_exact_fields(self, question: str) -> dict[str, set[str]]:
        text = normalized(question)
        matched = {
            field: set() for field in ("equipment_id", "shipper", "cargo", "spec")
        }
        for field, pattern in self.text_field_patterns.items():
            matched[field].update(pattern.findall(text))
        for date_match in DATE.finditer(text):
            value = _canonical_date(date_match.group(0))
            if value is None:
                matched[INVALID_CONSTRAINT] = {"invalid"}
                continue
            prefix = text[max(0, date_match.start() - 22) : date_match.start()]
            suffix = text[date_match.end() : date_match.end() + 16]
            date_context = prefix + suffix
            typed_fields = {
                fact for fact, cue in DATE_CUES.items() if cue.search(date_context)
            }
            if len(typed_fields) != 1:
                matched[INVALID_CONSTRAINT] = {"invalid"}
                continue
            field = next(iter(typed_fields))
            if value not in self.field_to_cases.get(field, {}):
                matched[INVALID_CONSTRAINT] = {"invalid"}
                continue
            matched.setdefault(field, set()).add(value)

        profile_values = self._matched_profile_values(text)
        for field, values in profile_values.items():
            matched.setdefault(field, set()).update(values)
        return {field: values for field, values in matched.items() if values}

    def plan(self, question: str) -> TargetPlan:
        """Return verified case pages and any safely scoped dense-query candidates."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question은 비어 있지 않은 문자열이어야 합니다.")
        if EXCLUSION.search(question):
            return TargetPlan(status="unresolved")

        query_ids = set(identifiers(question))
        direct = {
            key: self.id_to_cases[key] for key in query_ids if key in self.id_to_cases
        }
        hg_in_query = hg_ids(question)
        if len(hg_in_query) == 1 and not direct:
            case_id = next(iter(hg_in_query))
            if case_id in self.cases:
                return TargetPlan(status="single_identifier")

        # Preserve the tested indirect profile route. Its runner still checks that
        # the linked pages appear in the lexical candidate set before adding them.
        if not query_ids:
            profile_case, route = self.profile_index.candidate(question)
            if profile_case is not None and route == "eligible":
                return TargetPlan(status="existing_profile")

        if query_ids - direct.keys():
            return TargetPlan(status="unresolved")

        constraints = self._matched_exact_fields(question)
        if INVALID_CONSTRAINT in constraints:
            return TargetPlan(status="unresolved")
        if direct:
            matched_cases = set().union(*direct.values())
            if len(matched_cases) == 1 and len(direct) == 1 and len(hg_in_query) == 1:
                return TargetPlan(status="single_identifier")
            if len(matched_cases) == 1:
                if constraints and not self._constraints_include_cases(
                    constraints, matched_cases
                ):
                    return TargetPlan(status="unresolved")
                return TargetPlan(
                    status="explicit",
                    targets=self._pages_for_cases(matched_cases, question),
                )
            # Several exact IDs can describe parallel targets only when the query
            # explicitly asks for multiple cases. A single colliding ID is not enough.
            if (
                len(matched_cases) > 1
                and MULTI_TARGET.search(question)
                and all(len(cases) == 1 for cases in direct.values())
                and self._constraints_include_cases(constraints, matched_cases)
            ):
                return TargetPlan(
                    status="explicit",
                    targets=self._pages_for_cases(matched_cases, question),
                )

            # For a colliding ID, dense reranking is allowed only with a second
            # independent exact field that narrows the same source-grounded set.
            direct_fields = set().union(
                *(self.id_fields.get(identifier, set()) for identifier in direct)
            )
            independent_fields = set(constraints) - direct_fields
            if (
                any(len(cases) > 1 for cases in direct.values())
                and independent_fields
                and all(len(values) == 1 for values in constraints.values())
            ):
                narrowed = matched_cases & self._constraint_cases(constraints)
                return self._ambiguous_plan(narrowed, question)
            return TargetPlan(status="ambiguous")

        if not constraints:
            return TargetPlan(status="unresolved")
        if any(len(values) != 1 for values in constraints.values()):
            if (
                MULTI_TARGET.search(question)
                and len(constraints.get("shipper", ())) > 1
            ):
                return self._named_scope(question, constraints)
            return TargetPlan(status="unresolved")

        field_candidates = []
        for field, values in constraints.items():
            cases_for_field = set().union(
                *(self.field_to_cases[field].get(value, set()) for value in values)
            )
            if cases_for_field:
                field_candidates.append((field, cases_for_field))
        if len(field_candidates) < 2:
            return self._named_scope(question, constraints)

        candidate_cases = set.intersection(*(cases for _, cases in field_candidates))
        matched_fields = tuple(field for field, _ in field_candidates)
        if len(candidate_cases) == 1:
            case_id = next(iter(candidate_cases))
            targets = self._pages_for_cases([case_id], question)
            target = CaseTarget(
                case_id=targets[0].case_id,
                pages=targets[0].pages,
                matched_fields=matched_fields,
            )
            return TargetPlan(status="composite", targets=(target,))
        if len(candidate_cases) > 1:
            if "shipper" in constraints or DATE_FACTS & constraints.keys():
                return self._ambiguous_plan(
                    candidate_cases, question, limit=MAX_NAMED_SCOPE_CASES
                )
            return self._ambiguous_plan(candidate_cases, question)
        return TargetPlan(status="unresolved")

    def _constraint_cases(self, constraints: dict[str, set[str]]) -> set[str]:
        if not constraints or any(len(values) != 1 for values in constraints.values()):
            return set()
        per_field = [
            set(self.field_to_cases[field].get(next(iter(values)), set()))
            for field, values in constraints.items()
        ]
        return set.intersection(*per_field) if per_field and all(per_field) else set()

    def _constraints_include_cases(
        self, constraints: dict[str, set[str]], case_ids: set[str]
    ) -> bool:
        if not constraints:
            return True
        scoped = self._constraint_cases(constraints)
        return bool(scoped) and case_ids <= scoped

    def _ambiguous_plan(
        self,
        case_ids: set[str],
        question: str,
        *,
        limit: int = MAX_SCOPED_CASES,
    ) -> TargetPlan:
        if not case_ids or len(case_ids) > limit:
            return TargetPlan(status="ambiguous")
        targets = self._pages_for_cases(sorted(case_ids), question)
        candidate = CandidateQuery(
            text=question,
            pages=tuple(page for target in targets for page in target.pages),
            case_ids=tuple(target.case_id for target in targets),
        )
        return TargetPlan(
            status="ambiguous",
            candidate_queries=(candidate,),
        )

    def _named_scope(self, question, constraints):
        # A single known shipper or explicitly typed date bounds semantic search.
        # Separate named shippers may supply independent scopes, not exact matches.
        for field in ("shipper", *sorted(DATE_FACTS)):
            values = constraints.get(field, ())
            if not 1 <= len(values) <= 4:
                continue
            queries = []
            for value in sorted(values):
                cases = self.field_to_cases[field].get(value, set())
                plan = self._ambiguous_plan(
                    cases, question, limit=MAX_NAMED_SCOPE_CASES
                )
                queries.extend(plan.candidate_queries)
            if queries:
                return TargetPlan(
                    status="scoped_dense", candidate_queries=tuple(queries)
                )
        return TargetPlan(status="unresolved")


def load_target_lookup(
    pages: list[dict] | None = None,
    *,
    curated: Path = CURATED,
    case_index: Path = CASE_INDEX,
    facts: Path = FACTS,
) -> TargetLookup:
    if pages is None:
        pages = json.loads(CACHE_PATH.read_bytes())
    manifest_sha = sha256((curated / "manifest.json").read_bytes()).hexdigest()
    cases = load_cases(case_index, pages, manifest_sha)
    profiles = load_profiles(curated, cases)
    return TargetLookup(
        pages, cases, profiles, curated=curated, case_index=case_index, facts=facts
    )
