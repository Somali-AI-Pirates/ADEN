from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.documents import Document

from app.rag.conversation import ConversationTurn
from app.rag.tools import FinishRequest
from shared.contracts import DocumentSource


class SourceRef(DocumentSource):
    @property
    def source_id(self) -> str:
        return f"{self.book_id}:p{self.page:04d}"


def make_document(text: str, source: DocumentSource, unit_id: str) -> Document:
    return Document(
        text,
        metadata={
            "unit_id": unit_id,
            "source_id": f"{source.book_id}:p{source.page:04d}",
            "book_id": source.book_id,
            "file_name": source.file_name,
            "page": source.page,
        },
    )


@dataclass(frozen=True)
class SearchResult:
    """A ranked set of retrieval units with their original source metadata."""

    pages: tuple[Document, ...]
    table_ids: tuple[str, ...]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class EvidenceContext:
    """The exact page and SQL material supplied to the answer model."""

    pages: tuple[Document, ...]
    sql_results: tuple[dict[str, Any], ...]
    text: str
    model_input: str
    token_count: int
    context_hash: str
    page_context_hash: str
    source_ids: tuple[str, ...]
    candidate_source_ids: tuple[str, ...]
    excluded_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalRun:
    """A complete retrieval/planning run with A-compatible finish status."""

    question: str
    status: Literal["sufficient", "needs_clarification", "insufficient"]
    termination_reason: str
    finish: FinishRequest
    evidence: EvidenceContext
    searches: tuple[SearchResult, ...]
    sql_attempts: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]
    search_question: str | None = None
    answer_history: tuple[ConversationTurn, ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
