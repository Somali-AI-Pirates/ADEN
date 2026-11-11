import re

import pytest
import tiktoken

from app.generation.calendar import calendar_reference, validate_calendar_dates
from shared.contracts import Chunk, ModelAnswer
from shared.settings import load_settings
from tests.generation.test_calendar import SCOPE, RULE, context
from tests.generation.test_calendar_validation import FREE, QUESTION_AT_TIME, CORRECT, confirmed_context


def answer(text):
    return ModelAnswer(status="answered", answer=text, reason="계약과 실제 사건을 대조했습니다. [S1][S2][S3]", source_ids=["S1", "S2", "S3"])


def date_rows(notes):
    return [line for line in notes.splitlines() if re.match(r"^(보관료|장비반납지연료) (무료|유료) 날짜:", line)]


def test_year_long_periods_fit_output_budget_and_round_trip_without_losing_dates():
    asof="2032-04-01 16:00 KST"
    question=f"예약 HG2030-00001, 장비 HL-40GP-00001의 {asof} 기준 비용을 검산해 주세요."
    approval=f"계약 CT-00001. 대조 기준 {asof}. 승인 대조 추가 변경 없음; 최초 계약 적용."
    events="실제 양하일은 2030-03-12, 실제 적입 장비 반출일은 2031-03-12이다. 실제 공장비 반납일은 2032-03-12로 확인됐다."
    given=context([Chunk("approval","DOC3","DOC3.pdf",1,approval,SCOPE)],rule=FREE+RULE,event=events)
    rows=date_rows(calendar_reference(question,given))
    assert len(rows)==4 and any("~" in row for row in rows)
    encoding=tiktoken.get_encoding(load_settings().context_tokenizer)
    envelope=answer("\n".join(rows)).model_dump_json()
    assert len(encoding.encode(envelope)) < load_settings().max_output_tokens//2
    validate_calendar_dates(answer("\n".join(rows)),question,given)


def test_explicit_weekday_range_preserves_m19_nonconsecutive_paid_dates():
    text=CORRECT.replace("2030-03-15(금), 2030-03-18(월), 2030-03-19(화), 2030-03-20(수), 2030-03-21(목)",
                         "2030-03-15(금)~2030-03-21(목) (월~금)")
    validate_calendar_dates(answer(text),QUESTION_AT_TIME,confirmed_context())


@pytest.mark.parametrize("replacement", [
    "2030-03-15~2030-03-21", "2030-03-18~2030-03-21 (월~금)",
    "2030-03-21~2030-03-15 (월~금)", "2030-03-15(일)~2030-03-21(목) (월~금)",
    "2030-03-15~2030-03-21 (월~금), 2030-03-21",
])
def test_ranges_cannot_hide_weekends_omissions_reversed_dates_or_duplicates(replacement):
    text=CORRECT.replace("2030-03-15(금), 2030-03-18(월), 2030-03-19(화), 2030-03-20(수), 2030-03-21(목)",replacement)
    with pytest.raises(ValueError,match="날짜 대조"):
        validate_calendar_dates(answer(text),QUESTION_AT_TIME,confirmed_context())


def test_calendar_range_keeps_saturday_sunday_and_inclusive_end():
    text=CORRECT.replace("2030-03-23(토), 2030-03-24(일), 2030-03-25(월), 2030-03-26(화), 2030-03-27(수)","2030-03-23~2030-03-27")
    validate_calendar_dates(answer(text),QUESTION_AT_TIME,confirmed_context())
    with pytest.raises(ValueError,match="날짜 대조"):
        validate_calendar_dates(answer(text.replace("2030-03-23~2030-03-27","2030-03-23~2030-03-27 (월~금)")),QUESTION_AT_TIME,confirmed_context())


@pytest.mark.parametrize("scope,label", [
    ("보관료만 설명해 주세요.","보관료"),
    ("장비반납지연료만 알려 주세요.","장비반납지연료"),
    ("반납지연료 부분만 검산해 주세요.","장비반납지연료"),
    ("보관료만 설명해 주세요. 아니, 반납지연료만 설명해 주세요.","장비반납지연료"),
    ("보관료만 제외하고 설명해 주세요.","장비반납지연료"),
])
def test_cost_only_request_does_not_force_unrequested_calendar_fields(scope,label):
    question=QUESTION_AT_TIME+" "+scope
    rows=date_rows(calendar_reference(question,confirmed_context()))
    assert len(rows)==2 and all(row.startswith(label+" ") for row in rows)
    selected="\n".join(line for line in CORRECT.splitlines() if line.startswith(label+" "))
    validate_calendar_dates(answer(selected),question,confirmed_context())
    with pytest.raises(ValueError,match="날짜 대조"):
        validate_calendar_dates(answer("확인했습니다. [S1]"),question,confirmed_context())


@pytest.mark.parametrize("scope", [
    "보관료만이 아니라 장비반납지연료도 계산해 주세요.",
    "보관료만 설명해 주세요. 정정합니다. 두 비용 모두 설명해 주세요.",
])
def test_explicit_both_costs_restores_all_calendar_fields(scope):
    rows=date_rows(calendar_reference(QUESTION_AT_TIME+" "+scope,confirmed_context()))
    assert len(rows)==4
