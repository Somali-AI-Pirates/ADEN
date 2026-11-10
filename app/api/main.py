"""FastAPI CLI가 불러오는 서버 앱."""

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.lifespan import lifespan
from app.api.middleware import track_request
from app.api.routes import router

def create_app(*, resources_loader=None) -> FastAPI:
    """로컬 B/C 실행은 검색 대신 준비된 문맥을 공급하는 자원 로더를 받는다."""
    app = FastAPI(
        title="ADEN 해운 문서 질의응답",
        description=(
            "Advanced 해운 문서의 근거로 답변과 출처를 반환합니다. "
            "질문과 검색 방식을 입력하고 처리 상태·판단 이유를 확인하세요."
        ),
        lifespan=lifespan,
    )
    app.state.resources_loader = resources_loader
    app.middleware("http")(track_request)
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
