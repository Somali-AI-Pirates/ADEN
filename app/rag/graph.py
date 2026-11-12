"""LangGraph implementation of the bounded TableRAG A planning loop."""

import asyncio
import json
from collections.abc import Mapping, MutableMapping, Sequence
from contextlib import nullcontext
from time import perf_counter
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from openai import APIError, APITimeoutError
from pydantic import BaseModel, ValidationError

from app.rag.evidence import (
    assemble_evidence,
    build_observation,
    json_payload,
    source_id,
    token_count,
)
from app.rag.model import RagContextBudgetError, RagModelError
from app.rag.models import RetrievalRun
from app.rag.records import RecordIndex, codes, grounded
from app.rag.table_store import TableStore
from app.rag.state import Planner, Stage, TableRagRuntimeContext, TableRagState
from app.rag.tool_handlers import (
    AsyncRunner,
    PageLookup,
    Search,
    ToolDependencies,
    lookup_records,
    query_tables,
    read_sources,
    records_search_result,
    search_evidence,
)
from app.rag.tools import (
    FinishRequest,
    LookupRecordsRequest,
    QueryTablesRequest,
    ReadSourcesRequest,
    SearchEvidenceRequest,
)
from shared.table_rag_policy import (
    CONTEXT_WINDOW,
    MAX_PLANNER_CALLS,
    MAX_TOOL_OPERATIONS,
    PLAN_OUTPUT_TOKENS,
)



