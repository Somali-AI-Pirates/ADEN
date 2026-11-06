"""서버 시작 시 질의 자원을 준비하고 종료 시 해제한다."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.service.resources import load_resources
from shared.exceptions import ServiceError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱별 자원을 시작 때 준비하고 종료 때 비우고 해제한다."""
    resources = None
    app.state.resources = None

    try:
        resources = await asyncio.to_thread(load_resources, app.state.settings)
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
        if resources is not None:
            await asyncio.to_thread(resources.close)
