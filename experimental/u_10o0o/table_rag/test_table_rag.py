"""Contract checks independent of model/API and experiment labels."""

import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.pattern_retrieval.common import write_json, file_sha
from experimental.u_10o0o.table_rag.store import TableStore, split_tables, typed_cell
from experimental.u_10o0o.table_rag.agent import TableAgent
from experimental.u_10o0o.table_rag.retrieval import Retriever, merge_pages


@pytest.fixture
def store(tmp_path):
    db = sqlite3.connect(tmp_path / "tables.sqlite3")
    db.execute(
        "CREATE TABLE t_a(row_id TEXT, c0 TEXT, c1_value TEXT, c1_unit TEXT, c1_state TEXT)"
    )
    db.executemany(
        "INSERT INTO t_a VALUES(?,?,?,?,?)",
        [
            (str(i), "A" if i < 10 else "B", "0.10", "USD", "reported")
            for i in range(100)
        ],
    )
    db.execute("INSERT INTO t_a VALUES ('unknown', 'C', NULL, 'USD', '미결')")
    db.execute("CREATE TABLE t_b(c0 TEXT, label TEXT)")
    db.executemany("INSERT INTO t_b VALUES (?,?)", [("A", "alpha"), ("B", "beta")])
    db.commit()
    db.close()
    catalog = [
        {
            "table_id": name,
            "book_id": "TEST",
            "page": i + 1,
            "source_id": f"TEST:{i}",
            "line_start": 1,
            "line_end": 101,
            "columns": [],
            "heading": "fixture",
            "section": "table",
            "row_count": 101,
        }
        for i, name in enumerate(("t_a", "t_b"))
    ]
    write_json(tmp_path / "catalog.json", catalog)
    write_json(
        tmp_path / "store.json",
        {
            "files": {
                n: file_sha(tmp_path / n) for n in ("catalog.json", "tables.sqlite3")
            }
        },
    )
    return TableStore(tmp_path)


def test_full_table_aggregation_and_preview(store):
    r = store.query(
        "SELECT count(*) n, decimal_sum(c1_value) total FROM t_a",
        ["t_a"],
        preview_rows=8,
    )
    assert r["status"] == "ok" and r["rows"] == [{"n": 101, "total": "10.00"}]
    r = store.query("SELECT * FROM t_a", ["t_a"], preview_rows=8)
    assert len(r["rows"]) == 8 and r["preview_truncated"]


def test_join_range_group_cte_and_union(store):
    result = store.query(
        """WITH picked AS (
        SELECT c0, c1_value FROM t_a WHERE decimal_cmp(c1_value, '0.09') > 0)
        SELECT b.label, decimal_sum(p.c1_value) total FROM picked p
        JOIN t_b b ON p.c0=b.c0 GROUP BY b.label ORDER BY b.label""",
        ["t_a", "t_b"],
    )
    assert result["rows"] == [
        {"label": "alpha", "total": "1.00"},
        {"label": "beta", "total": "9.00"},
    ]
    result = store.query(
        "SELECT c0 FROM t_a WHERE c1_value IS NULL UNION SELECT c0 FROM t_b",
        ["t_a", "t_b"],
    )
    assert {r["c0"] for r in result["rows"]} == {"A", "B", "C"}
    assert len(result["sources"]) == 2


@pytest.mark.parametrize(
    "sql,allowed",
    [
        ("DELETE FROM t_a", ["t_a"]),
        ("PRAGMA table_info(t_a)", ["t_a"]),
        ("SELECT * FROM t_b", ["t_a"]),
        ("SELECT * FROM sqlite_master", ["t_a"]),
        ("SELECT load_extension('anything') FROM t_a", ["t_a"]),
        ("SELECT * FROM t_a; DELETE FROM t_a", ["t_a"]),
        ("WITH a AS (SELECT 1) DELETE FROM t_a", ["t_a"]),
    ],
)
def test_read_only_sql_rejections(store, sql, allowed):
    assert store.query(sql, allowed)["status"] != "ok"
    assert store.query("SELECT count(*) n FROM t_a", ["t_a"])["rows"][0]["n"] == 101


