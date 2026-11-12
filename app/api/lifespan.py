"""서버 시작 시 설정과 질의 자원을 준비하고 종료 시 정리한다."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from app.service.resources import load_resources
from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, configured_path, load_settings

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

    load_dotenv(PROJECT_ROOT / ".env")
    settings = load_settings(
        configured_path(DEFAULT_CONFIG)
    )  # 평가 CLI와 같은 ADEN_CONFIG
    app.state.settings = settings
    app.state.resources = None
    app.state.table_service = None
    app.state.readiness_reason = "검색과 답변 생성 기능을 준비하고 있습니다."

    resources = None
    table_service = None
    try:
        table_directory = os.environ.get("ADEN_TABLE_RAG_DIR", "").strip()
        legacy_loader = getattr(app.state, "resources_loader", None)
        if table_directory or (settings.rag_backend == "table_rag" and legacy_loader is None):
            if settings.dataset != "advanced":
                raise ServiceError(
                    "TableRAG 데이터는 advanced 설정에서만 사용할 수 있습니다.", 503
                )
            from app.rag.config import DEFAULT_DATA_DIRECTORY

            path = Path(table_directory) if table_directory else DEFAULT_DATA_DIRECTORY
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            loader = getattr(app.state, "table_service_loader", None)
            if loader is None:
                from app.rag.service import load_service

                loader = load_service
            table_service = await asyncio.to_thread(loader, path)
        else:
            loader = getattr(app.state, "resources_loader", None) or load_resources
            resources = await asyncio.to_thread(loader, settings)
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
        if table_service is not None:
            app.state.table_service = table_service
        else:
            app.state.resources = resources

    try:
        yield
    finally:
        app.state.resources = None
        app.state.table_service = None
        app.state.settings = None
        if resources is not None:
            await asyncio.to_thread(resources.close)
        if table_service is not None:
            await table_service.aclose()
