"""Typed handlers for TableRAG's four retrieval tools.

Handlers use shared dependencies but never patch graph state. The graph applies each
returned ToolDelta once after the handler completes.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from itertools import zip_longest
from time import perf_counter
from typing import Any, Literal

from langchain_core.documents import Document

from app.rag.evidence import (
    ENCODING,
    MAX_OBSERVATION_TOKENS,
    build_observation,
    json_payload,
    source_id,
    token_count,
)
from app.rag.models import SearchResult
from app.rag.records import RecordIndex
from app.rag.table_store import TableStore
from app.rag.tools import (
    LookupRecordsRequest,
    QueryTablesRequest,
    ReadSourcesRequest,
    SearchEvidenceRequest,
)
from shared.table_rag_policy import CANDIDATE_LIMIT, TABLE_LIMIT


Search = Callable[[str], SearchResult]
PageLookup = Callable[[str], Document | None]
AsyncRunner = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolDependencies:
    """Shared retrieval and storage resources owned by the graph's service."""

    search: Search
    record_index: RecordIndex | None
    table_store: TableStore
    page_lookup: PageLookup
    run_sync: AsyncRunner


@dataclass(frozen=True)
class ToolDelta:
    """A tool's state-independent result, ready for the graph to apply."""

    observation: dict[str, Any]
    searches: tuple[SearchResult, ...] = ()
    groups: tuple[tuple[Document, ...], ...] = ()
    record_calls: tuple[dict[str, Any], ...] = ()
    sql_attempts: tuple[dict[str, Any], ...] = ()
    visible_table_ids: frozenset[str] = frozenset()
    timing_key: Literal["search_seconds", "sql_seconds"] | None = None
    elapsed_seconds: float = 0.0
    span_output: dict[str, Any] = field(default_factory=dict)


