"""A-compatible source observations and final evidence assembly."""

import json
from collections.abc import Callable, Sequence
from hashlib import sha256
from typing import Any

import tiktoken
from langchain_core.documents import Document

from app.generation.context import build_context, validate_context
from app.rag.models import EvidenceContext, SearchResult
from shared.contracts import Chunk, RetrievedChunk
from shared.table_rag_policy import (
    CANDIDATE_LIMIT,
    MAX_CONTEXT_PAGES,
    MAX_CONTEXT_TOKENS,
)

ENCODING = tiktoken.get_encoding("o200k_base")
MAX_OBSERVATION_TOKENS = 6500
MAX_EXCERPT_TOKENS = 480
MAX_SQL_TOKENS = 2000


def token_count(text: str) -> int:
    return len(ENCODING.encode(text, disallowed_special=()))


def json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value else ""


def source_id(document: Document) -> str:
    metadata = document.metadata
    book_id = metadata.get("book_id")
    page = metadata.get("page")
    if not isinstance(book_id, str) or not isinstance(page, int):
        raise ValueError("Document is missing book_id or page metadata")
    return f"{book_id}:p{page:04d}"


def _passage(document: Document) -> dict[str, Any]:
    text = document.page_content
    tokens = ENCODING.encode(text, disallowed_special=())
    excerpt = ENCODING.decode(tokens[:MAX_EXCERPT_TOKENS])
    return {
        "source_id": source_id(document),
        "text": excerpt,
        "excerpt_truncated": len(tokens) > MAX_EXCERPT_TOKENS,
    }


def compact_sql(result: dict[str, Any], *, budget: int = 1700) -> dict[str, Any]:
    compact = dict(result)
    compact["rows"] = list(result.get("rows", []))
    while compact["rows"] and token_count(json_payload(compact)) > budget:
        compact["rows"].pop()
        compact["preview_truncated"] = True
    return compact


