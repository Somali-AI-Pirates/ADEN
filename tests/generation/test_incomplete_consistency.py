from unittest.mock import Mock

from app.generation.chain import generate_answer
from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk
from tests.pipeline_fakes import FakeModel


def test_incomplete_reason_requires_visible_answer_and_retries_same_evidence():
    context = build_context([RetrievedChunk(Chunk("DEMO", "DEMO", "DEMO.pdf", 1,
        "청구서상 금액은 USD 80입니다."), 1, 1.0, "fixed")], 1, None, len)
    first = FakeModel(["S1"], answer="청구서상 금액은 USD 80입니다. [S1]").invoke([])
    first["parsed"].reason = "산입 경계 규칙이 확인되지 않아 독립 검산은 미완료입니다. [S1]"
    second = FakeModel(["S1"], answer="청구서상 금액은 USD 80입니다. 산입 경계 규칙을 확인하지 못해 검산 미완료입니다. [S1]").invoke([])
    second["parsed"].reason = first["parsed"].reason
    model = FakeModel(["S1"])
    model.invoke = Mock(side_effect=[first, second])
    result = generate_answer("비용 산입을 검산해 주세요.", context, model, max_attempts=2)
    assert len(result.attempts) == 2
    assert result.attempts[0]["error"] == "ValueError"
    assert result.attempts[0]["model_input"][1] == result.attempts[1]["model_input"][1]
    assert "검산 미완료" in result.attempts[1]["model_input"][0]["content"]
    assert "본문" in result.attempts[1]["model_input"][0]["content"]


def test_incomplete_answer_does_not_require_unnecessary_retry():
    context = build_context([RetrievedChunk(Chunk("DEMO", "DEMO", "DEMO.pdf", 1,
        "청구서상 금액은 USD 80입니다."), 1, 1.0, "fixed")], 1, None, len)
    model = FakeModel(["S1"], answer="청구서상 금액은 USD 80이며, 독립 검산은 미완료입니다. [S1]")
    original = model.invoke
    def invoke(*args, **kwargs):
        value = original(*args, **kwargs)
        value["parsed"].reason = "경계 규칙이 없어 검산 미완료입니다. [S1]"
        return value
    model.invoke = Mock(side_effect=invoke)
    result = generate_answer("비용을 검산해 주세요.", context, model, max_attempts=2)
    assert len(result.attempts) == 1


def test_completed_answer_is_not_rejected_for_negated_incomplete_phrase():
    context = build_context([RetrievedChunk(Chunk("DEMO", "DEMO", "DEMO.pdf", 1,
        "적용 규칙과 실제 사건을 대조한 비용은 USD 80입니다."), 1, 1.0, "fixed")], 1, None, len)
    model = FakeModel(["S1"], answer="규칙과 실제 사건을 대조한 금액은 USD 80입니다. [S1]")
    original = model.invoke
    def invoke(*args, **kwargs):
        value = original(*args, **kwargs)
        value["parsed"].reason = "근거를 대조했으므로 검산 미완료가 아닙니다. [S1]"
        return value
    model.invoke = Mock(side_effect=invoke)
    result = generate_answer("비용을 검산해 주세요.", context, model, max_attempts=2)
    assert len(result.attempts) == 1
