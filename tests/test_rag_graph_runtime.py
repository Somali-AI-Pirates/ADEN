"""Runtime-context isolation and graph tool failure contract tests."""

import asyncio
import json

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
import pytest

import app.rag.graph as graph_module
from app.rag.graph import TableRagGraph
from app.rag.model_usage import ModelCalls
from app.rag.models import SearchResult, SourceRef, make_document


def _document(page: int, text: str = "synthetic page"):
    return make_document(
        text,
        SourceRef(book_id="SYN001", file_name="synthetic.pdf", page=page),
        f"SYN001:p{page:04d}:page",
    )


def _call(name, call_id, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "id": call_id, "args": args}]
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
            and tags
            and any(tag.startswith("graph:step:") for tag in tags)
        ):
            self.nodes.append(metadata["langgraph_node"])


class _Store:
    def __init__(self, *, query_result=None, query_error=None):
        self.catalog = {
            "t_cost": {
                "table_id": "t_cost",
                "book_id": "SYN001",
                "page": 1,
                "heading": "synthetic",
                "section": "test",
                "row_count": 1,
                "columns": ["amount"],
            }
        }
        self.by_page = {("SYN001", 1): ["t_cost"]}
        self.query_result = query_result or {
            "status": "rejected",
            "error": "synthetic SQL rejection",
            "tables": ["t_cost"],
        }
        self.query_error = query_error
        self.queries = []

    def schema(self, table_ids):
        return [self.catalog[item] for item in table_ids if item in self.catalog]

    def query(self, sql, table_ids):
        self.queries.append((sql, tuple(table_ids)))
        if self.query_error is not None:
            raise self.query_error
        return self.query_result


def _finish(summary, source):
    return _call(
        "finish",
        f"finish-{summary}",
        {
            "status": "sufficient",
            "selected_sources": [source],
            "interim_summary": summary,
            "conflicts": [],
        },
    )


def test_concurrent_runs_keep_model_calls_and_state_request_local():
    pages = {
        "질문 A": _document(1, "synthetic evidence A"),
        "질문 B": _document(2, "synthetic evidence B"),
    }
    by_source = {
        f"SYN001:p{page:04d}": document
        for page, document in ((1, pages["질문 A"]), (2, pages["질문 B"]))
    }
    progress_events = {"A": [], "B": []}
    planner_events = []

    def search(question):
        return SearchResult((pages[question],), (), {"query": question})

    async def script(history, *, finish_only):
        assert not finish_only
        initial = json.loads(history[0].content)
        label = initial["question"][-1]
        planner_events.append((label, initial["question"]))
        await asyncio.sleep(0.01)
        return _finish(
            label,
            by_source[
                initial["initial_evidence"][0]["passages"][0]["source_id"]
            ].metadata["source_id"],
        )

    graph = TableRagGraph(
        model=_FakeTableModel(script),
        search=search,
        table_store=_Store(),
        page_lookup=by_source.get,
    )

    async def run_one(label, question, request_id, trace_id):
        async def event_sink(event):
            progress_events[label].append(event)

        return await graph.run(
            question,
            calls=ModelCalls(),
            request_id=request_id,
            event_sink=event_sink,
            trace_id=trace_id,
        )

    async def run_both():
        return await asyncio.gather(
            run_one("A", "질문 A", "request-A", "trace-A"),
            run_one("B", "질문 B", "request-B", "trace-B"),
        )

    result_a, result_b = asyncio.run(run_both())

    assert set(planner_events) == {("A", "질문 A"), ("B", "질문 B")}
    assert result_a.diagnostics["request_id"] == "request-A"
    assert result_a.diagnostics["trace_id"] == "trace-A"
    assert result_a.evidence.source_ids == ("SYN001:p0001",)
    assert result_a.finish.interim_summary == "A"
    assert result_b.diagnostics["request_id"] == "request-B"
    assert result_b.diagnostics["trace_id"] == "trace-B"
    assert result_b.evidence.source_ids == ("SYN001:p0002",)
    assert result_b.finish.interim_summary == "B"
    assert all(
        event["stage"] == "reviewing"
        for label in progress_events
        for event in progress_events[label]
    )


def test_invalid_tool_arguments_are_recorded_without_running_handler():
    planner_calls = 0

    async def planner(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            return _call(
                "search_evidence",
                "bad-search",
                {"query": "유효하지 않은 종류", "kind": "not-a-kind"},
            )
        invalid = json.loads(history[-1].content)
        assert invalid["status"] == "invalid_arguments"
        assert invalid["error_class"] == "ValidationError"
        return _call(
            "finish",
            "finish",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "synthetic",
                "conflicts": [],
            },
        )

    document = _document(1)
    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=lambda _query: SearchResult((document,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", calls=ModelCalls(), request_id="invalid-args")
    )

    assert planner_calls == 2
    assert result.diagnostics["solve_calls"] == 1
    assert result.diagnostics["turns"][1]["status"] == "invalid_arguments"


def test_reading_an_unobserved_source_is_rejected_as_invalid_arguments():
    planner_calls = 0

    async def planner(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            return _call(
                "read_sources",
                "read-unseen",
                {"source_ids": ["SYN001:p9999"]},
            )
        invalid = json.loads(history[-1].content)
        assert invalid["status"] == "invalid_arguments"
        assert invalid["error_class"] == "ValueError"
        return _call(
            "finish",
            "finish",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "synthetic",
                "conflicts": [],
            },
        )

    document = _document(1)
    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=lambda _query: SearchResult((document,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", calls=ModelCalls(), request_id="read-unseen")
    )

    assert planner_calls == 2
    assert result.diagnostics["solve_calls"] == 1
    assert result.diagnostics["turns"][1]["status"] == "invalid_arguments"


def test_sql_rejection_remains_a_structured_tool_result():
    calls = 0

    async def planner(history, *, finish_only):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _call(
                "query_tables",
                "sql-rejected",
                {"table_ids": ["t_cost"], "sql": "DELETE FROM t_cost"},
            )
        observed = json.loads(history[-1].content)
        assert observed["sql"]["status"] == "rejected"
        assert observed["sql"]["error"] == "synthetic SQL rejection"
        return _call(
            "finish",
            "finish",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "synthetic",
                "conflicts": [],
            },
        )

    document = _document(1)
    store = _Store()
    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=lambda _query: SearchResult((document,), ("t_cost",), {}),
        table_store=store,
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", calls=ModelCalls(), request_id="sql-rejected")
    )

    assert calls == 2
    assert store.queries == [("DELETE FROM t_cost", ("t_cost",))]
    assert result.diagnostics["sql_calls"] == 1
    assert result.sql_attempts[0]["status"] == "rejected"
    assert result.searches[0].diagnostics == {}


