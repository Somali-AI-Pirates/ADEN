"""Identify ambiguous HG storage-charge questions from verified event records."""

import json
import re
from collections import defaultdict
from datetime import date
from hashlib import sha256

from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    PARTICLE,
    ProfileIndex,
    load_cases,
    load_profiles,
    normalized,
)
from experimental.u_10o0o.advanced_role_context import FACTS
from shared.contracts import ModelAnswer

DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
GATE_OUT = re.compile(
    r"\s*(?:에\s*)?(?:실제로\s*)?(?:(?:적입\s*)?반출(?:한|된)|밖으로\s*나간)"
)
STORAGE = re.compile(r"보관료|보관\s*비용")
AMOUNT = re.compile(r"얼마|금액|청구액")
OTHER_SCOPE = re.compile(
    r"제외|아닌|아니[라고며]|말고|빼고|예정|계획|반납|합계|합산|평균|모두|각각|비교|전체|~"
)
SPEC_ALIASES = {"20피트 일반형 컨테이너": "20gp", "40피트 일반형 컨테이너": "40gp"}


class StorageCaseLookup:
    def __init__(self, cases, profiles, gate_days):
        self.cases = cases
        self.shipper = ProfileIndex(profiles).patterns[0]
        self.cargo_names = {profile[1] for profile in profiles.values()}
        self.by_event = defaultdict(list)
        specs = {profile[2] for profile in profiles.values()}
        self.specs = {spec: spec for spec in specs} | SPEC_ALIASES
        alternatives = "|".join(
            re.escape(key)
            for key in sorted(self.specs, key=lambda key: (-len(key), key))
        )
        self.spec_pattern = re.compile(rf"(?<!\w)({alternatives}){PARTICLE}(?!\w)")
        for case, day in sorted(gate_days.items()):
            shipper, _, spec = profiles[case]
            self.by_event[shipper, spec, day].append(case)

    def candidates(self, question):
        text = normalized(question)
        if (
            identifiers(text.upper())
            or OTHER_SCOPE.search(text)
            or not STORAGE.search(text)
            or not AMOUNT.search(text)
        ):
            return ()

        dates = list(DATE.finditer(text))
        shippers = set(self.shipper.findall(text))
        specs = {self.specs[value] for value in self.spec_pattern.findall(text)}
        if len(dates) != 1 or len(shippers) != 1 or len(specs) != 1:
            return ()
        match = dates[0]
        event = GATE_OUT.match(text, match.end())
        if not event:
            return ()
        subject = text[event.end() :].lstrip()
        party = self.shipper.match(subject)
        if not party or not self.spec_pattern.match(subject[party.end() :].lstrip()):
            return ()
        try:
            day = date(*map(int, match.groups())).isoformat()
        except ValueError:
            return ()
        key = next(iter(shippers)), next(iter(specs)), day
        candidates = self.by_event.get(key, ())
        if candidates and any(cargo in text for cargo in self.cargo_names):
            return ()
        return tuple(candidates)

    def evidence_pages(self, candidates):
        return [self.cases[case][role] for case in candidates for role in (1, 3)]

    def clarify(self, question):
        if len(self.candidates(question)) < 2:
            return None
        return ModelAnswer(
            status="needs_clarification",
            answer="같은 조건에 해당하는 반출 건이 여러 개입니다. 확인하실 건의 화물명이나 예약번호를 알려주시겠어요?",
            reason="화주, 장비 규격, 실제 반출일이 일치하는 HG 사건이 여러 건입니다.",
            source_ids=[],
        )


def load_storage_lookup(pages, *, curated=CURATED, case_index=CASE_INDEX, facts=FACTS):
    manifest_sha = sha256((curated / "manifest.json").read_bytes()).hexdigest()
    cases = load_cases(case_index, pages, manifest_sha)
    profiles = load_profiles(curated, cases)
    path = facts / "role_facts.jsonl"
    summary = json.loads((facts / "summary.json").read_bytes())
    if (
        summary["curation_manifest_sha256"] != manifest_sha
        or summary["case_sources_sha256"]
        != sha256((case_index / "case_sources.jsonl").read_bytes()).hexdigest()
        or summary["role_facts_sha256"] != sha256(path.read_bytes()).hexdigest()
    ):
        raise ValueError("반출일 기록의 입력 지문이 다릅니다.")
    gate_days = {}
    for row in read_rows(path):
        if row["fact"] != "actual_gate_out_date":
            continue
        case, source, value = row["case_id"], row["source"], row["value"]
        if case not in cases or case in gate_days:
            raise ValueError("실제 반출일의 사건 출처가 누락되거나 중복됐습니다.")
        page = cases[case][1]
        if (
            row["state"] != "reported"
            or row["record_role"] != ROLES[1]
            or (source["file_name"], source["page"])
            != (page["file_name"], page["page"])
            or date.fromisoformat(value).isoformat() != value
            or value not in page["text"]
        ):
            raise ValueError("실제 반출일과 복원 원본이 일치하지 않습니다.")
        gate_days[case] = value
    if gate_days.keys() != cases.keys():
        raise ValueError("실제 반출일을 확인하지 못한 사건이 있습니다.")
    return StorageCaseLookup(cases, profiles, gate_days)