def build_observation(
    result: SearchResult,
    table_store: Any,
    *,
    subquery: str,
    sql_result: dict[str, Any] | None = None,
    final_candidate_sources: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return only passages, schemas, and SQL values actually observed this turn."""
    observation = {
        "subquery": subquery,
        "passages": [_passage(document) for document in result.pages[:8]],
        "tables": table_store.schema(list(result.table_ids)),
        "sql": compact_sql(sql_result) if sql_result is not None else None,
        "candidate_sources": [source_id(document) for document in result.pages],
    }
    if final_candidate_sources is not None:
        observation["final_candidate_sources"] = list(final_candidate_sources)
    while (
        len(observation["tables"]) > 1
        and token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS
    ):
        observation["tables"].pop()
    while (
        len(observation["passages"]) > 1
        and token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS
    ):
        observation["passages"].pop()
    if token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS:
        observation["tables"] = []
        observation["schema_omitted_for_budget"] = True
    if (
        observation["passages"]
        and token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS
    ):
        first_passage = observation["passages"][0]
        first_passage["text"] = ENCODING.decode(
            ENCODING.encode(first_passage["text"], disallowed_special=())[:100]
        )
        first_passage["excerpt_truncated"] = True
    if token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS:
        raise ValueError("Tool observation exceeds its fixed token budget")
    return observation


def merge_page_groups(
    groups: Sequence[Sequence[Document]], *, limit: int = CANDIDATE_LIMIT
) -> tuple[Document, ...]:
    merged: list[Document] = []
    seen: set[str] = set()
    for rank in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            document = group[rank]
            identifier = source_id(document)
            if identifier in seen:
                continue
            merged.append(document)
            seen.add(identifier)
            if len(merged) == limit:
                return tuple(merged)
    return tuple(merged)


def assemble_evidence(
    question: str,
    groups: Sequence[Sequence[Document]],
    selected_sources: Sequence[str],
    sql_results: Sequence[dict[str, Any]],
    page_lookup: Callable[[str], Document | None],
    *,
    candidate_limit: int = CANDIDATE_LIMIT,
    page_limit: int = MAX_CONTEXT_PAGES,
    token_budget: int = MAX_CONTEXT_TOKENS,
) -> EvidenceContext:
    if candidate_limit <= 0 or page_limit <= 0 or token_budget <= 0:
        raise ValueError("Candidate, page, and token limits must be positive")

    observed = {source_id(document): document for group in groups for document in group}
    required_sql_sources = {
        f"{source['book_id']}:p{source['page']:04d}"
        for result in sql_results
        if result.get("status") == "ok"
        for source in result.get("sources", [])
    }
    preferred_ids = list(
        dict.fromkeys([*selected_sources, *sorted(required_sql_sources)])
    )
    promoted = [
        observed[identifier] for identifier in preferred_ids if identifier in observed
    ][:candidate_limit]
    promoted_ids = {source_id(document) for document in promoted}
    remaining_groups = [
        [document for document in group if source_id(document) not in promoted_ids]
        for group in groups
    ]
    remaining_limit = candidate_limit - len(promoted)
    remaining = (
        merge_page_groups(remaining_groups, limit=remaining_limit)
        if remaining_limit
        else ()
    )
    candidates = tuple([*promoted, *remaining])
    by_source = {source_id(document): document for document in candidates}
    candidate_source_ids = tuple(by_source)
    selected = [
        by_source[identifier]
        for identifier in dict.fromkeys(selected_sources)
        if identifier in by_source
    ]
    preferred_ids = {source_id(document) for document in selected}
    selected.extend(
        document
        for identifier, document in by_source.items()
        if identifier not in preferred_ids
    )

    kept_sql: list[dict[str, Any]] = []
    required_sources: set[str] = set()
    for result in sql_results:
        if result.get("status") != "ok":
            continue
        sources = {
            f"{source['book_id']}:p{source['page']:04d}"
            for source in result.get("sources", [])
        }
        if (
            sources
            and sources <= by_source.keys()
            and len(required_sources | sources) <= page_limit
        ):
            kept_sql.append(result)
            required_sources.update(sources)

    if required_sources:
        selected = [
            by_source[identifier] for identifier in sorted(required_sources)
        ] + [
            document
            for document in selected
            if source_id(document) not in required_sources
        ]

    sql_text = json_payload(kept_sql)
    if token_count(sql_text) > MAX_SQL_TOKENS:
        kept_sql = []
        sql_text = ""

    retrieved = _context_candidates(selected)
    context = build_context(
        retrieved, page_limit, token_budget - token_count(sql_text), token_count
    )
    final_pages: list[Document] = []
    while True:
        validate_context(context, token_count)
        final_pages = []
        for item in context.final_chunks:
            identifier = f"{item.chunk.book_id}:p{item.chunk.page:04d}"
            page = page_lookup(identifier)
            if page is None:
                raise ValueError(
                    f"Missing original page for selected evidence {identifier}"
                )
            final_pages.append(page)

        actual = {source_id(page) for page in final_pages}
        if kept_sql and not required_sources <= actual:
            kept_sql = []
            sql_text = ""
            context = build_context(retrieved, page_limit, token_budget, token_count)
            continue

        answer_pages = [
            {
                "book_id": page.metadata["book_id"],
                "page": page.metadata["page"],
                "text": page.page_content,
                "source_id": source_id(page),
            }
            for page in final_pages
        ]
        model_input = json_payload({"pages": answer_pages, "sql": kept_sql})
        total_tokens = token_count(model_input)
        if total_tokens <= token_budget:
            break
        if kept_sql:
            kept_sql = []
            sql_text = ""
            context = build_context(retrieved, page_limit, token_budget, token_count)
            continue
        if not context.final_chunks:
            raise ValueError("Empty evidence payload exceeds its token budget")
        last_chunk_id = context.final_chunks[-1].chunk.chunk_id
        retrieved = [
            candidate
            for candidate in retrieved
            if candidate.chunk.chunk_id != last_chunk_id
        ]
        context = build_context(retrieved, page_limit, token_budget, token_count)

    final_source_ids = tuple(source_id(page) for page in final_pages)
    return EvidenceContext(
        pages=tuple(final_pages),
        sql_results=tuple(kept_sql),
        text=context.text,
        model_input=model_input,
        token_count=total_tokens,
        context_hash=sha256(model_input.encode("utf-8")).hexdigest(),
        page_context_hash=context.context_hash,
        source_ids=final_source_ids,
        candidate_source_ids=candidate_source_ids,
        excluded_chunk_ids=tuple(context.excluded_chunk_ids),
    )


def _context_candidates(documents: Sequence[Document]) -> list[RetrievedChunk]:
    candidates = []
    for index, document in enumerate(documents):
        metadata = document.metadata
        file_name = metadata.get("file_name")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Context documents require a file_name")
        book_id = metadata["book_id"]
        page = metadata["page"]
        chunk_id = metadata.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            chunk_id = f"evaluation:{index}:{book_id}:{page}"
        candidates.append(
            RetrievedChunk(
                chunk=Chunk(
                    chunk_id=chunk_id,
                    book_id=book_id,
                    file_name=file_name,
                    page=page,
                    text=document.page_content,
                    heading="",
                ),
                rank=index + 1,
                score=float(metadata.get("score", 0.0)),
                retriever="context-evidence-evaluation",
            )
        )
    return candidates
