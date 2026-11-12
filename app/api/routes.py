import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.service.query import answer_query
from app.service.resources import Resources
from shared.contracts import QueryRequest, QueryResponse
from shared.settings import Settings
from shared.exceptions import ServiceError

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(
        default="ok", title="앱 상태", description="ok는 앱이 실행 중임을 뜻합니다."
    )
    dataset: str = Field(title="데이터셋", description="현재 서버에서 사용하는 난이도")
    query_ready: bool = Field(
        title="질의 준비 여부", description="기본 질의 자원의 준비 여부"
    )


@router.get(
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


@router.post(
    "/query",
    response_model=QueryResponse,
    tags=["해운 질의응답"],
    summary="해운 문서에 질문하기",
    description="질문과 검색 방식을 입력하면 검색 자료를 바탕으로 답변과 출처를 반환합니다.",
    response_description="답변, 처리 상태, 판단 이유와 출처",
    responses={
        422: {
            "model": QueryResponse,
            "description": "질문 또는 검색 방식이 올바르지 않습니다.",
        },
        503: {
            "model": QueryResponse,
            "description": "검색·답변 기능을 연결하는 중입니다.",
        },
        501: {
            "model": QueryResponse,
            "description": "선택한 검색 방식은 준비 중입니다.",
        },
        502: {
            "model": QueryResponse,
            "description": "모델 호출 또는 답변 검증에 실패했습니다.",
        },
        504: {
            "model": QueryResponse,
            "description": "답변 처리 시간이 초과되었습니다.",
        },
    },
)
async def query(
    payload: QueryRequest,
    request: Request,
) -> QueryResponse:
    request_id: str = request.state.request_id
    resources: Resources | None = request.app.state.resources
    if resources is None:
        raise ServiceError(request.app.state.readiness_reason, 503)

    settings: Settings = request.app.state.settings
    return await asyncio.to_thread(
        answer_query,
        payload,
        resources,
        request_id,
        settings,
    )