def test_sql_timeout_and_invalid_value(store):
    assert (
        store.query("SELECT count(*) FROM t_a a,t_a b,t_a c,t_a d", ["t_a"], seconds=0)[
            "status"
        ]
        != "ok"
    )
    assert (
        store.query("SELECT decimal_sum('not a number') FROM t_a", ["t_a"])["status"]
        != "ok"
    )


def test_typed_cells_preserve_zero_state_currency_dates():
    assert typed_cell("0원", "금액")["value"] == "0"
    assert typed_cell("미결", "금액")["value"] is None
    assert typed_cell("금액: USD 1,200.10", "금액")["value"] == "1200.10"
    assert typed_cell("2029-03-04", "실제일")["type"] == "DATE"
    assert typed_cell("2029-03-04 / 2029-03-05", "날짜")["value"] is None
    assert typed_cell("USD 2 / KRW 3", "금액")["value"] is None
    assert typed_cell("123", "장비번호")["value"] is None


def test_physical_boundaries_and_wrapped_blank_gaps():
    def row(n, start, end, page=1, section="a"):
        return {
            "record_id": str(n),
            "source_id": str(page),
            "book_id": "TEST",
            "page": page,
            "section": section,
            "fields": {"x": "y"},
            "line_start": start,
            "line_end": end,
        }

    rows = [
        row(1, 1, 2),
        row(2, 5, 6),
        row(3, 9, 10),
        row(4, 12, 12, section="b"),
        row(5, 1, 2, page=2),
    ]
    lines = [
        "cell",
        "cell",
        "",
        "",
        "cell",
        "cell",
        "",
        "new heading",
        "cell",
        "cell",
        "",
        "cell",
    ]
    assert [len(t) for t in split_tables(rows, {"1": lines, "2": lines})] == [
        2,
        1,
        1,
        1,
    ]


def page(n):
    return {
        "book_id": "TEST",
        "file_name": "TEST.pdf",
        "page": n,
        "text": "source text " * 10,
    }


class FakeRetriever:
    def __init__(self, store):
        self.store, self.queries = store, []
        self.page_map = {("TEST", n): page(n) for n in (1, 2)}

    def search(self, q):
        self.queries.append(q)
        return {
            "query": q,
            "ranked": [(page(1), 1.0), (page(2), 0.9)],
            "tables": ["t_a", "t_b"],
        }

    def context(self, groups, preferred=(), sql_results=()):
        return merge_pages(groups), NS(final_chunks=[]), sql_results


def response(name, args, call_id):
    return NS(
        status="completed",
        usage=None,
        output=[
            NS(
                type="function_call",
                name=name,
                arguments=json.dumps(args),
                call_id=call_id,
            )
        ],
    )


def test_conditional_split_feedback_dependent_search_and_finish(monkeypatch, store):
    turns = [
        response(
            "solve_subquery",
            {
                "subquery": "count records",
                "interim_summary": "",
                "table_ids": ["t_a"],
                "sql": "SELECT count(*) n FROM t_a",
            },
            "1",
        ),
        response(
            "solve_subquery",
            {
                "subquery": "policy for 101 records",
                "interim_summary": "101 rows found",
                "table_ids": [],
                "sql": None,
            },
            "2",
        ),
        response(
            "finish",
            {
                "status": "sufficient",
                "selected_sources": ["TEST:p0001"],
                "interim_summary": "done",
                "conflicts": [],
            },
            "3",
        ),
    ]

    def api(client, *, history, **kwargs):
        if len(turns) == 1:
            assert any(
                isinstance(h, dict)
                and h.get("type") == "function_call_output"
                and "101" in h["output"]
                for h in history
            )
        return turns.pop(0), {"status": "complete"}

    monkeypatch.setattr("experimental.u_10o0o.table_rag.agent.api_call", api)
    retriever = FakeRetriever(store)
    result = TableAgent(retriever, client=object()).run("source question")
    assert retriever.queries == [
        "source question",
        "count records",
        "policy for 101 records",
    ]
    assert result["finish"]["status"] == "sufficient"
    assert result["sql_attempts"][0]["rows"] == [{"n": 101}]


