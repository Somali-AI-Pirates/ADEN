from unittest.mock import Mock

import pytest

from app.evaluation.samples import FakeLangfuseClient
from app.generation.chain import generate_answer
from app.generation.context import build_context
from app.observability.tracing import Tracer
from app.service.query import answer_query
from shared.contracts import Chunk, QueryRequest, RetrievedChunk
from shared.exceptions import ServiceError
from shared.settings import load_settings
from tests.pipeline_fakes import FakeModel, make_query_resources


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


# 부정 표현을 검산 미완료 표기로 오인하지 않는다(PR #53 검토)
INCOMPLETE_REASON = "산입 경계가 없어 검산 미완료입니다. [S1]"
NEGATED_ANSWERS = [
    "검산 미완료가 아닙니다. 규칙과 금액을 모두 확인했습니다. [S1]",
    "검산 미완료가 아니라 완료입니다. 청구서상 금액은 USD 80입니다. [S1]",
    "**검산 미완료**가 아닙니다. 청구서상 금액은 USD 80입니다. [S1]",
    "검산은 미완료 상태가 아닙니다. 청구서상 금액은 USD 80입니다. [S1]",
]
FIXED_ANSWER = "청구서상 금액은 USD 80입니다. 산입 경계 규칙을 확인하지 못해 검산 미완료입니다. [S1]"


def evidence():
    return build_context([RetrievedChunk(Chunk("DEMO", "DEMO", "DEMO.pdf", 1,
        "청구서상 금액은 USD 80입니다."), 1, 1.0, "fixed")], 1, None, len)


def scripted(*replies):
    """시도마다 (answer, reason)을 차례로 돌려주는 모델."""
    model = FakeModel(["S1"])
    original, queue = model.invoke, list(replies)

    def invoke(*args, **kwargs):
        value = original(*args, **kwargs)
        value["parsed"].answer, value["parsed"].reason = queue.pop(0)
        return value

    model.invoke = Mock(side_effect=invoke)
    return model


@pytest.mark.parametrize("negated", NEGATED_ANSWERS)
def test_negated_incomplete_answer_is_retried_with_same_question_and_evidence(negated):
    model = scripted((negated, INCOMPLETE_REASON), (FIXED_ANSWER, INCOMPLETE_REASON))
    client = FakeLangfuseClient()
    trace = Tracer(client).start_request("negated", question="비용을 검산해 주세요.", retrieval_mode="naive",
                                         dataset_id="test", config_hash="test")
    result = generate_answer("비용을 검산해 주세요.", evidence(), model, trace=trace, max_attempts=2)
    first, second = result.attempts
    assert (first["error"], second["error"]) == ("ValueError", None)
    assert result.model_answer.answer == FIXED_ANSWER
    assert first["model_input"][1] == second["model_input"][1]  # 같은 질문·문맥
    assert "검산 미완료" in second["model_input"][0]["content"]
    names = [o["name"] for o in client.observations]
    assert "generation-attempt-1" in names and "generation-attempt-2" in names


@pytest.mark.parametrize("answer", [
    "**검산 미완료**: 산입 경계 규칙을 확인하지 못했습니다. 청구서상 금액은 USD 80입니다. [S1]",
    "`검산 미완료` 산입 경계 규칙 미확인, 청구서상 금액 USD 80. [S1]",
    "보관료(검산 미완료): 청구서상 금액 USD 80, 산입 경계 규칙 미확인. [S1]",
    "보관료는 검산 미완료입니다. 반납지연료는 검산 미완료가 아니라 계약 규칙으로 확인했습니다. [S1]",
])
def test_visible_incomplete_mark_passes_first_attempt(answer):
    model = scripted((answer, INCOMPLETE_REASON))
    assert len(generate_answer("비용을 검산해 주세요.", evidence(), model, max_attempts=2).attempts) == 1


@pytest.mark.parametrize("reason", ["근거를 대조했으므로 검산 미완료가 아닙니다. [S1]",
                                    "검산 미완료가 아니라 완료입니다. [S1]"])
def test_negated_reason_does_not_require_retry(reason):
    model = scripted(("규칙과 실제 사건을 대조한 금액은 USD 80입니다. [S1]", reason))
    assert len(generate_answer("비용을 검산해 주세요.", evidence(), model, max_attempts=2).attempts) == 1


def test_negated_answer_exhausting_retries_keeps_existing_error_contract():
    given = evidence()
    with pytest.raises(ValueError, match="검산 구분"):
        generate_answer("비용을 검산해 주세요.", given, scripted(*[(NEGATED_ANSWERS[0], INCOMPLETE_REASON)] * 2),
                        max_attempts=2)
    resources = make_query_resources()
    resources.evidence_provider = lambda question, mode: (given.final_chunks, given)
    settings = load_settings()
    resources.model = scripted(*[(NEGATED_ANSWERS[0], INCOMPLETE_REASON)] * settings.generation_max_attempts)
    record = {}
    try:
        with pytest.raises(ServiceError) as caught:
            answer_query(QueryRequest(question="비용을 검산해 주세요."), resources, "negated-limit", settings,
                         run_record=record)
    finally:
        resources.close()
    assert caught.value.status_code == 502
    assert (record["status"], record["error_type"]) == ("error", "ValueError")
    assert len(record["attempts"]) == resources.model.invoke.call_count == settings.generation_max_attempts
