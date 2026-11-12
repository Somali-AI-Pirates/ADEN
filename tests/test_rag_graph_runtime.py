"""Runtime-context isolation and graph tool failure contract tests."""

import asyncio
import json
from contextlib import contextmanager

from langchain_core.messages import AIMessage
import pytest

from app.rag.graph import TableRagGraph
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


def test_concurrent_runs_keep_planner_stage_trace_and_state_request_local():
    pages = {
        "질문 A": _document(1, "synthetic evidence A"),
        "질문 B": _document(2, "synthetic evidence B"),
    }
    by_source = {
        f"SYN001:p{page:04d}": document
        for page, document in ((1, pages["질문 A"]), (2, pages["질문 B"]))
    }
    stage_events = {"A": [], "B": []}
    planner_events = []

    def search(question):
        return SearchResult((pages[question],), (), {"query": question})

    graph = TableRagGraph(
        search=search,
        table_store=_Store(),
        page_lookup=by_source.get,
    )

    async def run_one(label, question, request_id, trace_id):
        async def planner(history, *, finish_only):
            assert not finish_only
            initial = json.loads(history[0].content)
            planner_events.append((label, initial["question"]))
            await asyncio.sleep(0.01)
            return _finish(
                label,
                by_source[
                    initial["initial_evidence"][0]["passages"][0]["source_id"]
                ].metadata["source_id"],
            )

        @contextmanager
        def stage(name, **kwargs):
            current = {
                "name": name,
                "request_id": kwargs.get("input", {}).get("request_id"),
            }
            yield current
            stage_events[label].append(current)

        return await graph.run(
            question,
            planner=planner,
            request_id=request_id,
            stage=stage,
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
    assert all(event["request_id"] == "request-A" for event in stage_events["A"])
    assert all(event["request_id"] == "request-B" for event in stage_events["B"])


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
        search=lambda _query: SearchResult((document,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", planner=planner, request_id="invalid-args")
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
        search=lambda _query: SearchResult((document,), (), {}),
        table_store=_Store(),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", planner=planner, request_id="read-unseen")
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
        search=lambda _query: SearchResult((document,), ("t_cost",), {}),
        table_store=store,
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )
    result = asyncio.run(
        graph.run("synthetic question", planner=planner, request_id="sql-rejected")
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
        search=lambda _query: SearchResult((document,), ("t_cost",), {}),
        table_store=_Store(query_error=OSError("synthetic storage failure")),
        page_lookup=lambda source: document if source == "SYN001:p0001" else None,
    )

    with pytest.raises(OSError, match="synthetic storage failure"):
        asyncio.run(
            graph.run("synthetic question", planner=planner, request_id="sql-failure")
        )

CODE = "EV-SH-00660-D"


class _Records:
    def __init__(self):
        self.calls = []

    def lookup(self, targets):
        self.calls.append(targets)
        return {
            "targets": [
                {
                    "status": "matched",
                    "candidate_count": 1,
                    "truncated": False,
                    "entities": [
                        {
                            "id": "HG:synthetic",
                            "family": "HG",
                            "kind": "booking",
                            "label": "synthetic reservation",
                            "sources": [
                                {
                                    "source": "SYN001:p0002",
                                    "role": "events",
                                    "ordinal": 1,
                                }
                            ],
                            "attributes": [],
                            "identifiers": [
                                {
                                    "value": CODE,
                                    "kind": "booking",
                                    "relation": "exact",
                                }
                            ],
                        }
                    ],
                }
            ],
            "seconds": 0.001,
        }

    def discover(self, _query, _limit=6):
        return ["t_cost"]


def _trace_stage(events):
    @contextmanager
    def stage(name, **kwargs):
        event = {"name": name, "input": kwargs.get("input")}
        try:
            yield event
        except BaseException as error:
            event.update(level="ERROR", status_message=type(error).__name__)
            raise
        finally:
            events.append(event)

    return stage


@pytest.mark.parametrize(
    ("case", "tool_name", "args", "question", "expected_span"),
    [
        (
            "ungrounded_record",
            "lookup_records",
            {
                "targets": [
                    {
                        "identifiers": [CODE],
                        "constraints": [],
                        "roles": ["events"],
                    }
                ]
            },
            "synthetic question",
            "rag.lookup_records",
        ),
        (
            "unobserved_table",
            "query_tables",
            {"table_ids": ["t_unobserved"], "sql": "SELECT amount FROM t_unobserved"},
            "synthetic question",
            "rag.sql",
        ),
        (
            "unobserved_source",
            "read_sources",
            {"source_ids": ["SYN001:p9999"]},
            "synthetic question",
            "rag.read_sources",
        ),
        (
            "missing_original_source",
            "read_sources",
            {"source_ids": ["SYN001:p9999"]},
            "synthetic question",
            "rag.read_sources",
        ),
    ],
)
def test_rejected_state_checks_do_not_create_tool_spans(
    case, tool_name, args, question, expected_span
):
    events = []
    if case == "missing_original_source":
        initial_pages = tuple(_document(page) for page in range(1, 21)) + (
            _document(9999),
        )

        def page_lookup(source):
            return None if source == "SYN001:p9999" else _document(
                int(source.rsplit("p", 1)[1])
            )

    else:
        initial_pages = (_document(1),)
        def page_lookup(source):
            return initial_pages[0] if source == "SYN001:p0001" else None

    planner_calls = 0

    async def planner(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            assert not finish_only
            return _call(tool_name, f"invalid-{case}", args)
        observed = json.loads(history[-1].content)
        assert observed["status"] == "invalid_arguments"
        return _call(
            "finish",
            f"finish-{case}",
            {
                "status": "insufficient",
                "selected_sources": [],
                "interim_summary": "synthetic",
                "conflicts": [],
            },
        )

    graph = TableRagGraph(
        search=lambda _query: SearchResult(initial_pages, (), {}),
        table_store=_Store(),
        page_lookup=page_lookup,
    )
    result = asyncio.run(
        graph.run(
            question,
            planner=planner,
            request_id=f"trace-reject-{case}",
            stage=_trace_stage(events),
            trace_id=f"trace-{case}",
        )
    )

    assert planner_calls == 2
    assert result.diagnostics["solve_calls"] == 1
    assert expected_span not in {event["name"] for event in events}
    assert not any(event.get("level") == "ERROR" for event in events)


def test_valid_tool_spans_keep_their_names_inputs_and_outputs():
    pages = {
        "SYN001:p0001": _document(1, "synthetic table source"),
        "SYN001:p0002": _document(2, "synthetic record source"),
        "SYN001:p0003": _document(3, "synthetic policy source"),
    }
    store = _Store(
        query_result={
            "status": "ok",
            "columns": ["amount"],
            "rows": [{"amount": "1200"}],
            "sources": [{"book_id": "SYN001", "page": 1}],
            "tables": ["t_cost"],
            "preview_truncated": False,
        }
    )
    records = _Records()
    events = []

    def search(query):
        assert query == "운영 규정"
        return SearchResult((pages["SYN001:p0003"],), (), {"query": query})

    graph = TableRagGraph(
        search=search,
        table_store=store,
        page_lookup=pages.get,
        record_index=records,
    )
    planner_calls = 0

    async def planner(history, *, finish_only):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            assert not finish_only
            initial = json.loads(history[0].content)
            assert initial["initial_route"] == "records"
            return _call(
                "lookup_records",
                "lookup-span",
                {
                    "targets": [
                        {
                            "identifiers": [CODE],
                            "constraints": [],
                            "roles": ["events"],
                        }
                    ]
                },
            )
        if planner_calls == 2:
            return _call(
                "search_evidence",
                "search-span",
                {"query": "운영 규정", "kind": "documents"},
            )
        if planner_calls == 3:
            return _call(
                "read_sources",
                "read-span",
                {"source_ids": ["SYN001:p0003"]},
            )
        if planner_calls == 4:
            return _call(
                "search_evidence",
                "tables-span",
                {"query": "운임", "kind": "tables"},
            )
        if planner_calls == 5:
            return _call(
                "query_tables",
                "sql-span",
                {"table_ids": ["t_cost"], "sql": "SELECT amount FROM t_cost"},
            )
        assert finish_only
        return _call(
            "finish",
            "finish-all-tools",
            {
                "status": "sufficient",
                "selected_sources": [
                    "SYN001:p0002",
                    "SYN001:p0003",
                    "SYN001:p0001",
                ],
                "interim_summary": "all synthetic tools completed",
                "conflicts": [],
            },
        )

    result = asyncio.run(
        graph.run(
            f"{CODE} 예약의 근거와 운영 규정을 확인해줘",
            planner=planner,
            request_id="trace-valid",
            stage=_trace_stage(events),
            trace_id="trace-valid",
        )
    )

    tool_events = {
        event["name"]: event
        for event in events
        if event["name"]
        in {"rag.lookup_records", "rag.search_evidence", "rag.read_sources", "rag.sql"}
    }
    search_events = [
        event for event in events if event["name"] == "rag.search_evidence"
    ]

    assert result.status == "sufficient"
    assert set(tool_events) == {
        "rag.lookup_records",
        "rag.search_evidence",
        "rag.read_sources",
        "rag.sql",
    }
    assert len(search_events) == 2
    assert tool_events["rag.lookup_records"]["input"]["request_id"] == "trace-valid"
    assert tool_events["rag.lookup_records"]["input"]["targets"][0]["identifiers"] == [
        CODE
    ]
    assert set(tool_events["rag.lookup_records"]) == {"name", "input", "output"}
    assert set(tool_events["rag.lookup_records"]["output"]) == {
        "target_statuses",
        "source_count",
        "seconds",
        "observation",
    }
    assert {
        event["input"]["kind"]: event["input"]["query"] for event in search_events
    } == {"documents": "운영 규정", "tables": "운임"}
    for event in search_events:
        assert event["input"]["request_id"] == "trace-valid"
        assert set(event["output"]) == {
            "candidate_count",
            "table_count",
            "seconds",
            "observation",
        }
    assert tool_events["rag.read_sources"]["input"] == {
        "request_id": "trace-valid",
        "source_ids": ["SYN001:p0003"],
    }
    assert set(tool_events["rag.read_sources"]["output"]) == {
        "source_count",
        "observation",
        "seconds",
    }
    assert tool_events["rag.sql"]["input"] == {
        "request_id": "trace-valid",
        "table_ids": ["t_cost"],
        "sql": "SELECT amount FROM t_cost",
    }
    assert tool_events["rag.sql"]["output"]["status"] == "ok"
    assert tool_events["rag.sql"]["output"]["source_count"] == 1