def test_unexpected_storage_failure_still_propagates():
    async def planner(_history, *, finish_only):
        assert not finish_only
        return _call(
            "query_tables",
            "sql-failure",
            {"table_ids": ["t_cost"], "sql": "SELECT amount FROM t_cost"},
        )

    document = _document(1)
    graph = TableRagGraph(
        model=_FakeTableModel(planner),
        search=lambda _query: SearchResult((document,), ("t_cost",), {}),
        table_store=_Store(query_error=OSError("synthetic storage failure")),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )

    with pytest.raises(OSError, match="synthetic storage failure"):
        asyncio.run(
            graph.run(
                "synthetic question", calls=ModelCalls(), request_id="sql-failure"
            )
        )


def test_unknown_planner_tool_is_rejected_then_replanned():
    page = _document(1)
    planner_calls = 0

    async def script(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        assert not finish_only
        if planner_calls == 1:
            return _call("unsupported_tool", "unsupported-1", {"value": "ignored"})
        assert isinstance(history[-1], ToolMessage)
        assert history[-1].tool_call_id == "unsupported-1"
        rejected = json.loads(history[-1].content)
        assert rejected == {
            "status": "invalid_arguments",
            "error_class": "ValueError",
            "error": "Unsupported planner tool",
        }
        return _finish("complete", "")

    callback = _NodeCallback()
    graph = TableRagGraph(
        model=_FakeTableModel(script),
        search=lambda _query: SearchResult((page,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: page if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run(
            "synthetic question",
            calls=ModelCalls(),
            request_id="unsupported-tool",
            config={"callbacks": [callback]},
        )
    )

    assert planner_calls == 2
    assert result.diagnostics["solve_calls"] == 1
    assert result.diagnostics["turns"][1]["status"] == "invalid_arguments"
    assert callback.nodes == [
        "initial_search",
        "plan_next_step",
        "reject_tool",
        "plan_next_step",
        "assemble_evidence",
    ]


def test_runnable_config_observes_tool_nodes_and_review_progress():
    page = _document(1)
    planner_calls = 0
    progress_events = []

    async def script(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            assert not finish_only
            return _call(
                "search_evidence",
                "search-1",
                {"query": "synthetic follow-up", "kind": "documents"},
            )
        assert isinstance(history[-1], ToolMessage)
        return _finish("complete", "SYN001:p0001")

    callback = _NodeCallback()
    graph = TableRagGraph(
        model=_FakeTableModel(script),
        search=lambda _query: SearchResult((page,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: page if source == "SYN001:p0001" else None,
    )

    async def event_sink(event):
        progress_events.append(event)

    result = asyncio.run(
        graph.run(
            "synthetic question",
            calls=ModelCalls(),
            request_id="tool-node-callback",
            event_sink=event_sink,
            config={
                "callbacks": [callback],
                "metadata": {"task": "tool-node-callback"},
            },
        )
    )

    assert result.status == "sufficient"
    assert callback.nodes == [
        "initial_search",
        "plan_next_step",
        "search_evidence",
        "plan_next_step",
        "assemble_evidence",
    ]
    assert [event["stage"] for event in progress_events] == [
        "reviewing",
        "reviewing",
    ]
    assert result.diagnostics["turns"][1]["tool_seconds"] >= 0


def test_read_sources_handler_error_is_observed_by_node_callback(monkeypatch):
    page = _document(1)
    callback = _NodeCallback()

    async def failing_read_sources(*_args, **_kwargs):
        raise RuntimeError("synthetic source formatting failure")

    monkeypatch.setattr(graph_module, "read_sources", failing_read_sources)

    async def script(_history, *, finish_only):
        assert not finish_only
        return _call(
            "read_sources",
            "read-failure",
            {"source_ids": ["SYN001:p0001"]},
        )

    graph = TableRagGraph(
        model=_FakeTableModel(script),
        search=lambda _query: SearchResult((page,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: page if source == "SYN001:p0001" else None,
    )

    with pytest.raises(RuntimeError, match="synthetic source formatting failure"):
        asyncio.run(
            graph.run(
                "synthetic question",
                calls=ModelCalls(),
                request_id="read-source-error",
                config={"callbacks": [callback]},
            )
        )

    assert "read_sources" in callback.nodes
