from __future__ import annotations

import asyncio
import json
from typing import Literal

import httpx
import pytest
import yaml
from openai import OpenAI
from pydantic import BaseModel

from app.evaluation.model_runtime import (
    load_evaluation_policy,
    EvaluationCallError,
    EvaluationConfigurationError,
    EvaluationOutputError,
    EvaluationPolicy,
    EvaluationSession,
)
from shared.settings import DEFAULT_CONFIG, EvaluationSettings, Settings


class ParsedValue(BaseModel):
    value: float


class ParsedVerdict(BaseModel):
    verdict: Literal["supported", "unsupported"]


class ParsedStrictVerdict(BaseModel):
    verdict: Literal[0, 1]


def make_client(handler):
    transport = httpx.MockTransport(handler)
    client_http = httpx.Client(transport=transport)
    client = OpenAI(
        api_key="synthetic-test-key",
        base_url="https://api.openai.invalid/v1",
        http_client=client_http,
        max_retries=0,
    )
    return client, client_http


def responses_result(
    payload: dict, *, response_id: str = "resp-synthetic"
) -> httpx.Response:
    output_text = json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "id": response_id,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "gpt-6-luna",
            "output": [
                {
                    "id": "msg-synthetic",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": output_text, "annotations": []}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 123,
                "output_tokens": 44,
                "input_tokens_details": {"cached_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 17},
            },
        },
    )


def effort_denial() -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "message": "Requested reasoning.effort is not supported for this account.",
                "type": "invalid_request_error",
                "param": "reasoning.effort",
                "code": "unsupported_value",
            }
        },
    )


def run_parse(session, model=ParsedValue):
    return asyncio.run(
        session.parse_json(
            "synthetic evaluation prompt",
            model,
            model="gpt-6-luna",
            stage="faithfulness",
        )
    )


def test_settings_separate_evaluator_effort_from_generation_effort():
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    settings = Settings.model_validate(raw)

    assert settings.reasoning_effort == "low"
    assert settings.evaluation == EvaluationSettings(
        reasoning_effort="max",
        fallback_reasoning_effort="xhigh",
        max_output_tokens=128000,
        timeout_seconds=600,
        max_retries=2,
    )
    assert EvaluationPolicy.from_settings(settings).to_mapping() == {
        "reasoning_effort": "max",
        "fallback_reasoning_effort": "xhigh",
        "max_output_tokens": 128000,
        "timeout_seconds": 600,
        "max_retries": 2,
    }


def test_evaluation_session_is_lazy_and_manifest_effort_can_be_restored():
    session = EvaluationSession(EvaluationPolicy())
    with session:
        assert session.effective_reasoning_effort is None
        session.restore_effective_effort("xhigh")
        assert session.to_manifest()["effective_reasoning_effort"] == "xhigh"
        assert session.to_manifest()["fallback_used"] is True


def test_evaluation_policy_rejects_effort_downgrade_configuration():
    with pytest.raises(ValueError, match="must be max"):
        EvaluationPolicy(reasoning_effort="medium")


def test_load_evaluation_policy_uses_selected_yaml_and_keeps_model_policy_separate(
    tmp_path, monkeypatch
):
    config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["evaluation"]["max_output_tokens"] = 16384
    path = tmp_path / "evaluation.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    policy = load_evaluation_policy(path)

    assert policy.max_output_tokens == 16384
    assert policy.reasoning_effort == "max"
    assert config["reasoning_effort"] == "low"


def test_previous_32768_evaluator_limit_remains_configurable(tmp_path):
    config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["evaluation"]["max_output_tokens"] = 32768
    path = tmp_path / "legacy-evaluation.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    policy = load_evaluation_policy(path)

    assert policy.max_output_tokens == 32768


def test_minimal_runtime_yaml_uses_evaluator_defaults(tmp_path):
    path = tmp_path / "runtime.yaml"
    path.write_text("table_rag:\n  device: cpu\n", encoding="utf-8")

    policy = load_evaluation_policy(path)

    assert policy == EvaluationPolicy()


