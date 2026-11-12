from typing import Annotated, Literal

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.errors import QUERY_EXECUTION_ERRORS, query_error
from shared.contracts import QueryRequest, QueryResponse
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


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]


@router.get(
    "/health",
    tags=["서비스 상태"],
    summary="서비스 실행 상태 확인",
    description="앱의 실행 상태, 사용 중인 데이터셋과 질문 처리 준비 여부를 확인합니다.",
    response_description="현재 서비스 상태",
)
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        dataset=settings.dataset,
        query_ready=request.app.state.table_service is not None,
    )


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    tags=["서비스 상태"],
    summary="프로세스 실행 확인",
    description="앱 프로세스가 요청에 응답할 수 있는지 확인합니다. 외부 자원은 호출하지 않습니다.",
)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "필수 질의 자원을 사용할 수 없습니다.",
        }
    },
    tags=["서비스 상태"],
    summary="질의 자원 준비 확인",
    description="SQLite, 선택 벡터 저장소 및 실행 기록 저장 경로의 읽기·쓰기 준비 상태입니다.",
)
async def ready(request: Request) -> JSONResponse:
    service = request.app.state.table_service
    if service is None:
        result = ReadinessResponse(ready=False, checks={"service": False})
    else:
        status = await service.readiness()
        result = ReadinessResponse(ready=status.ready, checks=dict(status.checks))

    return JSONResponse(
        status_code=200 if result.ready else 503,
        content=result.model_dump(mode="json"),
        headers={"X-Request-ID": request.state.request_id},
    )


@router.post(
    "/query",
    response_model=QueryResponse,
    tags=["해운 질의응답"],
    summary="해운 문서에 질문하기",
    description="질문을 입력하면 서버에 설정된 검색·기록 조회를 통해 답변과 출처를 반환합니다.",
    response_description="답변, 처리 상태, 판단 이유와 출처",
    responses={
        422: {
            "model": QueryResponse,
            "description": "질문 또는 검색 방식이 올바르지 않습니다.",
        },
        429: {
            "model": QueryResponse,
            "description": "요청 대기열이 가득 찼습니다. Retry-After 이후 다시 시도해 주세요.",
        },
        503: {
            "model": QueryResponse,
            "description": "검색·답변 기능을 연결하는 중입니다.",
        },
        501: {
            "model": QueryResponse,
            "description": "이 서버는 검색 방식 선택을 지원하지 않습니다.",
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
    payload: Annotated[
        QueryRequest,
        Body(
            openapi_examples={
                "question": {
                    "summary": "기본 TableRAG에 질문하기",
                    "value": {
                        "question": "예약 접수번호를 받으면 예약이 확정된 건가요?"
                    },
                }
            }
        ),
    ],
    request: Request,
) -> QueryResponse:
    request_id: str = request.state.request_id
    table_service = request.app.state.table_service
    if table_service is None:
        raise ServiceError(request.app.state.readiness_reason, 503)

    if payload.retrieval_mode is not None:
        raise ServiceError(
            "이 서버는 DB 우선 TableRAG 검색을 사용합니다. 검색 방식 없이 질문을 보내 주세요.",
            501,
        )

    try:
        run = await table_service.ainvoke(payload.question, request_id=request_id)
    except QUERY_EXECUTION_ERRORS as error:
        public_error = query_error(error)
        assert public_error is not None
        raise public_error from error

    if run.response.request_id != request_id:
        raise ServiceError("요청 식별 정보를 확인하지 못했습니다.", 502)
    return run.response
