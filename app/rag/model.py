"""LangChain Responses API bindings for the two TableRAG A model roles."""

import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
from hashlib import sha256
from importlib.resources import files
import json
from time import perf_counter
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from openai import APIError, APITimeoutError, DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, ValidationError

from app.rag.streaming import EventSink, stream_answer
from app.observability.records import summarize_usage
from app.rag.tools import (
    FinishRequest,
    LookupRecordsRequest,
    QueryTablesRequest,
    ReadSourcesRequest,
    SearchEvidenceRequest,
)
from shared.table_rag_policy import (
    ANSWER_OUTPUT_TOKENS,
    BASE_URL,
    MODEL,
    PLAN_OUTPUT_TOKENS,
    REASONING_EFFORT,
    TIMEOUT_SECONDS,
)

_active_call: ContextVar[dict[str, Any] | None] = ContextVar(
    "table_rag_call", default=None
)


class RagModelError(RuntimeError):
    """The planner or answer provider did not return a usable response."""


class RagContextBudgetError(RagModelError):
    """The complete planner input exceeds its configured model window."""


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    citations: list[str]
    status: Literal["answered", "needs_clarification", "insufficient"]


def _prompt(name: str) -> str:
    return (
        files("app.rag")
        .joinpath("prompts")
        .joinpath(f"{name}.txt")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )


CONVERSATION_GUIDANCE = (_prompt("conversation-plan"), _prompt("conversation-answer"))
MISSING_EMAIL_TARGET_GUIDANCE = (
    "질문에는 이메일을 찾을 업무나 담당 창구가 생략됐다. 검색 후보에서 대상을 추정하지 말고 finish의 status를 needs_clarification으로, selected_sources를 빈 배열로 설정한다.",
    "어느 업무나 담당 창구의 이메일을 찾는지 묻는다. 후보 주소와 출처는 제시하지 않는다.",
)


def _tool(name, schema, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema.model_json_schema(),
            "strict": True,
        },
    }


