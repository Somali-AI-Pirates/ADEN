from unittest.mock import Mock

import pytest

from app.generation.calendar import calendar_reference
from app.generation.chain import generate_answer
from app.service.query import answer_query
from shared.contracts import Chunk, QueryRequest
from shared.exceptions import ServiceError
from shared.settings import load_settings
from tests.generation.test_calendar import QUESTION, SCOPE, RULE, context
from tests.pipeline_fakes import FakeModel, make_query_resources

ASOF = "2030-03-31 16:00 KST"
QUESTION_AT_TIME = QUESTION.replace("2030-03-31", ASOF)
FREE = "계약 CT-00001. 비용별 최초 계약 조건 비용 기본 무료 첫 유료 구간 다음 구간 보관료 2일 장비반납지연료 1일. "
APPROVAL = "계약 CT-00001. 대조 기준 " + ASOF + ". 승인 대조 추가 변경 없음; 최초 계약 적용."
CORRECT = ("보관료와 장비반납지연료의 날짜는 다음과 같습니다. [S1][S2][S3]\n"
    "보관료 무료 날짜: 2030-03-13(수), 2030-03-14(목) [S1][S2][S3]\n"
    "보관료 유료 날짜: 2030-03-15(금), 2030-03-18(월), 2030-03-19(화), 2030-03-20(수), 2030-03-21(목) [S1][S2][S3]\n"
    "장비반납지연료 무료 날짜: 2030-03-22(금) [S1][S2][S3]\n"
    "장비반납지연료 유료 날짜: 2030-03-23(토), 2030-03-24(일), 2030-03-25(월), 2030-03-26(화), 2030-03-27(수) [S1][S2][S3]")


def confirmed_context(approval=APPROVAL, free=FREE, extra=()):
    return context([Chunk("approval", "DOC3", "DOC3.pdf", 1, approval, SCOPE), *extra], rule=free+RULE)


def test_calendar_calculates_free_and_paid_dates_from_confirmed_unchanged_terms():
    notes = calendar_reference(QUESTION_AT_TIME, confirmed_context())
    assert "보관료 무료 날짜: 2030-03-13(수), 2030-03-14(목)" in notes
    assert "보관료 유료 날짜: 2030-03-15(금), 2030-03-18(월)" in notes
    assert "장비반납지연료 무료 날짜: 2030-03-22(금)" in notes


@pytest.mark.parametrize("answer", [
    CORRECT.replace("2030-03-15(금), 2030-03-18(월), 2030-03-19(화), 2030-03-20(수), 2030-03-21(목)", "2030-03-17~2030-03-21"),
    CORRECT.replace("2030-03-15(금)", "2030-03-15(일)"),
    CORRECT.replace("2030-03-15(금), ", ""),
    CORRECT.replace("2030-03-15(금)", "2030-03-15(금), 2030-03-15(금)"),
    CORRECT.replace("2030-03-13(수), 2030-03-14(목)", "2030-03-14(목), 2030-03-15(금)"),
    "보관료 유료 날짜는 2030-03-17부터 2030-03-21까지입니다. [S1][S2]",
])
def test_incorrect_final_calendar_fields_are_rejected(answer):
    with pytest.raises(ValueError, match="날짜 대조"):
        generate_answer(QUESTION_AT_TIME, confirmed_context(), FakeModel(["S1", "S2", "S3"], answer))


def test_correct_final_dates_are_accepted_without_context_mutation():
    given = confirmed_context()
    before = given.model_dump()
    result = generate_answer(QUESTION_AT_TIME, given, FakeModel(["S1", "S2", "S3"], CORRECT))
    assert result.model_answer.answer == CORRECT and given.model_dump() == before


def test_wrong_dates_get_feedback_and_retry_with_same_evidence():
    bad = FakeModel(["S1"], "보관료 유료 날짜는 2030-03-17부터 2030-03-21까지입니다. [S1]").invoke([])
    model = FakeModel(["S1"])
    model.invoke = Mock(side_effect=[bad, FakeModel(["S1", "S2", "S3"], CORRECT).invoke([])])
    result = generate_answer(QUESTION_AT_TIME, confirmed_context(), model, max_attempts=2)
    assert [a["error"] for a in result.attempts] == ["ValueError", None]
    assert "날짜" in result.attempts[1]["model_input"][0]["content"]
    assert result.attempts[0]["model_input"][1] == result.attempts[1]["model_input"][1]


@pytest.mark.parametrize("approval", [
    "승인 효력 확인 전입니다.",
    APPROVAL.replace("추가 변경 없음; 최초 계약 적용", "무료기간 연장 요청, 승인 효력 확인 전"),
    APPROVAL.replace("16:00", "17:00"),
])
def test_unconfirmed_changed_or_future_approval_does_not_force_paid_dates(approval):
    notes = calendar_reference(QUESTION_AT_TIME, confirmed_context(approval=approval))
    assert "보관료 유료 날짜:" not in notes


def test_conflicting_free_days_do_not_force_paid_dates():
    other = Chunk("other-contract", "DOC4", "DOC4.pdf", 1, FREE.replace("보관료 2일", "보관료 3일"), SCOPE)
    assert "보관료 유료 날짜:" not in calendar_reference(QUESTION_AT_TIME, confirmed_context(extra=[other]))


def test_no_fixed_asof_or_free_period_does_not_invent_paid_dates():
    assert "보관료 유료 날짜:" not in calendar_reference(QUESTION, confirmed_context())
    assert "보관료 유료 날짜:" not in calendar_reference(QUESTION_AT_TIME, confirmed_context(free=""))


def test_service_does_not_publish_wrong_dates_after_retry_limit():
    resources = make_query_resources()
    given = confirmed_context()
    resources.evidence_provider = lambda q, m: (given.final_chunks, given)
    resources.model = FakeModel(["S1"], "보관료 유료 날짜는 2030-03-17부터 2030-03-21까지입니다. [S1]")
    resources.model.invoke = Mock(wraps=resources.model.invoke)
    settings = load_settings()
    try:
        with pytest.raises(ServiceError) as caught:
            answer_query(QueryRequest(question=QUESTION_AT_TIME), resources, "wrong-calendar", settings)
        assert caught.value.status_code == 502
        assert resources.model.invoke.call_count == settings.generation_max_attempts
    finally:
        resources.close()
