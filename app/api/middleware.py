"""HTTP 요청 식별자와 운영 요약 로그."""

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


async def track_request(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    started = perf_counter()
    request_id = uuid4().hex
    request.state.request_id = request_id
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        latency_ms = round((perf_counter() - started) * 1000)
        route = request.scope.get("route")
        path = route.path if route is not None else "<unmatched>"
        logger.info(
            "http_request request_id=%s method=%s path=%s status_code=%s latency_ms=%s",
            request_id,
            request.method,
            path,
            status_code,
            latency_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "latency_scope": "http",
            },
        )
