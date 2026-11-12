from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experimental.u_10o0o.table_rag_workflow.agent import (
    Plan,
    Request,
    Selection,
    Support,
    WorkflowAgent,
    preferred_sources,
    simple_record,
    validate_plan,
)
from experimental.u_10o0o.table_rag_workflow.retrieval import raw_card


def request(rid="r1", span="청구서", query="청구서"):
    return Request(
        request_id=rid,
        question_span=span,
        need="청구 기록",
        kind="record",
        query=query,
        targets=[],
    )


def test_simple_lookup_has_closed_scope():
    assert simple_record("HG2029-00001 청구금액은 얼마야?") == (
        "HG2029-00001",
        "invoice",
    )
    assert simple_record("HG2029-00001 청구서 보여줘") == ("HG2029-00001", "invoice")
    for q in [
        "HG2029-00001 청구서가 왜 이렇게 나왔어?",
        "HG2029-00001 청구서 말고 승인서 보여줘",
        "HG2029-00001 청구서와 규정 보여줘",
        "HG2029-00001과 HG2029-00002 청구서 보여줘",
        "동명이인 HG2029-00001 청구서 보여줘",
        "20GP 청구서 보여줘",
    ]:
        assert simple_record(q) is None


def test_plan_cannot_invent_identifier_or_request_span():
    plan = Plan(requests=[request()], needs_clarification=False, reason="")
    validate_plan(plan, "청구서 알려줘")
    with pytest.raises(ValueError):
        validate_plan(plan, "승인 내용 알려줘")
    plan.requests[0].query = "HG2029-99999 청구서"
    warnings = validate_plan(plan, "청구서 알려줘")
    assert warnings
    assert plan.requests[0].query == "청구서 알려줘"


def test_evidence_stays_bound_to_each_request():
    reqs = [request("A"), request("B")]
    selected = Selection(
        status="sufficient",
        coverage=[
            Support(
                request_id="A",
                status="supported",
                evidence_ids=["a", "b"],
                missing_query=None,
            ),
            Support(
                request_id="B",
                status="supported",
                evidence_ids=["c"],
                missing_query=None,
            ),
        ],
        additional_obligations=[],
        sql=None,
        reason="",
    )
    assert preferred_sources(
        selected,
        {"a": {"source": "p1"}, "b": {"source": "p2"}, "c": {"source": "p3"}},
        reqs,
    ) == ["p1", "p3", "p2"]
    selected.coverage.pop()
    with pytest.raises(ValueError):
        preferred_sources(selected, {}, reqs)


def test_unobserved_evidence_rejected():
    selected = Selection(
        status="sufficient",
        coverage=[
            Support(
                request_id="r1",
                status="supported",
                evidence_ids=["invented"],
                missing_query=None,
            )
        ],
        additional_obligations=[],
        sql=None,
        reason="",
    )
    with pytest.raises(ValueError):
        preferred_sources(selected, {}, [request()])


def test_matched_excerpt_is_not_always_the_page_prefix():
    page = {
        "book_id": "AS001",
        "page": 1,
        "text": "머리말\n소개\n문서 제목\n일반 설명\n\n필요한 청구 증빙과 수납 기록입니다.\n끝",
    }
    card = raw_card(page, "청구 증빙 수납 기록")
    assert "청구 증빙" in card["text"]
    assert card["text"] in page["text"]


def fake_retriever():
    page = {"book_id": "AS001", "page": 1, "text": "청구서 원문"}
    chunk = SimpleNamespace(book_id="AS001", page=1, text="청구서 원문")
    retriever = Mock()
    retriever.by_source = {"AS001:p0001": page}
    retriever.store.by_page = {("AS001", 1): []}
    retriever.context.return_value = (
        [(page, 0)],
        SimpleNamespace(final_chunks=[SimpleNamespace(chunk=chunk)]),
        [],
    )
    return retriever


def test_exact_record_query_skips_llm_and_hybrid():
    r = fake_retriever()
    client = Mock()
    index = Mock()
    index.lookup.return_value = {
        "targets": [
            {
                "status": "matched",
                "truncated": False,
                "entities": [{"sources": [{"source": "AS001:p0001"}]}],
            }
        ],
        "seconds": 0,
    }
    result = WorkflowAgent(r, client, index=index, catalog=Mock()).run(
        "HG2029-00001 청구서 보여줘"
    )
    assert result["finish"]["status"] == "sufficient"
    assert result["selection_audit"]["fast_path"]
    assert result["turns"] == []
    client.responses.create.assert_not_called()
    r.search.assert_not_called()


def test_selected_source_loss_downgrades_fast_path():
    r = fake_retriever()
    r.context.return_value = ([], SimpleNamespace(final_chunks=[]), [])
    index = Mock()
    index.lookup.return_value = {
        "targets": [
            {
                "status": "matched",
                "truncated": False,
                "entities": [{"sources": [{"source": "AS001:p0001"}]}],
            }
        ],
        "seconds": 0,
    }
    result = WorkflowAgent(r, Mock(), index=index, catalog=Mock()).run(
        "HG2029-00001 청구서 보여줘"
    )
    assert result["finish"]["status"] == "insufficient"


def test_ambiguous_record_uses_search_instead_of_choosing_a_case(monkeypatch):
    from experimental.u_10o0o.table_rag_workflow import agent as module

    r = fake_retriever()
    index = Mock()
    index.lookup.return_value = {
        "targets": [{"status": "ambiguous", "truncated": False, "entities": []}],
        "seconds": 0,
    }
    lookup = module.Target(
        identifiers=["HG2029-00001"], constraints=[], roles=["invoice"]
    )
    req = request()
    req.targets = [lookup]
    search_mock = Mock(return_value={"ranked": [], "kind": "documents"})
    monkeypatch.setattr(module, "search", search_mock)
    results, _ = WorkflowAgent(r, Mock(), index=index, catalog=Mock())._execute(req)
    assert search_mock.call_count == 1
    assert results[-1]["request_ids"] == ["r1"]


def test_api_failure_preserves_fallback_evidence_and_failure_status(monkeypatch):
    from experimental.u_10o0o.table_rag_workflow import agent as module

    r = fake_retriever()
    page = r.by_source["AS001:p0001"]
    monkeypatch.setattr(
        module,
        "api_call",
        lambda *args, **kwargs: (
            None,
            {"status": "timeout", "seconds": 30, "calls": 1},
        ),
    )
    monkeypatch.setattr(
        module,
        "search",
        lambda *args, **kwargs: {"ranked": [(page, 1.0)], "kind": "documents"},
    )
    result = WorkflowAgent(r, Mock(), index=Mock(), catalog=Mock()).run("절차를 알려줘")
    assert result["finish"]["status"] == "insufficient"
    assert result["turns"][0]["status"] == "provider_failure"
    assert len(result["context"].final_chunks) == 1
    assert len(result["turns"]) == 1


def test_lookup_storage_error_is_not_silently_replaced_by_search():
    index = Mock()
    index.lookup.side_effect = RuntimeError("snapshot differs")
    with pytest.raises(RuntimeError, match="snapshot differs"):
        WorkflowAgent(fake_retriever(), Mock(), index=index, catalog=Mock()).run(
            "HG2029-00001 청구서 보여줘"
        )
