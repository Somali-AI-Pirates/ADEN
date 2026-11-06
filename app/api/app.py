import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from time import perf_counter
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import QueryHandler, router
from shared.contracts import QueryResponse
from shared.exceptions import ServiceError
from app.service.resources import load_resources
from shared.settings import DEFAULT_CONFIG, load_settings
from app.service.query import answer_query
from app.service.records import REQUEST_RECORD

logger = logging.getLogger(__name__)


def create_app(
    config_path: Path = DEFAULT_CONFIG,
    *,
    query_handler: QueryHandler | None = None,
    run_record_sink: Callable[[dict], None] | None = None,
) -> FastAPI:
    settings = load_settings(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resources = None
        if query_handler is None:
            try:
                resources = await asyncio.to_thread(load_resources, settings)
            except ServiceError as error:
                app.state.readiness_reason = str(error)
            except Exception as error:
                logger.error("질문 처리 준비 실패 (%s)", type(error).__name__)
                app.state.readiness_reason = "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요."
            else:
                async def handle_query(payload, request_id):
                    return await asyncio.to_thread(
                        answer_query, payload, resources, request_id, settings,
                        run_record=REQUEST_RECORD.get(),
                    )

                app.state.query_handler = handle_query
        try:
            yield
        finally:
            app.state.query_handler = None
            if resources is not None:
                await asyncio.to_thread(resources.close)

    app = FastAPI(
        title="ADEN 해운 문서 질의응답",
        description=(
            "해운 문서에서 근거를 찾아 답하는 서비스입니다. "
            "‘해운 문서에 질문하기’를 펼친 뒤 질문과 검색 방식을 입력해 주세요. "
            "응답의 처리 상태와 판단 이유, 출처 문서와 페이지를 함께 확인할 수 있습니다."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.query_handler = query_handler
    app.state.readiness_reason = "검색과 답변 생성 기능을 준비하고 있습니다."

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
                record.update(latency_ms=round((perf_counter() - started) * 1000), latency_scope="http")
                if run_record_sink is not None:
                    try:
                        await asyncio.to_thread(run_record_sink, dict(record))
                    except Exception as error:
                        logger.warning("실행 기록 전달 실패 (%s)", type(error).__name__)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _exc: RequestValidationError) -> JSONResponse:
        error = QueryResponse(
            request_id=request.state.request_id,
            status="error",
            answer="",
            reason="질문과 검색 방식을 확인해 주세요.",
            citations=[],
        )
        return JSONResponse(status_code=422, content=error.model_dump(mode="json"))

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError) -> JSONResponse:
        response = QueryResponse(
            request_id=request.state.request_id, status="error", answer="",
            reason=str(error), citations=[],
        )
        return JSONResponse(
            status_code=error.status_code,
            content=response.model_dump(mode="json"),
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.error("질문 처리 오류 (%s)", type(error).__name__)
        response = QueryResponse(
            request_id=request.state.request_id, status="error", answer="",
            reason="질문 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            citations=[],
        )
        return JSONResponse(
            status_code=500, content=response.model_dump(mode="json"),
            headers={"X-Request-ID": request.state.request_id},
        )

    app.include_router(router)
    return app
