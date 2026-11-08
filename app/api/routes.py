from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.api.errors import query_error_body
from app.service.query import answer_query
from app.service.resources import Resources
from shared.contracts import QueryRequest, QueryResponse
from shared.exceptions import ServiceError
from shared.settings import Settings


class QueryValidationRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handle_request(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                body = query_error_body(
                    request.state.request_id,
                    "요청 본문에 비어 있지 않은 질문만 입력해 주세요.",
                )
                return JSONResponse(
                    status_code=422, content=body.model_dump(mode="json")
                )

        return handle_request


health_router = APIRouter()
query_router = APIRouter(route_class=QueryValidationRoute)


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(
        default="ok", title="앱 상태", description="ok는 앱이 실행 중임을 뜻합니다."
    )
    dataset: str = Field(title="데이터셋", description="현재 서버에서 사용하는 난이도")
    query_ready: bool = Field(
        title="질의 준비 여부", description="기본 질의 자원의 준비 여부"
    )


@health_router.get(
    "/health",
    tags=["서비스 상태"],
    summary="서비스 실행 상태 확인",
    description="앱의 실행 상태, 사용 중인 데이터셋과 질문 처리 준비 여부를 확인합니다.",
    response_description="현재 서비스 상태",
)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        dataset=settings.dataset,
        query_ready=request.app.state.resources is not None,
    )


@query_router.post(
    "/query",
    response_model=QueryResponse,
    tags=["해운 질의응답"],
    summary="해운 문서에 질문하기",
    description="질문을 입력하면 서버에 설정된 검색 방식으로 자료를 찾아 답변과 출처를 반환합니다.",
    response_description="답변, 처리 상태, 판단 이유와 출처",
    responses={
        422: {
            "model": QueryResponse,
            "description": "요청 본문에 비어 있지 않은 질문만 입력해 주세요.",
        },
        503: {
            "model": QueryResponse,
            "description": "검색·답변 기능을 연결하는 중입니다.",
        },
        501: {
            "model": QueryResponse,
            "description": "서버에 설정된 검색 방식을 사용할 수 없습니다.",
        },
        502: {
            "model": QueryResponse,
            "description": "모델 호출 또는 답변 검증에 실패했습니다.",
        },
        504: {
            "model": QueryResponse,
            "description": "답변 처리 시간이 초과되었습니다.",
        },
        500: {
            "model": QueryResponse,
            "description": "서버에서 예상하지 못한 오류가 발생했습니다.",
        },
    },
)
def query(
    payload: QueryRequest,
    request: Request,
    response: Response,
) -> QueryResponse:
    request_id: str = request.state.request_id
    resources: Resources | None = request.app.state.resources
    if resources is None:
        response.status_code = 503
        return query_error_body(request_id, request.app.state.readiness_reason)

    settings: Settings = request.app.state.settings
    try:
        return answer_query(
            question=payload.question,
            resources=resources,
            request_id=request_id,
            settings=settings,
        )
    except ServiceError as error:
        response.status_code = error.status_code
        return query_error_body(request_id, str(error))
