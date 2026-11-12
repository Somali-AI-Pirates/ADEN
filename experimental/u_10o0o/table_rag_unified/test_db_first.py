"""DB-first behavior: real record lookup, fake planner, no models or network."""

from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.table_rag.test_table_rag import page, response
from experimental.u_10o0o.table_rag_unified import agent as m
from experimental.u_10o0o.table_rag_unified.test_unified import index as index


def environment(index, monkeypatch, commands):
    calls = []
    pages = {f"TEST:p{n:04d}": page(n) for n in (1, 2, 3)}

    def lookup(targets):
        calls.append("records")
        return index.lookup(targets)

    def search(question):
        calls.append("hybrid")
        return {
            "query": question,
            "ranked": [(pages["TEST:p0003"], 1.0)],
            "tables": [],
            "seconds": 0,
        }

    def context(groups, selected, sql, **kwargs):
        observed = {m.source_id(p): p for g in groups for p, _ in g}
        retained = [observed[s] for s in selected if s in observed]
        return (
            [(p, 0.0) for p in retained],
            NS(
                final_chunks=[
                    NS(chunk=NS(book_id="TEST", page=p["page"])) for p in retained
                ]
            ),
            [],
        )

    r = NS(
        search=search,
        store=NS(schema=lambda ids: [], by_page={}),
        by_source=pages,
        context=context,
    )
    replies = iter(commands)

    def api(*args, **kwargs):
        calls.append("model")
        return next(replies), {"status": "complete"}

    monkeypatch.setattr(m, "api_call", api)
    return r, NS(lookup=lookup), calls


def finish(*sources):
    return response(
        "finish",
        {
            "status": "sufficient",
            "selected_sources": list(sources),
            "interim_summary": "",
            "conflicts": [],
        },
        "done",
    )


@pytest.mark.parametrize("db_first", [False, True])
def test_unique_record_can_finish_without_hybrid(index, monkeypatch, db_first):
    r, store, calls = environment(index, monkeypatch, [finish("TEST:p0001")])
    result = m.UnifiedAgent(r, object(), store, db_first=db_first).run(
        "HG2029-00001 청구액"
    )
    assert calls == (
        ["records", "model"] if db_first else ["hybrid", "records", "model"]
    )
    assert result["finish"]["status"] == "sufficient"
    assert result["selection_audit"]["not_retained"] == []
    assert [p.chunk.page for p in result["context"].final_chunks] == [1]


def test_missing_rule_triggers_hybrid_and_keeps_both_sources(index, monkeypatch):
    commands = [
        response(
            "search_evidence",
            {"kind": "documents", "query": "감액 승인 적용 규정"},
            "search",
        ),
        finish("TEST:p0001", "TEST:p0003"),
    ]
    r, store, calls = environment(index, monkeypatch, commands)
    result = m.UnifiedAgent(r, object(), store, db_first=True).run(
        "HG2029-00001 청구액과 적용 규정"
    )
    assert calls == ["records", "model", "hybrid", "model"]
    assert [p.chunk.page for p in result["context"].final_chunks] == [1, 3]
    assert result["finish"]["status"] == "sufficient"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("일반 업무 절차", ["hybrid", "model"]),
        ("HG2029-99999 청구액", ["records", "hybrid", "model"]),
        ("HG2029-00001 제외한 나머지", ["hybrid", "model"]),
    ],
)
def test_unresolved_input_starts_with_search(index, monkeypatch, question, expected):
    r, store, calls = environment(index, monkeypatch, [finish("TEST:p0003")])
    result = m.UnifiedAgent(r, object(), store, db_first=True).run(question)
    assert calls == expected
    assert result["searches"][0]["kind"] == "documents"


def test_ambiguous_code_does_not_suppress_search(index, monkeypatch):
    ambiguous = index.lookup([{"constraints": [{"field": "spec", "value": "20GP"}]}])
    r, store, calls = environment(
        NS(lookup=lambda t: ambiguous), monkeypatch, [finish("TEST:p0003")]
    )
    m.UnifiedAgent(r, object(), store, db_first=True).run("HG2029-00001 청구액")
    assert calls == ["records", "hybrid", "model"]


def test_two_unique_targets_preserve_both_records(index, monkeypatch):
    r, store, calls = environment(
        index, monkeypatch, [finish("TEST:p0001", "TEST:p0002")]
    )
    result = m.UnifiedAgent(r, object(), store, db_first=True).run(
        "HG2029-00001과 HG2029-00002 청구 비교"
    )
    assert calls == ["records", "model"]
    assert [p.chunk.page for p in result["context"].final_chunks] == [1, 2]


def test_database_error_is_not_hidden_by_search(monkeypatch):
    def fail(targets):
        raise ValueError("snapshot mismatch")

    r, store, calls = environment(NS(lookup=fail), monkeypatch, [])
    with pytest.raises(ValueError, match="snapshot mismatch"):
        m.UnifiedAgent(r, object(), store, db_first=True).run("HG2029-00001 청구액")
    assert calls == ["records"]