def _visible_content(content: Any) -> str:
    """Extract visible text while excluding Responses API reasoning blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    visible = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"text", "output_text", "refusal"}:
            continue
        text = block.get("text") or block.get("refusal")
        if isinstance(text, str) and text:
            visible.append(text)
    return "\n".join(visible)


def _visible_output(message: Any) -> dict[str, Any]:
    """Record user-visible text and tool arguments without provider metadata."""
    if not isinstance(message, AIMessage):
        return {"type": type(message).__name__}
    return {
        "content": _visible_content(message.content),
        "tool_calls": [
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "args": call.get("args"),
            }
            for call in message.tool_calls
        ],
    }


def _visible_message(message: Any) -> dict[str, Any]:
    """Serialize only the message fields sent as visible conversation input."""
    if isinstance(message, AIMessage):
        return {
            "type": "assistant",
            "content": _visible_content(message.content),
            "tool_calls": _visible_output(message)["tool_calls"],
        }
    if isinstance(message, ToolMessage):
        return {
            "type": "tool",
            "name": message.name,
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if isinstance(message, SystemMessage):
        return {"type": "system", "content": message.content}
    if hasattr(message, "content"):
        return {"type": "user", "content": message.content}
    return {"type": type(message).__name__}


def _safe_message(message: Any):
    """Rebuild conversation messages without provider metadata or hidden reasoning."""
    if isinstance(message, AIMessage):
        response_metadata = {}
        if message.response_metadata.get("status") is not None:
            response_metadata["status"] = message.response_metadata["status"]
        return AIMessage(
            content=_visible_content(message.content),
            tool_calls=[
                {
                    "name": call["name"],
                    "args": call["args"],
                    "id": call.get("id"),
                    "type": "tool_call",
                }
                for call in message.tool_calls
            ],
            usage_metadata=message.usage_metadata,
            response_metadata=response_metadata,
        )
    if isinstance(message, ToolMessage):
        return ToolMessage(
            content=message.content,
            tool_call_id=message.tool_call_id,
            name=message.name,
        )
    if isinstance(message, HumanMessage):
        return HumanMessage(content=message.content)
    if isinstance(message, SystemMessage):
        return SystemMessage(content=message.content)
    raise TypeError(f"Unsupported planner message type: {type(message).__name__}")


async def _record_http_attempt(_request) -> None:
    call = _active_call.get()
    if call is not None:
        call["provider_attempts"] += 1


class _UsageCallback(BaseCallbackHandler):
    """Capture LangChain usage without retaining prompts, reasoning or responses."""

    def __init__(self, call: dict[str, Any]) -> None:
        self.call = call

    def on_llm_end(self, response, **kwargs) -> None:
        for group in response.generations:
            for generation in group:
                message = getattr(generation, "message", None)
                if isinstance(message, AIMessage):
                    self.call["usage"] = message.usage_metadata
                    self.call["provider_response_id"] = message.response_metadata.get(
                        "id"
                    )
                    return


class ModelCalls:
    """One request's model accounting and generation spans."""

    def __init__(
        self, trace, on_event: EventSink | None = None, *, guidance: tuple[str, str] = ("", "")
    ) -> None:
        self.trace = trace
        self.on_event = on_event
        self.plan_guidance, self.answer_guidance = guidance
        self.calls: list[dict[str, Any]] = []

    async def invoke(
        self,
        role,
        runnable,
        inputs,
        *,
        model_input,
        tool_schema=None,
        output_capture=None,
        stream_runnable=None,
    ):
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
        stage = (
            self.trace.stage if self.trace else lambda *args, **kwargs: nullcontext({})
        )
        try:
            with stage(
                f"{role}-{len(self.calls)}",
                as_type="generation",
                model=MODEL,
                input=model_input,
            ) as span:
                try:
                    config = {"callbacks": [_UsageCallback(call)]}
                    if role == "answer" and self.on_event is not None:
                        result = await stream_answer(
                            stream_runnable, inputs, config=config, emit=self.on_event
                        )
                    else:
                        result = await runnable.ainvoke(inputs, config=config)
                    message = result.get("raw") if isinstance(result, dict) else result
                    if (
                        isinstance(message, AIMessage)
                        and message.response_metadata.get("status", "completed")
                        != "completed"
                    ):
                        call["status"] = "incomplete_response"
                        raise RagModelError(f"{role} returned an incomplete response")
                    visible_output = (
                        output_capture(result)
                        if output_capture is not None
                        else _visible_output(message)
                    )
                    call["status"] = "complete"
                    call["visible_output"] = visible_output
                    span.update(output=visible_output)
                    return result
                except APITimeoutError as error:
                    call["status"] = "timeout"
                    raise TimeoutError("TableRAG model deadline exceeded") from error
                except APIError as error:
                    call["status"] = "provider_error"
                    raise RagModelError(f"{role} provider failure") from error
                except (OutputParserException, ValidationError) as error:
                    call["status"] = "invalid_response"
                    raise RagModelError(f"{role} response validation failed") from error
                except RagModelError:
                    if call["status"] == "error":
                        call["status"] = "invalid_output"
                    raise
                finally:
                    call["latency_ms"] = round((perf_counter() - started) * 1000)
                    usage = call["usage"]
                    if self.trace is not None:
                        self.trace.record_attempt(
                            role=role,
                            attempt=call["attempt"],
                            model=MODEL,
                            model_input=model_input,
                            model_output=call.get("visible_output"),
                            tool_schema=tool_schema,
                            input_tokens=(usage or {}).get("input_tokens"),
                            output_tokens=(usage or {}).get("output_tokens"),
                            latency_ms=call.get("latency_ms"),
                            model_parameters=call["model_parameters"],
                            error=(
                                call["status"] if call["status"] != "complete" else None
                            ),
                        )
                    if usage:
                        usage_details = dict(span.get("usage_details", {}))
                        usage_details.update(
                            {
                                key.removesuffix("_tokens"): usage[key]
                                for key in (
                                    "input_tokens",
                                    "output_tokens",
                                    "total_tokens",
                                )
                                if usage.get(key) is not None
                            }
                        )
                        span["usage_details"] = usage_details
                    metadata = dict(span.get("metadata", {}))
                    metadata.update(
                        {
                            "provider_attempts": call["provider_attempts"],
                            "usage_known": usage is not None,
                        }
                    )
                    span["metadata"] = metadata
        finally:
            _active_call.reset(token)

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


