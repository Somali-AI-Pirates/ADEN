"""Evidence selected from an observed query must survive candidate merging."""

from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.table_rag.agent import TableAgent
from experimental.u_10o0o.table_rag.retrieval import Retriever
from experimental.u_10o0o.table_rag.test_table_rag import page, response


def test_selected_deep_source_survives_but_unknown_source_does_not():
    retriever = object.__new__(Retriever)
    groups = [
        [(page(n), 1 / n) for n in range(1, 21)],
        [(page(n), 1 / n) for n in range(21, 41)],
    ]
    preferred = ["TEST:p0040", "UNKNOWN:p0001"]
    _, old, _ = retriever.context(groups, preferred)
    ranked, new, _ = retriever.context(groups, preferred, preserve_evidence=True)
    assert 40 not in {c.chunk.page for c in old.final_chunks}
    assert 40 in {c.chunk.page for c in new.final_chunks}
    assert len(ranked) == 20 and len(new.final_chunks) <= 8 and new.token_count <= 8000
    assert all(c.chunk.book_id == "TEST" for c in new.final_chunks)


def test_empty_preference_preserves_legacy_context():
    retriever = object.__new__(Retriever)
    groups = [[(page(n), 1 / n) for n in range(1, 21)]]
    old = retriever.context(groups)
    new = retriever.context(groups, preserve_evidence=True)
    assert old[0] == new[0] and old[1].context_hash == new[1].context_hash


@pytest.mark.parametrize("preferred", [["UNKNOWN:p0001"], ["TEST:p0002"], []])
def test_missing_selected_evidence_cannot_finish_sufficient(monkeypatch, preferred):
    retriever = NS(
        store=NS(schema=lambda _: []),
        search=lambda q: {"query": q, "ranked": [(page(1), 1)], "tables": []},
        context=lambda *args, **kwargs: ([], NS(final_chunks=[]), []),
    )
    monkeypatch.setattr(
        "experimental.u_10o0o.table_rag.agent.api_call",
        lambda *args, **kwargs: (
            response(
                "finish",
                {
                    "status": "sufficient",
                    "selected_sources": preferred,
                    "interim_summary": "",
                    "conflicts": [],
                },
                "end",
            ),
            {"status": "complete"},
        ),
    )
    result = TableAgent(retriever, object(), preserve_evidence=True).run("질문")
    assert result["finish"]["status"] == "insufficient"


def test_sql_and_selected_sources_share_one_budget():
    retriever = object.__new__(Retriever)
    groups = [[(page(n), 1 / n) for n in range(1, 21)], [(page(40), 1)]]
    sql = {
        "status": "ok",
        "sources": [{"book_id": "TEST", "page": 40}],
        "rows": [{"sum": "10.00"}],
    }
    _, context, kept = retriever.context(
        groups, ["TEST:p0040"], [sql], preserve_evidence=True
    )
    assert kept == [sql] and 40 in {c.chunk.page for c in context.final_chunks}
    assert len(context.final_chunks) <= 8
