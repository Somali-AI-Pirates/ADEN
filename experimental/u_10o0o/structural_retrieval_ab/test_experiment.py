"""Boundary checks for experimental source selection, not product behavior."""

from types import SimpleNamespace
import sys
import pytest
import tiktoken
from .selection import (
    Evidence,
    RequestEvidence,
    Selection,
    validate_and_select,
    focused_fragments,
    observed_payload,
)
from .paragraphs import extract


def payload(sources):
    return {
        "candidate_source_ids": list(sources),
        "observed_evidence": [
            {"source_id": sid, "fragments": [text]} for sid, text in sources.items()
        ],
    }


def request(sources, status="supported"):
    return RequestEvidence(
        request="Compare the requested records",
        status=status,
        evidence=[Evidence(source_id=s, quote=t) for s, t in sources.items()],
        scope="same event",
    )


def test_joint_support_is_not_reduced_to_one_page():
    src = {"A": "invoice total is 100 dollars", "B": "payment received is 80 dollars"}
    result = Selection(
        requests=[request(src)], status="sufficient", summary="two sources"
    )
    selected, status, audit = validate_and_select(result, payload(src), ["A"])
    assert set(selected) == {"A", "B"} and status == "sufficient"
    assert audit["covered_supported_requests"] == 1


def test_unobserved_source_cannot_be_promoted():
    shown = {"A": "original contract has a fixed date"}
    result = Selection(
        requests=[request({"B": "original contract has a fixed date"})],
        status="sufficient",
        summary="bad source",
    )
    selected, status, audit = validate_and_select(result, payload(shown), ["A"])
    assert selected == ["A"] and status == "insufficient" and audit["errors"]


def test_unseen_text_on_discovered_page_does_not_count():
    result = Selection(
        requests=[request({"A": "future approval granted in September"})],
        status="sufficient",
        summary="unseen body",
    )
    _, status, audit = validate_and_select(
        result, payload({"A": "a short title with no date"}), ["A"]
    )
    assert status == "insufficient" and audit["valid_supported_requests"] == 0


def test_nine_joint_pages_cannot_be_called_complete():
    src = {str(i): f"actual evidence paragraph number {i}" for i in range(9)}
    result = Selection(
        requests=[request(src)], status="sufficient", summary="needs nine pages"
    )
    selected, status, _ = validate_and_select(result, payload(src), [])
    assert len(selected) <= 8 and status == "insufficient"


def test_missing_request_prevents_false_sufficient():
    result = Selection(
        requests=[
            request({"A": "a real invoice for 100 dollars"}),
            request({}, "missing"),
        ],
        status="sufficient",
        summary="partial",
    )
    _, status, _ = validate_and_select(
        result, payload({"A": "a real invoice for 100 dollars"}), []
    )
    assert status == "insufficient"


def test_focused_windows_preserve_source_and_budget():
    encoding = tiktoken.get_encoding("o200k_base")
    text = (
        "Contract header\n"
        + "unrelated scheduling text. " * 500
        + "\nThe seal number must be reported to the carrier.\n"
        + "unrelated text. " * 100
    )
    chunks = focused_fragments("seal number reported carrier", text, encoding)
    assert sum(len(encoding.encode(x)) for x in chunks) <= 386
    assert all(x in text for x in chunks)
    assert any("seal number" in x for x in chunks)


def test_same_observation_does_not_read_hidden_candidates():
    x = {
        "question": "test",
        "candidate_source_ids": ["A", "B"],
        "retrieved_candidates": [
            {"source_id": "A", "text": "visible sentence"},
            {"source_id": "B", "text": "hidden information"},
        ],
        "diagnostics": {
            "observations": [
                {
                    "passages": [
                        {"source_id": "A", "text": "visible sentence"},
                        {"source_id": "C", "text": "outside final pool"},
                    ]
                }
            ]
        },
    }
    p = observed_payload(x)
    assert [s["source_id"] for s in p["observed_evidence"]] == ["A"]
    assert "hidden" not in str(p)


def test_paragraphs_keep_exact_source_spans_and_dates():
    page = {
        "file_name": "AS007.pdf",
        "page": 1,
        "text": "IS01 | example\n\nRole title\n\nEffective 2026-11-03\n\nNew submissions after 2026-11-03 require the fixed original submission route. Old submissions remain unchanged.\n\nIS01-001-01 | row data.",
    }
    units = extract([page])
    assert len(units) == 1
    u = units[0]
    assert page["text"][u["start"] : u["end"]] == u["raw_text"]
    assert "2026-11-03" in u["text"] and u["source_id"] == "AS007:p0001"


def test_wide_pool_reranks_once_and_restricts_observation(monkeypatch):
    from .retrieval import WideRetriever

    docs = [SimpleNamespace(metadata={"source_id": str(i)}) for i in range(30)]
    ranked = [(d, 1.0) for d in docs]
    calls = []
    fake = SimpleNamespace(
        rank_dense=lambda *a, **k: ranked,
        _rrf=lambda a, b, limit: calls.append(("rrf", limit)) or ranked,
        _source_result=lambda d, s: {"source_id": d.metadata["source_id"]},
    )
    monkeypatch.setitem(sys.modules, "app.rag.retrieval", fake)
    monkeypatch.setitem(
        sys.modules,
        "app.rag.models",
        SimpleNamespace(SearchResult=lambda **kw: SimpleNamespace(**kw)),
    )

    def tables(units, pages):
        assert len(units) == len(pages) == 20
        return ()

    base = SimpleNamespace(
        corpus=SimpleNamespace(
            documents=docs,
            page_count=30,
            pages_by_source={str(i): d for i, d in enumerate(docs)},
        ),
        _query_vector=lambda q: [],
        _bm25=SimpleNamespace(fielded_search=lambda *a, **k: ranked),
        _rerank=lambda q, p: (
            calls.append(("rerank", len(p))) or (list(reversed(p)), {"pairs": len(p)})
        ),
        _table_ids=tables,
    )
    result = WideRetriever(base).search("query")
    assert calls == [("rrf", 100), ("rerank", 30)]
    assert len(result.pages) == 20 and result.pages[0] is docs[-1]


@pytest.mark.parametrize("question", ["", "  "])
def test_wide_empty_query_rejected(question):
    from .retrieval import WideRetriever

    with pytest.raises(ValueError):
        WideRetriever(None).search(question)


def test_korean_token_boundaries_remain_literal():
    encoding = tiktoken.get_encoding("o200k_base")
    text = (
        "원문 머리말\n"
        + "냉동 장비 배차 절차와 서류 검토 중입니다. " * 300
        + " 봉인번호 변경은 선사에게 전달한다. " * 30
    )
    pieces = focused_fragments("봉인번호 선사", text, encoding)
    assert all(piece in text for piece in pieces)
    assert all("\ufffd" not in piece for piece in pieces)
