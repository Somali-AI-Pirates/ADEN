import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.observability.run_store import RequestRunStoreError
from app.rag.admission import RequestBusyError, RequestUnavailableError
from app.rag.model import RagContextBudgetError, RagModelError
from shared.contracts import QueryResponse
from shared.exceptions import ServiceError

logger = logging.getLogger(__name__)


QUERY_EXECUTION_ERRORS = (
    RequestBusyError,
    RequestUnavailableError,
    RequestRunStoreError,
    RagContextBudgetError,
    TimeoutError,
    RagModelError,
)


def query_error(error: Exception, *, stream: bool = False) -> ServiceError | None:
    """Classify once while preserving the HTTP and streaming messages."""
    headers = None
    if isinstance(error, RequestBusyError):
        status = 429
        message = "요청이 몰려 처리 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요."
        stream_message = message
        headers = {"Retry-After": str(error.retry_after)}
    elif isinstance(error, RequestUnavailableError):
        status = 503
        message = "질의 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요."
        stream_message = message
    elif isinstance(error, RequestRunStoreError):
        status = 503
        message = "실행 기록을 저장하지 못했습니다. 서버 저장 공간을 확인해 주세요."
        stream_message = "실행 기록을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
    elif isinstance(error, RagContextBudgetError):
        status = 502
        message = "질문의 복잡도가 현재 처리 한도를 넘었습니다. 범위를 좁혀 다시 시도해 주세요."
        stream_message = "질문의 복잡도가 처리 한도를 넘었습니다. 범위를 좁혀 주세요."
    elif isinstance(error, TimeoutError):
        status = 504
        message = "답변 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        stream_message = (
            "답변 처리 시간이 초과되었습니다. 질문 범위를 좁혀 다시 시도해 주세요."
        )
    elif isinstance(error, RagModelError):
        status = 502
        message = "답변 생성 서비스에 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        stream_message = "답변을 검증하지 못했습니다. 잠시 후 다시 시도해 주세요."
    else:
        return None
    return ServiceError(
        stream_message if stream else message, status, response_headers=headers
    )


def _log_error(request: Request, error: Exception, status_code: int) -> None:
    request_id = request.state.request_id
    error_type = type(error.__cause__ or error).__name__
    level = logging.ERROR if status_code >= 500 else logging.WARNING
    logger.log(
        level,
        "query_error request_id=%s status_code=%s error_type=%s",
        request_id,
        status_code,
        error_type,
        extra={
            "request_id": request_id,
            "status_code": status_code,
            "error_type": error_type,
        },
    )


async def invalid_request(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    _log_error(request, error, 422)
    response = QueryResponse(
        request_id=request.state.request_id,
        status="error",
        answer="",
        reason="질문과 검색 방식을 확인해 주세요.",
        citations=[],
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


async def service_error(request: Request, error: ServiceError) -> JSONResponse:
    _log_error(request, error, error.status_code)
    response = QueryResponse(
        request_id=request.state.request_id,
        status="error",
        answer="",
        reason=str(error),
        citations=[],
    )
    headers = {**error.response_headers, "X-Request-ID": request.state.request_id}
    return JSONResponse(
        status_code=error.status_code,
        content=response.model_dump(mode="json"),
        headers=headers,
    )


async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    _log_error(request, error, 500)
    response = QueryResponse(
        request_id=request.state.request_id,
        status="error",
        answer="",
        reason="질문 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        citations=[],
    )
    return JSONResponse(
        status_code=500,
        content=response.model_dump(mode="json"),
        headers={"X-Request-ID": request.state.request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(ServiceError, service_error)
    app.add_exception_handler(Exception, unexpected_error)
