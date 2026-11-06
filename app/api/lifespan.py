"""서버 시작 시 질의 자원을 준비하고 종료 시 해제한다."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import QueryHandler
from app.service.query import answer_query
from app.service.records import REQUEST_RECORD
from app.service.resources import load_resources
from shared.exceptions import ServiceError
from shared.settings import Settings

logger = logging.getLogger(__name__)


def create_lifespan(settings: Settings, query_handler: QueryHandler | None):
    """앱별 설정과 선택한 질의 처리 함수를 시작·종료 절차에 연결한다."""

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
                app.state.readiness_reason = (
                    "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요."
                )
            else:

                async def handle_query(payload, request_id):
                    return await asyncio.to_thread(
                        answer_query,
                        payload,
                        resources,
                        request_id,
                        settings,
                        run_record=REQUEST_RECORD.get(),
                    )

                app.state.query_handler = handle_query
        try:
            yield
        finally:
            app.state.query_handler = None
            if resources is not None:
                await asyncio.to_thread(resources.close)

    return lifespan
