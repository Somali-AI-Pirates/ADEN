import asyncio
import json

import httpx
from langchain_core.messages import AIMessageChunk

from app.rag.model import GeneratedAnswer, ModelCalls, load_model
from app.rag import model as model_module
from app.rag.streaming import stream_answer


def test_partial_json_escapes_and_reasoning_are_not_rendered():
    answer = '예약 "확인"\n한국어 😀'
    raw = json.dumps(
        {"answer": answer, "citations": ["S1"], "status": "answered"}, ensure_ascii=True
    )

    class Runnable:
        async def astream(self, *_args, **_kwargs):
            yield AIMessageChunk(content=[{"type": "reasoning", "text": "hidden"}])
            for char in raw:
                yield AIMessageChunk(content=char)

    async def scenario():
        values = []

        async def emit(v):
            values.append(v)

        await stream_answer(Runnable(), {}, config={}, emit=emit)
        assert "".join(v["delta"] for v in values) == answer
        assert len(values) > 1
        assert all(v["type"] == "answer_delta" for v in values)

    asyncio.run(scenario())


def test_real_langchain_responses_stream_uses_one_call_and_keeps_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test")
    calls_seen = []
    text = json.dumps(
        {
            "answer": "원문 확인이 가능합니다.",
            "citations": ["S1"],
            "status": "answered",
        },
        ensure_ascii=False,
    )
    output = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-luna",
        "status": "completed",
        "output": [output],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    chunks = [
        {
            "type": "response.created",
            "response": {**base, "status": "in_progress", "output": [], "usage": None},
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**output, "status": "in_progress", "content": []},
        },
        {
            "type": "response.content_part.added",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
    ]
    chunks += [
        {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": text[i : i + 3],
        }
        for i in range(0, len(text), 3)
    ]
    chunks += [
        {
            "type": "response.output_text.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": text,
        },
        {
            "type": "response.content_part.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": output["content"][0],
        },
        {"type": "response.output_item.done", "output_index": 0, "item": output},
        {"type": "response.completed", "response": base},
    ]

    def handle(request):
        calls_seen.append(json.loads(request.content))
        body = "".join(
            "event: "
            + c["type"]
            + "\ndata: "
            + json.dumps({**c, "sequence_number": n})
            + "\n\n"
            for n, c in enumerate(chunks)
        )
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )

    monkeypatch.setattr(
        model_module,
        "DefaultAsyncHttpxClient",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )

    async def scenario():
        model = load_model()
        events = []

        async def emit(v):
            events.append(v)

        calls = ModelCalls(None, on_event=emit)
        try:
            answer = await model.answer("질문과 근거", context_hash="test", calls=calls)
        finally:
            await model.aclose()
        assert isinstance(answer, GeneratedAnswer)
        assert "".join(v["delta"] for v in events) == answer.answer
        assert len(events) > 1
        assert calls.summary()["provider_attempts"] == 1
        assert calls.summary()["logical_calls"] == 1
        assert calls.summary()["missing_usage"] == 0
        assert calls_seen[0]["stream"] is True
        assert "hidden" not in str(events)

    asyncio.run(scenario())
