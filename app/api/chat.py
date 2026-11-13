"""Browser APIs: streamed TableRAG responses and original PDF viewing."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.errors import query_error
from app.api.trace_links import completed_trace_url
from app.rag.admission import (
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestReservation,
    RequestUnavailableError,
)
from app.rag.conversation import MAX_HISTORY_TURNS, ConversationTurn
from app.rag.lifecycle import await_to_completion
from app.rag.model import RagModelError
from app.rag.streaming import EventSink
from shared.exceptions import ServiceError

if TYPE_CHECKING:
    from app.rag.service import RagService

router = APIRouter()
logger = logging.getLogger(__name__)
_BOOK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    history: tuple[ConversationTurn, ...] = Field(default=(), max_length=MAX_HISTORY_TURNS)

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("질문을 입력해 주세요.")
        return value


def _event(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _public_error(error: Exception) -> str:
    public_error = query_error(error, stream=True)
    if public_error is not None:
        return str(public_error)
    return "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."


class _ReservedStreamingResponse(StreamingResponse):
    """Own the pre-admitted slot until the stream iterator has been cleaned up."""

    def __init__(self, *args, reservation, **kwargs):
        super().__init__(*args, **kwargs)
        self._reservation = reservation

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            close_iterator = getattr(self.body_iterator, "aclose", None)
            try:
                if close_iterator is not None:
                    _, cancellation_requested = await await_to_completion(
                        close_iterator()
                    )
                    if cancellation_requested:
                        raise asyncio.CancelledError
            finally:
                self._reservation.release()


async def _cancel_admission(task: asyncio.Task) -> None:
    """Join a reservation attempt and return a token if it raced with cancel."""
    if not task.done():
        task.cancel()
    try:
        reservation, _ = await await_to_completion(task)
    except asyncio.CancelledError:
        return
    except Exception:
        return
    reservation.release()


async def _reserve_before_stream(service, request: Request, request_id: str):
    """Cancel queued admission when the client disconnects before SSE headers."""
    task = asyncio.create_task(service.reserve_request(request_id))
    try:
        while not task.done():
            if await request.is_disconnected():
                raise asyncio.CancelledError
            await asyncio.wait((task,), timeout=0.1)

        reservation = task.result()
        if await request.is_disconnected():
            reservation.release()
            raise asyncio.CancelledError
        return reservation
    except asyncio.CancelledError:
        await _cancel_admission(task)
        raise


@router.post(
    "/query/stream",
    response_class=StreamingResponse,
    summary="답변과 출처를 스트리밍",
    description=(
        "TableRAG 전용 SSE. start, progress, answer_delta 이후 final 또는 error를 "
        "보냅니다. final 전 텍스트는 임시이며, final은 검증·실행 기록 저장 후 전달됩니다. "
        "연결이 끊기면 해당 요청의 모델 작업을 취소합니다."
    ),
    responses={
        429: {
            "description": "요청 대기열이 가득 찼습니다. Retry-After 이후 다시 시도해 주세요."
        },
        503: {"description": "질의 서비스를 사용할 수 없습니다."},
        504: {"description": "대기 또는 요청 처리 시간이 초과되었습니다."},
    },
)
async def query_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    service = request.app.state.table_service
    if service is None:
        raise ServiceError(
            getattr(request.app.state, "readiness_reason", "TableRAG를 준비해 주세요."),
            503,
        )
    request_id = request.state.request_id
    try:
        reservation = await _reserve_before_stream(service, request, request_id)
    except (
        RequestBusyError,
        RequestUnavailableError,
        RequestDeadlineExceeded,
    ) as error:
        public_error = query_error(error, stream=True)
        assert public_error is not None
        raise public_error from error

    return _ReservedStreamingResponse(
        _stream_query_events(
            service,
            question=payload.question,
            conversation_history=payload.history,
            request_id=request_id,
            reservation=reservation,
            trace_link_prefix=getattr(request.app.state, "trace_link_prefix", None),
        ),
        media_type="text/event-stream",
        reservation=reservation,
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )


async def _stream_query_events(
    service: "RagService",
    *,
    question: str,
    request_id: str,
    reservation: RequestReservation,
    conversation_history: tuple[ConversationTurn, ...] = (),
    trace_link_prefix: str | None = None,
) -> AsyncIterator[str]:
    """Send queued SSE events and clean up the request task on exit."""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)

    async def event_sink(event: dict) -> None:
        await queue.put({**event, "request_id": request_id})

    task = asyncio.create_task(
        _produce_query_events(
            service,
            question=question,
            request_id=request_id,
            reservation=reservation,
            event_sink=event_sink,
            conversation_history=conversation_history,
            trace_link_prefix=trace_link_prefix,
        )
    )
    try:
        while True:
            if queue.empty() and task.done():
                if task.cancelled():
                    logger.info("chat_stream_cancelled request_id=%s", request_id)
                    terminal = {
                        "type": "error",
                        "message": "서버가 요청 처리를 종료했습니다. 다시 시도해 주세요.",
                        "request_id": request_id,
                    }
                else:
                    error = task.exception()
                    if error is not None:
                        logger.warning(
                            "chat_stream_failed request_id=%s error_type=%s",
                            request_id,
                            type(error).__name__,
                        )
                    terminal = {
                        "type": "error",
                        "message": "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                        "request_id": request_id,
                    }
                yield _event(terminal)
                break

            if queue.empty():
                get_event = asyncio.create_task(queue.get())
                try:
                    done, _ = await asyncio.wait(
                        (get_event, task),
                        timeout=10,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    get_event.cancel()
                    try:
                        await await_to_completion(get_event)
                    except asyncio.CancelledError:
                        pass
                    raise
                if get_event not in done:
                    get_event.cancel()
                    try:
                        await await_to_completion(get_event)
                    except asyncio.CancelledError:
                        pass
                    if task.done():
                        continue
                    # Comments keep proxies alive; clients do not render them.
                    yield ": keep-alive\n\n"
                    continue
                event = get_event.result()
            else:
                event = queue.get_nowait()

            yield _event(event)
            if event["type"] in {"final", "error"}:
                break
    finally:
        task.cancel()
        cancellation_requested = False
        try:
            _, cancellation_requested = await await_to_completion(task)
        except asyncio.CancelledError:
            # The child was cancelled; its service cleanup has completed.
            pass
        finally:
            reservation.release()
        if cancellation_requested:
            raise asyncio.CancelledError


async def _produce_query_events(
    service: "RagService",
    *,
    question: str,
    request_id: str,
    reservation: RequestReservation,
    event_sink: EventSink,
    conversation_history: tuple[ConversationTurn, ...] = (),
    trace_link_prefix: str | None = None,
) -> None:
    """Run one reserved request and emit its final response or error."""
    try:
        await event_sink({"type": "start"})
        result = await service.ainvoke(
            question,
            request_id=request_id,
            event_sink=event_sink,
            reservation=reservation,
            conversation_history=conversation_history,
        )
        if result.response.request_id != request_id:
            raise RagModelError("Mismatched request identity")
        final = {"type": "final", "response": result.response.model_dump(mode="json")}
        if trace_link_prefix:
            trace_url = completed_trace_url(trace_link_prefix, result.trace)
            if trace_url:
                final["trace_url"] = trace_url
        await event_sink(final)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "chat_stream_failed request_id=%s error_type=%s",
            request_id,
            type(error).__name__,
        )
        await event_sink({"type": "error", "message": _public_error(error)})


@router.get("/documents/{book_id}", summary="원본 PDF 열람")
def read_document(book_id: str, request: Request):
    if not _BOOK_ID.fullmatch(book_id):
        raise ServiceError("문서 ID가 올바르지 않습니다.", 404)
    settings = request.app.state.settings
    if settings is None:
        raise ServiceError("문서 설정이 준비되지 않았습니다.", 503)
    root = settings.documents_dir.resolve()
    path = root / f"{book_id}.pdf"
    # Serve only direct children of the configured original-document directory.
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        raise ServiceError("원본 문서를 찾을 수 없습니다.", 404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
