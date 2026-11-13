import asyncio
import json

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError
import pytest

from app.rag.graph import MAX_TOOL_OPERATIONS, TableRagGraph
from app.rag.model_usage import ModelCalls
from app.rag.evidence import assemble_evidence, source_id, token_count
from app.rag.models import SearchResult, SourceRef, make_document
from app.rag.tools import (
    FinishRequest,
    LookupRecordsRequest,
    QueryTablesRequest,
    SearchEvidenceRequest,
)


CODE = "EV-SH-00660-D"


def _document(page: int, text: str = "원본 페이지 근거"):
    return make_document(
        text,
        SourceRef(book_id="AS001", file_name="AS001.pdf", page=page),
        f"AS001:p{page:04d}:page",
    )


class _Store:
    def __init__(self):
        self.queries = []
        self.catalog = {
            "t_cost": {
                "table_id": "t_cost",
                "book_id": "AS001",
                "page": 1,
                "heading": "운임",
                "section": "비용",
                "row_count": 1,
                "columns": ["amount"],
            }
        }
        self.by_page = {("AS001", 1): ["t_cost"]}

    def schema(self, table_ids):
        return [
            self.catalog[table_id] for table_id in table_ids if table_id in self.catalog
        ]

    def query(self, sql, table_ids):
        self.queries.append((sql, tuple(table_ids)))
        return {
            "status": "ok",
            "columns": ["amount"],
            "rows": [{"amount": "1200"}],
            "sources": [{"book_id": "AS001", "page": 1}],
            "tables": ["t_cost"],
            "preview_truncated": False,
        }


class _RecordIndex:
    def __init__(self, *, status="matched", source="AS001:p0002"):
        self.status = status
        self.source = source
        self.calls = []
        self.discover_calls = []

    def lookup(self, targets, *, roles=()):
        self.calls.append((targets, roles))
        return {
            "targets": [
                {
                    "status": self.status,
                    "candidate_count": 1 if self.status == "matched" else 2,
                    "truncated": False,
                    "entities": [
                        {
                            "id": "HG:1",
                            "family": "HG",
                            "kind": "booking",
                            "label": "예약 사건",
                            "sources": [
                                {"source": self.source, "role": "events", "ordinal": 1}
                            ],
                            "attributes": [],
                            "identifiers": [
                                {"value": CODE, "kind": "booking", "relation": "exact"}
                            ],
                        }
                    ],
                }
            ],
            "seconds": 0.001,
        }

    def discover(self, query, limit=6):
        self.discover_calls.append((query, limit))
        return ["t_cost"]


def _call(name, call_id, args):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "id": call_id, "args": args}],
    )


class _FakeTableModel:
    def __init__(self, script):
        self.script = script
        self.requests = []

    async def plan(
        self, history, *, finish_only: bool, calls: ModelCalls, config: RunnableConfig
    ):
        self.requests.append(
            {
                "history": list(history),
                "finish_only": finish_only,
                "calls": calls,
                "config": config,
            }
        )
        return await self.script(history, finish_only=finish_only)


class _NodeCallback(BaseCallbackHandler):
    def __init__(self):
        self.nodes = []

    def on_chain_start(
        self,
        serialized,
        inputs,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ):
        if (
            metadata
            and metadata.get("langgraph_node")
            and metadata["langgraph_node"] != "__start__"
            and tags
            and any(tag.startswith("graph:step:") for tag in tags)
        ):
            self.nodes.append(metadata["langgraph_node"])


def _graph(planner, *, question="원 질문", context_window=1_050_000, index=None):
    first = _document(1, "운임표 원본")
    second = _document(2, "예약 사건 현장 기록 원본")
    third = _document(3, "운영 규정 원본")
    pages = {
        f"AS001:p{number:04d}": page
        for number, page in enumerate((first, second, third), 1)
    }
    store = _Store()
    queries = []

    def search(query):
        queries.append(query)
        return SearchResult((third,), (), {"query": query})

    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=search,
        table_store=store,
        page_lookup=pages.get,
        record_index=index,
        context_window=context_window,
        plan_overhead_tokens=0,
    )
    result = asyncio.run(
        graph.run(
            question,
            calls=ModelCalls(),
            request_id="req-1",
            trace_id="trace-1",
        )
    )
    return result, store, queries, index


