"""Strict model-facing contracts for integrated retrieval and table access."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


RecordField = Literal[
    "shipper",
    "cargo",
    "spec",
    "contract_id",
    "actual_discharge_date",
    "actual_gate_out_date",
    "actual_designated_return_date",
    "scheduled_return_date",
    "system_entry_date",
    "recorded_amendment_effective_date",
]
RecordRole = Literal[
    "contract", "events", "approval", "invoice", "receipt", "settlement", "record"
]


class RecordConstraint(StrictToolInput):
    field: RecordField
    value: str = Field(min_length=1, max_length=200)


class RecordTarget(StrictToolInput):
    identifiers: list[str] = Field(max_length=4)
    constraints: list[RecordConstraint] = Field(max_length=8)
    roles: list[RecordRole] = Field(max_length=7)


class LookupRecordsRequest(StrictToolInput):
    targets: list[RecordTarget] = Field(min_length=1, max_length=4)


class SearchEvidenceRequest(StrictToolInput):
    query: str = Field(min_length=1, max_length=700)
    kind: Literal["documents", "tables"]

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace text")
        return value


class ReadSourcesRequest(StrictToolInput):
    source_ids: list[str] = Field(min_length=1, max_length=2)


class QueryTablesRequest(StrictToolInput):
    table_ids: list[str] = Field(min_length=1, max_length=12)
    sql: str = Field(min_length=1, max_length=5000)


class FinishRequest(StrictToolInput):
    """Choose only discovered sources and end the retrieval plan."""

    status: Literal["sufficient", "needs_clarification", "insufficient"]
    selected_sources: list[str] = Field(max_length=8)
    interim_summary: str = Field(max_length=1500)
    conflicts: list[str] = Field(max_length=6)
