from typing import Annotated, Literal

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, Field

from app.observability.run_store import RequestRunStoreError
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
        Body(openapi_examples={
            "question": {
                "summary": "기본 TableRAG에 질문하기",
                "value": {"question": "예약 접수번호를 받으면 예약이 확정된 건가요?"},
            }
        }),
    ],
    request: Request,
) -> QueryResponse:
    request_id: str = request.state.request_id
    table_service = request.app.state.table_service
    if table_service is None:
        raise ServiceError(request.app.state.readiness_reason, 503)

    from app.rag.service import RagContextBudgetError, RagModelError

    if payload.retrieval_mode is not None:
        raise ServiceError(
            "이 서버는 DB 우선 TableRAG 검색을 사용합니다. 검색 방식 없이 질문을 보내 주세요.",
            501,
        )

    try:
        run = await table_service.ainvoke(payload.question, request_id=request_id)
    except RequestRunStoreError as error:
        raise ServiceError(
            "실행 기록을 저장하지 못했습니다. 서버 저장 공간을 확인해 주세요.", 503
        ) from error
    except RagContextBudgetError as error:
        raise ServiceError(
            "질문의 복잡도가 현재 처리 한도를 넘었습니다. 범위를 좁혀 다시 시도해 주세요.",
            502,
        ) from error
    except TimeoutError as error:
        raise ServiceError(
            "답변 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
            504,
        ) from error
    except RagModelError as error:
        raise ServiceError(
            "답변 생성 서비스에 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            502,
        ) from error

    if run.response.request_id != request_id:
        raise ServiceError("요청 식별 정보를 확인하지 못했습니다.", 502)
    return run.response
