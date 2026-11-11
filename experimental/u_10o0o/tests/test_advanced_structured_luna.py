import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
)
from experimental.u_10o0o.advanced_structured_luna import (
    DOMAIN_INSTRUCTIONS,
    FUNCTION_MODE,
    FUNCTION_NAME,
    JSON_SCHEMA_MODE,
    LOOKUP_PROPOSAL_SCHEMA,
    PROMPT_SHA256,
    SCHEMA_SHA256,
    LookupProposal,
    StructuredLuna,
)


def _proposal():
    return {
        "route": "lookup",
        "targets": [
            {
                "family": "HG",
                "anchor": "HG2030-00017 비용 청구 명세",
                "constraints": [
                    {
                        "field": "case_id",
                        "raw": "HG2030-00017",
                        "polarity": "include",
                    }
                ],
                "fields": ["비용 청구 명세"],
            }
        ],
    }


def _response(*, output=None, output_text=None, status="completed", usage=None):
    return SimpleNamespace(
        output=output or [],
        output_text=output_text,
        status=status,
        usage=usage
        or SimpleNamespace(
            input_tokens=230,
            output_tokens=44,
            input_tokens_details=SimpleNamespace(cached_tokens=12),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
    )


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response, error)


def test_function_tool_mode_returns_proposal_without_executing_the_tool():
    call = SimpleNamespace(
        type="function_call",
        name=FUNCTION_NAME,
        arguments=json.dumps(_proposal(), ensure_ascii=False),
    )
    client = FakeClient(_response(output=[call]))
    interpreter = StructuredLuna(client)
    question = "HG2030-00017 비용 청구 명세를 확인해줘"

    result = interpreter.invoke(question, mode="tool")

    assert result.status == "completed"
    assert result.proposal == LookupProposal.model_validate(_proposal())
    assert result.tool_requested is True
    assert result.telemetry.attempted is True
    assert result.telemetry.mode == "tool"
    assert result.telemetry.input_tokens == 230
    assert result.telemetry.cached_input_tokens == 12
    assert result.telemetry.output_tokens == 44
    assert result.telemetry.reasoning_tokens == 5
    assert result.telemetry.latency_ms >= 0
    assert "question" not in result.telemetry.as_dict()

    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == MODEL_ID == "gpt-6-luna"
    assert request["input"] == question
    assert request["instructions"] == DOMAIN_INSTRUCTIONS
    assert request["reasoning"] == {"effort": REASONING_EFFORT} == {"effort": "low"}
    assert request["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert request["store"] is False
    assert request["timeout"] == 10.0
    assert request["parallel_tool_calls"] is False
    assert "max_tool_calls" not in request
    assert request["tool_choice"] == "auto"
    assert "text" not in request
    assert request["tools"] == [
        {
            "type": "function",
            "name": FUNCTION_NAME,
            "description": request["tools"][0]["description"],
            "parameters": LOOKUP_PROPOSAL_SCHEMA,
            "strict": True,
        }
    ]
    assert "do not execute" in request["tools"][0]["description"]


def test_json_schema_mode_uses_the_same_prompt_and_wire_shape():
    client = FakeClient(
        _response(output_text=json.dumps(_proposal(), ensure_ascii=False))
    )
    interpreter = StructuredLuna(client)

    result = interpreter.invoke("HG2030-00017 비용 청구 명세를 확인해줘", mode="json")

    assert result.status == "completed"
    assert result.proposal == LookupProposal.model_validate(_proposal())
    assert result.tool_requested is False
    request = client.responses.calls[0]
    assert request["instructions"] == DOMAIN_INSTRUCTIONS
    assert request["model"] == MODEL_ID
    assert request["tools"] == []
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": FUNCTION_NAME,
        "schema": LOOKUP_PROPOSAL_SCHEMA,
        "strict": True,
    }
    assert request["input"] == "HG2030-00017 비용 청구 명세를 확인해줘"
    assert PROMPT_SHA256
    assert SCHEMA_SHA256


