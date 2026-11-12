"""Browser APIs: streamed TableRAG responses and original PDF viewing."""

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.observability.run_store import RequestRunStoreError
from app.rag.admission import (
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestUnavailableError,
)
from app.rag.lifecycle import await_to_completion
from app.rag.model import RagContextBudgetError, RagModelError
from shared.exceptions import ServiceError

router = APIRouter()
logger = logging.getLogger(__name__)
_BOOK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)

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
    if isinstance(error, RequestBusyError):
        return "요청이 몰려 처리 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, RequestUnavailableError):
        return "질의 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, RequestRunStoreError):
        return "실행 기록을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, TimeoutError):
        return "답변 처리 시간이 초과되었습니다. 질문 범위를 좁혀 다시 시도해 주세요."
    if isinstance(error, RagContextBudgetError):
        return "질문의 복잡도가 처리 한도를 넘었습니다. 범위를 좁혀 주세요."
    if isinstance(error, RagModelError):
        return "답변을 검증하지 못했습니다. 잠시 후 다시 시도해 주세요."
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
async def query_stream(payload: ChatRequest, request: Request):
    service = request.app.state.table_service
    if service is None:
        raise ServiceError(
            getattr(request.app.state, "readiness_reason", "TableRAG를 준비해 주세요."),
            503,
        )
    request_id = request.state.request_id
    try:
        reservation = await _reserve_before_stream(service, request, request_id)
    except RequestBusyError as error:
        raise ServiceError(
            _public_error(error),
            429,
            response_headers={"Retry-After": str(error.retry_after)},
        ) from error
    except RequestUnavailableError as error:
        raise ServiceError(_public_error(error), 503) from error
    except RequestDeadlineExceeded as error:
        raise ServiceError(_public_error(error), 504) from error

    async def events():
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def emit(event: dict):
            await queue.put({**event, "request_id": request_id})

        async def run():
            try:
                await emit({"type": "start"})
                result = await service.ainvoke(
                    payload.question,
                    request_id=request_id,
                    on_event=emit,
                    reservation=reservation,
                )
                if result.response.request_id != request_id:
                    raise RagModelError("Mismatched request identity")
                await emit(
                    {
                        "type": "final",
                        "response": result.response.model_dump(mode="json"),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "chat_stream_failed request_id=%s error_type=%s",
                    request_id,
                    type(error).__name__,
                )
                await emit({"type": "error", "message": _public_error(error)})

        task = asyncio.create_task(run())
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

    return _ReservedStreamingResponse(
        events(),
        media_type="text/event-stream",
        reservation=reservation,
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )


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