class TableModel:
    def __init__(self, model: ChatOpenAI) -> None:
        self.model = model
        self._closed = False
        self.plan_prompt = _prompt("plan")
        self.answer_prompt = _prompt("answer")
        self.tools = [
            _tool(
                "lookup_records",
                LookupRecordsRequest,
                "명시 코드와 질문에 있는 필드 조건을 사용해 HG·HA·IS 기록을 조회한다.",
            ),
            _tool(
                "search_evidence",
                SearchEvidenceRequest,
                "documents는 페이지·표 5:5 하이브리드 검색과 KURE 리랭킹, tables는 표 카탈로그 검색이다.",
            ),
            _tool(
                "read_sources",
                ReadSourcesRequest,
                "이미 발견한 최대 두 원본 페이지를 읽는다.",
            ),
            _tool(
                "query_tables",
                QueryTablesRequest,
                "관측한 표에 읽기 전용 SQL을 실행한다.",
            ),
            _tool(
                "finish",
                FinishRequest,
                "확보한 근거를 바탕으로 상태와 최종 출처를 고른다.",
            ),
        ]
        finish_tool = self.tools[-1]
        plan_prompt = ChatPromptTemplate.from_messages(
            [("system", "{instructions}"), MessagesPlaceholder("history")]
        )
        self._plan = {
            only_finish: plan_prompt
            | model.bind_tools(
                [finish_tool] if only_finish else self.tools,
                tool_choice="finish" if only_finish else "required",
                strict=True,
                parallel_tool_calls=False,
                max_output_tokens=PLAN_OUTPUT_TOKENS,
            )
            for only_finish in (False, True)
        }
        answer_prompt = ChatPromptTemplate.from_messages(
            [("system", "{instructions}"), ("human", "{payload}")]
        )
        self._answer = answer_prompt | model.with_structured_output(
            GeneratedAnswer,
            method="json_schema",
            include_raw=True,
            strict=True,
            max_output_tokens=ANSWER_OUTPUT_TOKENS,
        )
        # include_raw structured parsers buffer model output in LangChain.
        # Stream the same schema-bound model directly, then validate complete JSON.
        self._answer_stream = answer_prompt | model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "GeneratedAnswer",
                    "schema": GeneratedAnswer.model_json_schema(),
                    "strict": True,
                },
            },
            max_output_tokens=ANSWER_OUTPUT_TOKENS,
        )

    def prompt_hashes(self) -> dict[str, str]:
        return {
            "plan": sha256(self.plan_prompt.encode()).hexdigest(),
            "answer": sha256(self.answer_prompt.encode()).hexdigest(),
        }

    def planner_overhead(self, count_tokens) -> int:
        return count_tokens(self.plan_prompt) + count_tokens(
            json.dumps(self.tools, ensure_ascii=False)
        )

    async def plan(self, history, *, finish_only: bool, calls: ModelCalls) -> AIMessage:
        tools = [self.tools[-1]] if finish_only else self.tools
        instructions = "\n\n".join(filter(None, (self.plan_prompt, calls.plan_guidance)))
        model_input = {
            "system": instructions,
            "history": [_visible_message(message) for message in history],
            "tools": tools,
            "tool_choice": "finish" if finish_only else "required",
            "parallel_tool_calls": False,
        }
        message = await calls.invoke(
            "planner",
            self._plan[finish_only],
            {"instructions": instructions, "history": [_safe_message(item) for item in history]},
            model_input=model_input,
            tool_schema=tools,
            output_capture=lambda response: _visible_output(
                response.get("raw") if isinstance(response, dict) else response
            ),
        )
        if not isinstance(message, AIMessage):
            raise RagModelError("Invalid planner response")
        return _safe_message(message)

    async def answer(
        self, payload: str, *, context_hash: str, calls: ModelCalls
    ) -> GeneratedAnswer:
        def capture_answer(response):
            raw = response.get("raw") if isinstance(response, dict) else None
            # A schema dict also supports Responses API streaming. Validate the
            # complete JSON ourselves; a partial JSON parser is only for display.
            if not isinstance(raw, AIMessage):
                raise RagModelError("Invalid structured answer")
            parsed = GeneratedAnswer.model_validate_json(_visible_content(raw.content))
            if not parsed.answer.strip():
                raise RagModelError("Invalid structured answer")
            response["parsed"] = parsed
            return parsed.model_dump()

        instructions = "\n\n".join(filter(None, (self.answer_prompt, calls.answer_guidance)))
        result = await calls.invoke(
            "answer",
            self._answer,
            {"instructions": instructions, "payload": payload},
            model_input={
                "system": instructions,
                "payload": payload,
                "context_hash": context_hash,
                "response_format": {
                    "type": "json_schema",
                    "schema": GeneratedAnswer.model_json_schema(),
                    "strict": True,
                },
            },
            output_capture=capture_answer,
            stream_runnable=self._answer_stream,
        )
        answer = result.get("parsed") if isinstance(result, dict) else None
        if not isinstance(answer, GeneratedAnswer) or not answer.answer.strip():
            calls.calls[-1]["status"] = "invalid_answer"
            raise RagModelError("Invalid structured answer")
        return answer

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        from app.rag.lifecycle import await_to_completion

        _, cancelled = await await_to_completion(_close_model_clients(self.model))
        if cancelled:
            raise asyncio.CancelledError