def test_integrated_tools_preserve_db_sources_and_sql_evidence():
    question = f"{CODE} 예약 표와 운영 규정 확인"
    index = _RecordIndex()
    planner_calls = 0
    observed_history = []

    async def planner(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        observed_history.append(list(history))
        if planner_calls == 1:
            assert not finish_only
            assert isinstance(history[0], HumanMessage)
            initial = json.loads(history[0].content)
            assert initial["initial_route"] == "records"
            assert initial["initial_evidence"][0]["kind"] == "records"
            return _call(
                "lookup_records",
                "lookup-1",
                {
                    "targets": [
                        {"identifiers": [CODE], "constraints": [], "roles": ["events"]}
                    ]
                },
            )
        if planner_calls == 2:
            assert isinstance(history[-1], ToolMessage)
            assert history[-1].tool_call_id == "lookup-1"
            return _call(
                "search_evidence",
                "search-1",
                {"query": "운영 규정", "kind": "documents"},
            )
        if planner_calls == 3:
            assert json.loads(history[-1].content)["kind"] == "documents"
            return _call(
                "read_sources",
                "read-1",
                {"source_ids": ["AS001:p0003"]},
            )
        if planner_calls == 4:
            assert json.loads(history[-1].content)["kind"] == "sources"
            return _call(
                "search_evidence",
                "tables-1",
                {"query": "운임 금액", "kind": "tables"},
            )
        if planner_calls == 5:
            assert finish_only is False
            assert json.loads(history[-1].content)["kind"] == "tables"
            return _call(
                "query_tables",
                "sql-1",
                {"table_ids": ["t_cost"], "sql": "SELECT amount FROM t_cost"},
            )

        assert finish_only is True
        assert json.loads(history[-1].content)["kind"] == "sql"
        return _call(
            "finish",
            "finish-1",
            {
                "status": "sufficient",
                "selected_sources": ["AS001:p0002", "AS001:p0003", "AS001:p0001"],
                "interim_summary": "예약 기록과 표, 규정 근거를 확인했습니다.",
                "conflicts": [],
            },
        )

    result, store, queries, index = _graph(planner, question=question, index=index)

    assert result.termination_reason == "finished"
    assert result.status == "sufficient"
    assert planner_calls == MAX_TOOL_OPERATIONS + 1
    assert result.diagnostics["initial_route"] == "records"
    assert result.diagnostics["record_calls"]
    assert result.diagnostics["observations"]
    assert result.observations == tuple(result.diagnostics["observations"])
    assert set(result.evidence.source_ids) == {
        "AS001:p0001",
        "AS001:p0002",
        "AS001:p0003",
    }
    assert result.evidence.sql_results[0]["rows"] == [{"amount": "1200"}]
    assert store.queries == [("SELECT amount FROM t_cost", ("t_cost",))]
    assert queries == ["운영 규정"]
    assert len(index.calls) == 2
    assert index.discover_calls == [("운임 금액", 6)]
    assert [type(message).__name__ for message in observed_history[1]] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
    ]


