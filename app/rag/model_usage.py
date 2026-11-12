"""Request-local usage, retry, and visible-output records for TableRAG model calls."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.callbacks.manager import BaseCallbackManager
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import ensure_config

from app.observability.records import summarize_usage
from app.rag.streaming import EventSink
from shared.table_rag_policy import MODEL, REASONING_EFFORT

_active_call: ContextVar[dict[str, Any] | None] = ContextVar(
    "table_rag_call", default=None
)


async def _record_http_attempt(_request) -> None:
    call = _active_call.get()
    if call is not None:
        call["provider_attempts"] += 1


class _UsageCallback(BaseCallbackHandler):
    """Capture usage without retaining prompts, reasoning, or responses."""

    def __init__(self, call: dict[str, Any]) -> None:
        self.call = call

    def on_llm_end(self, response, **kwargs) -> None:
        for group in response.generations:
            for generation in group:
                message = getattr(generation, "message", None)
                if message is not None:
                    self.call["usage"] = getattr(message, "usage_metadata", None)
                    self.call["provider_response_id"] = getattr(
                        message, "response_metadata", {}
                    ).get("id")
                    return


class ModelCalls:
    """Request-local model accounting; execution stays in TableModel."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self.event_sink = event_sink
        self.calls: list[dict[str, Any]] = []

    @contextmanager
    def track_call(
        self,
        role: str,
        model_input: Any,
        *,
        tool_schema: Any = None,
    ) -> Iterator[dict[str, Any]]:
        call = {
            "role": role,
            "model": MODEL,
            "attempt": len(self.calls) + 1,
            "model_input": model_input,
            "tool_schema": tool_schema,
            "model_parameters": {"reasoning_effort": REASONING_EFFORT},
            "provider_attempts": 0,
            "usage": None,
            "status": "error",
        }
        self.calls.append(call)
        token = _active_call.set(call)
        started = perf_counter()
        try:
            yield call
        finally:
            call["latency_ms"] = round((perf_counter() - started) * 1000)
            _active_call.reset(token)

    def callback_config(
        self, config: RunnableConfig | None, call: dict[str, Any]
    ) -> RunnableConfig:
        configured = ensure_config(config)
        callbacks = configured.get("callbacks")
        usage_callback = _UsageCallback(call)
        if isinstance(callbacks, BaseCallbackManager):
            callbacks = callbacks.copy()
            callbacks.add_handler(usage_callback)
        else:
            callbacks = list(callbacks) if callbacks is not None else []
            if usage_callback not in callbacks:
                callbacks.append(usage_callback)
        configured["callbacks"] = callbacks
        configured["metadata"] = {
            **configured.get("metadata", {}),
            "role": call["role"],
            "attempt": call["attempt"],
            "tool_schema": call["tool_schema"],
            "model_parameters": call["model_parameters"],
        }
        return configured

    @staticmethod
    def model_run_config(call: dict[str, Any]) -> RunnableConfig:
        """Name the actual model run while leaving the prompt chain unnamed."""
        return {"run_name": f"{call['role']}-{call['attempt']}"}

    @staticmethod
    def record_output(call: dict[str, Any], visible_output: Any) -> None:
        call["visible_output"] = visible_output
        call["status"] = "complete"

    def summary(self) -> dict[str, Any]:
        attempts = [
            {
                "attempt": call["attempt"],
                "role": call["role"],
                "model": call["model"],
                "model_input": call["model_input"],
                "model_output": call.get("visible_output"),
                "tool_schema": call["tool_schema"],
                "model_parameters": call["model_parameters"],
                "input_tokens": (call["usage"] or {}).get("input_tokens"),
                "output_tokens": (call["usage"] or {}).get("output_tokens"),
                "latency_ms": call.get("latency_ms"),
                "error": call["status"] if call["status"] != "complete" else None,
            }
            for call in self.calls
        ]
        return {
            **summarize_usage([call["usage"] or {} for call in self.calls]),
            "calls": list(self.calls),
            "attempts": attempts,
            "logical_calls": len(self.calls),
            "provider_attempts": sum(call["provider_attempts"] for call in self.calls),
            "missing_usage": sum(call["usage"] is None for call in self.calls),
        }
