from datetime import date

import pytest

from app.generation.calendar import calendar_reference, counted_dates
from app.generation.chain import generate_answer
from app.generation.context import build_context
from app.evaluation.samples import FakeLangfuseClient
from app.observability.tracing import Tracer
from shared.contracts import Chunk, RetrievedChunk
from tests.pipeline_fakes import FakeModel

QUESTION = "예약 HG2030-00001, 장비 HL-40GP-00001의 2030-03-31 기준 비용을 검산해 주세요."
SCOPE = "HG2030-00001 / HL-40GP-00001 / "
RULE = ("보관료는 실제 양하일을 제외하고 다음 날부터 실제 적입 장비 반출일까지 포함한다. "
        "월요일부터 금요일만 세고 주말은 제외한다. 별도 공휴일 제외는 없다. "
        "장비반납지연료는 실제 반출일로부터 1일 뒤를 첫 산입일로 하여 실제 지정 장치장 반납일을 제외한다. "
        "반납지연료는 달력일 기준이다.")
EVENT = ("실제 양하일은 2030-03-12, 실제 적입 장비 반출일은 2030-03-21이다. "
         "실제 공장비 반납일은 2030-03-28로 확인됐다.")


def context(extra=(), *, rule=RULE, event=EVENT):
    chunks = [Chunk("contract", "DOC1", "DOC1.pdf", 1, rule, SCOPE+"계약"),
              Chunk("event", "DOC2", "DOC2.pdf", 1, event, SCOPE+"실제 사건"), *extra]
    return build_context([RetrievedChunk(c, i, 1., "fixed") for i, c in enumerate(chunks, 1)], 8, None, len)


def test_weekday_paid_dates_skip_sunday_and_keep_friday():
    counted = counted_dates(date(2030, 3, 13), date(2030, 3, 21), weekdays_only=True)
    assert [d.day for d in counted] == [13, 14, 15, 18, 19, 20, 21]
    assert [d.day for d in counted[2:]] == [15, 18, 19, 20, 21]


def test_calendar_dates_keep_weekend_and_leap_day():
    assert counted_dates(date(2024, 2, 28), date(2024, 3, 2), weekdays_only=False) == [
        date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1), date(2024, 3, 2)]
    assert counted_dates(date(2031, 12, 31), date(2032, 1, 1), weekdays_only=False) == [
        date(2031, 12, 31), date(2032, 1, 1)]


@pytest.mark.parametrize("start,end", [(date(2030, 3, 21), date(2030, 3, 13)), (date(2020, 1, 1), date(2022, 1, 1))])
def test_invalid_or_unbounded_intervals_are_rejected(start, end):
    with pytest.raises(ValueError):
        counted_dates(start, end, weekdays_only=True)


def test_contract_and_actual_events_produce_separate_cost_periods():
    notes = calendar_reference(QUESTION, context())
    storage, returned = notes.split("장비반납지연료 [")
    assert "보관료 [S1][S2]: 산입 7일" in storage
    assert "2030-03-15(금)" in storage and "2030-03-17" not in storage
    assert "산입 6일" in returned and "2030-03-27(수)" in returned
    assert "2030-03-28(" not in returned


def test_inclusive_return_end_and_calendar_storage_are_preserved():
    rule = RULE.replace("월요일부터 금요일만 세고 주말은 제외한다. 별도 공휴일 제외는 없다.", "토요일·일요일을 포함한 달력 날짜를 센다.").replace("반납일을 제외한다", "반납일을 포함한다")
    notes = calendar_reference(QUESTION, context(rule=rule))
    assert "보관료 [S1][S2]: 산입 9일" in notes
    assert "장비반납지연료 [S1][S2]: 산입 7일" in notes


def test_missing_or_conflicting_evidence_does_not_invent_dates():
    assert calendar_reference(QUESTION, context(rule="계약 규칙 없음")) == ""
    assert calendar_reference(QUESTION, context(event="반납 예약 예정 2030-03-28")) == ""
    conflicting = Chunk("conflict", "DOC3", "DOC3.pdf", 1, EVENT.replace("2030-03-21", "2030-03-22"), SCOPE)
    assert calendar_reference(QUESTION, context([conflicting])) == ""
    other_rule = Chunk("rule-conflict", "DOC4", "DOC4.pdf", 1, RULE.replace("반납일을 제외한다", "반납일을 포함한다"), SCOPE)
    assert calendar_reference(QUESTION, context([other_rule])) == ""


def test_other_equipment_and_future_events_are_not_applied():
    other = Chunk("other", "DOC3", "DOC3.pdf", 1, EVENT.replace("2030-03-21", "2030-03-22"), SCOPE.replace("HL-40GP-00001", "HL-40GP-99999"))
    assert calendar_reference(QUESTION, context([other])) == calendar_reference(QUESTION, context())
    assert calendar_reference(QUESTION.replace("2030-03-31", "2030-03-11"), context()) == ""
    assert calendar_reference(QUESTION.replace("2030-03-31 기준", "2030-03-11기준"), context()) == ""


def test_mismatched_source_map_does_not_create_a_calendar_citation():
    given = context()
    given.source_map = {"S2": given.source_map["S1"], "S1": given.source_map["S2"]}
    assert calendar_reference(QUESTION, given) == ""


def test_unknown_holiday_exclusions_and_non_calculation_questions_are_untouched():
    assert calendar_reference(QUESTION, context(rule=RULE.replace("별도 공휴일 제외는 없다", "공휴일은 제외한다"))) == ""
    assert calendar_reference("예약이 확정됐나요?", context()) == ""


def test_generation_uses_local_calendar_without_changing_sources_or_extra_calls():
    given = context()
    before = given.model_dump()
    model = FakeModel(["S1"])
    result = generate_answer(QUESTION, given, model)
    assert "로컬 날짜 산입 대조표" in model.messages[0].content
    assert "2030-03-15(금)" in model.messages[0].content
    assert result.attempts[0]["calendar_reference"] in model.messages[0].content
    assert len(result.attempts) == 1 and given.model_dump() == before


def test_calendar_reference_reaches_real_trace_without_breaking_generation():
    client = FakeLangfuseClient()
    trace = Tracer(client).start_request("calendar", question=QUESTION, retrieval_mode="naive", dataset_id="advanced", config_hash="test")
    result = generate_answer(QUESTION, context(), FakeModel(["S1"]), trace=trace)
    generation = next(o for o in client.observations if o['name'] == 'generation-attempt-1')
    assert generation['ended']
    assert generation['updates'][-1]['metadata']['calendar_reference'] == result.attempts[0]['calendar_reference']
