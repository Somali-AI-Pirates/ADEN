"""오프라인 지식 산출물 v1. 기존 API·Chunk 계약과 분리한다."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts import Chunk


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(KnowledgeModel):
    dataset: Literal["basic"] = "basic"
    book_id: str
    file_name: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1)
    block_id: str


class EvidenceBlock(KnowledgeModel):
    block_id: str
    heading: str
    text: str = Field(min_length=1)
    source_origin: Literal["carrier_guidance", "customer_inquiry_collection"]
    contains_quotation: bool
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"
    source: SourceRef


class Condition(KnowledgeModel):
    field: str
    operator: Literal["eq", "lt", "gte", "in", "exists"]
    value: str | bool | int | list[str]


class DocumentProfile(KnowledgeModel):
    book_id: str = Field(pattern=r"^SB(?:0[1-9]|1[0-5])$")
    pages: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_origin: Literal["carrier_guidance", "customer_inquiry_collection"]


class BasicCatalog(KnowledgeModel):
    schema_version: Literal[1]
    dataset: Literal["basic"]
    documents: list[DocumentProfile] = Field(min_length=1)
    rules: list[dict[str, Any]]

    @model_validator(mode="after")
    def unique_documents(self):
        if len({d.book_id for d in self.documents}) != len(self.documents):
            raise ValueError("catalog의 문서 ID가 중복됩니다.")
        return self


class BusinessRule(KnowledgeModel):
    rule_id: str
    title: str
    text: str
    normalization: Literal["structured"] = "structured"
    scope: dict[str, Any] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list)
    effect: dict[str, Any] = Field(min_length=1)
    exceptions: list[str] = Field(default_factory=list)
    review_status: Literal["source_checked"] = "source_checked"
    executable: Literal[False] = False
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"
    sources: list[SourceRef] = Field(min_length=1)


class GuidanceExcerpt(KnowledgeModel):
    guidance_id: str
    title: str
    text: str
    review_status: Literal["needs_review"] = "needs_review"
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"
    sources: list[SourceRef] = Field(min_length=1)


class CaseRecord(KnowledgeModel):
    record_id: str
    title: str
    record_type: Literal["illustrative_case", "external_inquiry"]
    text: str
    # Basic의 상담 예시는 실제 예약 원장이 아니다. 상태와 값을 추정하지 않는다.
    actual_state: None = None
    review_status: Literal["source_only"] = "source_only"
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"
    sources: list[SourceRef] = Field(min_length=1)


class IndexMetadata(KnowledgeModel):
    dataset: Literal["basic"] = "basic"
    content_kind: Literal["source_guidance", "case_record", "structured_rule"]
    entity_id: str
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"
    sources: list[SourceRef] = Field(min_length=1)


class IndexEntry(KnowledgeModel):
    schema_version: Literal[1] = 1
    chunk: Chunk
    embedding_text: str
    metadata: IndexMetadata