def test_responses_parse_sends_explicit_policy_and_returns_typed_usage():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return responses_result({"value": 0.75})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            call = run_parse(session)

        assert call.output == ParsedValue(value=0.75)
        assert captured["reasoning"] == {"effort": "max"}
        assert captured["max_output_tokens"] == 128000
        assert captured["text"]["format"]["type"] == "json_schema"
        assert "temperature" not in captured
        assert "top_p" not in captured
        assert call.metadata.api_surface == "responses.parse"
        assert call.metadata.effective_reasoning_effort == "max"
        assert call.metadata.input_tokens == 123
        assert call.metadata.output_tokens == 44
        assert call.metadata.reasoning_tokens == 17
        assert call.metadata.cached_input_tokens == 3
        assert call.metadata.response_id == "resp-synthetic"
        assert call.to_mapping()["output"] == {"value": 0.75}
    finally:
        client_http.close()


def test_generate_json_uses_responses_json_mode_and_no_sampling_controls():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return responses_result({"ok": True})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            call = session.generate_json(
                "synthetic Judge prompt", model="gpt-6-luna", stage="judge"
            )

        assert json.loads(call.output) == {"ok": True}
        assert captured["reasoning"] == {"effort": "max"}
        assert captured["max_output_tokens"] == 128000
        assert captured["text"] == {"format": {"type": "json_object"}}
        assert "temperature" not in captured
        assert "top_p" not in captured
        assert call.metadata.api_surface == "responses.json"
        assert call.metadata.input_tokens == 123
        assert call.metadata.output_tokens == 44
        assert call.metadata.reasoning_tokens == 17
        assert call.metadata.cached_input_tokens == 3
        assert call.to_mapping()["text"] == call.output
    finally:
        client_http.close()


def test_generate_json_explicit_effort_denial_falls_back_once_and_pins_xhigh():
    efforts = []

    def handler(request):
        body = json.loads(request.content)
        efforts.append(body["reasoning"]["effort"])
        if body["reasoning"]["effort"] == "max":
            return effort_denial()
        return responses_result({"ok": True})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            first = session.generate_json("first", model="gpt-6-luna", stage="judge")
            second = session.generate_json("second", model="gpt-6-luna", stage="judge")

        assert json.loads(first.output) == {"ok": True}
        assert json.loads(second.output) == {"ok": True}
        assert efforts == ["max", "xhigh", "xhigh"]
        assert first.metadata.fallback_used is True
        assert first.metadata.effective_reasoning_effort == "xhigh"
        assert [attempt.reasoning_effort for attempt in first.metadata.attempts] == [
            "max",
            "xhigh",
        ]
    finally:
        client_http.close()


def test_generate_json_rejects_incomplete_response():
    def handler(request):
        raw = json.loads(responses_result({"partial": True}).content)
        raw["status"] = "incomplete"
        raw["incomplete_details"] = {"reason": "max_output_tokens"}
        return httpx.Response(200, json=raw)

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationOutputError) as caught:
                session.generate_json("prompt", model="gpt-6-luna", stage="judge")
        assert session.effective_reasoning_effort == "max"
        assert caught.value.evaluation_details["status_code"] is None
        assert caught.value.evaluation_details["response_status"] == "incomplete"
        assert caught.value.evaluation_details["incomplete_reason"] == "max_output_tokens"
    finally:
        client_http.close()


def test_generate_json_does_not_record_unrecognized_incomplete_reason():
    def handler(request):
        raw = json.loads(responses_result({"partial": True}).content)
        raw["status"] = "incomplete"
        raw["incomplete_details"] = {"reason": "synthetic-private-detail"}
        return httpx.Response(200, json=raw)

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationOutputError) as caught:
                session.generate_json("prompt", model="gpt-6-luna", stage="judge")
        assert caught.value.evaluation_details["response_status"] == "incomplete"
        assert caught.value.evaluation_details["incomplete_reason"] is None
        assert "synthetic-private-detail" not in str(caught.value)
        assert "synthetic-private-detail" not in json.dumps(caught.value.evaluation_details)
    finally:
        client_http.close()


def test_generate_json_rejects_refusal():
    def handler(request):
        raw = json.loads(responses_result({"unused": True}).content)
        raw["output"][0]["content"] = [
            {"type": "refusal", "refusal": "synthetic refusal"}
        ]
        return httpx.Response(200, json=raw)

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationOutputError):
                session.generate_json("prompt", model="gpt-6-luna", stage="judge")
    finally:
        client_http.close()


