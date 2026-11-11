import json
from types import SimpleNamespace

import pytest

from experimental.u_10o0o.advanced_luna_metrics import (
    _gold,
    _verified_prediction,
    score_luna,
)


@pytest.fixture
def lookup():
    return SimpleNamespace(
        cases={"case-a": {}, "case-b": {}},
        page_roles={
            ("book-a", 1): ("case-a", "Role A"),
            ("book-b", 1): ("case-b", "Role B"),
        },
    )


def test_gold_keeps_known_abstain_target_and_suppresses_clarify(lookup):
    citation = {"book_id": "book-a", "page": 1, "quote": "grounded excerpt"}
    known_abstain = _gold(
        {
            "expected_action": "abstain",
            "case_keys": ["case-a"],
            "evidence_requirements": [{"primary": [citation]}],
        },
        lookup,
    )
    ambiguous = _gold(
        {
            "expected_action": "clarify",
            "case_keys": ["case-a", "case-b"],
            "evidence_requirements": [{"primary": [citation]}],
        },
        lookup,
    )
    document_only = _gold(
        {"expected_action": "answer", "evidence_requirements": []}, lookup
    )

    assert known_abstain == {
        "cases": {"case-a"},
        "roles": {("case-a", "Role A")},
        "label": "target",
    }
    assert ambiguous == {"cases": set(), "roles": set(), "label": "clarify"}
    assert document_only == {"cases": set(), "roles": set(), "label": "no_target"}


def test_only_verified_effective_targets_are_predictions(lookup):
    cases, roles = _verified_prediction(
        {
            "targets": [{"case": "case-a", "roles": ["Role A"]}],
            "model_targets": [{"case": "wrong"}],
        },
        lookup,
    )

    assert cases == {"case-a"}
    assert roles == {("case-a", "Role A")}
    with pytest.raises(ValueError, match="unverified case target"):
        _verified_prediction(
            {"targets": [{"case": "unknown", "roles": ["Role A"]}]}, lookup
        )
    with pytest.raises(ValueError, match="unverified case-role"):
        _verified_prediction(
            {"targets": [{"case": "case-a", "roles": ["Role B"]}]}, lookup
        )


def _context(*, include_quote=False):
    chunks = []
    if include_quote:
        chunk = SimpleNamespace(
            book_id="book-a", page=1, text="A grounded excerpt is present."
        )
        chunks.append(SimpleNamespace(chunk=chunk))
    return SimpleNamespace(final_chunks=chunks)


def test_score_luna_aggregates_targets_roles_ambiguity_and_fact_gains(tmp_path, lookup):
    citation = {"book_id": "book-a", "page": 1, "quote": "grounded excerpt"}
    questions = [
        {"id": "known", "question": "private question text", "split": "scenarios"},
        {"id": "ambiguous", "question": "private question text", "split": "scenarios"},
        {"id": "documents", "question": "private question text", "split": "scenarios"},
    ]
    references = [
        {
            "id": "known",
            "scenario": 1,
            "variant": "known",
            "expected_action": "abstain",
            "case_keys": ["case-a"],
            "evidence_groups": [[citation]],
            "evidence_requirements": [
                {"role": "generic", "primary": [citation], "alternatives": []}
            ],
        },
        {
            "id": "ambiguous",
            "scenario": 2,
            "variant": "ambiguous",
            "expected_action": "clarify",
            "question_type": "clarify",
            "case_keys": ["case-a", "case-b"],
            "evidence_groups": [],
            "evidence_requirements": [],
        },
        {
            "id": "documents",
            "scenario": 3,
            "variant": "documents",
            "expected_action": "answer",
            "evidence_groups": [],
            "evidence_requirements": [],
        },
    ]
    reference_path = tmp_path / "references.json"
    reference_path.write_text(json.dumps(references), encoding="utf-8")
    files = {"scenarios": (tmp_path / "questions.json", reference_path)}
    traces = {}
    retrievals = {}
    for question in questions:
        qid = question["id"]
        for arm in ("rules", "luna"):
            traces[qid, arm] = {
                "targets": [],
                "model_targets": [],
                "status": "unresolved",
                "provider_called": arm == "luna",
                "telemetry": {"latency_ms": 75.0, "input_tokens": 100}
                if arm == "luna"
                else {},
                "decision_and_pack_ms": 3.0,
            }
            retrievals[qid, arm] = ([], [], _context(), (), "")
    traces["known", "rules"]["targets"] = [{"case": "case-a", "roles": ["Role A"]}]
    traces["known", "rules"]["status"] = "verified"
    traces["known", "luna"]["targets"] = [{"case": "case-a", "roles": ["Role A"]}]
    traces["known", "luna"]["model_targets"] = [{"case": "case-a", "roles": ["Role A"]}]
    traces["known", "luna"]["status"] = "verified"
    traces["ambiguous", "luna"]["targets"] = [{"case": "case-a", "roles": ["Role A"]}]
    traces["ambiguous", "luna"]["status"] = "verified"
    retrievals["known", "luna"] = ([], [], _context(include_quote=True), (), "")

    summaries = score_luna(tmp_path, questions, files, retrievals, traces, lookup)
    luna = summaries["overall"]["all"]["luna"]
    saved = (tmp_path / "luna-scores.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in saved.splitlines()]

    assert luna["target"]["exact_on_target_questions"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert luna["role"]["exact_on_role_questions"]["value"] == 1.0
    assert luna["wrong_confirmation"]["clarify"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert luna["wrong_confirmation"]["questions_with_wrong_case"]["denominator"] == 2
    assert luna["facts"]["final"]["all_facts_complete"]["value"] == 1.0
    assert summaries["paired_final_facts"]["gains"] == 1
    assert rows[0]["arms"]["luna"]["target"]["matches_gold_targets"] is True
    assert "private question text" not in saved
    assert "grounded excerpt" not in saved
