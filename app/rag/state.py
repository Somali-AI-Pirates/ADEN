"""Request-local state and context for the TableRAG graph."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage

from app.rag.models import EvidenceContext, SearchResult
from app.rag.tools import FinishRequest
from shared.contracts import ConversationTurn


Planner = Callable[..., Awaitable[AIMessage]]
Stage = Callable[..., Any]


class TableRagRuntimeContext(TypedDict):
    """Request-scoped dependencies supplied to LangGraph at invocation time."""

    planner: Planner
    stage: Stage | None
    trace_id: str | None
    plan_guidance_tokens: int
    include_clarification_fallback: bool


class TableRagState(TypedDict):
    """Mutable workflow data; shared services stay outside this request state."""

    question: str
    conversation_history: tuple[ConversationTurn, ...]
    request_id: str
    messages: list[BaseMessage]
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
    next_step: Literal["plan", "execute", "assemble"]