async def _close_model_clients(model: ChatOpenAI) -> None:
    try:
        await model.root_async_client.close()
    finally:
        await asyncio.to_thread(model.root_client.close)


async def _close_transports(async_client, sync_client) -> None:
    try:
        if async_client is not None:
            await async_client.aclose()
    finally:
        if sync_client is not None:
            await asyncio.to_thread(sync_client.close)


async def load_model(*, api_key: str | None = None) -> TableModel:
    """Create clients with explicit ownership, including construction failures."""
    from app.rag.lifecycle import await_to_completion

    if api_key is not None and not api_key.strip():
        raise ValueError("An explicit OpenAI API key cannot be empty")
    sync_client = None
    async_client = None
    model = None
    try:
        sync_client = DefaultHttpxClient()
        async_client = DefaultAsyncHttpxClient(
            event_hooks={"request": [_record_http_attempt]}
        )
        model_options = {
            "model": MODEL,
            "base_url": BASE_URL,
            "use_responses_api": True,
            "output_version": "responses/v1",
            "use_previous_response_id": False,
            "store": False,
            "reasoning": {"effort": REASONING_EFFORT},
            "timeout": TIMEOUT_SECONDS,
            "max_retries": 0,
            "cache": False,
            "http_client": sync_client,
            "http_async_client": async_client,
        }
        if api_key is not None:
            model_options["api_key"] = api_key
        model = ChatOpenAI(**model_options)
        return TableModel(model)
    except BaseException:
        cleanup = (
            _close_model_clients(model)
            if model is not None
            else _close_transports(async_client, sync_client)
        )
        _, cancelled = await await_to_completion(cleanup)
        if cancelled:
            raise asyncio.CancelledError
        raise
