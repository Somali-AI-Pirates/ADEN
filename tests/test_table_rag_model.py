import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.observability.tracing import Tracer
from app.rag import model as model_module
from app.rag.model import ModelCalls, load_model


def _response(output, number):
    return httpx.Response(
        200,
        json={
            "id": f"resp_{number}",
            "object": "response",
            "created_at": 1,
            "model": "gpt-6-luna",
            "status": "completed",
            "output": output,
            "usage": {
                "input_tokens": 40,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 5},
                "total_tokens": 50,
            },
        },
    )


def _tool(number, name, arguments):
    return {
        "type": "function_call",
        "id": f"fc_{number}",
        "call_id": f"call_{number}",
        "name": name,
        "arguments": json.dumps(arguments),
        "status": "completed",
    }


def _transport(monkeypatch, handler):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test")
    monkeypatch.setattr(
        model_module,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


class _TraceClient:
    def __init__(self):
        self.observations = []

    def create_trace_id(self, *, seed):
        return seed

    def start_observation(self, **values):
        self.observations.append(values)
        return _Observation(self, values)


class _Observation:
    def __init__(self, client, values):
        self.client = client
        self.values = values

    def start_observation(self, **values):
        return self.client.start_observation(**values)

    def update(self, **values):
        self.values.update(values)

    def end(self):
        pass


def test_responses_tool_roundtrip_and_answer_scope(monkeypatch):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/responses"
        assert payload["model"] == "gpt-6-luna"
        assert payload["reasoning"] == {"effort": "medium"}
        assert payload["store"] is False
        if len(requests) < 3:
            assert "include" not in payload
        if len(requests) == 1:
            assert payload["tool_choice"] == "required"
            assert payload["parallel_tool_calls"] is False
            assert payload["max_output_tokens"] == 3500
            return _response(
                [
                    {
                        "type": "reasoning",
                        "id": "rs_first",
                        "summary": [],
                        "encrypted_content": "opaque-reasoning",
                    },
                    _tool(
                        1,
                        "lookup_records",
                        {
                            "targets": [
                                {
                                    "identifiers": ["EV-SH-00660-D"],
                                    "constraints": [],
                                    "roles": ["events"],
                                }
                            ]
                        },
                    ),
                ],
                1,
            )
        if len(requests) == 2:
            assert payload["tool_choice"] == {"type": "function", "name": "finish"}
            leaked = [
                item
                for item in payload["input"]
                if "opaque-reasoning" in json.dumps(item)
            ]
            assert not leaked, leaked
            assert any(
                item.get("type") == "function_call_output"
                and item.get("call_id") == "call_1"
                for item in payload["input"]
            )
            return _response(
                [
                    _tool(
                        2,
                        "finish",
                        {
                            "status": "sufficient",
                            "selected_sources": ["AS001:p0001"],
                            "interim_summary": "",
                            "conflicts": [],
                        },
                    )
                ],
                2,
            )
        assert payload["max_output_tokens"] == 2200
        assert payload["text"]["format"]["type"] == "json_schema"
        assert "tools" not in payload and "include" not in payload
        assert len(payload["input"]) == 2
        assert "planner-only-marker" not in json.dumps(payload["input"])
        return _response(
            [
                {
                    "type": "message",
                    "id": "msg_final",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "answer": "확인했습니다.",
                                    "citations": ["AS001:p0001"],
                                    "status": "answered",
                                }
                            ),
                            "annotations": [],
                        }
                    ],
                }
            ],
            3,
        )

    _transport(monkeypatch, handle)

    async def run():
        gateway = load_model()
        client = _TraceClient()
        trace = Tracer(client).start_request(
            "trace-test",
            question="",
            retrieval_mode="table_rag_a",
            dataset_id="advanced",
            config_hash="test-config",
        )
        calls = ModelCalls(trace)
        first_input = HumanMessage(content="planner-only-marker")
        try:
            first = await gateway.plan([first_input], finish_only=False, calls=calls)
            assert first.tool_calls[0]["id"] == "call_1"
            assert "opaque-reasoning" not in json.dumps(first.model_dump(mode="json"))
            second = await gateway.plan(
                [
                    first_input,
                    first,
                    ToolMessage(
                        content="observed",
                        tool_call_id="call_1",
                        name="lookup_records",
                        additional_kwargs={"provider_private": "opaque-reasoning"},
                    ),
                ],
                finish_only=True,
                calls=calls,
            )
            assert second.tool_calls[0]["name"] == "finish"
            answer = await gateway.answer(
                '{"question":"질문","evidence":{"pages":[],"sql":[]}}',
                context_hash="evidence-hash",
                calls=calls,
            )
            assert answer.citations == ["AS001:p0001"]
            assert calls.summary()["provider_attempts"] == 3
            assert all(call["usage"]["total_tokens"] == 50 for call in calls.calls)
            attempts = calls.summary()["attempts"]
            assert [attempt["attempt"] for attempt in attempts] == [1, 2, 3]
            assert [attempt["role"] for attempt in attempts] == [
                "planner",
                "planner",
                "answer",
            ]
            assert attempts[1]["model_input"]["history"][-1] == {
                "type": "tool",
                "name": "lookup_records",
                "tool_call_id": "call_1",
                "content": "observed",
            }
            assert "opaque-reasoning" not in json.dumps(attempts[1]["model_input"])
            assert attempts[0]["tool_schema"][0]["function"]["name"] == "lookup_records"
            assert attempts[2]["model_input"]["context_hash"] == "evidence-hash"
            spans = [
                span for span in client.observations if span["as_type"] == "generation"
            ]
            assert len(spans) == 3
            assert [span["name"] for span in spans] == [
                "planner-1",
                "planner-2",
                "answer-3",
            ]
            assert spans[-1]["input"]["request"]["context_hash"] == "evidence-hash"
            assert all(span["usage_details"]["total"] == 50 for span in spans)
            serialized = json.dumps(client.observations)
            assert "planner-only-marker" in serialized
            assert "opaque-reasoning" not in serialized
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_timeout_counts_one_attempt_and_unknown_usage(monkeypatch):
    def handle(request):
        raise httpx.ReadTimeout("timeout", request=request)

    _transport(monkeypatch, handle)

    async def run():
        gateway = load_model()
        calls = ModelCalls(None)
        try:
            with pytest.raises(TimeoutError):
                await gateway.plan(
                    [HumanMessage(content="test")], finish_only=False, calls=calls
                )
            assert calls.summary()["provider_attempts"] == 1
            assert calls.calls[0]["usage"] is None
            assert calls.calls[0]["status"] == "timeout"
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_answer_defense_is_per_call_and_preserves_schema_and_generation_settings(
    monkeypatch,
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return _response(
            [
                {
                    "type": "message",
                    "id": "msg",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "answer": "확인했습니다.",
                                    "citations": [],
                                    "status": "insufficient",
                                }
                            ),
                            "annotations": [],
                        }
                    ],
                }
            ],
            len(requests),
        )

    _transport(monkeypatch, handle)

    async def run():
        gateway = load_model()
        try:
            for defense in ["", "추가 방어 문장", ""]:
                calls = ModelCalls(None)
                await gateway.answer(
                    "fixed payload",
                    context_hash="fixed-hash",
                    calls=calls,
                    defense=defense,
                )
                assert calls.calls[-1]["model_input"][
                    "system"
                ] == gateway.answer_prompt + ("\n\n" + defense if defense else "")
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert len(requests) == 3
    assert requests[0]["input"] == requests[2]["input"]
    assert requests[0]["input"][1] == requests[1]["input"][1]
    assert "추가 방어 문장" in json.dumps(requests[1]["input"], ensure_ascii=False)
    for key in ["model", "max_output_tokens", "reasoning", "text"]:
        assert requests[0][key] == requests[1][key] == requests[2][key]