def _usage(message: AIMessage) -> dict[str, Any]:
    usage = message.usage_metadata
    if usage:
        return {
            "status": "known",
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    provider_usage = message.response_metadata.get(
        "token_usage"
    ) or message.response_metadata.get("usage")
    if isinstance(provider_usage, Mapping):
        return {
            "status": "known",
            "input_tokens": provider_usage.get("prompt_tokens"),
            "output_tokens": provider_usage.get("completion_tokens"),
            "total_tokens": provider_usage.get("total_tokens"),
        }
    return {
        "status": "unknown",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def _tool_arguments(value: Any, model: type[BaseModel]) -> BaseModel:
    if isinstance(value, Mapping):
        return model.model_validate(value)
    if isinstance(value, str):
        return model.model_validate_json(value)
    raise ValueError("Tool arguments must be a JSON object")


def _serialized_message_tokens(messages: Sequence[BaseMessage]) -> int:
    serialized = json.dumps(
        [message.model_dump(mode="json") for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return token_count(serialized)


def _span(stage: Stage | None, name: str, **kwargs: Any):
    if stage is None:
        return nullcontext({})
    return stage(name, **kwargs)


def _set_span_output(span: Any, output: dict[str, Any]) -> None:
    if isinstance(span, MutableMapping):
        span.update(output=output)


def _planner_error_details(error: Exception) -> tuple[str, str, str]:
    if isinstance(error, RagContextBudgetError):
        return (
            "context_budget_exceeded",
            "context_budget_exceeded",
            "context_budget_exceeded",
        )
    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "timeout", "timeout", "planner_timeout"
    if isinstance(error, APIError):
        return "provider_error", "provider_error", "planner_provider_error"
    if isinstance(error, RagModelError):
        return "model_error", "model_error", "planner_model_error"
    raise TypeError(f"Unexpected planner error: {type(error).__name__}")


def _turn(
    number: int,
    status: str,
    *,
    seconds: float = 0.0,
    usage: dict[str, Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    return {
        "turn": number,
        "status": status,
        "seconds": seconds,
        "usage": usage
        or {
            "status": "unknown",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        **details,
    }


def _has_explicit_exclusion(question: str) -> bool:
    return any(marker in question for marker in ("제외", "말고", "빼고", "아닌"))


class TableRagGraph:
    """Compile once around request-shared retrieval and storage resources."""

    def __init__(
        self,
        search: Search,
        table_store: TableStore,
        page_lookup: PageLookup,
        *,
        record_index: RecordIndex | None = None,
        context_window: int = CONTEXT_WINDOW,
        plan_overhead_tokens: int = 0,
        planner_output_tokens: int = PLAN_OUTPUT_TOKENS,
        run_sync: AsyncRunner = asyncio.to_thread,
    ) -> None:
        if (
            context_window <= 0
            or plan_overhead_tokens < 0
            or planner_output_tokens <= 0
        ):
            raise ValueError("Planner token limits must be positive")
        self.context_window = context_window
        self.plan_overhead_tokens = plan_overhead_tokens
        self.planner_output_tokens = planner_output_tokens
        self.dependencies = ToolDependencies(
            search=search,
            record_index=record_index,
            table_store=table_store,
            page_lookup=page_lookup,
            run_sync=run_sync,
        )
        self.graph = self._compile()

    def _compile(self):
        workflow = StateGraph(TableRagState, context_schema=TableRagRuntimeContext)
        workflow.add_node("initial_search", self._initial_search)
        workflow.add_node("plan", self._plan)
        workflow.add_node("execute", self._execute)
        workflow.add_node("assemble", self._assemble)
        workflow.add_edge(START, "initial_search")
        workflow.add_edge("initial_search", "plan")
        workflow.add_conditional_edges(
            "plan",
            lambda state: "execute" if state["next_step"] == "execute" else "assemble",
            {"execute": "execute", "assemble": "assemble"},
        )
        workflow.add_edge("execute", "plan")
        workflow.add_edge("assemble", END)
        return workflow.compile()

    async def _initial_search(
        self,
        state: TableRagState,
        runtime: Runtime[TableRagRuntimeContext],
    ) -> dict[str, Any]:
        started = perf_counter()
        question = state["question"]
        groups = []
        searches = []
        observations = []
        record_calls = []
        initial_route = "hybrid"
        explicit_codes = codes(question)
        record_lookup_allowed = (
            bool(explicit_codes)
            and len(explicit_codes) <= 4
            and not _has_explicit_exclusion(question)
        )

        with _span(
            runtime.context["stage"],
            "rag.initial_search",
            input={"request_id": state["request_id"], "question": question},
        ) as span:
            if record_lookup_allowed:
                if self.dependencies.record_index is None:
                    raise RuntimeError("Integrated record index is not ready")
                lookup_input = [
                    {"identifiers": [identifier], "constraints": [], "roles": []}
                    for identifier in explicit_codes
                ]
                resolution = await self.dependencies.run_sync(
                    self.dependencies.record_index.lookup,
                    lookup_input,
                )
                record_calls.append(resolution)
                records_result, record_observation = await self.dependencies.run_sync(
                    records_search_result,
                    question,
                    resolution,
                    self.dependencies.table_store,
                    self.dependencies.page_lookup,
                )
                searches.append(records_result)
                groups.append(list(records_result.pages))
                observations.append(record_observation)
                initial_route = "records"
                record_lookup_unique = bool(resolution["targets"]) and all(
                    target["status"] == "matched"
                    and not target["truncated"]
                    and target["entities"]
                    and all(entity["sources"] for entity in target["entities"])
                    for target in resolution["targets"]
                )
            else:
                record_lookup_unique = False

            if not record_lookup_unique:
                result = await self.dependencies.run_sync(
                    self.dependencies.search, question
                )
                observation = await self.dependencies.run_sync(
                    build_observation,
                    result,
                    self.dependencies.table_store,
                    subquery=question,
                )
                searches.append(result)
                groups.append(list(result.pages))
                observations.append(observation)
                initial_route = "hybrid"

            elapsed = perf_counter() - started
            _set_span_output(
                span,
                {
                    "route": initial_route,
                    "candidate_count": sum(len(result.pages) for result in searches),
                    "candidate_source_ids": list(
                        dict.fromkeys(
                            source_id(page)
                            for result in searches
                            for page in result.pages
                        )
                    ),
                    "record_lookup_count": len(record_calls),
                    "observations": observations,
                    "seconds": elapsed,
                },
            )
        initial_payload = json_payload(
            {
                "question": question,
                "initial_route": initial_route,
                "initial_evidence": observations,
            }
        )
        return {
            "messages": [HumanMessage(content=initial_payload)],
            "searches": searches,
            "groups": groups,
            "observations": observations,
            "record_calls": record_calls,
            "initial_route": initial_route,
            "visible_table_ids": {
                table["table_id"]
                for observation in observations
                for table in observation.get("tables", [])
            },
            "solve_count": 0,
            "planner_count": 0,
            "turns": [],
            "planner_usage": [],
            "sql_attempts": [],
            "finish": FinishRequest(
                status="insufficient",
                selected_sources=[],
                interim_summary="",
                conflicts=[],
            ),
            "error_kind": None,
            "error_class": None,
            "termination_reason": "running",
            "timings": {"search_seconds": elapsed, "sql_seconds": 0.0},
            "input_tokens": 0,
        }

    def _planner_failure(
        self,
        state: TableRagState,
        *,
        planner_count: int,
        input_tokens: int,
        status: str,
        error_kind: str,
        termination_reason: str,
        error_class: str | None = None,
        seconds: float = 0.0,
        span: Any | None = None,
    ) -> dict[str, Any]:
        turn = _turn(
            planner_count,
            status,
            seconds=seconds,
            input_tokens=input_tokens,
        )
        if error_class:
            turn["error_class"] = error_class
        if span is not None:
            output = {"status": status, "seconds": seconds}
            if error_class:
                output["error_class"] = error_class
            _set_span_output(span, output)
        return {
            "planner_count": planner_count,
            "input_tokens": input_tokens,
            "error_kind": error_kind,
            "error_class": error_class,
            "termination_reason": termination_reason,
            "timings": {
                **state["timings"],
                "planner_seconds": state["timings"].get("planner_seconds", 0.0)
                + seconds,
            },
            "turns": [*state["turns"], turn],
            "planner_usage": [*state["planner_usage"], turn["usage"]],
            "next_step": "assemble",
        }

    async def _plan(
        self,
        state: TableRagState,
        runtime: Runtime[TableRagRuntimeContext],
    ) -> dict[str, Any]:
        planner_count = state["planner_count"] + 1
        messages = state["messages"]
        finish_only = state["solve_count"] >= MAX_TOOL_OPERATIONS
        input_tokens = _serialized_message_tokens(messages) + self.plan_overhead_tokens
        if input_tokens + self.planner_output_tokens > self.context_window:
            with _span(
                runtime.context["stage"],
                "rag.plan",
                input={"request_id": state["request_id"], "turn": planner_count},
            ) as span:
                return self._planner_failure(
                    state,
                    planner_count=planner_count,
                    input_tokens=input_tokens,
                    status="context_budget_exceeded",
                    error_kind="context_budget_exceeded",
                    termination_reason="context_budget_exceeded",
                    span=span,
                )

        started = perf_counter()
        with _span(
            runtime.context["stage"],
            "rag.plan",
            input={"request_id": state["request_id"], "turn": planner_count},
        ) as span:
            try:
                response = await runtime.context["planner"](
                    messages,
                    finish_only=finish_only,
                )
            except (
                RagContextBudgetError,
                TimeoutError,
                APIError,
                RagModelError,
            ) as error:
                status, error_kind, termination_reason = _planner_error_details(error)
                return self._planner_failure(
                    state,
                    planner_count=planner_count,
                    input_tokens=input_tokens,
                    status=status,
                    error_kind=error_kind,
                    termination_reason=termination_reason,
                    error_class=type(error).__name__,
                    seconds=perf_counter() - started,
                    span=span,
                )

            elapsed = perf_counter() - started
            if not isinstance(response, AIMessage):
                return self._planner_failure(
                    state,
                    planner_count=planner_count,
                    input_tokens=input_tokens,
                    status="invalid_response_type",
                    error_kind="format_error",
                    termination_reason="invalid_response_type",
                    error_class=type(response).__name__,
                    seconds=elapsed,
                    span=span,
                )
            usage = _usage(response)
            _set_span_output(
                span,
                {
                    "status": "response_received",
                    "seconds": elapsed,
                    "usage": usage,
                    "tool_count": len(response.tool_calls),
                    "tools": [call.get("name") for call in response.tool_calls],
                },
            )

        turn = _turn(
            planner_count,
            "complete",
            seconds=elapsed,
            usage=usage,
            finish_only=finish_only,
        )
        update = {
            "planner_count": planner_count,
            "input_tokens": input_tokens,
            "messages": [*messages, response],
            "turns": [*state["turns"], turn],
            "planner_usage": [*state["planner_usage"], usage],
            "timings": {
                **state["timings"],
                "planner_seconds": state["timings"].get("planner_seconds", 0.0)
                + elapsed,
            },
        }
        calls = response.tool_calls
        if len(calls) != 1:
            turn.update(status="invalid_tool_count", tool_count=len(calls))
            return {
                **update,
                "error_kind": "format_error",
                "termination_reason": "invalid_tool_count",
                "next_step": "assemble",
            }

        call = calls[0]
        call_id = call.get("id")
        name = call.get("name")
        turn["tool"] = name
        if not isinstance(call_id, str) or not call_id:
            turn["status"] = "invalid_tool_call_id"
            return {
                **update,
                "error_kind": "format_error",
                "termination_reason": "invalid_tool_call_id",
                "next_step": "assemble",
            }
        if finish_only and name != "finish":
            turn["status"] = "finish_required"
            return {
                **update,
                "error_kind": "format_error",
                "termination_reason": "finish_required",
                "next_step": "assemble",
            }
        if name == "finish":
            try:
                finish = _tool_arguments(call.get("args"), FinishRequest)
            except (ValidationError, ValueError) as error:
                turn.update(
                    status="invalid_finish_arguments",
                    error_class=type(error).__name__,
                )
                return {
                    **update,
                    "error_kind": "format_error",
                    "error_class": type(error).__name__,
                    "termination_reason": "invalid_finish_arguments",
                    "next_step": "assemble",
                }
            if not isinstance(finish, FinishRequest):
                raise TypeError("Expected finish tool arguments")
            discovered = {
                source_id(document) for group in state["groups"] for document in group
            }
            unavailable = [
                identifier
                for identifier in finish.selected_sources
                if identifier not in discovered
            ]
            if unavailable:
                selected_sources = [
                    identifier
                    for identifier in finish.selected_sources
                    if identifier in discovered
                ]
                finish = finish.model_copy(
                    update={
                        "status": "insufficient",
                        "selected_sources": selected_sources,
                        "conflicts": list(
                            dict.fromkeys(
                                [
                                    *finish.conflicts,
                                    "선택한 출처를 조회하지 못했습니다.",
                                ]
                            )
                        )[:6],
                    }
                )
            turn.update(status="finished", finish_status=finish.status)
            return {
                **update,
                "finish": finish,
                "termination_reason": "finished",
                "next_step": "assemble",
            }

        if planner_count >= MAX_PLANNER_CALLS:
            turn["status"] = "max_planner_calls"
            return {
                **update,
                "error_kind": "step_limit",
                "termination_reason": "max_planner_calls",
                "next_step": "assemble",
            }
        return {**update, "tool_call": call, "next_step": "execute"}

    async def _execute(
        self,
        state: TableRagState,
        runtime: Runtime[TableRagRuntimeContext],
    ) -> dict[str, Any]:
        call = state["tool_call"]
        if call is None:
            raise RuntimeError("Execution step has no validated tool call")
        call_id = call["id"]
        name = call["name"]
        solve_count = state["solve_count"] + 1
        request_models = {
            "lookup_records": LookupRecordsRequest,
            "search_evidence": SearchEvidenceRequest,
            "read_sources": ReadSourcesRequest,
            "query_tables": QueryTablesRequest,
        }
        try:
            request_model = request_models.get(name)
            if request_model is None:
                raise ValueError("Unsupported planner tool")
            request = _tool_arguments(call.get("args"), request_model)
        except (ValidationError, ValueError) as error:
            return self._invalid_tool_result(state, call_id, name, solve_count, error)

        tool_started = perf_counter()
        documents: list[Document] = []
        if isinstance(request, LookupRecordsRequest):
            try:
                grounded(request, state["question"], state["observations"])
            except ValueError as error:
                return self._invalid_tool_result(
                    state,
                    call_id,
                    name,
                    solve_count,
                    error,
                )
        elif isinstance(request, QueryTablesRequest):
            if not set(request.table_ids).issubset(state["visible_table_ids"]):
                return self._invalid_tool_result(
                    state,
                    call_id,
                    name,
                    solve_count,
                    ValueError("SQL references a table whose schema was not observed"),
                )
        elif isinstance(request, ReadSourcesRequest):
            observed_source_ids = {
                source_id(document)
                for group in state["groups"]
                for document in group
            }
            if not set(request.source_ids).issubset(observed_source_ids):
                return self._invalid_tool_result(
                    state,
                    call_id,
                    name,
                    solve_count,
                    ValueError("read_sources requires previously discovered sources"),
                )
            documents = []
            for identifier in request.source_ids:
                document = self.dependencies.page_lookup(identifier)
                if document is None:
                    return self._invalid_tool_result(
                        state,
                        call_id,
                        name,
                        solve_count,
                        ValueError("Original page is unavailable"),
                    )
                documents.append(document)

        if isinstance(request, SearchEvidenceRequest):
            with _span(
                runtime.context["stage"],
                "rag.search_evidence",
                input={
                    "request_id": state["request_id"],
                    "query": request.query,
                    "kind": request.kind,
                },
            ) as span:
                delta = await search_evidence(
                    request,
                    dependencies=self.dependencies,
                )
                _set_span_output(span, delta.span_output)
        elif isinstance(request, LookupRecordsRequest):
            with _span(
                runtime.context["stage"],
                "rag.lookup_records",
                input={
                    "request_id": state["request_id"],
                    "targets": request.model_dump()["targets"],
                },
            ) as span:
                delta = await lookup_records(
                    request,
                    question=state["question"],
                    dependencies=self.dependencies,
                )
                _set_span_output(span, delta.span_output)
        elif isinstance(request, QueryTablesRequest):
            with _span(
                runtime.context["stage"],
                "rag.sql",
                input={
                    "request_id": state["request_id"],
                    "table_ids": request.table_ids,
                    "sql": request.sql,
                },
            ) as span:
                delta = await query_tables(
                    request,
                    dependencies=self.dependencies,
                )
                _set_span_output(span, delta.span_output)
        else:
            with _span(
                runtime.context["stage"],
                "rag.read_sources",
                input={
                    "request_id": state["request_id"],
                    "source_ids": request.source_ids,
                },
            ) as span:
                delta = await read_sources(
                    request,
                    documents=documents,
                    dependencies=self.dependencies,
                )
                span_output = {
                    **delta.span_output,
                    "seconds": perf_counter() - tool_started,
                }
                _set_span_output(span, span_output)

        timings = dict(state["timings"])
        if delta.timing_key is not None:
            timings[delta.timing_key] = (
                timings.get(delta.timing_key, 0.0) + delta.elapsed_seconds
            )
        visible_table_ids = set(state["visible_table_ids"])
        visible_table_ids.update(delta.visible_table_ids)
        turn = _turn(
            len(state["turns"]) + 1,
            "executed",
            tool=name,
            arguments=request.model_dump(),
            observation=delta.observation,
            tool_seconds=perf_counter() - tool_started,
        )
        return {
            "messages": [
                *state["messages"],
                ToolMessage(
                    content=json_payload(delta.observation),
                    tool_call_id=call_id,
                    name=name,
                ),
            ],
            "searches": [*state["searches"], *delta.searches],
            "groups": [
                *state["groups"],
                *(list(group) for group in delta.groups),
            ],
            "observations": [*state["observations"], delta.observation],
            "record_calls": [*state["record_calls"], *delta.record_calls],
            "sql_attempts": [*state["sql_attempts"], *delta.sql_attempts],
            "visible_table_ids": visible_table_ids,
            "solve_count": solve_count,
            "turns": [*state["turns"], turn],
            "timings": timings,
            "next_step": "plan",
        }

    @staticmethod
    def _invalid_tool_result(
        state: TableRagState,
        call_id: str,
        name: str,
        solve_count: int,
        error: Exception,
    ) -> dict[str, Any]:
        message = {
            "status": "invalid_arguments",
            "error_class": type(error).__name__,
            "error": str(error)[:160],
        }
        return {
            "messages": [
                *state["messages"],
                ToolMessage(
                    content=json_payload(message),
                    tool_call_id=call_id,
                    name=name,
                ),
            ],
            "solve_count": solve_count,
            "turns": [
                *state["turns"],
                _turn(len(state["turns"]) + 1, "invalid_arguments", tool=name),
            ],
            "next_step": "plan",
        }

    async def _assemble(
        self,
        state: TableRagState,
        runtime: Runtime[TableRagRuntimeContext],
    ) -> dict[str, Any]:
        started = perf_counter()
        with _span(
            runtime.context["stage"],
            "rag.assemble",
            input={"request_id": state["request_id"]},
        ) as span:
            evidence = await self.dependencies.run_sync(
                assemble_evidence,
                state["question"],
                state["groups"],
                state["finish"].selected_sources,
                state["sql_attempts"],
                self.dependencies.page_lookup,
            )
            elapsed = perf_counter() - started
            _set_span_output(
                span,
                {
                    "candidate_count": len(evidence.candidate_source_ids),
                    "candidate_source_ids": list(evidence.candidate_source_ids),
                    "selected_count": len(evidence.pages),
                    "final_context_chunks": [
                        {
                            "source_id": source_id(page),
                            "book_id": page.metadata["book_id"],
                            "page": page.metadata["page"],
                            "text": page.page_content,
                        }
                        for page in evidence.pages
                    ],
                    "included_sql_results": list(evidence.sql_results),
                    "model_context_text": evidence.model_input,
                    "sql_count": len(evidence.sql_results),
                    "token_count": evidence.token_count,
                    "context_hash": evidence.context_hash,
                    "seconds": elapsed,
                },
            )
        timings = {
            **state["timings"],
            "assembly_seconds": elapsed,
        }
        timings["total_seconds"] = sum(
            value for key, value in timings.items() if key.endswith("_seconds")
        )
        finish = state["finish"]
        final_source_ids = set(evidence.source_ids)
        missing_selected = [
            identifier
            for identifier in finish.selected_sources
            if identifier not in final_source_ids
        ]
        if missing_selected or (
            finish.status == "sufficient" and not finish.selected_sources
        ):
            conflicts = list(finish.conflicts)
            if missing_selected:
                conflicts.append("선택한 출처 일부가 최종 문맥에 남지 않았습니다.")
            if not finish.selected_sources:
                conflicts.append("충분 판정에 연결된 원문 출처가 없습니다.")
            finish = finish.model_copy(
                update={
                    "status": "insufficient",
                    "conflicts": list(dict.fromkeys(conflicts))[:6],
                }
            )
        diagnostics = {
            "request_id": state["request_id"],
            "trace_id": runtime.context["trace_id"],
            "termination_reason": state["termination_reason"],
            "error_kind": state.get("error_kind"),
            "error_class": state.get("error_class"),
            "initial_route": state["initial_route"],
            "planner_calls": state["planner_count"],
            "solve_calls": state["solve_count"],
            "search_calls": len(state["searches"]),
            "record_calls": state["record_calls"],
            "observations": state["observations"],
            "sql_calls": len(state["sql_attempts"]),
            "planner_input_tokens": state["input_tokens"],
            "planner_usage": state["planner_usage"],
            "turns": state["turns"],
            "timings": timings,
        }
        return {"evidence": evidence, "finish": finish, "diagnostics": diagnostics}

    async def run(
        self,
        question: str,
        *,
        planner: Planner,
        request_id: str,
        stage: Stage | None = None,
        trace_id: str | None = None,
    ) -> RetrievalRun:
        state = await self.graph.ainvoke(
            {
                "question": question,
                "request_id": request_id,
                "messages": [],
                "searches": [],
                "groups": [],
                "observations": [],
                "record_calls": [],
                "initial_route": "hybrid",
                "sql_attempts": [],
                "visible_table_ids": set(),
                "solve_count": 0,
                "planner_count": 0,
                "turns": [],
                "planner_usage": [],
                "finish": FinishRequest(
                    status="insufficient",
                    selected_sources=[],
                    interim_summary="",
                    conflicts=[],
                ),
                "tool_call": None,
                "error_kind": None,
                "error_class": None,
                "termination_reason": "running",
                "evidence": None,
                "diagnostics": None,
                "timings": {"search_seconds": 0.0, "sql_seconds": 0.0},
                "input_tokens": 0,
                "next_step": "assemble",
            },
            context={
                "planner": planner,
                "stage": stage,
                "trace_id": trace_id,
            },
            config={"recursion_limit": 32},
        )
        finish = state["finish"]
        evidence = state["evidence"]
        diagnostics = state["diagnostics"]
        if evidence is None or diagnostics is None:
            raise RuntimeError("Graph finished without assembling evidence")
        return RetrievalRun(
            question=question,
            status=finish.status,
            finish=finish,
            evidence=evidence,
            searches=tuple(state["searches"]),
            sql_attempts=tuple(state["sql_attempts"]),
            termination_reason=state["termination_reason"],
            diagnostics=diagnostics,
        )
