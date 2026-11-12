"""Browser APIs: streamed TableRAG responses and original PDF viewing."""

import asyncio
from contextlib import suppress
import json
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.observability.run_store import RequestRunStoreError
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
    if isinstance(error, RequestRunStoreError):
        return "실행 기록을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, TimeoutError):
        return "답변 처리 시간이 초과되었습니다. 질문 범위를 좁혀 다시 시도해 주세요."
    if isinstance(error, RagContextBudgetError):
        return "질문의 복잡도가 처리 한도를 넘었습니다. 범위를 좁혀 주세요."
    if isinstance(error, RagModelError):
        return "답변을 검증하지 못했습니다. 잠시 후 다시 시도해 주세요."
    return "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."


@router.post(
    "/query/stream",
    response_class=StreamingResponse,
    summary="답변과 출처를 스트리밍",
    description=(
        "TableRAG 전용 SSE. start, progress, answer_delta 이후 final 또는 error를 "
        "보냅니다. final 전 텍스트는 임시이며, final은 검증·실행 기록 저장 후 전달됩니다. "
        "연결이 끊기면 해당 요청의 모델 작업을 취소합니다."
    ),
)
async def query_stream(payload: ChatRequest, request: Request):
    service = request.app.state.table_service
    if service is None:
        raise ServiceError(
            getattr(request.app.state, "readiness_reason", "TableRAG를 준비해 주세요."),
            503,
        )
    request_id = request.state.request_id

    async def events():
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def emit(event: dict):
            await queue.put({**event, "request_id": request_id})

        async def run():
            try:
                await emit({"type": "start"})
                result = await service.ainvoke(
                    payload.question, request_id=request_id, on_event=emit
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
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    # Comments keep proxies alive; clients do not render them.
                    yield ": keep-alive\n\n"
                    continue
                yield _event(event)
                if event["type"] in {"final", "error"}:
                    break
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
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
