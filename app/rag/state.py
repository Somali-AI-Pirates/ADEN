"""Request-local state and context for the TableRAG graph."""

from typing import Any, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage

from app.rag.conversation import ConversationTurn
from app.rag.models import EvidenceContext, SearchResult
from app.rag.model_usage import ModelCalls
from app.rag.streaming import EventSink
from app.rag.tools import FinishRequest


class TableRagRuntimeContext(TypedDict):
    """Request-scoped dependencies supplied to LangGraph at invocation time."""

    calls: ModelCalls
    event_sink: EventSink | None
    trace_id: str | None
    conversation_history: tuple[ConversationTurn, ...]


class TableRagState(TypedDict):
    """Mutable workflow data; shared services stay outside this request state."""

    question: str
    search_question: str
    request_id: str
    messages: list[BaseMessage]
    answer_history: tuple[ConversationTurn, ...]
    searches: list[SearchResult]
    groups: list[list[Document]]
    observations: list[dict[str, Any]]
    record_calls: list[dict[str, Any]]
    initial_route: str
    sql_attempts: list[dict[str, Any]]
    visible_table_ids: set[str]
    solve_count: int
    planner_count: int
    turns: list[dict[str, Any]]
    planner_usage: list[dict[str, Any]]
    finish: FinishRequest
    tool_call: dict[str, Any] | None
    error_kind: str | None
    error_class: str | None
    termination_reason: str
    evidence: EvidenceContext | None
    diagnostics: dict[str, Any] | None
    timings: dict[str, float]
    input_tokens: int
    next_step: Literal[
        "initial_search",
        "plan_next_step",
        "lookup_records",
        "search_evidence",
        "read_sources",
        "query_tables",
        "reject_tool",
        "assemble_evidence",
    ]