def test_auto_tool_mode_leaves_no_call_to_the_rules_fallback():
    safe_no_tool = {"route": "documents", "targets": []}
    client = FakeClient(_response(output_text=json.dumps(safe_no_tool)))

    result = StructuredLuna(client).invoke("절차를 알려줘", mode="tool")

    assert result.status == "no_tool_call"
    assert result.proposal is None
    assert result.tool_requested is False

    unsafe_no_tool = {"route": "lookup", "targets": [_proposal()["targets"][0]]}
    client = FakeClient(_response(output_text=json.dumps(unsafe_no_tool)))
    result = StructuredLuna(client).invoke("HG2030-00017 청구내역", mode="tool")
    assert result.status == "no_tool_call"
    assert result.proposal is None
    assert result.tool_requested is False


def test_function_call_item_is_counted_even_when_its_arguments_are_invalid():
    call = SimpleNamespace(
        type="function_call", name=FUNCTION_NAME, arguments='{"route":"lookup"}'
    )
    client = FakeClient(_response(output=[call]))

    result = StructuredLuna(client).invoke("question", mode=FUNCTION_MODE)

    assert result.status == "invalid_proposal"
    assert result.proposal is None
    assert result.tool_requested is True
    assert len(client.responses.calls) == 1


def test_function_mode_rejects_wrong_or_multiple_function_calls():
    wrong = SimpleNamespace(
        type="function_call", name="another_tool", arguments=json.dumps(_proposal())
    )
    client = FakeClient(_response(output=[wrong]))

    result = StructuredLuna(client).invoke("question", mode=FUNCTION_MODE)

    assert result.status == "invalid_proposal"
    assert result.tool_requested is True


def test_timeout_is_sanitized_and_never_retried():
    client = FakeClient(error=TimeoutError("private question content"))

    result = StructuredLuna(client).invoke("private question content", mode="tool")

    assert result.status == "timeout"
    assert result.proposal is None
    assert result.telemetry.attempted is True
    assert len(client.responses.calls) == 1
    assert "private question content" not in repr(result)


def test_input_validation_happens_without_a_provider_attempt():
    client = FakeClient(_response(output_text=json.dumps(_proposal())))
    interpreter = StructuredLuna(client)

    with pytest.raises(ValueError, match="Unsupported output mode"):
        interpreter.invoke("question", mode="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        interpreter.invoke("   ", mode=JSON_SCHEMA_MODE)
    with pytest.raises(ValueError, match="at most 10 seconds"):
        interpreter.invoke("question", mode=JSON_SCHEMA_MODE, timeout_seconds=10.1)

    assert client.responses.calls == []


def test_default_client_pins_official_endpoint_and_disables_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    interpreter = StructuredLuna()

    try:
        assert str(interpreter.client.base_url).rstrip("/") == API_BASE_URL
        assert interpreter.client.max_retries == 0
    finally:
        interpreter.client.close()


def test_pydantic_proposal_is_strict_and_matches_required_wire_shape():
    assert LOOKUP_PROPOSAL_SCHEMA["required"] == ["route", "targets"]
    assert LOOKUP_PROPOSAL_SCHEMA["additionalProperties"] is False
    target_schema = LOOKUP_PROPOSAL_SCHEMA["properties"]["targets"]["items"]
    assert target_schema["required"] == ["family", "anchor", "constraints", "fields"]
    assert target_schema["additionalProperties"] is False
    assert target_schema["properties"]["constraints"]["items"]["required"] == [
        "field",
        "raw",
        "polarity",
    ]

    with pytest.raises(ValidationError):
        LookupProposal.model_validate({**_proposal(), "unexpected": "extra"})
    with pytest.raises(ValidationError):
        LookupProposal.model_validate(
            {"route": "lookup", "targets": [{"family": "HG"}]}
        )


def test_prompt_scopes_constraint_labels_to_locally_supported_filters():
    assert "For HG, use only these field labels" in DOMAIN_INSTRUCTIONS
    assert "For HA, constraints may use only case_id" in DOMAIN_INSTRUCTIONS
    assert "For IS, constraints may use only record_id" in DOMAIN_INSTRUCTIONS
    assert (
        "Date field labels in constraints are only for a literal date value"
        in DOMAIN_INSTRUCTIONS
    )