def test_unsplit_query_and_maximum_iterations(monkeypatch, store):
    calls = []

    def api(client, *, last, **kwargs):
        calls.append(last)
        if last:
            return response(
                "finish",
                {
                    "status": "insufficient",
                    "selected_sources": [],
                    "interim_summary": "",
                    "conflicts": [],
                },
                "f",
            ), {"status": "complete"}
        return response(
            "solve_subquery",
            {
                "subquery": "retry",
                "interim_summary": "",
                "table_ids": ["unknown"],
                "sql": "SELECT 1",
            },
            "q",
        ), {"status": "complete"}

    monkeypatch.setattr("experimental.u_10o0o.table_rag.agent.api_call", api)
    r = FakeRetriever(store)
    result = TableAgent(r, client=object()).run("q")
    assert len(calls) == 6 and calls[-1]
    assert len(r.queries) == 1
    assert result["finish"]["status"] == "insufficient"


def test_context_budget_and_sql_source_retention():
    r = object.__new__(Retriever)
    rows = [(page(n), 1.0 / n) for n in range(1, 21)]
    sql = {
        "status": "ok",
        "sources": [{"book_id": "TEST", "page": 19}],
        "rows": [{"total": "10.00"}],
    }
    ranked, context, kept = r.context([rows], ["TEST:p0002"], [sql])
    assert (
        len(ranked) == 20
        and len(context.final_chunks) <= 8
        and context.token_count <= 8000
    )
    assert any(c.chunk.page == 19 for c in context.final_chunks) and kept
    assert not r.context(
        [rows], (), [{**sql, "sources": [{"book_id": "MISSING", "page": 1}]}]
    )[2]


def test_duplicate_sql_columns_must_use_aliases(store):
    assert (
        store.query(
            "SELECT a.c0, b.c0 FROM t_a a JOIN t_b b USING(c0)", ["t_a", "t_b"]
        )["status"]
        == "sql_error"
    )


