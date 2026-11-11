"""Behavior checks for source scope and candidate preservation."""

from types import SimpleNamespace

import numpy as np

from experimental.u_10o0o.embedding_ablation.kure_followup import (
    Scope,
    candidates,
    page_kind,
)


def test_only_recognized_case_sources_are_excluded():
    rows = [
        {"text": "IS12 | 요율 담당\n규정 및 처리 기록 HG2029-01234"},
        {"text": "원문 인계철\nHG2029-01234 / 화주 / 비용 조건 약정"},
        {"text": "계약철\nHA28-C025 계약 조건 확인"},
        {"text": "새로운 양식의 미인식 기록"},
    ]
    scope = Scope(rows, {})
    assert scope.allowed.tolist() == [True, False, False, True]
    assert page_kind(rows[0]) == "policy_records"


def test_specific_identifiers_and_source_names_keep_full_search():
    scope = Scope([], {"sample": ("테스트화주", "보호용 상자", "20GP")})
    assert scope.decide("HG2029-00123의 비용")["view"] == "full"
    assert scope.decide("hl – 20gp – 1234 상태")["view"] == "full"
    assert scope.decide("테스트화주의 반출일")["view"] == "full"
    assert scope.decide("보호용 상자는 언제 나갔어?")["view"] == "full"
    general = scope.decide("20피트도 새 요율을 적용해?")
    assert general["view"] == "general_policy_view"
    assert not general["target_confirmed"]
    ambiguous = scope.decide("우리 계약서는 주말도 세나요?")
    assert ambiguous["scope_confirmation_required"]
    assert ambiguous["view"] == "general_policy_view"


def test_union_preserves_dense_only_evidence_lost_by_rrf_cutoff():
    pages = [
        {"file_name": f"AS{i:03}.pdf", "page": 1, "text": "안내"} for i in range(40)
    ]
    index = SimpleNamespace(fielded=lambda q, top_k: [(p, 1.0) for p in pages[:20]])
    engine = SimpleNamespace(pages=pages, index=index)
    scores = np.arange(40, dtype=np.float32)
    scope = Scope(pages, {})
    short, *_ = candidates(engine, scope, "질문", scores, scoped=False, limit=20)
    union, *_ = candidates(engine, scope, "질문", scores, scoped=False, limit=40)
    gold = pages[24]
    assert gold not in [p for p, _ in short]
    assert gold in [p for p, _ in union]
    assert len(union) == 40


def test_scope_filter_runs_before_each_retrievers_cutoff():
    pages = [
        {
            "file_name": f"AS{i:03}.pdf",
            "page": 1,
            "text": "HG2029-12345 조건" if i < 25 else "IS01 | 규정",
        }
        for i in range(50)
    ]
    calls = []

    def fielded(question, *, top_k):
        calls.append(top_k)
        return [(p, 50 - i) for i, p in enumerate(pages[:top_k])]

    engine = SimpleNamespace(pages=pages, index=SimpleNamespace(fielded=fielded))
    scope = Scope(pages, {})
    pool, sparse, dense, decision = candidates(
        engine, scope, "일반 요율은?", np.arange(50)[::-1].copy(), scoped=True, limit=40
    )
    assert calls == [50]
    assert len(sparse) == len(dense) == 20
    assert all(page_kind(p) == "policy_records" for p, _ in pool)
    assert decision["filter_applied"]


def test_gold_is_not_opened_before_all_conditions_validate(monkeypatch, tmp_path):
    import pytest

    from app.evaluation import dataset
    from experimental.u_10o0o.embedding_ablation import kure_followup_report

    reads = []

    def incomplete(*args):
        raise ValueError("one condition is incomplete")

    monkeypatch.setattr(kure_followup_report, "validate_saved", incomplete)
    monkeypatch.setattr(dataset, "load_references", lambda path: reads.append(path))
    with pytest.raises(ValueError, match="incomplete"):
        kure_followup_report.score(tmp_path, tmp_path)
    assert reads == []
