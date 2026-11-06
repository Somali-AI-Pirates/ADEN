import asyncio
import logging
from collections.abc import Callable
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def register_request_tracking(
    app: FastAPI,
    run_record_sink: Callable[[dict], None] | None = None,
) -> None:
    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        started = perf_counter()
        request.state.request_id = uuid4().hex
        request.state.run_record = {}
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            record = request.state.run_record
            if record:
                record.update(
                    latency_ms=round((perf_counter() - started) * 1000),
                    latency_scope="http",
                )
                if run_record_sink is not None:
                    try:
                        await asyncio.to_thread(run_record_sink, dict(record))
                    except Exception as error:
                        logger.warning("실행 기록 전달 실패 (%s)", type(error).__name__)
