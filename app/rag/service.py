"""One question's admission, retrieval, answer validation, and saved result."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import tiktoken
from anyio import CapacityLimiter, to_thread
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig

from app.observability.run_store import save_request_failure, save_request_run
from app.observability.tracing import Tracer
from app.rag.admission import (
    RequestAdmission,
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestReservation,
    RequestUnavailableError,
)
from app.rag.config import RETRIEVAL_MODE, TableRagSettings
from app.rag.lifecycle import (
    await_to_completion,
    close_async_resource,
    close_sync_resource,
)
from app.rag.model import RagContextBudgetError, RagModelError, TableModel
from app.rag.model_usage import ModelCalls
from app.rag.readiness import ReadinessProbe, ReadinessStatus
from app.rag.result import (
    RagRun,
    answer_trace_fields,
    failure_metadata,
    retrieval_trace_fields,
    validate_answer,
)
from app.rag.streaming import EventSink, emit_progress
from shared.table_rag_policy import CONTEXT_WINDOW, MAX_CONTEXT_PAGES

if TYPE_CHECKING:
    from app.rag.models import SearchResult
    from app.rag.table_store import TableStore

logger = logging.getLogger(__name__)


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
        resource_stack: AsyncExitStack | None = None,
        settings: TableRagSettings | None = None,
        readiness_probe: ReadinessProbe | None = None,
    ) -> None:
        self._page_lookup = page_lookup
        self._model = model
        self.langfuse = tracer
        self.manifest = manifest
        self._record_index = record_index
        self._run_directory = run_directory
        self._close_search = close_search
        self._resource_stack = resource_stack or AsyncExitStack()
        if resource_stack is None:
            self._resource_stack.push_async_callback(
                close_sync_resource, self.langfuse.shutdown
            )
            if self._close_search is not None:
                self._resource_stack.push_async_callback(
                    close_sync_resource, self._close_search
                )
            if self._record_index is not None:
                self._resource_stack.push_async_callback(
                    close_sync_resource, self._record_index.close
                )
            self._resource_stack.push_async_callback(
                close_async_resource, self._model.aclose
            )
        self._settings = settings or TableRagSettings()
        self._admission = RequestAdmission(
            max_active=self._settings.max_active_requests,
            max_waiting=self._settings.max_waiting_requests,
            queue_timeout=self._settings.queue_timeout_seconds,
            request_timeout=self._settings.request_timeout_seconds,
        )
        # Direct dependency injection can supply a probe for its own stores.
        # The product loader always supplies the concrete bundle/store probe.
        self._readiness = readiness_probe or ReadinessProbe({"resources": lambda: True})
        if resource_stack is None:
            self._resource_stack.push_async_callback(
                close_async_resource, self._readiness.aclose
            )
        self._close_task: asyncio.Task | None = None
        self.config_hash = sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        from app.rag.graph import TableRagGraph

        self._encoding = tiktoken.get_encoding("o200k_base")
        self._workers = CapacityLimiter(2)
        self._graph = TableRagGraph(
            search=search,
            model=model,
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
        result, cancelled = await await_to_completion(
            to_thread.run_sync(
                partial(function, *args, **kwargs), limiter=self._workers
            )
        )
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def readiness(self) -> ReadinessStatus:
        if self._closed:
            return ReadinessStatus(False, {"resources": False, "accepting": False})
        status = await self._readiness.get()
        checks = dict(status.checks)
        checks["accepting"] = self._admission.accepting and not self._closed
        return ReadinessStatus(all(checks.values()), checks)

    async def reserve_request(self, request_id: str) -> RequestReservation:
        arrived_at = asyncio.get_running_loop().time()
        try:
            if self._closed:
                raise RequestUnavailableError("TableRAG service is closed")
            try:
                async with asyncio.timeout_at(
                    arrived_at + self._settings.request_timeout_seconds
                ):
                    ready = (await self.readiness()).ready
            except TimeoutError as error:
                raise RequestDeadlineExceeded(
                    "Request processing budget exceeded during readiness"
                ) from error
            if not ready:
                raise RequestUnavailableError(
                    "Required TableRAG resources are unavailable"
                )
            reservation = await self._admission.acquire(
                request_id, arrived_at=arrived_at
            )
        except (
            RequestBusyError,
            RequestUnavailableError,
            RequestDeadlineExceeded,
        ) as error:
            logger.info(
                "rag_admission request_id=%s outcome=%s elapsed_ms=%.3f",
                request_id,
                type(error).__name__,
                (asyncio.get_running_loop().time() - arrived_at) * 1000,
            )
            raise
        except asyncio.CancelledError:
            logger.info(
                "rag_admission request_id=%s outcome=cancelled elapsed_ms=%.3f",
                request_id,
                (asyncio.get_running_loop().time() - arrived_at) * 1000,
            )
            raise
        logger.info(
            "rag_admission request_id=%s outcome=accepted queue_ms=%.3f active=%s waiting=%s",
            request_id,
            reservation.queue_ms,
            self._admission.active_count,
            self._admission.waiting_count,
        )
        return reservation

    async def ainvoke(
        self,
        question: str,
        *,
        request_id: str,
        event_sink: EventSink | None = None,
        reservation: RequestReservation | None = None,
    ) -> RagRun:
        question = question.strip()
        if not question:
            if reservation is not None and reservation.request_id == request_id:
                reservation.release()
            raise ValueError("질문을 입력해 주세요.")

        reservation = reservation or await self.reserve_request(request_id)
        # Claim before entering finally: an invalid/cross-request token must
        # never release a slot belonging to another execution.
        reservation.claim(self._admission, request_id)
        outcome = "error"
        deadline = asyncio.timeout_at(reservation.deadline)
        try:
            async with deadline:
                if asyncio.get_running_loop().time() >= reservation.deadline:
                    raise RequestDeadlineExceeded("Request processing budget exceeded")
                # Recheck cached readiness after waiting for admission.
                if not (await self.readiness()).ready:
                    raise RequestUnavailableError(
                        "Required TableRAG resources are unavailable"
                    )
                result = await self._answer_question(
                    question, request_id=request_id, event_sink=event_sink
                )

            outcome = "complete"
            return result
        except TimeoutError as error:
            exceeded = deadline.expired() or isinstance(error, RequestDeadlineExceeded)
            outcome = "deadline_exceeded" if exceeded else "model_timeout"
            if exceeded:
                raise RequestDeadlineExceeded(
                    "Request processing budget exceeded"
                ) from error
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            # _run_sync joins in-flight workers before cancellation propagates.
            # The slot is therefore held through cancellation and atomic saves.
            self._admission.complete(reservation)
            logger.info(
                "rag_request request_id=%s outcome=%s queue_ms=%.3f execution_ms=%.3f "
                "elapsed_ms=%.3f deadline_overrun_ms=%.3f cleanup_complete=true",
                request_id,
                outcome,
                reservation.queue_ms,
                (asyncio.get_running_loop().time() - reservation.admitted_at) * 1000,
                (asyncio.get_running_loop().time() - reservation.arrived_at) * 1000,
                max(0, asyncio.get_running_loop().time() - reservation.deadline) * 1000,
            )

    async def _answer_question(
        self, question: str, *, request_id: str, event_sink: EventSink | None = None
    ) -> RagRun:
        started = perf_counter()
        trace = self.langfuse.start_request(
            request_id,
            question=question,
            retrieval_mode=RETRIEVAL_MODE,
            dataset_id="advanced",
            config_hash=self.config_hash,
            metadata={"question_sha256": sha256(question.encode()).hexdigest()},
        )
        calls = ModelCalls(event_sink=event_sink)
        langfuse_handler = trace.langchain_handler()
        langchain_config: RunnableConfig = (
            {"callbacks": [langfuse_handler]} if langfuse_handler else {}
        )
        response = None
        retrieval = None
        candidates = ()
        model_context_tokens = 0
        failure = None

        try:
            await emit_progress(
                event_sink, "retrieving", "관련 기록과 문서를 찾고 있습니다."
            )
            retrieval = await self._graph.run(
                question,
                calls=calls,
                event_sink=event_sink,
                config=langchain_config,
                request_id=request_id,
                trace_id=trace.trace_id,
            )
            evidence = retrieval.evidence
            candidates = tuple(
                page
                for source_id in evidence.candidate_source_ids
                if (page := self._page_lookup(source_id)) is not None
            )
            model_context_tokens = self._count_tokens(evidence.model_input)
            trace.record_retrieval(
                **retrieval_trace_fields(retrieval, candidates, model_context_tokens),
                k=MAX_CONTEXT_PAGES,
            )
            _check_termination(retrieval.termination_reason)
            await emit_progress(
                event_sink, "answering", "근거를 바탕으로 답변을 작성하고 있습니다."
            )
            answer = await self._model.answer(
                json.dumps(
                    {
                        "question": question,
                        "evidence": json.loads(evidence.model_input),
                        "retrieval_status": retrieval.status,
                    },
                    ensure_ascii=False,
                ),
                context_hash=evidence.context_hash,
                calls=calls,
                config=langchain_config,
            )
            await emit_progress(
                event_sink, "validating", "답변과 출처를 확인하고 있습니다."
            )
            response = validate_answer(
                answer, evidence.pages, request_id, retrieval.status
            )
        except BaseException as error:
            failure = error
            raise
        finally:
            latency_ms = (perf_counter() - started) * 1000
            trace_status = trace.finish(
                **answer_trace_fields(response, latency_ms, failure)
            )
            if isinstance(failure, Exception):
                metadata = failure_metadata(
                    trace_status,
                    calls.summary(),
                    latency_ms,
                    retrieval,
                    candidates,
                    model_context_tokens,
                )
                await self._save_failed_request(question, request_id, failure, metadata)
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
            await emit_progress(event_sink, "saving", "답변 기록을 저장하고 있습니다.")
            await self._run_sync(
                save_request_run,
                run.to_record(),
                service_manifest=self.manifest,
                output_root=self._run_directory,
            )
        return run

    async def _save_failed_request(
        self,
        question: str,
        request_id: str,
        failure: Exception,
        metadata: dict[str, Any],
    ) -> None:
        failure.rag_metadata = metadata
        if self._run_directory is not None:
            await self._run_sync(
                save_request_failure,
                question,
                request_id,
                failure,
                service_manifest=self.manifest,
                config_hash=self.config_hash,
                output_root=self._run_directory,
            )

    async def aclose(self) -> None:
        if self._close_task is not None and self._close_task.done():
            return
        if self._close_task is None:
            self._closed = True
            self._admission.accepting = False
            self._close_task = asyncio.create_task(self._drain_and_close())
        _, cancelled = await await_to_completion(self._close_task)
        if cancelled:
            raise asyncio.CancelledError

    async def _drain_and_close(self) -> None:
        try:
            await self._admission.aclose(self._settings.shutdown_grace_seconds)
        finally:
            resources, self._resource_stack = self._resource_stack, None
            if resources is not None:
                await close_async_resource(resources.aclose)


def _check_termination(reason: str) -> None:
    if reason == "context_budget_exceeded":
        raise RagContextBudgetError("TableRAG planner context budget exceeded")
    if reason in {"timeout", "model_timeout", "planner_timeout"}:
        raise TimeoutError("TableRAG planner deadline exceeded")
    if reason != "finished":
        raise RagModelError(f"TableRAG planner stopped: {reason}")
