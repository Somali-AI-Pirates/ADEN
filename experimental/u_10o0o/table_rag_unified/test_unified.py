"""Boundary tests without network, model loading, or evaluation labels."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.pattern_retrieval.common import file_sha, write_json
from experimental.u_10o0o.table_rag import retrieval
from experimental.u_10o0o.table_rag.test_table_rag import page, response
from experimental.u_10o0o.table_rag_unified import agent as m
from experimental.u_10o0o.table_rag_unified.index import SCHEMA, RecordIndex, codes


@pytest.fixture
def index(tmp_path):
    with sqlite3.connect(tmp_path / "index.sqlite3") as db:
        db.executescript(SCHEMA)
        for n in (1, 2):
            e = f"HG:HG2029-{n:05d}"
            db.execute("INSERT INTO entities VALUES(?,?,?,?)", (e, "HG", "case", e))
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?)",
                (e, f"TEST:p{n:04d}", "invoice", 3),
            )
            db.execute(
                "INSERT INTO keys VALUES(?,?,?,?,?)",
                (f"HG2029-{n:05d}", "reservation", e, f"TEST:p{n:04d}", "identity"),
            )
            db.execute(
                "INSERT INTO attributes VALUES(?,?,?,?)",
                (e, "spec", "20gp", f"TEST:p{n:04d}"),
            )
            db.execute(
                "INSERT INTO attributes VALUES(?,?,?,?)",
                (e, "actual_gate_out_date", f"2029-01-0{n}", f"TEST:p{n:04d}"),
            )
    write_json(
        tmp_path / "manifest.json",
        {"database_sha256": file_sha(tmp_path / "index.sqlite3")},
    )
    return RecordIndex(tmp_path)


def test_exact_alias_shared_class_dates_and_conflict(index):
    assert codes("hg2029 – 00001") == ("HG2029-00001",)
    targets = [
        {"identifiers": ["HG2029-00001"], "roles": ["invoice"]},
        {"constraints": [{"field": "spec", "value": "20GP"}]},
        {"constraints": [{"field": "actual_gate_out_date", "value": "2029-01-02"}]},
        {"identifiers": ["HG2029-00001", "HG2029-00002"]},
        {},
    ]
    r = index.lookup(targets)["targets"]
    assert [t["status"] for t in r] == [
        "matched",
        "ambiguous",
        "matched",
        "conflict",
        "insufficient_constraints",
    ]
    assert r[1]["candidate_count"] == 2 and len(r[1]["entities"]) == 2
    assert r[2]["entities"][0]["id"].endswith("00002")
    assert (
        index.lookup([{"identifiers": ["HG2029-00001"], "roles": ["receipt"]}])[
            "targets"
        ][0]["entities"][0]["sources"]
        == []
    )


def test_readonly_and_fingerprint(index):
    from experimental.u_10o0o.table_rag_unified.index import connect

    with connect(index.path) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM keys")
    with index.path.open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="fingerprint"):
        RecordIndex(index.directory)


def test_grounding_rejects_excluded_invented_and_empty():
    def request(key):
        return m.Lookup(
            targets=[
                m.Target(identifiers=[key] if key else [], constraints=[], roles=[])
            ]
        )

    for key, question in [
        ("HG2029-00001", "HG2029-00001은 제외하고 다른 사건"),
        ("HG2029-00002", "HG2029-00001 청구"),
        ("", "청구"),
    ]:
        with pytest.raises(ValueError):
            m.grounded(request(key), question, [])
    m.grounded(request("HG2029-00001"), "hg2029-00001 청구", [])
    m.grounded(request("HG2029-00001"), "앞선 사건", [{"text": "HG2029-00001"}])


def test_context_switch_is_thread_local(monkeypatch):
    monkeypatch.setattr(m, "ORIGINAL_COMPLETE", lambda *a: "native")
    previous = retrieval.complete_sources
    with m.integrated_runtime():

        def call(enabled):
            t = m.UNIFIED.set(enabled)
            try:
                return retrieval.complete_sources("q", [], None, None, 8000)
            finally:
                m.UNIFIED.reset(t)

        with ThreadPoolExecutor(2) as p:
            off, on = list(p.map(call, [False, True]))
        assert off == "native" and on[2] == "native_hg_disabled"
    assert retrieval.complete_sources is previous


@pytest.mark.parametrize("step", ["query_tables", "read_sources", "search_evidence"])
def test_sql_read_and_table_discovery_do_not_repeat_hybrid(monkeypatch, step):
    searches, sqls = [], []

    def search(q):
        searches.append(q)
        return {"query": q, "ranked": [(page(1), 1)], "tables": ["t_a"], "seconds": 0}

    store = NS(
        schema=lambda ids: [{"table_id": t, "columns": []} for t in ids],
        catalog={"t_a": {"book_id": "TEST", "page": 1}},
        query=lambda sql, ids: (
            sqls.append(sql) or {"status": "ok", "rows": [], "sources": []}
        ),
    )
    r = NS(
        search=search,
        store=store,
        page_map={("TEST", 1): page(1)},
        by_source={"TEST:p0001": page(1)},
        context=lambda *a, **kw: ([], NS(final_chunks=[]), []),
    )
    args = {
        "query_tables": {"table_ids": ["t_a"], "sql": "SELECT * FROM t_a"},
        "read_sources": {"source_ids": ["TEST:p0001"]},
        "search_evidence": {"kind": "tables", "query": "처리 기록"},
    }
    replies = iter(
        [
            response(step, args[step], "one"),
            response(
                "finish",
                {
                    "status": "insufficient",
                    "selected_sources": [],
                    "interim_summary": "",
                    "conflicts": [],
                },
                "two",
            ),
        ]
    )
    monkeypatch.setattr(
        m, "api_call", lambda *a, **kw: (next(replies), {"status": "complete"})
    )
    result = m.UnifiedAgent(r, object(), NS(discover=lambda q: ["t_a"])).run(
        "업무 규정"
    )
    assert len(searches) == 1
    assert result["turns"][0]["status"] == "executed"
    assert len(sqls) == (step == "query_tables")


def test_unobserved_sql_does_not_execute(monkeypatch):
    retriever = NS(
        search=lambda q: {"query": q, "ranked": [], "tables": []},
        store=NS(schema=lambda ids: []),
        context=lambda *a, **kw: ([], NS(final_chunks=[]), []),
    )
    calls = iter(
        [
            response(
                "query_tables",
                {"table_ids": ["invented"], "sql": "SELECT * FROM invented"},
                "one",
            ),
            response(
                "finish",
                {
                    "status": "insufficient",
                    "selected_sources": [],
                    "interim_summary": "",
                    "conflicts": [],
                },
                "two",
            ),
        ]
    )
    monkeypatch.setattr(
        m, "api_call", lambda *a, **kw: (next(calls), {"status": "complete"})
    )
    result = m.UnifiedAgent(retriever, object(), NS()).run("업무 규정")
    assert result["turns"][0]["status"] == "invalid_arguments"
    assert result["sql_attempts"] == []


def test_record_sources_restore_real_page_shape_without_book_id(index):
    raw = {"file_name": "TEST.pdf", "page": 1, "text": "original"}
    retriever = NS(
        by_source={"TEST:p0001": raw}, store=NS(by_page={("TEST", 1): ["t_a"]})
    )
    result = m.records_result(
        "q", index.lookup([{"identifiers": ["HG2029-00001"]}]), retriever
    )
    assert result["ranked"] == [(raw, 0.0)] and result["tables"] == ["t_a"]


@pytest.mark.parametrize(
    "value",
    ["20GP", "20피트 일반형", "20피트 일반형 컨테이너", "20피트짜리 일반 컨테이너"],
)
def test_spec_literal_grounding_and_lookup_share_normalization(index, value):
    request = m.Lookup(
        targets=[
            m.Target(
                identifiers=[],
                constraints=[m.Constraint(field="spec", value=value)],
                roles=[],
            )
        ]
    )
    m.grounded(request, "20피트 일반형 컨테이너의 청구", [])
    result = index.lookup([t.model_dump() for t in request.targets])["targets"][0]
    assert result["status"] == "ambiguous" and result["candidate_count"] == 2
    with pytest.raises(ValueError):
        m.grounded(request, "40피트 일반형 컨테이너의 청구", [])
