"""FastAPI CLI가 불러오는 서버 앱."""

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.lifespan import lifespan
from app.api.middleware import track_request
from app.api.routes import router


app = FastAPI(
    title="ADEN 해운 문서 질의응답",
    description=(
        "해운 문서에서 근거를 찾아 답하는 서비스입니다. "
        "‘해운 문서에 질문하기’를 펼친 뒤 질문과 검색 방식을 입력해 주세요. "
        "응답의 처리 상태와 판단 이유, 출처 문서와 페이지를 함께 확인할 수 있습니다."
    ),
    lifespan=lifespan,
)

app.middleware("http")(track_request)
register_error_handlers(app)
app.include_router(router)
