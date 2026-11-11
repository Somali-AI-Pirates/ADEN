import json

import pytest

from experimental.u_10o0o import advanced_semantic_window_ab as semantic_ab
from experimental.u_10o0o.advanced_semantic_first_miss import (
    first_miss,
    nominated,
    stages_for,
)
from experimental.u_10o0o.pattern_retrieval.established_score import score_item


def test_nominated_uses_reranked_top_three_then_the_rrf20_tail():
    query = {
        "head20": [("AS099", 1)],
        "head40": [("AS001", 1), ("AS002", 1), ("AS003", 1), ("AS004", 1)],
        "rrf20": [("AS001", 1), ("AS003", 1), ("AS005", 1), ("AS006", 1)],
    }

    assert nominated(query) == [
        ("AS001", 1),
        ("AS002", 1),
        ("AS003", 1),
        ("AS005", 1),
        ("AS006", 1),
    ]


def test_nominated_uses_head20_when_the_archived_expansion_is_empty():
    query = {
        "head20": [("AS001", 1), ("AS002", 1), ("AS003", 1)],
        "head40": [],
        "rrf20": [("AS001", 1), ("AS004", 1)],
    }

    assert nominated(query) == [
        ("AS001", 1),
        ("AS002", 1),
        ("AS003", 1),
        ("AS004", 1),
    ]


def test_first_miss_reports_complete_requirement_before_unsupported_plan():
    coverage = {
        "retriever_union": True,
        "rrf20_union": True,
        "request_nomination_union": True,
        "merged20": True,
        "expanded20": True,
        "final8": True,
    }

    assert first_miss(coverage, "unsupported_dependency_or_set") == "complete"


def test_first_miss_keeps_unsupported_plan_for_uncovered_requirement():
    coverage = {
        "retriever_union": True,
        "rrf20_union": True,
        "request_nomination_union": False,
        "merged20": False,
        "expanded20": False,
        "final8": False,
    }

    assert first_miss(coverage, "unsupported_dependency_or_set") == "plan_fallback"


def test_stages_for_deduplicates_each_saved_request_and_preserves_rrf_stage():
    first, second = ("book", 1), ("book", 2)
    work = {
        "query_stats": [
            {
                "union": [first, second],
                "rrf20": [first],
                "head20": [second],
                "head40": [],
            },
            {
                "union": [second],
                "rrf20": [second],
                "head20": [],
                "head40": [first],
            },
        ]
    }
    trace = {
        "pool": [first, second],
        "expanded": [second],
        "packed": [second],
    }

    assert stages_for(work, trace) == {
        "retriever_union": [first, second],
        "rrf20_union": [first, second],
        "request_nomination_union": [second, first],
        "merged20": [first, second],
        "expanded20": [second],
        "final8": [second],
    }


def test_established_score_uses_the_saved_context_for_final_coverage():
    quote = "This exact wording exists only in the saved context."
    reference = {
        "variant": "synthetic",
        "evidence_requirements": [
            {
                "primary": [{"book_id": "AS001", "page": 2, "quote": quote}],
                "alternatives": [],
            }
        ],
    }
    source_pages = {
        ("AS001", 1): {
            "book_id": "AS001",
            "page": 1,
            "text": "Candidate text does not satisfy the requirement.",
        }
    }
    meta = {
        "suite": "semantic_queries",
        "question_id": "synthetic-1",
    }
    trace = {
        "arm": "control",
        "pool": [{"book_id": "AS001", "page": 1}],
        "ranked": [{"book_id": "AS001", "page": 1}],
        "context_tokens": 10,
    }
    context = {
        "pages": [{"book_id": "AS001", "page": 2, "text": f"Intro. {quote} Closing."}]
    }

    scored = score_item(meta, reference, trace, context, source_pages)

    assert scored["stages"]["final"]["all_facts"] == 1
    assert scored["stages"]["initial"]["all_facts"] == 0


def test_score_gate_requires_complete_retrieval_before_loading_references(
    tmp_path, monkeypatch
):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"status": "retrieval_running", "results": 639}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        semantic_ab,
        "load_references",
        lambda *_: pytest.fail("references opened before retrieval completed"),
    )

    with pytest.raises(
        ValueError, match="Save every retrieval before opening references"
    ):
        semantic_ab.score_saved(tmp_path, [], [])


def test_stage_audit_rejects_directly_resolved_role_pages():
    work = {"verified_tasks": 1, "query_stats": []}
    trace = {"requested_role_sources": [[("AS001", 1)]]}

    with pytest.raises(ValueError, match="search-only request steps"):
        stages_for(work, trace)
