"""Streaming presentation of the existing structured answer, without a second call."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.utils.json import parse_partial_json

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


async def emit_progress(event_sink: EventSink | None, stage: str, message: str) -> None:
    """Send a user-visible progress event when a request has an event sink."""
    if event_sink is not None:
        await event_sink({"type": "progress", "stage": stage, "message": message})


def _text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in content
        if isinstance(block, str)
        or (isinstance(block, dict) and block.get("type") in {"text", "output_text"})
    )


async def stream_answer(runnable, inputs, *, config, emit: EventSink):
    """Accumulate raw LangChain model chunks while emitting only answer text.

    Raw JSON, tool arguments, reasoning and citations never become text deltas.
    The normal model/parser/citation checks still run on the complete result.
    """
    result = None
    published = ""
    async for part in runnable.astream(inputs, config=config):
        result = part if result is None else result + part
        try:
            partial = parse_partial_json(_text(result))
        except (ValueError, TypeError):
            continue
        answer = partial.get("answer") if isinstance(partial, dict) else None
        if not isinstance(answer, str):
            continue
        # Hold a trailing surrogate until the other half arrives.
        if answer and 0xD800 <= ord(answer[-1]) <= 0xDBFF:
            answer = answer[:-1]
        if not answer.startswith(published):
            raise OutputParserException("Answer stream changed already published text")
        delta = answer[len(published) :]
        if delta:
            await emit({"type": "answer_delta", "delta": delta})
            published = answer
    if result is None:
        raise OutputParserException("Empty answer stream")
    return {"raw": result, "parsed": None}