def test_five_tool_operations_force_finish_on_sixth_planner_call():
    planner_calls = 0
    finish_modes = []

    async def planner(_history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        finish_modes.append(finish_only)
        if planner_calls <= MAX_TOOL_OPERATIONS:
            assert not finish_only
            return _call(
                "search_evidence",
                f"search-{planner_calls}",
                {"query": f"검색 {planner_calls}", "kind": "documents"},
            )
        assert finish_only
        return _call(
            "finish",
            "finish-6",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "확인할 수 없습니다.",
                "conflicts": [],
            },
        )

    result, _, queries, _ = _graph(planner)
    assert result.termination_reason == "finished"
    assert planner_calls == MAX_TOOL_OPERATIONS + 1
    assert finish_modes == [False] * MAX_TOOL_OPERATIONS + [True]
    assert len(queries) == MAX_TOOL_OPERATIONS + 1  # initial search plus five calls
    assert result.diagnostics["solve_calls"] == MAX_TOOL_OPERATIONS


def test_ambiguous_explicit_code_falls_back_to_hybrid():
    index = _RecordIndex(status="ambiguous")

    async def planner(_history, *, finish_only):
        assert not finish_only
        return _call(
            "finish",
            "finish-1",
            {
                "status": "needs_clarification",
                "selected_sources": [],
                "interim_summary": "대상을 확인해야 합니다.",
                "conflicts": [],
            },
        )

    result, _, queries, _ = _graph(
        planner,
        question=f"{CODE}의 실제 날짜는?",
        index=index,
    )
    assert result.diagnostics["initial_route"] == "hybrid"
    assert queries == [f"{CODE}의 실제 날짜는?"]
    assert result.status == "needs_clarification"


def test_excluded_code_does_not_take_db_first_route():
    index = _RecordIndex()

    async def planner(_history, *, finish_only):
        assert not finish_only
        return _call(
            "finish",
            "finish-1",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "제외된 코드입니다.",
                "conflicts": [],
            },
        )

    result, _, queries, _ = _graph(
        planner,
        question=f"{CODE} 말고 다른 예약의 날짜",
        index=index,
    )
    assert result.diagnostics["initial_route"] == "hybrid"
    assert queries == [f"{CODE} 말고 다른 예약의 날짜"]
    assert index.calls == []


def test_timeout_is_distinct_from_missing_evidence():
    async def planner(_history, *, finish_only):
        raise TimeoutError("provider timed out")

    result, _, _, _ = _graph(planner)
    assert result.status == "insufficient"
    assert result.termination_reason == "planner_timeout"
    assert result.diagnostics["error_kind"] == "timeout"
    assert result.evidence.source_ids == ("AS001:p0003",)


def test_planner_context_budget_stops_before_provider_call():
    called = False

    async def planner(_history, *, finish_only):
        nonlocal called
        called = True
        raise AssertionError("planner must not be called")

    result, _, _, _ = _graph(planner, context_window=1)
    assert not called
    assert result.termination_reason == "context_budget_exceeded"
    assert result.diagnostics["error_kind"] == "context_budget_exceeded"


def test_tool_schemas_reject_extra_fields_wrong_types_and_blank_queries():
    with pytest.raises(ValidationError):
        SearchEvidenceRequest.model_validate(
            {"query": "유효한 검색", "kind": "documents", "unexpected": True}
        )
    with pytest.raises(ValidationError):
        SearchEvidenceRequest.model_validate({"query": "   ", "kind": "documents"})
    with pytest.raises(ValidationError):
        LookupRecordsRequest.model_validate({"targets": [{"identifiers": []}]})
    with pytest.raises(ValidationError):
        QueryTablesRequest.model_validate(
            {"table_ids": ["t_cost"], "sql": "SELECT 1", "limit": 100}
        )
    with pytest.raises(ValidationError):
        FinishRequest.model_validate(
            {
                "status": "unknown",
                "selected_sources": [],
                "interim_summary": "",
                "conflicts": [],
            }
        )


