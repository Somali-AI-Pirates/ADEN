"""서버 시작 시 설정과 질의 자원을 준비하고 종료 시 정리한다."""

import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.service.resources import load_resources
from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, load_settings

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
    settings = load_settings(DEFAULT_CONFIG)
    app.state.settings = settings
    app.state.resources = None
    app.state.readiness_reason = "검색과 답변 생성 기능을 준비하고 있습니다."

    resources = None
    try:
        resources = await asyncio.to_thread(load_resources, settings)
    except ServiceError as error:
        app.state.readiness_reason = str(error)
    except Exception as error:
        logger.error("질문 처리 준비 실패 (%s)", type(error).__name__)
        app.state.readiness_reason = (
            "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요."
        )
    else:
        app.state.resources = resources

    try:
        yield
    finally:
        app.state.resources = None
        app.state.settings = None
        if resources is not None:
            await asyncio.to_thread(resources.close)
