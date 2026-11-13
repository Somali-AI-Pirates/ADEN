"""LangChain bindings for conversation interpretation, planning, and answers."""

import asyncio
from hashlib import sha256
from importlib.resources import files
import json
from typing import Any, Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from openai import (
    APIError,
    APITimeoutError,
    DefaultAsyncHttpxClient,
    DefaultHttpxClient,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rag.conversation import ConversationDecision, ConversationTurn
from app.rag.streaming import stream_answer
from app.rag.model_usage import ModelCalls, _record_http_attempt
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


class RagModelError(RuntimeError):
    """The planner or answer provider did not return a usable response."""


class RagContextBudgetError(RagModelError):
    """The complete planner input exceeds its configured model window."""


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    reason: str = Field(min_length=1, description="상태를 고른 업무상 이유와 확인하지 못한 범위")
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


class TableModel:
    def __init__(self, model: ChatOpenAI) -> None:
        self.model = model
        self._closed = False
        self.plan_prompt = _prompt("plan")
        self.answer_prompt = _prompt("answer")
        self.conversation_prompt = _prompt("conversation")
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
        self._plan_prompt = ChatPromptTemplate.from_messages(
            [SystemMessage(content=self.plan_prompt), MessagesPlaceholder("history")]
        )
        self._plan = {
            only_finish: model.bind_tools(
                [finish_tool] if only_finish else self.tools,
                tool_choice="finish" if only_finish else "required",
                strict=True,
                parallel_tool_calls=False,
                max_output_tokens=PLAN_OUTPUT_TOKENS,
            )
            for only_finish in (False, True)
        }
        self._answer_prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=self.answer_prompt),
                MessagesPlaceholder("conversation_history", optional=True),
                ("human", "{payload}"),
            ]
        )
        self._conversation_model = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ConversationDecision",
                    "schema": ConversationDecision.model_json_schema(),
                    "strict": True,
                },
            },
            max_output_tokens=1200,
        )
        # Streaming and non-streaming answers share the same response schema.
        self._answer_model = model.bind(
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
            "conversation": sha256(self.conversation_prompt.encode()).hexdigest(),
        }

    def planner_overhead(self, count_tokens) -> int:
        return count_tokens(self.plan_prompt) + count_tokens(
            json.dumps(self.tools, ensure_ascii=False)
        )

    async def plan(
        self,
        history,
        *,
        finish_only: bool,
        calls: ModelCalls,
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        tools = [self.tools[-1]] if finish_only else self.tools
        model_input = {
            "system": self.plan_prompt,
            "history": [_visible_message(message) for message in history],
            "tools": tools,
            "tool_choice": "finish" if finish_only else "required",
            "parallel_tool_calls": False,
        }
        inputs = {"history": [_safe_message(item) for item in history]}
        with calls.track_call("planner", model_input, tool_schema=tools) as call:
            request_config = calls.callback_config(config, call)
            model = self._plan[finish_only].with_config(calls.model_run_config(call))
            runnable = self._plan_prompt | model
            try:
                message = await runnable.ainvoke(inputs, config=request_config)
                if (
                    isinstance(message, AIMessage)
                    and message.response_metadata.get("status", "completed")
                    != "completed"
                ):
                    call["status"] = "incomplete_response"
                    raise RagModelError("planner returned an incomplete response")
                if not isinstance(message, AIMessage):
                    call["status"] = "invalid_output"
                    raise RagModelError("Invalid planner response")
                calls.record_output(call, _visible_output(message))
                return _safe_message(message)
            except APITimeoutError as error:
                call["status"] = "timeout"
                raise TimeoutError("TableRAG model deadline exceeded") from error
            except APIError as error:
                call["status"] = "provider_error"
                raise RagModelError("planner provider failure") from error
            except (OutputParserException, ValidationError) as error:
                call["status"] = "invalid_response"
                raise RagModelError("planner response validation failed") from error
            except RagModelError:
                if call["status"] == "error":
                    call["status"] = "invalid_output"
                raise

    async def interpret_conversation(
        self,
        question: str,
        turns: dict[int, ConversationTurn],
        older_available: bool,
        *,
        calls: ModelCalls,
        config: RunnableConfig | None = None,
    ) -> ConversationDecision:
        payload = json.dumps(
            {
                "question": question,
                "older_available": older_available,
                "turns": [
                    {"turn_id": turn_id, **turn.model_dump()}
                    for turn_id, turn in turns.items()
                ],
            },
            ensure_ascii=False,
        )
        messages = [
            SystemMessage(content=self.conversation_prompt),
            HumanMessage(content=payload),
        ]
        model_input = {"messages": [_visible_message(message) for message in messages]}
        with calls.track_call("conversation", model_input) as call:
            request_config = calls.callback_config(config, call)
            model = self._conversation_model.with_config(calls.model_run_config(call))
            try:
                response = await model.ainvoke(messages, config=request_config)
                if not isinstance(response, AIMessage) or (
                    response.response_metadata.get("status", "completed") != "completed"
                ):
                    raise RagModelError("Invalid conversation interpretation")
                decision = ConversationDecision.model_validate_json(
                    _visible_content(response.content)
                )
            except APITimeoutError as error:
                call["status"] = "timeout"
                raise TimeoutError("Conversation model deadline exceeded") from error
            except APIError as error:
                call["status"] = "provider_error"
                raise RagModelError("Conversation model provider failure") from error
            except (ValidationError, RagModelError) as error:
                call["status"] = "invalid_output"
                raise RagModelError("Conversation interpretation validation failed") from error
            calls.record_output(call, decision.model_dump())
            return decision

    async def answer(
        self,
        payload: str,
        *,
        context_hash: str,
        calls: ModelCalls,
        config: RunnableConfig | None = None,
        conversation_history: tuple[ConversationTurn, ...] = (),
    ) -> GeneratedAnswer:
        history = [
            message
            for turn in conversation_history
            for message in (
                HumanMessage(content=turn.question),
                AIMessage(content=turn.answer),
            )
        ]
        inputs = {"payload": payload, "conversation_history": history}
        model_input = {
            "system": self.answer_prompt,
            "payload": payload,
            "context_hash": context_hash,
            "response_format": {
                "type": "json_schema",
                "schema": GeneratedAnswer.model_json_schema(),
                "strict": True,
            },
        }
        if history:
            model_input["conversation_history"] = [
                _visible_message(message) for message in history
            ]
        with calls.track_call("answer", model_input) as call:
            request_config = calls.callback_config(config, call)
            model = self._answer_model.with_config(calls.model_run_config(call))
            runnable = self._answer_prompt | model
            try:
                if calls.event_sink is not None:
                    result = await stream_answer(
                        runnable,
                        inputs,
                        config=request_config,
                        emit=calls.event_sink,
                    )
                    raw = result.get("raw") if isinstance(result, dict) else None
                else:
                    raw = await runnable.ainvoke(inputs, config=request_config)
                if (
                    isinstance(raw, AIMessage)
                    and raw.response_metadata.get("status", "completed") != "completed"
                ):
                    call["status"] = "incomplete_response"
                    raise RagModelError("answer returned an incomplete response")
                if not isinstance(raw, AIMessage):
                    call["status"] = "invalid_output"
                    raise RagModelError("Invalid structured answer")
                answer = GeneratedAnswer.model_validate_json(
                    _visible_content(raw.content)
                )
                if not answer.answer.strip():
                    call["status"] = "invalid_answer"
                    raise RagModelError("Invalid structured answer")
                calls.record_output(call, answer.model_dump())
                return answer
            except APITimeoutError as error:
                call["status"] = "timeout"
                raise TimeoutError("TableRAG model deadline exceeded") from error
            except APIError as error:
                call["status"] = "provider_error"
                raise RagModelError("answer provider failure") from error
            except (OutputParserException, ValidationError) as error:
                call["status"] = "invalid_response"
                raise RagModelError("answer response validation failed") from error
            except RagModelError:
                if call["status"] == "error":
                    call["status"] = "invalid_output"
                raise

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
