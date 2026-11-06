"""팀이 검토하는 지식 데이터 v2. 런타임 Chunk/API와는 별도 계약이다."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Dataset = Literal["basic", "intermediate", "advanced"]


class CuratedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CuratedSource(CuratedModel):
    source_id: str
    dataset: Dataset
    book_id: str
    file_name: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1)
    heading: str
    sections: list[str] = Field(default_factory=list)


class CuratedRule(CuratedModel):
    rule_id: str
    text: str
    kind: Literal["business_rule", "reading_rule", "period_rule"]
    scope: Literal["linked_source_context"] = "linked_source_context"
    parameters: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(min_length=1)
    review_status: Literal["source_checked", "source_matched"]
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"


class CuratedRecord(CuratedModel):
    record_id: str
    kind: Literal["table_row", "case_context", "narrative", "external_quotation"]
    source_id: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    section: str
    fields: dict[str, str] = Field(default_factory=dict)
    text: str = ""
    identifiers: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    context_rule_ids: list[str] = Field(default_factory=list)
    related_rule_ids: list[str] = Field(default_factory=list)
    dates: list[dict[str, str]] = Field(default_factory=list)
    amounts: list[dict[str, Any]] = Field(default_factory=list)
    states: dict[str, str] = Field(default_factory=dict)
    cost_terms: dict[str, Any] = Field(default_factory=dict)
    review_status: Literal["parsed", "needs_review"]
    trust: Literal["retrieved_evidence"] = "retrieved_evidence"


class CuratedCase(CuratedModel):
    case_id: str
    record_ids: list[str] = Field(min_length=1)
    # 참조는 연결 후보이며 현재 상태·승인 효력을 확정하지 않는다.
    resolution: Literal["references_only"] = "references_only"
