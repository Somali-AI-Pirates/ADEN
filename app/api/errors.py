import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shared.contracts import QueryResponse
from shared.exceptions import ServiceError

logger = logging.getLogger(__name__)


async def invalid_request(
    request: Request, _exc: RequestValidationError
) -> JSONResponse:
    error = QueryResponse(
        request_id=request.state.request_id,
        status="error",
        answer="",
        reason="질문과 검색 방식을 확인해 주세요.",
        citations=[],
    )
    return JSONResponse(status_code=422, content=error.model_dump(mode="json"))


async def service_error(request: Request, error: ServiceError) -> JSONResponse:
    response = QueryResponse(
        request_id=request.state.request_id,
        status="error",
        answer="",
        reason=str(error),
        citations=[],
    )
    return JSONResponse(
        status_code=error.status_code,
        content=response.model_dump(mode="json"),
        headers={"X-Request-ID": request.state.request_id},
    )


async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.error("질문 처리 오류 (%s)", type(error).__name__)
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