def test_only_explicit_initial_effort_denial_falls_back_once_and_pins_xhigh():
    efforts = []

    def handler(request):
        body = json.loads(request.content)
        efforts.append(body["reasoning"]["effort"])
        if body["reasoning"]["effort"] == "max":
            return effort_denial()
        return responses_result({"value": 0.5})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            call = run_parse(session)
            assert run_parse(session).output.value == 0.5

        assert efforts == ["max", "xhigh", "xhigh"]
        mapping = call.to_mapping()
        assert mapping["fallback_used"] is True
        assert mapping["fallback_reason"] == "requested_reasoning_effort_denied"
        assert mapping["effective_reasoning_effort"] == "xhigh"
        assert [item["reasoning_effort"] for item in mapping["attempts"]] == [
            "max",
            "xhigh",
        ]
        assert "not supported for this account" not in json.dumps(mapping)
        assert "synthetic-test-key" not in json.dumps(mapping)
    finally:
        client_http.close()


def test_denial_after_effort_is_pinned_aborts_without_mixing_efforts():
    efforts = []

    def handler(request):
        body = json.loads(request.content)
        efforts.append(body["reasoning"]["effort"])
        if len(efforts) == 1:
            return responses_result({"value": 1.0})
        return effort_denial()

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            run_parse(session)
            with pytest.raises(EvaluationConfigurationError) as caught:
                run_parse(session)

        assert efforts == ["max", "max"]
        assert caught.value.abort_evaluation is True
        assert caught.value.evaluation_details["effective_reasoning_effort"] == "max"
        assert (
            caught.value.evaluation_details["attempts"][-1]["error_param"]
            == "reasoning.effort"
        )
        assert "not supported for this account" not in str(caught.value)
    finally:
        client_http.close()


@pytest.mark.parametrize("failure_kind", ["rate_limit", "transport_timeout"])
def test_rate_limit_and_timeout_never_downgrade_effort(failure_kind):
    efforts = []
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        efforts.append(body["reasoning"]["effort"])
        if calls == 1 and failure_kind == "rate_limit":
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "synthetic rate limit",
                        "type": "rate_limit_error",
                        "param": None,
                        "code": "rate_limit_exceeded",
                    }
                },
            )
        if calls == 1:
            raise httpx.ReadTimeout("synthetic secret-like transport text")
        return responses_result({"value": 0.25})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationCallError) as caught:
                run_parse(session)
            assert run_parse(session).output.value == 0.25

        assert efforts == ["max", "max"]
        assert caught.value.evaluation_details["fallback_used"] is False
        assert "synthetic secret-like" not in str(caught.value)
        assert "synthetic rate limit" not in json.dumps(caught.value.evaluation_details)
    finally:
        client_http.close()


def test_schema_error_preserves_only_raw_numeric_verdicts():
    def handler(request):
        return responses_result(
            {"verdict": 2, "private_reasoning": "must not be retained"}
        )

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationOutputError) as caught:
                run_parse(session, ParsedStrictVerdict)

        assert caught.value.raw_verdicts == [{"verdict": 2}]
        assert caught.value.evaluation_details["raw_verdicts"] == [{"verdict": 2}]
        diagnostic = json.dumps(caught.value.evaluation_details)
        assert "private_reasoning" not in diagnostic
        assert "must not be retained" not in diagnostic
    finally:
        client_http.close()


def test_schema_validation_failure_does_not_fallback_or_expose_model_text():
    efforts = []

    def handler(request):
        body = json.loads(request.content)
        efforts.append(body["reasoning"]["effort"])
        return responses_result({"unexpected": "synthetic private output"})

    client, client_http = make_client(handler)
    try:
        with EvaluationSession(EvaluationPolicy(), client=client) as session:
            with pytest.raises(EvaluationOutputError) as caught:
                run_parse(session)

        assert efforts == ["max"]
        assert caught.value.evaluation_details["effective_reasoning_effort"] == "max"
        assert caught.value.evaluation_details["fallback_used"] is False
        assert "synthetic private output" not in str(caught.value)
        assert "synthetic private output" not in json.dumps(
            caught.value.evaluation_details
        )
    finally:
        client_http.close()
