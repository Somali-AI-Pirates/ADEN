"""Request evidence must be read, selected and retained; no model/network access."""

from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.table_rag.test_table_rag import page, response
from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id
from experimental.u_10o0o.table_rag_unified import agent as m
from experimental.u_10o0o.table_rag_unified.evidence import (
    EvidenceFinish,
    validate_requirements,
)


def proposal(source="TEST:p0010", quote="최초 신청은 고객 포털로 제출한다."):
    return dict(
        status="sufficient",
        selected_sources=[source],
        interim_summary="",
        conflicts=[],
        requirements=[
            dict(
                question_span="최초 제출처",
                requirement="최초 제출처 확인",
                status="supported",
                evidence=[dict(source_id=source, quote=quote)],
            )
        ],
    )


def setup(monkeypatch, replies):
    pages = [page(i) for i in range(1, 21)]
    for p in pages:
        p["text"] = f"참고 기록 {p['page']}의 일반적인 처리 내역입니다."
    pages[9]["text"] = "최초 신청은 고객 포털로 제출한다."
    r = NS(
        store=NS(schema=lambda ids: [], by_page={}),
        by_source={source_id(p): p for p in pages},
    )
    r.search = lambda q: dict(
        query=q, ranked=[(p, 1.0) for p in pages], tables=[], seconds=0
    )
    r.context = lambda groups, preferred, sql, **kw: Retriever.context(
        r, groups, preferred, sql, **kw
    )
    calls = []
    answers = iter(replies)

    def api(*args, **kwargs):
        calls.append(kwargs)
        return next(answers), dict(status="complete")

    monkeypatch.setattr(m, "api_call", api)
    return r, calls


def test_unread_tail_is_read_and_preserved(monkeypatch):
    r, calls = setup(
        monkeypatch,
        [
            response("read_sources", {"source_ids": ["TEST:p0010"]}, "read"),
            response("finish", proposal(), "finish"),
        ],
    )
    result = m.UnifiedAgent(r, object(), NS(), db_first=True, verify_evidence=True).run(
        "최초 제출처를 알려줘"
    )
    assert result["finish"]["status"] == "sufficient"
    assert result["context"].final_chunks[0].chunk.page == 10
    assert result["selection_audit"]["requirements_errors"] == []
    assert "unread_candidates" in calls[0]["history"][0]["content"]


def test_preview_alone_cannot_be_quoted(monkeypatch):
    r, _ = setup(
        monkeypatch,
        [
            response("finish", proposal(), "bad"),
            response("read_sources", {"source_ids": ["TEST:p0010"]}, "read"),
            response("finish", proposal(), "good"),
        ],
    )
    result = m.UnifiedAgent(r, object(), NS(), verify_evidence=True).run(
        "최초 제출처를 알려줘"
    )
    assert result["turns"][0]["status"] == "invalid_arguments"
    assert result["finish"]["status"] == "sufficient"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("invented_request", "question_span"),
        ("unselected", "selected_sources"),
        ("missing", "unresolved"),
        ("truncated_context", "final context"),
    ],
)
def test_invalid_support_is_detected(change, expected):
    data = proposal()
    if change == "invented_request":
        data["requirements"][0]["question_span"] = "없는 조건"
    elif change == "unselected":
        data["selected_sources"] = []
    elif change == "missing":
        data["requirements"][0]["status"] = "missing"
    finish = EvidenceFinish.model_validate(data)
    obs = [
        {
            "passages": [
                {"source_id": "TEST:p0010", "text": "최초 신청은 고객 포털로 제출한다."}
            ]
        }
    ]
    final = (
        [{"book_id": "TEST", "page": 10, "text": "잘린 내용"}]
        if change == "truncated_context"
        else None
    )
    assert any(
        expected in e
        for e in validate_requirements(finish, "최초 제출처를 알려줘", obs, final)
    )
