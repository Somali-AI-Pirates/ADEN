import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from shared.contracts import QueryResponse

logger = logging.getLogger(__name__)


def query_error_body(request_id: str, reason: str) -> QueryResponse:
    return QueryResponse(
        request_id=request_id,
        status="error",
        answer="",
        reason=reason,
        citations=[],
    )


def _is_query_request(request: Request) -> bool:
    route = request.scope.get("route")
    return request.method == "POST" and getattr(route, "path", None) == "/query"


async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "서버 요청 처리 오류 (%s) request_id=%s", type(error).__name__, request_id
    )
    headers = {"X-Request-ID": request_id} if request_id is not None else None
    message = "서버에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    if _is_query_request(request) and request_id is not None:
        body = query_error_body(request_id, message)
        content = body.model_dump(mode="json")
    else:
        content = {"detail": message}

    return JSONResponse(status_code=500, content=content, headers=headers)
