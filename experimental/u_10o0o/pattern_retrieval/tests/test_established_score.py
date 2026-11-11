import pytest

from experimental.u_10o0o.pattern_retrieval import established_score as scorer


def _page(book, page, text):
    return {"book_id": book, "page": page, "text": text}


def _ref(book, page, quote):
    return {"book_id": book, "page": page, "quote": quote}


def test_official_page_scoring_uses_exact_page_and_group_or_and():
    reference = {
        "evidence_groups": [
            [_ref("BOOK-A", 2, "one"), _ref("BOOK-A", 3, "alternative")],
            [_ref("BOOK-B", 5, "two")],
        ]
    }

    scored = scorer.official_metrics(
        reference,
        [_page("BOOK-A", 3, "alternative"), _page("BOOK-B", 5, "two")],
    )
    wrong_page = scorer.official_metrics(reference, [_page("BOOK-A", 2, "one")])

    assert scored["matched_groups"] == 2
    assert scored["all_groups"] == 1
    assert scored["matched_pages"] == 2
    assert wrong_page["matched_groups"] == 1
    assert wrong_page["all_groups"] == 0


def test_category_scores_saved_final_context_and_alternative_evidence():
    requirement = {
        "primary": [_ref("BOOK-A", 1, "primary evidence")],
        "alternatives": [[_ref("BOOK-B", 8, "accepted alternative")]],
    }
    trace = {
        "arm": "baseline",
        "pool": [{"book_id": "BOOK-A", "page": 1}],
        "after_hg_pool": [{"book_id": "BOOK-B", "page": 8}],
        "ranked": [{"book_id": "BOOK-B", "page": 8}],
        "context_tokens": 13,
        "routes": {"hg": "found"},
    }
    # A validated lookup may return a source outside the initial pool. The actual
    # packed context is empty, so scoring must report that selection failed.
    row = scorer.score_item(
        {"id": "synthetic:1", "suite": "scenarios", "question_id": "1"},
        {"scenario": 1, "variant": "fixture", "evidence_requirements": [requirement]},
        trace,
        {"pages": []},
        {
            ("BOOK-A", 1): _page("BOOK-A", 1, "primary evidence"),
            ("BOOK-B", 8): _page("BOOK-B", 8, "accepted alternative"),
        },
    )

    assert row["lookup_added_final_pages"] == 0
    assert row["stages"]["available"]["all_facts"] == 1
    assert row["stages"]["final"]["all_facts"] == 0
    assert row["failure_stage"] == "selection_or_budget"

    row["id"] = "synthetic:2"
    row = scorer.score_item(
        {"id": "synthetic:2", "suite": "scenarios", "question_id": "2"},
        {"scenario": 1, "variant": "fixture", "evidence_requirements": [requirement]},
        trace,
        {"pages": [_page("BOOK-B", 8, "accepted alternative")]},
        {
            ("BOOK-A", 1): _page("BOOK-A", 1, "primary evidence"),
            ("BOOK-B", 8): _page("BOOK-B", 8, "accepted alternative"),
        },
    )
    assert row["stages"]["final"]["all_facts"] == 1
    assert row["stages"]["final"]["alternative_only_hits"] == 1


def test_negative_controls_are_unscored_for_retrieval_success():
    row = scorer.score_item(
        {"id": "negative:1", "suite": "negative_control", "question_id": "1"},
        {"variant": "out_of_scope"},
        {"arm": "A", "pool": [], "ranked": [], "context_tokens": 0},
        {"pages": [_page("BOOK-X", 4, "unrelated")]},
        {("BOOK-X", 4): _page("BOOK-X", 4, "unrelated")},
    )

    assert row["scored"] is False
    assert row["complete_metric"] is None
    assert row["returned_pages"] == 1
    assert row["failure_stage"] == "not_scored"


def test_incomplete_pair_is_rejected_before_reference_open(tmp_path, monkeypatch):
    a_dir = tmp_path / "A"
    baseline_dir = tmp_path / "baseline"
    a_dir.mkdir()
    baseline_dir.mkdir()
    (a_dir / "manifest.json").write_text('{"status":"running"}', encoding="utf-8")
    (baseline_dir / "manifest.json").write_text(
        '{"status":"complete"}', encoding="utf-8"
    )
    opened = []

    def unexpected_reference_read(*args, **kwargs):
        opened.append(args)
        raise AssertionError("reference was opened before result validation")

    monkeypatch.setattr(scorer, "load_references", unexpected_reference_read)
    with pytest.raises(ValueError, match="All 280 retrievals"):
        scorer.score(a_dir, baseline_dir, tmp_path / "audit.json", tmp_path / "scores")

    assert opened == []
