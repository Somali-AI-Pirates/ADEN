from types import SimpleNamespace

import pytest

from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    DOMAIN_INSTRUCTIONS,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
    LunaInterpreter,
)
from experimental.u_10o0o.advanced_question_router import QuestionInterpretation


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response, error)


def _response():
    parsed = QuestionInterpretation(
        route="lookup",
        targets=[
            {
                "anchor": f"예약번호 HG2028-00001의 {ROLES[3]}",
                "constraints": [
                    {
                        "field": "case_id",
                        "raw": "HG2028-00001",
                        "polarity": "include",
                    }
                ],
                "roles": [ROLES[3]],
            }
        ],
    )
    return SimpleNamespace(
        output_parsed=parsed,
        status="completed",
        usage=SimpleNamespace(
            input_tokens=231,
            output_tokens=42,
            input_tokens_details=SimpleNamespace(cached_tokens=19),
            output_tokens_details=SimpleNamespace(reasoning_tokens=4),
        ),
    )


def test_luna_interpreter_uses_strict_question_only_low_effort_request():
    client = FakeClient(_response())
    interpreter = LunaInterpreter(client)
    question = f"예약번호 HG2028-00001의 {ROLES[3]}를 보여줘"

    result = interpreter.invoke(question, timeout_seconds=7.5, rules_version="rules-v3")

    assert result["route"] == "lookup"
    assert result["targets"][0]["roles"] == [ROLES[3]]
    call = client.responses.calls[0]
    assert call["model"] == MODEL_ID == "gpt-6-luna"
    assert call["instructions"] == DOMAIN_INSTRUCTIONS
    assert call["input"] == question
    assert call["text_format"] is QuestionInterpretation
    assert call["reasoning"] == {"effort": REASONING_EFFORT} == {"effort": "low"}
    assert call["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert call["tools"] == []
    assert call["store"] is False
    assert call["timeout"] == 7.5
    assert interpreter.last_call == {
        "model": "gpt-6-luna",
        "reasoning_effort": "low",
        "rules_version": "rules-v3",
        "status": "completed",
        "latency_ms": interpreter.last_call["latency_ms"],
        "api_status_code": None,
        "input_tokens": 231,
        "cached_input_tokens": 19,
        "output_tokens": 42,
        "reasoning_tokens": 4,
        "response_status": "completed",
        "started_at_utc": interpreter.last_call["started_at_utc"],
    }
    assert "question" not in interpreter.last_call
    assert "prompt" not in interpreter.last_call
    assert "output" not in interpreter.last_call


def test_luna_interpreter_sanitizes_timeout_and_records_one_attempt():
    client = FakeClient(error=TimeoutError("question text must not leak"))
    interpreter = LunaInterpreter(client)

    with pytest.raises(TimeoutError, match="timed out") as exc_info:
        interpreter.invoke("private question", timeout_seconds=1, rules_version=None)

    assert str(exc_info.value) == "GPT-6 Luna interpretation timed out."
    assert len(client.responses.calls) == 1
    assert interpreter.last_call["status"] == "timeout"
    assert interpreter.last_call["latency_ms"] >= 0
    assert "private question" not in repr(interpreter.last_call)


def test_luna_interpreter_sanitizes_provider_failure_and_missing_schema_output():
    failed_client = FakeClient(error=RuntimeError("sensitive provider response"))
    failed = LunaInterpreter(failed_client)
    with pytest.raises(RuntimeError, match="interpretation failed") as exc_info:
        failed.invoke("question", timeout_seconds=2, rules_version="v1")
    assert str(exc_info.value) == "GPT-6 Luna interpretation failed."
    assert failed.last_call["status"] == "provider_error"
    assert len(failed_client.responses.calls) == 1

    empty_client = FakeClient(
        SimpleNamespace(output_parsed=None, status="incomplete", usage=None)
    )
    empty = LunaInterpreter(empty_client)
    with pytest.raises(RuntimeError, match="no structured interpretation"):
        empty.invoke("question", timeout_seconds=2, rules_version="v1")
    assert empty.last_call["status"] == "no_parsed_output"
    assert empty.last_call["response_status"] == "incomplete"


def test_luna_interpreter_rejects_invalid_timeout_without_making_a_call():
    client = FakeClient(_response())
    interpreter = LunaInterpreter(client)

    with pytest.raises(ValueError, match="positive finite"):
        interpreter.invoke("question", timeout_seconds=float("inf"), rules_version=None)

    assert client.responses.calls == []


def test_luna_interpreter_pins_official_endpoint_and_disables_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    interpreter = LunaInterpreter()

    try:
        assert str(interpreter.client.base_url).rstrip("/") == API_BASE_URL
        assert interpreter.client.max_retries == 0
    finally:
        interpreter.client.close()
