"""Protect full-question inputs and actual-context evidence verification."""

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from experimental.u_10o0o import advanced_search
from experimental.u_10o0o.embedding_ablation import kure_resolution as run
from experimental.u_10o0o.embedding_ablation import kure_resolution_review as review
from experimental.u_10o0o.embedding_ablation.kure_v2 import maxsim


def test_long_query_cap_preserves_trained_short_query_floor():
    backbone = SimpleNamespace()
    run.configure_query([backbone], 128)
    assert backbone.query_length == 128
    assert backbone.query_expansion == {
        "strategy": "min",
        "length": 64,
        "attend": False,
    }
    run.configure_query([backbone], 64)
    assert backbone.query_expansion["strategy"] == "fixed"


def test_oversized_queries_fail_before_encoding():
    model = SimpleNamespace(
        prompts={"query": "[Q] "},
        tokenizer=lambda text, **kw: {"input_ids": range(130)},
    )
    with pytest.raises(ValueError, match="130 tokens"):
        run.query_lengths(model, [{"question": "길이가 너무 긴 질문"}], 128)
    assert run.query_lengths(model, [{"question": "기존 절단 대조군"}], 64) == [130]


def test_padding_variable_queries_does_not_add_similarity():
    query = torch.tensor([[[1.0, 0.0], [-1.0, 0.0]]])
    document = torch.tensor([[[0.5, 1.0], [0.25, 1.0]]])
    valid = torch.ones((1, 2), dtype=torch.bool)
    padded = torch.nn.functional.pad(query, (0, 0, 0, 3))
    torch.testing.assert_close(
        maxsim(query, document, valid), maxsim(padded, document, valid)
    )


def test_full_rank_keeps_reranker_fourth_and_restores_original_helper(monkeypatch):
    pages = [
        {"file_name": f"AS{i:03}.pdf", "page": 1, "text": "내용"} for i in range(5)
    ]
    pool = list(zip(pages, np.arange(5), strict=True))
    ranked = list(reversed(pool))
    original = advanced_search.retain_reranked_head
    observed = []

    def fake_pack(engine, q, arm, candidates, reranked, decision):
        observed.extend(advanced_search.retain_reranked_head(candidates, reranked))
        return {}, {}

    monkeypatch.setattr(run.common, "pack", fake_pack)
    engine = SimpleNamespace(
        reranker=SimpleNamespace(
            tokenizer=lambda *args, **kw: {"input_ids": [[0] * 700 for _ in pages]}
        )
    )
    trace, _ = run.pack(engine, {"question": "질문"}, "D", pool, ranked, {})
    assert observed == ranked
    assert trace["work"]["rerank_tokens"] == 3500
    assert trace["work"]["rerank_truncated"] == 0
    assert advanced_search.retain_reranked_head is original


def test_only_present_approved_scope_and_all_facts_can_pass():
    bundle = [
        {"book_id": "AS001", "page": 1, "quote": "기존 접수는 기존 경로"},
        {"book_id": "AS002", "page": 2, "quote": "신규 접수는 새 경로"},
    ]
    first = {"book_id": "AS001", "page": 1, "text": "기존 접수는\n기존 경로"}
    second = {"book_id": "AS002", "page": 2, "text": "신규 접수는 새 경로"}
    assert not review.supports(bundle, [first])
    assert review.supports(bundle, [first, second])
    assert not review.supports(bundle, [{**first, "book_id": "AS099"}, second])
    assert not review.supports([], [first])


def test_unapproved_related_page_is_not_an_automatic_pass():
    original = {
        "id": "test",
        "scored": True,
        "complete_metric": "all_facts",
        "stages": {"final": {"all_facts": 0}},
    }
    page = {"book_id": "AS001", "page": 1, "text": "미승인 금액은 배분하지 않는다."}
    verdict = review.semantic_result(
        original, {"pages": [page]}, {"ambiguous": {}, "rules": {}}
    )
    assert verdict["complete"] is False
    registry = {
        "ambiguous": {},
        "rules": {
            "test": {
                "alternatives": [
                    [
                        {
                            "book_id": "AS001",
                            "page": 1,
                            "quote": "미승인 금액은 배분하지 않는다.",
                        }
                    ]
                ]
            }
        },
    }
    assert (
        review.semantic_result(original, {"pages": [page]}, registry)["complete"]
        is True
    )
    registry["ambiguous"]["test"] = "적용 계약을 특정할 수 없음"
    assert (
        review.semantic_result(original, {"pages": [page]}, registry)["complete"]
        is None
    )


def test_references_are_not_opened_with_incomplete_retrieval(monkeypatch, tmp_path):
    called = []

    def reject(*args, **kwargs):
        raise ValueError("incomplete")

    monkeypatch.setattr(run, "check", reject)
    monkeypatch.setattr(review, "references", lambda _: called.append(True))
    with pytest.raises(ValueError, match="incomplete"):
        review.score(tmp_path, tmp_path)
    assert called == []
