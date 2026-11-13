import asyncio
import json

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage

from app.rag import model as model_module
from app.rag.conversation import ConversationTurn
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


def test_conversation_and_answer_use_responses_message_roles(monkeypatch):
    turn = ConversationTurn(
        question="HG2029-00001 건 처리일은?", answer="5월 4일입니다."
    )
    payloads = []

    def handle(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            assert payload["text"]["format"]["name"] == "ConversationDecision"
            value = {
                "status": "resolved",
                "search_question": "HG2029-00001 건 청구액은?",
                "turn_ids": [1],
            }
        else:
            assert payload["text"]["format"]["name"] == "GeneratedAnswer"
            assert [item["role"] for item in payload["input"]] == [
                "system", "user", "assistant", "user"
            ]
            assert turn.question in json.dumps(payload["input"][1], ensure_ascii=False)
            assert turn.answer in json.dumps(payload["input"][2], ensure_ascii=False)
            value = {
                "status": "insufficient", "answer": "근거 부족", "citations": [],
                "reason": "이번 문맥에서 청구액을 확인하지 못했습니다.",
            }
        return _response(
            [{
                "type": "message",
                "id": f"msg_{len(payloads)}",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(value), "annotations": []}],
            }],
            len(payloads),
        )

    _transport(monkeypatch, handle)

    async def run():
        model = await load_model(api_key="offline-test")
        calls = ModelCalls()
        try:
            result = await model.interpret_conversation(
                "그 건 청구액은?", {1: turn}, False, calls=calls
            )
            assert result.status == "resolved"
            await model.answer(
                '{"question":"그 건 청구액은?","retrieval_status":"insufficient"}',
                context_hash="test",
                conversation_history=(turn,),
                calls=calls,
            )
            assert calls.summary()["provider_attempts"] == 2
        finally:
            await model.aclose()

    asyncio.run(run())


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
                                    "reason": "대상의 원문 기록을 확인할 수 있습니다.",
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

    class TraceNames(BaseCallbackHandler):
        def __init__(self):
            self.chains = []
            self.generations = []

        def on_chain_start(self, serialized, inputs, *, name=None, **kwargs):
            self.chains.append(name)

        def on_chat_model_start(self, serialized, messages, *, name=None, **kwargs):
            self.generations.append(name)

    trace = TraceNames()
    config = {"callbacks": [trace]}

    async def run():
        gateway = await load_model()
        calls = ModelCalls()
        first_input = HumanMessage(content="planner-only-marker")
        try:
            first = await gateway.plan(
                [first_input], finish_only=False, calls=calls, config=config
            )
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
                config=config,
            )
            assert second.tool_calls[0]["name"] == "finish"
            answer = await gateway.answer(
                '{"question":"질문","evidence":{"pages":[],"sql":[]}}',
                context_hash="evidence-hash",
                calls=calls,
                config=config,
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
            assert [call["status"] for call in calls.calls] == [
                "complete",
                "complete",
                "complete",
            ]
            assert calls.calls[0]["visible_output"]["tool_calls"][0]["name"] == (
                "lookup_records"
            )
            assert calls.calls[2]["visible_output"]["answer"] == "확인했습니다."
            assert "opaque-reasoning" not in json.dumps(calls.calls)
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert trace.chains == [
        "plan-tool-call",
        "planner-prompt",
        "plan-tool-call",
        "planner-prompt",
        "generate-answer",
        "answer-prompt",
    ]
    assert trace.generations == ["planner-1", "planner-2", "answer-3"]


def test_timeout_counts_one_attempt_and_unknown_usage(monkeypatch):
    def handle(request):
        raise httpx.ReadTimeout("timeout", request=request)

    _transport(monkeypatch, handle)

    async def run():
        gateway = await load_model()
        calls = ModelCalls()
        try:
            with pytest.raises(TimeoutError):
                await gateway.plan(
                    [HumanMessage(content="test")], finish_only=False, calls=calls
                )
            assert calls.summary()["provider_attempts"] == 1
            assert calls.calls[0]["usage"] is None
            assert calls.calls[0]["status"] == "timeout"
            assert calls.summary()["missing_usage"] == 1
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_load_model_passes_explicit_api_key_to_client(monkeypatch):
    captured = {}
    client = object()

    def create_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(model_module, "DefaultHttpxClient", lambda **kwargs: object())
    monkeypatch.setattr(model_module, "DefaultAsyncHttpxClient", lambda **kwargs: object())
    monkeypatch.setattr(model_module, "ChatOpenAI", create_client)
    monkeypatch.setattr(model_module, "TableModel", lambda model: model)

    gateway = asyncio.run(load_model(api_key="explicit-test-key"))

    assert gateway is client
    assert captured["api_key"] == "explicit-test-key"


def test_load_model_rejects_empty_explicit_api_key():
    with pytest.raises(ValueError, match="explicit OpenAI API key"):
        asyncio.run(load_model(api_key=""))

@pytest.mark.parametrize("failure_stage", ["async_transport", "client", "prompts"])
def test_model_partial_initialization_closes_each_owned_transport(monkeypatch, failure_stage):
    from types import SimpleNamespace

    closed = []

    class SyncTransport:
        def close(self):
            closed.append("sync")

    class AsyncTransport:
        async def aclose(self):
            closed.append("async")

    sync = SyncTransport()
    asynchronous = AsyncTransport()
    monkeypatch.setattr(model_module, "DefaultHttpxClient", lambda: sync)

    def async_transport(**kwargs):
        if failure_stage == "async_transport":
            raise ValueError("construction failed")
        return asynchronous

    def client(**kwargs):
        if failure_stage == "client":
            raise ValueError("construction failed")
        return SimpleNamespace(
            root_client=sync,
            root_async_client=SimpleNamespace(close=asynchronous.aclose),
        )

    def prompts(model):
        raise ValueError("construction failed")

    monkeypatch.setattr(model_module, "DefaultAsyncHttpxClient", async_transport)
    monkeypatch.setattr(model_module, "ChatOpenAI", client)
    monkeypatch.setattr(model_module, "TableModel", prompts)
    with pytest.raises(ValueError, match="construction failed"):
        asyncio.run(load_model(api_key="offline-test"))
    assert closed == (["sync"] if failure_stage == "async_transport" else ["async", "sync"])


def test_model_close_is_idempotent_and_closes_sync_after_async_error():
    from types import SimpleNamespace

    closed = []

    async def close_async():
        closed.append("async")
        raise RuntimeError("async close failed")

    wrapper = object.__new__(model_module.TableModel)
    wrapper._closed = False
    wrapper.model = SimpleNamespace(
        root_async_client=SimpleNamespace(close=close_async),
        root_client=SimpleNamespace(close=lambda: closed.append("sync")),
    )

    async def run():
        with pytest.raises(RuntimeError, match="async close failed"):
            await wrapper.aclose()
        await wrapper.aclose()

    asyncio.run(run())
    assert closed == ["async", "sync"]


def test_model_close_completes_after_repeated_cancellation():
    from types import SimpleNamespace

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        closed = []

        async def close_async():
            started.set()
            await release.wait()
            closed.append("async")

        wrapper = object.__new__(model_module.TableModel)
        wrapper._closed = False
        wrapper.model = SimpleNamespace(
            root_async_client=SimpleNamespace(close=close_async),
            root_client=SimpleNamespace(close=lambda: closed.append("sync")),
        )
        task = asyncio.create_task(wrapper.aclose())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wrapper.aclose()
        assert closed == ["async", "sync"]

    asyncio.run(run())
