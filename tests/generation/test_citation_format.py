"""Citation syntax errors must not escape the generation validator."""
from unittest.mock import Mock

import pytest

from app.generation.chain import generate_answer
from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk
from tests.pipeline_fakes import FakeModel


@pytest.fixture
def context():
    return build_context([RetrievedChunk(Chunk("rule", "DOC", "DOC.pdf", 1,
        "예약 접수와 확정은 별도입니다."), 1, 1.0, "fixed")], 1, None, len)


def model_output(**updates):
    output = FakeModel(["S1"]).invoke([])
    output["parsed"] = output["parsed"].model_copy(update=updates)
    return output


@pytest.mark.parametrize("field", ["answer", "reason"])
@pytest.mark.parametrize("mark", ["[S1, S99]", "[S1-S99]", "[ S1 ]", "[s1]", "[S1", "[S1\n]"])
def test_malformed_citation_is_rejected_in_answer_and_reason(context, field, mark):
    model = FakeModel(["S1"])
    model.invoke = Mock(return_value=model_output(**{field: "확인한 사실입니다. " + mark}))
    with pytest.raises(ValueError, match="출처 표기"):
        generate_answer("예약 확정 조건을 알려 주세요.", context, model)


@pytest.mark.parametrize("reason", ["조건을 확인했습니다.", "조건을 확인했습니다. [S1]"])
def test_answered_requires_a_body_citation_even_with_source_ids(context, reason):
    model = FakeModel(["S1"])
    model.invoke = Mock(return_value=model_output(answer="예약 접수와 확정은 별도입니다.", reason=reason))
    with pytest.raises(ValueError, match="본문.*출처"):
        generate_answer("예약 확정 조건을 알려 주세요.", context, model)


@pytest.mark.parametrize("status", ["needs_clarification", "abstained"])
def test_non_answered_can_ask_or_abstain_without_citations(context, status):
    model = FakeModel([])
    model.invoke = Mock(return_value=model_output(status=status, answer="대상 예약을 확인해 주세요.",
        reason="대상을 정할 수 없습니다.", source_ids=[]))
    assert generate_answer("예약 상태를 알려 주세요.", context, model).model_answer.status == status


def test_malformed_citation_uses_existing_retry_and_preserves_attempts(context):
    model = FakeModel(["S1"])
    model.invoke = Mock(side_effect=[model_output(reason="확인했습니다. [S1, S99]"), model_output()])
    result = generate_answer("예약 확정 조건을 알려 주세요.", context, model, max_attempts=2)
    assert [attempt["error"] for attempt in result.attempts] == ["ValueError", None]
    assert result.model_answer.source_ids == ["S1"]


def test_normal_bracketed_words_are_not_mistaken_for_citations(context):
    result = generate_answer("예약 확정 조건을 알려 주세요.", context,
        FakeModel(["S1"], "[예약] 접수와 확정은 별도입니다. [S1]"))
    assert result.model_answer.status == "answered"
