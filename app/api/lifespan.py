"""서버 시작 시 설정과 질의 자원을 준비하고 종료 시 정리한다."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.trace_links import load_trace_link_prefix
from app.rag.config import load_table_rag_settings
from shared.exceptions import ServiceError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("app").setLevel(logging.INFO)
    # 요청 요약은 앱 미들웨어가 라우트 경로와 요청 ID로 기록한다.
    logging.getLogger("uvicorn.access").disabled = True

    settings = load_table_rag_settings()
    app.state.settings = settings
    app.state.table_service = None
    app.state.trace_link_prefix = None
    app.state.readiness_reason = "검색과 답변 생성 기능을 준비하고 있습니다."

    table_service = None
    try:
        loader = getattr(app.state, "table_service_loader", None)
        if loader is None:
            from app.rag.bootstrap import load_service

            table_service = await load_service(settings=settings)
        else:
            # Tests can provide a lightweight synchronous service factory.
            table_service = await asyncio.to_thread(loader, settings.data_directory)
    except FileNotFoundError:
        app.state.readiness_reason = (
            "TableRAG 데이터 또는 모델 캐시가 없습니다. "
            "데이터 가이드의 prepare_table_rag build와 check를 실행해 주세요."
        )
    except ServiceError as error:
        app.state.readiness_reason = str(error)
    except Exception as error:
        logger.error("질문 처리 준비 실패 (%s)", type(error).__name__)
        app.state.readiness_reason = (
            "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요."
        )
    else:
        app.state.table_service = table_service

    try:
        if getattr(getattr(table_service, "langfuse", None), "enabled", False):
            app.state.trace_link_prefix = await load_trace_link_prefix(settings)
        yield
    finally:
        app.state.table_service = None
        app.state.trace_link_prefix = None
        app.state.settings = None
        if table_service is not None:
            await table_service.aclose()
