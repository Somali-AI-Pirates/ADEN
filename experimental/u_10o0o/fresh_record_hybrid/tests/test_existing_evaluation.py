from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experimental.u_10o0o.fresh_record_hybrid import existing_evaluation as evaluation


def test_prepare_freezes_question_only_rows_without_parsing_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    expected_counts = (30, 30, 140, 20, 40, 20)
    for (suite, question_path, reference_path), count in zip(
        evaluation.INPUTS, expected_counts, strict=True
    ):
        qpath = root / question_path
        qpath.parent.mkdir(parents=True, exist_ok=True)
        qpath.write_text(
            json.dumps(
                [
                    {
                        "id": f"{suite}-{index}",
                        "question": f"Question {index}",
                        "label": "excluded",
                    }
                    for index in range(count)
                ]
            ),
            encoding="utf-8",
        )
        rpath = root / reference_path
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text("GOLD CONTENT MUST NOT BE PARSED", encoding="utf-8")

    output = tmp_path / "prepared"
    manifest = evaluation.prepare(output, root=root)
    rows = [
        json.loads(line)
        for line in (output / "questions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert len(rows) == 280
    assert all(set(row) == {"id", "question"} for row in rows)
    assert manifest["question_count"] == 280
    assert all("GOLD CONTENT" not in json.dumps(row) for row in rows)
    assert "GOLD CONTENT" not in (output / "dataset-manifest.json").read_text()
    assert (
        hashlib.sha256((output / "questions.jsonl").read_bytes()).hexdigest()
        == manifest["questions_sha256"]
    )
    with pytest.raises(FileExistsError):
        evaluation.prepare(output, root=root)


def test_official_evidence_groups_are_and_within_group_page_alternatives() -> None:
    groups = [
        [{"book_id": "AS001", "page": 2}, {"book_id": "AS002", "page": 3}],
        [{"book_id": "AS004", "page": 7}],
    ]

    assert evaluation._official_coverage(groups, {"AS002:003", "AS004:007"}) == [
        True,
        True,
    ]
    assert evaluation._official_coverage(groups, {"AS002:003"}) == [True, False]


def test_category_requirements_preserve_primary_or_alternative_bundle_semantics() -> (
    None
):
    requirements = [
        {
            "primary": [{"book_id": "AS001", "page": 1, "quote": "Fee USD 100"}],
            "alternatives": [[{"book_id": "AS002", "page": 2, "quote": "Fee USD 100"}]],
        },
        {
            "primary": [
                {"book_id": "AS003", "page": 3, "quote": "Actual date"},
                {"book_id": "AS004", "page": 4, "quote": "Receipt confirmed"},
            ],
            "alternatives": [],
        },
    ]

    alt_and_other = {
        "AS002:002": "Fee  USD　100",
        "AS003:003": "Actual date",
        "AS004:004": "Receipt confirmed",
    }
    hits, primary_hits = evaluation._requirements_coverage(requirements, alt_and_other)
    assert hits == [True, True]
    assert primary_hits == [False, True]

    partial_second_bundle = {
        "AS001:001": "Fee USD 100",
        "AS003:003": "Actual date",
    }
    hits, _ = evaluation._requirements_coverage(requirements, partial_second_bundle)
    assert hits == [True, False]


def test_four_arm_result_gate_rejects_missing_or_changed_pair_inputs() -> None:
    question = {"id": "Q-1", "question": "Find the source."}
    question_hash = hashlib.sha256(question["question"].encode()).hexdigest()
    pair_values = {
        "bm25_top50": ["a"],
        "dense_top50": ["b"],
        "hybrid_unit_ids": ["a", "b"],
        "candidate_unit_ids": ["a", "b"],
        "candidate_source_ids": ["AS001:001"],
        "related_unit_ids": [],
        "reranked_unit_ids": ["a", "b"],
        "rerank_order": [{"unit_id": "a", "score": 1.0}],
        "rerank": {"pairs": 2},
        "relation": {"status": "not_found"},
        "new_related_unit_ids": [],
    }
    rows = []
    for condition in evaluation.CONDITIONS:
        rows.append(
            {
                "id": question["id"],
                "condition": condition,
                "question_sha256": question_hash,
                **pair_values,
            }
        )

    assert len(evaluation._validate_complete_results(rows, [question])) == 4
    with pytest.raises(ValueError, match="incomplete"):
        evaluation._validate_complete_results(rows[:-1], [question])

    changed = [dict(row) for row in rows]
    changed[1]["dense_top50"] = ["different"]
    with pytest.raises(ValueError, match="changed dense_top50"):
        evaluation._validate_complete_results(changed, [question])


def test_existing_dataset_aggregate_counts_match_documented_suites() -> None:
    rows = []

    def add(suite: str, count: int, scorable_count: int, prefix: str) -> None:
        for index in range(count):
            scoreable = index < scorable_count
            arm = {
                "candidate_complete": scoreable,
                "final_complete": scoreable,
                "candidate_fact_hits": int(scoreable),
                "final_fact_hits": int(scoreable),
                "candidate_fact_count": int(scoreable),
                "final_fact_count": int(scoreable),
                "candidate_primary_page_hits": None,
                "candidate_primary_page_count": None,
                "final_primary_page_hits": None,
                "final_primary_page_count": None,
                "candidate_referenced_page_hits": None,
                "candidate_referenced_page_count": None,
                "final_referenced_page_hits": None,
                "final_referenced_page_count": None,
                "timings_ms": {},
                "context_tokens": 1,
            }
            rows.append(
                {
                    "id": f"{prefix}-{index}",
                    "suite": suite,
                    "category": suite,
                    "scoreable": scoreable,
                    "negative_control": suite.endswith("negative_control"),
                    "arms": {
                        condition: dict(arm) for condition in evaluation.CONDITIONS
                    },
                }
            )

    add("official/development", 30, 29, "DEV")
    add("official/additional", 30, 29, "ADD")
    add("categories/scenarios", 140, 140, "SCEN")
    add("categories/negative_control", 20, 0, "NEG")
    add("categories/work_rules", 20, 20, "WORK")
    add("categories/semantic_queries", 40, 40, "SEM")

    summary = evaluation._summarize(rows)
    assert summary["official_60"]["questions"] == 60
    assert summary["official_scoreable"]["questions"] == 58
    assert summary["categories_160"]["questions"] == 160
    assert summary["categories_160"]["scoreable_questions"] == 140
    assert summary["categories_180"]["questions"] == 180
    assert summary["categories_180"]["scoreable_questions"] == 160
    assert summary["categories_220"]["questions"] == 220
    assert summary["categories_220"]["scoreable_questions"] == 200
    assert summary["category_positive_200"]["questions"] == 200
    assert summary["negative_controls_20"]["questions"] == 20


def test_model_metadata_uses_frozen_ids_revisions_and_runtime_dtype() -> None:
    policy = {
        "embedding_model": "embed/model",
        "embedding_revision": "e" * 40,
        "reranker_model": "rank/model",
        "reranker_revision": "r" * 40,
    }
    models = {
        "embedding": {
            "model_id": "embed/model",
            "revision": "e" * 40,
            "device": "cuda",
            "dtype": "torch.float32",
            "max_tokens": 2048,
            "normalize_embeddings": True,
        },
        "reranker": {
            "model_id": "rank/model",
            "revision": "r" * 40,
            "device": "cuda",
            "dtype": "torch.float32",
            "max_pair_tokens": 2048,
        },
    }

    evaluation._validate_model_metadata(models, policy)
    models["reranker"]["revision"] = "x" * 40
    with pytest.raises(ValueError, match="model metadata"):
        evaluation._validate_model_metadata(models, policy)