def test_sql_error_is_returned_and_documents_can_finish(monkeypatch, store):
    calls = []

    def api(client, *, history, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return response(
                "solve_subquery",
                {
                    "subquery": "inspect record",
                    "interim_summary": "",
                    "table_ids": ["t_a"],
                    "sql": "SELECT missing_column FROM t_a",
                },
                "a",
            ), {"status": "complete"}
        assert any(
            isinstance(h, dict)
            and h.get("type") == "function_call_output"
            and "sql_error" in h["output"]
            for h in history
        )
        return response(
            "finish",
            {
                "status": "sufficient",
                "selected_sources": ["TEST:p0001"],
                "interim_summary": "documents sufficient",
                "conflicts": [],
            },
            "b",
        ), {"status": "complete"}

    monkeypatch.setattr("experimental.u_10o0o.table_rag.agent.api_call", api)
    result = TableAgent(FakeRetriever(store), client=object()).run("q")
    assert result["sql_attempts"][0]["status"] == "sql_error"
    assert result["finish"]["status"] == "sufficient"


def test_provider_timeout_preserves_search_results(monkeypatch, store):
    monkeypatch.setattr(
        "experimental.u_10o0o.table_rag.agent.api_call",
        lambda *a, **k: (None, {"status": "timeout"}),
    )
    result = TableAgent(FakeRetriever(store), client=object()).run("q")
    assert len(result["ranked"]) == 2
    assert result["finish"]["status"] == "insufficient"
    assert result["turns"][0]["status"] == "provider_failure"


def test_observation_never_changes_schema_identifiers_or_exceeds_budget(store):
    from experimental.u_10o0o.table_rag.agent import observation
    from experimental.u_10o0o.table_rag.retrieval import json_payload
    from experimental.u_10o0o.context_evidence import _count_tokens

    store.catalog["t_a"]["columns"] = [
        {"name": "extremely_long_" * 3000, "type": "TEXT"}
    ]
    retriever = FakeRetriever(store)
    observed = observation(retriever, retriever.search("q"), budget=2000)
    assert _count_tokens(json_payload(observed)) <= 2000
    assert observed["tables"] == [] and observed["schema_omitted_for_budget"]


def test_constant_only_sql_has_no_grounded_source(store):
    assert store.query("SELECT 999 amount", ["t_a"])["status"] == "sql_error"


def test_answer_resume_checks_payload_and_never_repeats_paid_calls(
    monkeypatch, tmp_path
):
    from experimental.u_10o0o.table_rag.benchmark import cached_answer

    calls = []

    def generate(*a, **k):
        calls.append(True)
        return {
            "answer": None,
            "telemetry": {"status": "timeout"},
            "invalid_citations": [],
        }

    monkeypatch.setattr(
        "experimental.u_10o0o.table_rag.benchmark.generate_answer", generate
    )
    q = {"id": "fixture", "question": "q"}
    first = cached_answer(
        tmp_path, object(), q, "baseline", [{"source_id": "TEST:1", "text": "a"}]
    )
    assert (
        cached_answer(
            tmp_path, object(), q, "baseline", [{"source_id": "TEST:1", "text": "a"}]
        )
        == first
    )
    assert len(calls) == 1
    with pytest.raises(ValueError, match="differs"):
        cached_answer(
            tmp_path,
            object(),
            {**q, "question": "changed"},
            "baseline",
            [{"source_id": "TEST:1", "text": "a"}],
        )
    assert len(calls) == 1


def test_reference_gate_refuses_incomplete_outputs(tmp_path):
    from experimental.u_10o0o.table_rag.benchmark import saved_results

    write_json(
        tmp_path / "run.json",
        {"status": "partial", "count": 279, "references_opened": False},
    )
    with pytest.raises(ValueError, match="All 280"):
        saved_results(tmp_path)


def test_raw_and_table_representations_share_one_candidate_page():
    from experimental.u_10o0o.table_rag.retrieval import distinct_page_units

    ranked = [({**page(1), "chunk_id": "table"}, 3.0), (page(1), 2.0), (page(2), 1.0)]
    result = distinct_page_units(ranked, 2)
    assert [r[0]["page"] for r in result] == [1, 2]
    assert result[0][0]["chunk_id"] == "table"


def test_selected_baseline_date_roles_and_optional_spec(monkeypatch):
    from experimental.u_10o0o.table_rag import hg

    lookup = NS(
        _matched_profile_values=lambda _: {
            "shipper": {"가상 화주"},
            "cargo": {"가상 화물"},
            "spec": set(),
        }
    )
    captures = []

    def unique(_, fields):
        captures.append(fields)
        return "CASE"

    monkeypatch.setattr(hg, "unique_case", unique)
    case, role = hg.date_target(
        "2029-06-20 기준 2029-06-10 실제 양하된 가상 화주 가상 화물의 비용", lookup
    )
    assert (case, role) == ("CASE", "actual_discharge_date")
    assert (
        captures[0]["actual_discharge_date"] == "2029-06-10"
        and "spec" not in captures[0]
    )
    assert (
        hg.date_target(
            "2029-06-20 기준 2029-06-21 실제 양하된 가상 화주 가상 화물", lookup
        )[0]
        is None
    )
    assert (
        hg.date_target(
            "2029-06-20 기준 2029-06-10 양하 예정 가상 화주 가상 화물", lookup
        )[0]
        is None
    )
    assert (
        hg.date_target("2029-06-20 기준 2029-06-10 실제 양하된 두 사건 비교", lookup)[0]
        is None
    )


def test_date_extension_does_not_override_existing_case_lookup(monkeypatch):
    from experimental.u_10o0o.table_rag import hg

    native = ([], ([], NS(), ["NATIVE"], "explicit", "native"), "explicit_hg")
    monkeypatch.setattr(hg, "complete_sources", lambda *a: native)
    monkeypatch.setattr(
        hg, "date_target", lambda *a: ("OTHER", "actual_discharge_date")
    )
    monkeypatch.setattr(
        hg,
        "apply_sources",
        lambda *a: pytest.fail("An existing verified target must not be overwritten"),
    )
    assert hg.complete_sources_with_dates("q", [], NS(), NS(), 8000) == native
