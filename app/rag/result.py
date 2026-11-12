"""Validate a TableRAG answer and serialize its evidence for saved evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document

from app.generation.citations import resolve_citations
from app.observability.records import compute_context_hash
from app.rag.config import RETRIEVAL_MODE
from app.rag.model import GeneratedAnswer, RagModelError
from app.rag.models import RetrievalRun
from shared.contracts import AnswerStatus, Citation, QueryResponse


def _source(document: Document) -> dict[str, Any]:
    return {
        key: document.metadata[key]
        for key in ("source_id", "book_id", "page", "file_name")
    }


def _page_record(document: Document) -> dict[str, Any]:
    return {**_source(document), "chunk_id": document.metadata["unit_id"]}


def validate_answer(
    answer: GeneratedAnswer,
    pages: tuple[Document, ...],
    request_id: str,
    retrieval_status: str = "sufficient",
) -> QueryResponse:
    if retrieval_status != "sufficient" and answer.status != retrieval_status:
        raise RagModelError("Answer status contradicts the retrieval decision")
    sources = {page.metadata["source_id"]: Citation(**_source(page)) for page in pages}
    try:
        citations = resolve_citations(answer.citations, sources)
    except ValueError as error:
        raise RagModelError(
            "Answer cites a source outside the final context"
        ) from error
    if answer.status == "answered" and not citations:
        raise RagModelError("Answered status requires an evidence citation")
    status: AnswerStatus = (
        "abstained" if answer.status == "insufficient" else answer.status
    )
    reasons = {
        "answered": "최종 문맥의 문서 또는 표 조회 근거를 사용했습니다.",
        "needs_clarification": "질문에 답하려면 추가 정보가 필요합니다.",
        "abstained": "답변에 필요한 근거를 확인하지 못했습니다.",
    }
    return QueryResponse(
        request_id=request_id,
        status=status,
        answer=answer.answer,
        reason=reasons[status],
        citations=citations,
    )


def retrieval_record(
    retrieval: RetrievalRun, candidates: tuple[Document, ...], model_context_tokens: int
) -> dict[str, Any]:
    evidence = retrieval.evidence
    chunks = [
        {**_page_record(page), "text": page.page_content} for page in evidence.pages
    ]
    return {
        "retrieved_candidates": [
            {**_page_record(page), "text": page.page_content} for page in candidates
        ],
        "final_context_chunks": chunks,
        "candidate_source_ids": list(evidence.candidate_source_ids),
        "final_source_ids": list(evidence.source_ids),
        "context_text": evidence.model_input,
        "context_hash": evidence.context_hash,
        "final_context_hash": compute_context_hash(chunks),
        "model_context_text": evidence.model_input,
        "model_context_hash": evidence.context_hash,
        "model_context_token_count": model_context_tokens,
        "evidence_token_count": evidence.token_count,
        "budget_excluded_chunk_ids": list(evidence.excluded_chunk_ids),
        "sql_results": list(retrieval.sql_attempts),
        "included_sql_results": list(evidence.sql_results),
        "diagnostics": {
            **retrieval.diagnostics,
            "termination_reason": retrieval.termination_reason,
            "finish_status": retrieval.status,
            "planner_selected_sources": list(retrieval.finish.selected_sources),
            "planner_sources_missing_from_final": sorted(
                set(retrieval.finish.selected_sources) - set(evidence.source_ids)
            ),
            "searches": [search.diagnostics for search in retrieval.searches],
            "selection_context_hash": evidence.page_context_hash,
        },
    }


@dataclass(frozen=True)
class RagRun:
    response: QueryResponse
    retrieval: RetrievalRun
    candidates: tuple[Document, ...]
    usage: dict[str, Any]
    latency_ms: float
    model_context_tokens: int
    trace: dict[str, Any]
    config_hash: str

    def to_record(self) -> dict[str, Any]:
        return {
            **self.response.model_dump(mode="json"),
            "dataset_id": "advanced",
            "question": self.retrieval.question,
            "retrieval_mode": RETRIEVAL_MODE,
            "config_hash": self.config_hash,
            **retrieval_record(
                self.retrieval, self.candidates, self.model_context_tokens
            ),
            "trace_id": self.trace.get("langfuse_trace_id"),
            **self.trace,
            "usage": self.usage,
            "attempts": self.usage.get("attempts", []),
            "latency_ms": round(self.latency_ms),
            "latency_scope": "service",
        }


def retrieval_trace_fields(
    retrieval: RetrievalRun, candidates: tuple[Document, ...], model_context_tokens: int
) -> dict[str, Any]:
    """The normalized retrieval/final-context payload consumed by HTTP evaluation."""
    record = retrieval_record(retrieval, candidates, model_context_tokens)
    evidence = retrieval.evidence
    return {
        "retrieved_candidates": record["retrieved_candidates"],
        "final_context_chunks": record["final_context_chunks"],
        "final_context_hash": record["final_context_hash"],
        "model_context_text": evidence.model_input,
        "model_context_hash": evidence.context_hash,
        "model_context_token_count": model_context_tokens,
        "budget_excluded_chunk_ids": evidence.excluded_chunk_ids,
    }


def answer_trace_fields(
    response: QueryResponse | None, latency_ms: float, failure: BaseException | None
) -> dict[str, Any]:
    return {
        "status": response.status if response else "error",
        "answer": response.answer if response else None,
        "reason": response.reason if response else None,
        "citations": [c.model_dump(mode="json") for c in response.citations]
        if response
        else [],
        "latency_ms": round(latency_ms),
        "error_type": type(failure).__name__ if failure else None,
    }


def failure_metadata(
    trace_status: dict[str, Any],
    usage: dict[str, Any],
    latency_ms: float,
    retrieval: RetrievalRun | None,
    candidates: tuple[Document, ...],
    model_context_tokens: int,
) -> dict[str, Any]:
    metadata = {
        **trace_status,
        "usage": usage,
        "attempts": usage.get("attempts", []),
        "latency_ms": latency_ms,
    }
    if retrieval is not None:
        metadata.update(retrieval_record(retrieval, candidates, model_context_tokens))
    return metadata
