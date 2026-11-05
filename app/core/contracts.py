from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RetrievalMode = Literal["naive", "hybrid", "hybrid_rerank"]
AnswerStatus = Literal["answered", "needs_clarification", "abstained"]
ResponseStatus = Literal[AnswerStatus, "error"]


class DocumentSource(BaseModel):
    book_id: str = Field(title="문서 ID", description="PDF 파일명에서 .pdf를 뺀 값")
    file_name: str = Field(title="문서명", description="원본 PDF 파일명")
    page: int = Field(ge=1, title="페이지", description="PDF 첫 장을 1쪽으로 센 페이지 번호")


@dataclass(frozen=True)
class PageDocument:
    book_id: str
    file_name: str
    page: int
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    book_id: str
    file_name: str
    page: int
    text: str
    heading: str = ""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    rank: int
    score: float
    retriever: str


class Citation(DocumentSource):
    source_id: str = Field(title="출처 ID", description="이번 답변에서 사용한 자료 ID", examples=["S1"])


class ContextBundle(BaseModel):
    text: str
    final_chunks: list[RetrievedChunk]
    source_map: dict[str, Citation]
    context_hash: str
    token_count: int = Field(ge=0)


class ModelAnswer(BaseModel):
    status: AnswerStatus
    answer: str
    reason: str
    source_ids: list[str]


class GenerationResult(BaseModel):
    model_answer: ModelAnswer
    attempts: list[dict[str, Any]]
    usage: dict[str, Any] | None


class QueryRequest(BaseModel):
    question: str = Field(
        title="질문",
        description="해운 문서에서 확인하고 싶은 내용을 입력해 주세요.",
        examples=["예약 접수번호를 받으면 예약이 확정된 건가요?"],
    )
    retrieval_mode: RetrievalMode | None = Field(
        default=None,
        title="검색 방식",
        description=(
            "naive는 의미 검색, hybrid는 의미 검색과 키워드 검색의 결합, "
            "hybrid_rerank는 결합한 후보를 다시 정렬하는 방식입니다. "
            "생략하면 서버 설정의 기본 검색 방식을 사용합니다."
        ),
        examples=["naive"],
    )

    @field_validator("question")
    @classmethod
    def require_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("질문을 입력해 주세요.")
        return question


class QueryResponse(BaseModel):
    request_id: str = Field(title="요청 ID", description="질문과 실행 기록을 연결하는 고유 번호")
    status: ResponseStatus = Field(
        title="처리 상태",
        description=(
            "answered는 답변 완료, needs_clarification은 추가 정보 요청, "
            "abstained는 근거 부족 등으로 답변 보류, error는 처리 오류를 뜻합니다."
        ),
    )
    answer: str = Field(title="답변", description="문서에 근거한 답변 또는 추가로 확인할 질문")
    reason: str = Field(title="판단 이유", description="답변, 추가 확인, 보류 또는 오류가 발생한 이유")
    citations: list[Citation] = Field(title="출처", description="답변에 실제 사용한 문서와 페이지")
