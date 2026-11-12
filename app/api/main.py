"""FastAPI CLI가 불러오는 서버 앱."""

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.errors import register_error_handlers
from app.api.lifespan import lifespan
from app.api.middleware import track_request
from app.api.routes import router


def create_app(*, table_service_loader=None) -> FastAPI:
    """TableRAG API를 만든다. 경로 기반 로더는 격리 테스트용이다."""
    app = FastAPI(
        title="ADEN 해운 문서 질의응답",
        description=(
            "Advanced 해운 문서의 근거로 답변과 출처를 반환합니다. "
            "질문을 입력하고 처리 상태·판단 이유를 확인하세요."
        ),
        lifespan=lifespan,
    )
    app.state.table_service_loader = table_service_loader
    app.middleware("http")(track_request)
    register_error_handlers(app)
    app.include_router(router)
    app.include_router(chat_router)
    return app


app = create_app()