def test_unobserved_table_is_rejected_before_sql():
    calls = 0

    async def planner(history, *, finish_only):
        nonlocal calls
        calls += 1
        assert not finish_only
        if calls == 1:
            return _call(
                "query_tables",
                "bad-sql-1",
                {
                    "table_ids": ["t_unobserved"],
                    "sql": "SELECT amount FROM t_unobserved",
                },
            )
        observation = json.loads(history[-1].content)
        assert history[-1].tool_call_id == "bad-sql-1"
        assert observation["status"] == "invalid_arguments"
        return _call(
            "finish",
            "finish-1",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "표를 먼저 확인해야 합니다.",
                "conflicts": [],
            },
        )

    result, store, queries, _ = _graph(planner)
    assert calls == 2
    assert queries == ["원 질문"]
    assert store.queries == []
    assert result.diagnostics["solve_calls"] == 1


def test_sufficient_finish_without_discovered_final_source_is_downgraded():
    async def planner(_history, *, finish_only):
        assert not finish_only
        return _call(
            "finish",
            "finish-1",
            {
                "status": "sufficient",
                "selected_sources": ["AS001:p9999"],
                "interim_summary": "근거를 찾았습니다.",
                "conflicts": [],
            },
        )

    result, _, _, _ = _graph(planner)
    assert result.status == "insufficient"
    assert result.finish.conflicts == ["선택한 출처를 조회하지 못했습니다."]


def test_selected_discovered_source_is_promoted_before_candidate_limit():
    pages = [_document(number, f"원문 근거 {number}") for number in range(1, 22)]
    by_id = {source_id(page): page for page in pages}
    selected_source = source_id(pages[-1])

    evidence = assemble_evidence(
        "질문",
        [pages],
        [selected_source],
        [],
        by_id.get,
    )

    assert len(evidence.candidate_source_ids) == 20
    assert selected_source in evidence.candidate_source_ids
    assert selected_source in evidence.source_ids


def test_selected_source_lost_to_context_budget_downgrades_finish():
    oversized = _document(1, '"' * 8100)

    async def planner(_history, *, finish_only):
        assert not finish_only
        return _call(
            "finish",
            "finish-1",
            {
                "status": "sufficient",
                "selected_sources": ["AS001:p0001"],
                "interim_summary": "근거를 찾았습니다.",
                "conflicts": [],
            },
        )

    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=lambda _question: SearchResult((oversized,), (), {}),
        table_store=_Store(),
        page_lookup=lambda identifier: (
            oversized if identifier == "AS001:p0001" else None
        ),
    )
    result = asyncio.run(
        graph.run("질문", calls=ModelCalls(), request_id="budget-test")
    )

    assert result.status == "insufficient"
    assert "선택한 출처 일부가 최종 문맥에 남지 않았습니다." in result.finish.conflicts
    assert result.evidence.source_ids == ()
    assert result.evidence.token_count == token_count(result.evidence.model_input)
    assert result.evidence.token_count <= 8000


def test_runnable_config_observes_graph_nodes_and_model_config():
    events = []
    callback = _NodeCallback()
    first = _document(1)

    async def script(_history, *, finish_only):
        assert not finish_only
        assert events == [
            {
                "type": "progress",
                "stage": "reviewing",
                "message": "찾은 기록과 문서 근거를 확인하고 있습니다.",
            }
        ]
        return _call(
            "finish",
            "finish-observed",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "근거 부족",
                "conflicts": [],
            },
        )

    model = _FakeTableModel(script)
    graph = TableRagGraph(
        model=model,
        search=lambda _question: SearchResult((first,), (), {}),
        table_store=_Store(),
        page_lookup=lambda _source: first,
    )

    async def event_sink(event):
        events.append(event)

    result = asyncio.run(
        graph.run(
            "질문",
            calls=ModelCalls(),
            request_id="callback-test",
            event_sink=event_sink,
            config={
                "callbacks": [callback],
                "metadata": {"task": "graph-callback-test"},
            },
        )
    )

    assert result.status == "insufficient"
    assert callback.nodes == [
        "initial_search",
        "plan_next_step",
        "assemble_evidence",
    ]
    assert model.requests[0]["config"]["metadata"]["task"] == "graph-callback-test"