def record_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Bound record metadata before returning it to the planning model."""
    targets = []
    for target in result.get("targets", []):
        entities = []
        for entity in target.get("entities", [])[:2]:
            identifiers = entity.get("identifiers", [])
            entities.append(
                {
                    "id": entity["id"],
                    "family": entity["family"],
                    "kind": entity["kind"],
                    "label": entity["label"],
                    "sources": entity.get("sources", []),
                    "attributes": entity.get("attributes", []),
                    "identifiers": identifiers[:8],
                    "identifiers_truncated": len(identifiers) > 8,
                }
            )
        targets.append(
            {
                "status": target["status"],
                "candidate_count": target["candidate_count"],
                "truncated": target["truncated"],
                "entities": entities,
                "entity_metadata_truncated": len(target.get("entities", [])) > 2,
            }
        )
    return {"targets": targets, "seconds": result.get("seconds", 0.0)}


def records_search_result(
    question: str,
    result: dict[str, Any],
    table_store: Any,
    page_lookup: PageLookup,
) -> tuple[SearchResult, dict[str, Any]]:
    source_groups = []
    for target in result["targets"]:
        if target["truncated"]:
            continue
        for entity in target["entities"]:
            source_groups.append([source["source"] for source in entity["sources"]])
    source_ids = list(
        dict.fromkeys(
            source
            for group in zip_longest(*source_groups)
            for source in group
            if source is not None
        )
    )[:CANDIDATE_LIMIT]
    pages = []
    for identifier in source_ids:
        page = page_lookup(identifier)
        if page is None:
            raise ValueError(
                f"Record index refers to missing original page {identifier}"
            )
        pages.append(page)
    table_ids = tuple(
        dict.fromkeys(
            table_id
            for page in pages
            for table_id in table_store.by_page.get(
                (page.metadata["book_id"], page.metadata["page"]), []
            )
        )
    )[:TABLE_LIMIT]
    resolution = record_observation(result)
    search_result = SearchResult(
        pages=tuple(pages),
        table_ids=table_ids,
        diagnostics={"kind": "records", "resolution": resolution},
    )
    observation = build_observation(
        search_result,
        table_store,
        subquery=question,
    )
    observation["kind"] = "records"
    observation["record_resolution"] = resolution
    return search_result, observation


async def search_evidence(
    request: SearchEvidenceRequest,
    *,
    dependencies: ToolDependencies,
) -> ToolDelta:
    started = perf_counter()
    if request.kind == "documents":
        result = await dependencies.run_sync(dependencies.search, request.query)
    else:
        if dependencies.record_index is None:
            raise RuntimeError("Integrated record index is not ready")
        table_ids = await dependencies.run_sync(
            dependencies.record_index.discover,
            request.query,
            TABLE_LIMIT,
        )
        pages = []
        seen_sources: set[str] = set()
        for table_id in table_ids:
            table = dependencies.table_store.catalog[table_id]
            identifier = f"{table['book_id']}:p{table['page']:04d}"
            page = dependencies.page_lookup(identifier)
            if page is None:
                raise ValueError(f"Table catalog refers to missing page {identifier}")
            page_id = source_id(page)
            if page_id not in seen_sources:
                pages.append(page)
                seen_sources.add(page_id)
        result = SearchResult(
            pages=tuple(pages),
            table_ids=tuple(table_ids),
            diagnostics={"kind": "tables", "query": request.query},
        )
    search_seconds = perf_counter() - started
    observation = await dependencies.run_sync(
        build_observation,
        result,
        dependencies.table_store,
        subquery=request.query,
    )
    observation["kind"] = request.kind
    return ToolDelta(
        observation=observation,
        searches=(result,),
        groups=(result.pages,),
        visible_table_ids=frozenset(
            table["table_id"] for table in observation.get("tables", [])
        ),
        timing_key="search_seconds",
        elapsed_seconds=search_seconds,
        span_output={
            "candidate_count": len(result.pages),
            "table_count": len(observation["tables"]),
            "seconds": search_seconds,
            "observation": observation,
        },
    )


async def lookup_records(
    request: LookupRecordsRequest,
    *,
    question: str,
    dependencies: ToolDependencies,
) -> ToolDelta:
    if dependencies.record_index is None:
        raise RuntimeError("Integrated record index is not ready")
    started = perf_counter()
    result = await dependencies.run_sync(
        dependencies.record_index.lookup,
        [target.model_dump() for target in request.targets],
    )
    search_result, observation = await dependencies.run_sync(
        records_search_result,
        question,
        result,
        dependencies.table_store,
        dependencies.page_lookup,
    )
    search_seconds = perf_counter() - started
    return ToolDelta(
        observation=observation,
        searches=(search_result,),
        groups=(search_result.pages,),
        record_calls=(result,),
        visible_table_ids=frozenset(
            table["table_id"] for table in observation.get("tables", [])
        ),
        timing_key="search_seconds",
        elapsed_seconds=search_seconds,
        span_output={
            "target_statuses": [target["status"] for target in result["targets"]],
            "source_count": len(search_result.pages),
            "seconds": search_seconds,
            "observation": observation,
        },
    )


async def read_sources(
    request: ReadSourcesRequest,
    *,
    documents: Sequence[Document],
    dependencies: ToolDependencies,
) -> ToolDelta:
    started = perf_counter()
    passages = []
    table_ids = []
    for identifier, document in zip(request.source_ids, documents, strict=True):
        encoded = ENCODING.encode(document.page_content, disallowed_special=())
        passages.append(
            {
                "source_id": identifier,
                "text": ENCODING.decode(encoded[:2500]),
                "excerpt_truncated": len(encoded) > 2500,
            }
        )
        key = (document.metadata["book_id"], document.metadata["page"])
        table_ids.extend(dependencies.table_store.by_page.get(key, []))
    table_ids = list(dict.fromkeys(table_ids))[:TABLE_LIMIT]
    observation = {
        "kind": "sources",
        "passages": passages,
        "tables": dependencies.table_store.schema(table_ids),
    }
    while (
        observation["tables"]
        and token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS
    ):
        observation["tables"].pop()
    while token_count(json_payload(observation)) > MAX_OBSERVATION_TOKENS:
        largest = max(
            observation["passages"],
            key=lambda passage: len(
                ENCODING.encode(passage["text"], disallowed_special=())
            ),
        )
        encoded = ENCODING.encode(largest["text"], disallowed_special=())
        if len(encoded) <= 128:
            raise ValueError("Source observation exceeds its fixed token budget")
        largest["text"] = ENCODING.decode(encoded[: int(len(encoded) * 0.75)])
        largest["excerpt_truncated"] = True
    elapsed = perf_counter() - started
    return ToolDelta(
        observation=observation,
        visible_table_ids=frozenset(
            table["table_id"] for table in observation.get("tables", [])
        ),
        span_output={
            "source_count": len(passages),
            "observation": observation,
            "seconds": elapsed,
        },
    )


async def query_tables(
    request: QueryTablesRequest,
    *,
    dependencies: ToolDependencies,
) -> ToolDelta:
    started = perf_counter()
    sql_result = await dependencies.run_sync(
        dependencies.table_store.query,
        request.sql,
        request.table_ids,
    )
    sql_seconds = perf_counter() - started
    sql_pages = []
    searches: tuple[SearchResult, ...] = ()
    groups: tuple[tuple[Document, ...], ...] = ()
    if sql_result.get("status") == "ok":
        seen_sql_pages: set[str] = set()
        for source in sql_result["sources"]:
            identifier = f"{source['book_id']}:p{source['page']:04d}"
            if identifier in seen_sql_pages:
                continue
            document = dependencies.page_lookup(identifier)
            if document is None:
                raise ValueError(
                    f"Original page is unavailable for SQL source {identifier}"
                )
            sql_pages.append(document)
            seen_sql_pages.add(identifier)
        sql_group = tuple(sql_pages)
        groups = (sql_group,)
        searches = (
            SearchResult(
                pages=sql_group,
                table_ids=tuple(request.table_ids),
                diagnostics={"kind": "sql", "status": "ok"},
            ),
        )
    observation = {
        "kind": "sql",
        "sql": sql_result,
        "source_ids": [source_id(page) for page in sql_pages],
        "tables": [],
    }
    return ToolDelta(
        observation=observation,
        searches=searches,
        groups=groups,
        sql_attempts=(sql_result,),
        timing_key="sql_seconds",
        elapsed_seconds=sql_seconds,
        span_output={
            "status": sql_result.get("status"),
            "source_count": len(sql_pages),
            "seconds": sql_seconds,
            "result": sql_result,
        },
    )
