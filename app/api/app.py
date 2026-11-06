from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.lifespan import lifespan
from app.api.middleware import register_request_tracking
from app.api.routes import router
from shared.settings import DEFAULT_CONFIG, load_settings


def create_app(
    config_path: Path = DEFAULT_CONFIG,
    *,
    run_record_sink: Callable[[dict], None] | None = None,
) -> FastAPI:
    """설정을 읽고 시작·종료, 요청 기록, 오류 처리와 라우터를 연결한다."""
    settings = load_settings(config_path)
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
    app.state.resources = None
    app.state.readiness_reason = "검색과 답변 생성 기능을 준비하고 있습니다."

    register_request_tracking(app, run_record_sink)
    register_error_handlers(app)
    app.include_router(router)
    return app
