"""Request entry point shared by the Advanced TableRAG API and evaluation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import tiktoken
from anyio import CapacityLimiter, to_thread
from dotenv import load_dotenv
from langchain_core.documents import Document

from app.generation.citations import resolve_citations
from app.observability.records import compute_context_hash
from app.observability.run_store import save_request_failure, save_request_run
from app.observability.tracing import Tracer
from app.rag.config import DEFAULT_DATA_DIRECTORY, DEFAULT_RUN_DIRECTORY, RETRIEVAL_MODE
from app.rag.corpus import load_corpus
from app.rag.streaming import EventSink
from app.rag.model import (
    ANSWER_OUTPUT_TOKENS,
    CONTEXT_WINDOW,
    MODEL,
    PLAN_OUTPUT_TOKENS,
    REASONING_EFFORT,
    TIMEOUT_SECONDS,
    GeneratedAnswer,
    ModelCalls,
    RagContextBudgetError,
    RagModelError,
    TableModel,
    load_model,
)
from shared.contracts import AnswerStatus, Citation, QueryResponse
from shared.settings import PROJECT_ROOT

if TYPE_CHECKING:
    from app.rag.models import RetrievalRun, SearchResult
    from app.rag.table_store import TableStore

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _source(document: Document) -> dict[str, Any]:
    return {
        key: document.metadata[key]
        for key in ("source_id", "book_id", "page", "file_name")
    }


def _page_record(document: Document) -> dict[str, Any]:
    return {**_source(document), "chunk_id": document.metadata["unit_id"]}


def _response(
    answer: GeneratedAnswer, pages: tuple[Document, ...], request_id: str,
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


def _retrieval_record(
    retrieval: RetrievalRun, candidates: tuple[Document, ...], model_context_tokens: int
) -> dict[str, Any]:
    evidence = retrieval.evidence
    chunks = [
        {**_page_record(page), "text": page.page_content} for page in evidence.pages
    ]
    return {
        "retrieved_candidates": [{**_page_record(page), "text": page.page_content} for page in candidates],
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
            **_retrieval_record(
                self.retrieval, self.candidates, self.model_context_tokens
            ),
            "trace_id": self.trace.get("langfuse_trace_id"),
            **self.trace,
            "usage": self.usage,
            "attempts": self.usage.get("attempts", []),
            "latency_ms": round(self.latency_ms),
            "latency_scope": "service",
        }


class RagService:
    def __init__(
        self,
        *,
        search: Callable[[str], SearchResult],
        page_lookup: Callable[[str], Document | None],
        table_store: TableStore,
        model: TableModel,
        tracer: Tracer,
        manifest: dict[str, Any],
        record_index: Any = None,
        run_directory: Path | None = None,
        close_search: Callable[[], None] | None = None,
    ) -> None:
        self._page_lookup = page_lookup
        self._model = model
        self.tracer = tracer
        self.manifest = manifest
        self._record_index = record_index
        self._run_directory = run_directory
        self._close_search = close_search
        self.config_hash = _hash(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        )
        from app.rag.graph import TableRagGraph

        self._encoding = tiktoken.get_encoding("o200k_base")
        self._workers = CapacityLimiter(2)
        self._graph = TableRagGraph(
            search=search,
            page_lookup=page_lookup,
            table_store=table_store,
            record_index=record_index,
            context_window=CONTEXT_WINDOW,
            plan_overhead_tokens=model.planner_overhead(self._count_tokens),
            run_sync=self._run_sync,
        )
        self._closed = False

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

    async def _run_sync(self, function, *args, **kwargs):
        return await to_thread.run_sync(
            partial(function, *args, **kwargs), limiter=self._workers
        )

    async def ainvoke(
        self, question: str, *, request_id: str, on_event: EventSink | None = None
    ) -> RagRun:
        if self._closed:
            raise RuntimeError("TableRAG service is closed")
        question = question.strip()
        if not question:
            raise ValueError("질문을 입력해 주세요.")
        started = perf_counter()
        trace = self.tracer.start_request(
            request_id,
            question=question,
            retrieval_mode=RETRIEVAL_MODE,
            dataset_id="advanced",
            config_hash=self.config_hash,
            metadata={"question_sha256": _hash(question)},
        )
        calls = ModelCalls(trace, on_event=on_event)
        response = None
        retrieval = None
        candidates = ()
        model_context_tokens = 0
        failure = None

        async def progress(stage: str, message: str):
            if on_event is not None:
                await on_event({"type": "progress", "stage": stage, "message": message})

        async def plan(history, *, finish_only: bool):
            await progress("reviewing", "찾은 기록과 문서 근거를 확인하고 있습니다.")
            return await self._model.plan(history, finish_only=finish_only, calls=calls)

        try:
            await progress("retrieving", "관련 기록과 문서를 찾고 있습니다.")
            retrieval = await self._graph.run(
                question,
                planner=plan,
                request_id=request_id,
                stage=trace.stage,
                trace_id=trace.trace_id,
            )
            evidence = retrieval.evidence
            candidates = tuple(
                page
                for source_id in evidence.candidate_source_ids
                if (page := self._page_lookup(source_id)) is not None
            )
            model_context_tokens = self._count_tokens(evidence.model_input)
            recorded_evidence = _retrieval_record(retrieval, candidates, model_context_tokens)
            trace.record_retrieval(
                retrieved_candidates=recorded_evidence["retrieved_candidates"],
                final_context_chunks=recorded_evidence["final_context_chunks"],
                final_context_hash=recorded_evidence["final_context_hash"],
                model_context_text=evidence.model_input,
                model_context_hash=evidence.context_hash,
                model_context_token_count=model_context_tokens,
                budget_excluded_chunk_ids=evidence.excluded_chunk_ids,
                k=8,
            )
            _check_termination(retrieval.termination_reason)
            await progress("answering", "근거를 바탕으로 답변을 작성하고 있습니다.")
            answer = await self._model.answer(
                _json(
                    {"question": question, "evidence": json.loads(evidence.model_input),
                     "retrieval_status": retrieval.status}
                ),
                context_hash=evidence.context_hash,
                calls=calls,
            )
            await progress("validating", "답변과 출처를 확인하고 있습니다.")
            response = _response(answer, evidence.pages, request_id, retrieval.status)
        except BaseException as error:
            failure = error
            raise
        finally:
            latency_ms = (perf_counter() - started) * 1000
            trace_status = trace.finish(
                status=response.status if response else "error",
                answer=response.answer if response else None,
                reason=response.reason if response else None,
                citations=[c.model_dump(mode="json") for c in response.citations] if response else [],
                latency_ms=round(latency_ms),
                error_type=type(failure).__name__ if failure else None,
            )
            if isinstance(failure, Exception):
                metadata = {
                    **trace_status,
                    "usage": calls.summary(),
                    "attempts": calls.summary().get("attempts", []),
                    "latency_ms": latency_ms,
                }
                if retrieval is not None:
                    metadata.update(
                        _retrieval_record(retrieval, candidates, model_context_tokens)
                    )
                setattr(failure, "rag_metadata", metadata)
                if self._run_directory is not None:
                    await self._run_sync(
                        save_request_failure, question, request_id, failure,
                        service_manifest=self.manifest,
                        config_hash=self.config_hash,
                        output_root=self._run_directory,
                    )
        assert response is not None and retrieval is not None
        run = RagRun(
            response=response,
            retrieval=retrieval,
            candidates=candidates,
            usage=calls.summary(),
            latency_ms=latency_ms,
            model_context_tokens=model_context_tokens,
            trace=trace_status,
            config_hash=self.config_hash,
        )
        if self._run_directory is not None:
            await progress("saving", "답변 기록을 저장하고 있습니다.")
            await self._run_sync(
                save_request_run, run.to_record(),
                service_manifest=self.manifest,
                output_root=self._run_directory,
            )
        return run

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._model.aclose()
        finally:
            try:
                if self._record_index is not None:
                    await self._run_sync(self._record_index.close)
            finally:
                try:
                    if self._close_search is not None:
                        await self._run_sync(self._close_search)
                finally:
                    await asyncio.to_thread(self.tracer.shutdown)


def _check_termination(reason: str) -> None:
    if reason == "context_budget_exceeded":
        raise RagContextBudgetError("TableRAG planner context budget exceeded")
    if reason in {"timeout", "model_timeout", "planner_timeout"}:
        raise TimeoutError("TableRAG planner deadline exceeded")
    if reason != "finished":
        raise RagModelError(f"TableRAG planner stopped: {reason}")


def _runtime_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    modules = sorted((root / "app/rag").glob("*.py"))
    modules.extend(
        root / name
        for name in (
            "app/generation/context.py",
            "app/generation/citations.py",
            "app/indexing/keyword_store.py",
            "app/ingestion/layout.py",
            "app/observability/records.py",
            "app/observability/tracing.py",
            "shared/contracts.py",
        )
    )
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in modules
    }


def load_service(directory: str | Path = DEFAULT_DATA_DIRECTORY) -> RagService:
    """Load the verified DB-first TableRAG bundle and its pinned models."""
    from app.rag.retrieval import load_retriever
    from app.rag.records import RecordIndex

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for TableRAG")
    directory = Path(directory).resolve()
    started = perf_counter()
    corpus = load_corpus(directory)
    backend = os.environ.get("ADEN_TABLE_RAG_VECTOR_BACKEND", "numpy")
    if backend not in {"numpy", "qdrant"}:
        raise ValueError("ADEN_TABLE_RAG_VECTOR_BACKEND must be numpy or qdrant")
    dense_index = None
    if backend == "qdrant":
        from app.rag.qdrant_index import DEFAULT_COLLECTION, open_qdrant_index

        dense_index = open_qdrant_index(
            directory,
            corpus,
            collection=os.environ.get("ADEN_TABLE_RAG_COLLECTION", DEFAULT_COLLECTION),
        )
    record_index = None
    try:
        device = os.environ.get("ADEN_TABLE_RAG_DEVICE", "cpu")
        retriever = load_retriever(corpus, device=device, dense_index=dense_index)
        record_index = RecordIndex(directory)
        model = load_model()
        tracer = Tracer()
        if os.environ.get("ADEN_TABLE_RAG_TRACE", "1") == "1":
            try:
                tracer = Tracer.from_env()
            except Exception as error:
                logger.warning("TableRAG tracing unavailable (%s)", type(error).__name__)
        corpus_manifest = (directory / "corpus.json").read_bytes()
        manifest = {
            "policy": RETRIEVAL_MODE,
            "db_first": True,
            "module_sha256": _runtime_fingerprints(),
            "runtime_versions": {
                name: version(name)
                for name in (
                    "langchain-core",
                    "langchain-openai",
                    "langgraph",
                    "sentence-transformers",
                    "torch",
                    "numpy",
                    "qdrant-client",
                    "tiktoken",
                    "pydantic",
                    "openai",
                    "anyio",
                )
            },
            "sqlite_version": sqlite3.sqlite_version,
            "snapshot_sha256": sha256(corpus_manifest).hexdigest(),
            "embedding": corpus.embedding,
            "retrieval": retriever.manifest,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "context_window": CONTEXT_WINDOW,
            "plan_output_tokens": PLAN_OUTPUT_TOKENS,
            "answer_output_tokens": ANSWER_OUTPUT_TOKENS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_retries": 0,
            "prompt_sha256": model.prompt_hashes(),
            "candidate_pages": 20,
            "final_pages": 8,
            "evidence_tokens": 8000,
        }
        logger.info("DB-first TableRAG ready in %.2f seconds", perf_counter() - started)
        return RagService(
            search=retriever.search,
            page_lookup=corpus.pages_by_source.get,
            table_store=corpus.table_store,
            model=model,
            tracer=tracer,
            manifest=manifest,
            record_index=record_index,
            run_directory=DEFAULT_RUN_DIRECTORY,
            close_search=retriever.close,
        )
    except BaseException:
        try:
            if record_index is not None:
                record_index.close()
        finally:
            if dense_index is not None:
                dense_index.close()
        raise
