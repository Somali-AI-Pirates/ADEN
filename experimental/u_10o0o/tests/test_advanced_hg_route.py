from types import SimpleNamespace

import pytest

from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_hg_route import decide

CASE = "HG2099-99001"
OTHER = "HG2099-99002"


class Lookup:
    def _matched_profile_values(self, text):
        return {
            "shipper": {"갑상사"} if "갑상사" in text else set(),
            "cargo": {"센서"} if "센서" in text else set(),
            "spec": {"20gp"} if "20gp" in text else set(),
        }


class Relations:
    def __init__(self):
        self.fields = []

    def code_candidates(self, value):
        return {
            CASE: {CASE},
            OTHER: {OTHER},
            "EV-FAKE-99001-A": {CASE},
            "CT-90001": {CASE, OTHER},
            "HL-20GP-99001": {CASE},
            "HL-20GP-99002": {CASE},
        }.get(value, set())

    def identify(self, value):
        if value == "HL-20GP-99001":
            return [
                SimpleNamespace(
                    case_id=CASE,
                    related_code_type="equipment_class",
                    related_code="20GP",
                )
            ]
        return []

    def identities_for(self, field, value):
        self.fields.append((field, value))
        return {
            ("shipper", "갑상사"): {CASE, OTHER},
            ("cargo", "센서"): {CASE},
            ("spec", "20gp"): {CASE, OTHER},
            ("actual_discharge_date", "2099-01-02"): {CASE},
            ("actual_discharge_date", "2099-01-03"): {OTHER},
        }.get((field, value), set())


def proposal(anchor, cue, *, date_type="unspecified", route="lookup"):
    target = SimpleNamespace(
        anchor=anchor,
        cues=[SimpleNamespace(raw=cue, kind="identifier" if "-" in cue else "shipper")],
        roles=[ROLES[3]],
        date_type=date_type,
    )
    return SimpleNamespace(route=route, targets=[target])


def test_lookup_binds_exact_evidence_code_and_keeps_as_of_out_of_identity():
    relations = Relations()
    anchor = "증빙 EV-FAKE-99001-A 건의 청구 명세를 보여줘."
    question = "2099-02-03 16:00 KST 기준 " + anchor
    chosen = decide(
        question,
        Lookup(),
        relations,
        proposal(anchor, "EV-FAKE-99001-A"),
        tool_requested=True,
    )
    assert chosen.path == "lookup"
    assert [(case.case_id, case.roles) for case in chosen.cases] == [
        (CASE, (ROLES[3],))
    ]
    assert not relations.fields


@pytest.mark.parametrize(("day", "expected"), [("02", "lookup"), ("03", "hg_search")])
def test_discharge_date_is_verified_independently_from_as_of(day, expected):
    relations = Relations()
    anchor = f"갑상사의 2099-01-{day} 양하 센서 건에서 청구액은?"
    question = "2099-02-03 16:00 KST 기준 " + anchor
    result = decide(
        question,
        Lookup(),
        relations,
        proposal(anchor, "갑상사", date_type="actual"),
        tool_requested=True,
    )
    assert result.path == expected
    assert ("actual_discharge_date", f"2099-01-{day}") in relations.fields
    assert not any(value == "2099-02-03" for _, value in relations.fields)


def test_truncated_equipment_code_is_not_promoted_to_a_case():
    anchor = f"{CASE}와 HL-20GP-9900의 청구 기록"
    decision = decide(
        anchor,
        Lookup(),
        Relations(),
        proposal(anchor, "HL-20GP-9900"),
        tool_requested=True,
    )
    assert decision.path == "hg_search"
    assert not decision.cases


def test_unknown_prefix_cannot_be_ignored_even_with_a_verified_reservation():
    anchor = f"{CASE}와 ZZ-1234의 청구 명세"
    decision = decide(
        anchor, Lookup(), Relations(), proposal(anchor, CASE), tool_requested=True
    )
    assert decision.path == "hg_search"
    assert not decision.cases
    assert decide("ZZ-1234의 청구 명세", Lookup(), Relations()).path == "hg_search"


def test_contract_and_equipment_class_can_still_be_ambiguous():
    anchor = "계약 CT-90001의 20GP 청구 명세"
    decision = decide(
        anchor, Lookup(), Relations(), proposal(anchor, "CT-90001"), tool_requested=True
    )
    assert decision.path == "clarify"
    assert decision.candidate_count == 2
    assert not decision.cases


@pytest.mark.parametrize(
    ("equipment", "path"), [("HL-20GP-99001", "lookup"), ("HL-20GP-99002", "hg_search")]
)
def test_equipment_class_must_be_linked_to_the_exact_equipment_code(equipment, path):
    anchor = f"{equipment} 장비의 20GP 청구 명세"
    result = decide(
        anchor, Lookup(), Relations(), proposal(anchor, equipment), tool_requested=True
    )
    assert result.path == path


def test_an_explicit_unsupported_equipment_class_cannot_be_ignored():
    question = "HL-20GP-99001 장비의 40HC 청구 명세"
    result = decide(
        question,
        Lookup(),
        Relations(),
        proposal(question, "HL-20GP-99001"),
        tool_requested=True,
    )
    assert result.reason == "unverified_equipment_class"
    assert result.path == "hg_search"


def test_model_cannot_discard_a_second_explicit_case():
    anchor = f"{CASE}의 청구 명세"
    decision = decide(
        anchor + f"와 {OTHER}의 수납을 비교해줘",
        Lookup(),
        Relations(),
        proposal(anchor, CASE),
        tool_requested=True,
    )
    assert decision.reason == "unbound_identifier"
    assert decision.path == "hg_search"


def test_mixed_keeps_general_search_and_no_tool_has_dedicated_fallback():
    question = f"{CASE}의 청구액과 일반 업무 규정을 같이 보여줘"
    assert (
        decide(
            question,
            Lookup(),
            Relations(),
            proposal(question, CASE),
            tool_requested=True,
        ).path
        == "mixed"
    )
    assert decide(question, Lookup(), Relations()).path == "hg_search"
    assert decide("일반 반납 절차가 뭐야?", Lookup(), Relations()).path == "general"


def test_model_date_without_hg_case_cues_cannot_force_hg_fallback():
    question = "2099-01-02 접수 기준으로 해당 통지 절차가 어떻게 되나요?"
    selected = decide(
        question, Lookup(), Relations(), proposal(question, "접수"), tool_requested=True
    )
    assert selected.reason == "unverified_date_type"
    assert selected.path == "general"
