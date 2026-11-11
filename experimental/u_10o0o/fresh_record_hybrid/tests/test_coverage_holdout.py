from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experimental.u_10o0o.fresh_record_hybrid import coverage_holdout as holdout
from experimental.u_10o0o.fresh_record_hybrid import relation_overlap, retrieval


@dataclass
class FakeEmbeddingUsage:
    input_tokens: int = 3
    truncated_tokens: int = 0

    @property
    def used_tokens(self) -> int:
        return self.input_tokens - self.truncated_tokens


@dataclass
class FakePairUsage:
    query_tokens: int = 2
    query_truncated_tokens: int = 0
    document_tokens: int = 4
    document_truncated_tokens: int = 0
    pair_tokens: int = 6
    pair_truncated_tokens: int = 0

    @property
    def truncated(self) -> bool:
        return self.pair_truncated_tokens > 0


class FakeEmbedder:
    query_prefix = ""

    def __init__(self) -> None:
        self.calls = 0

    def encode(
        self, texts: list[str], *, query: bool, batch_size: int
    ) -> tuple[np.ndarray, list[FakeEmbeddingUsage]]:
        assert query is True
        assert batch_size == 4
        self.calls += 1
        return np.asarray([[1.0, 0.0]], dtype=np.float32), [
            FakeEmbeddingUsage() for _ in texts
        ]


class FakeReranker:
    def __init__(self) -> None:
        self.calls = 0

    def score_pairs(
        self, pairs: list[tuple[str, str]], *, batch_size: int
    ) -> tuple[np.ndarray, list[FakePairUsage]]:
        assert batch_size == 8
        self.calls += 1
        scores = np.asarray(
            [float(len(pairs) - index) for index in range(len(pairs))],
            dtype=np.float32,
        )
        return scores, [FakePairUsage() for _ in pairs]


class CharacterEncoding:
    def encode(self, text: str, *, disallowed_special: tuple[str, ...]) -> list[int]:
        del disallowed_special
        return [ord(character) for character in text]

    def decode(self, tokens: list[int], *, errors: str = "strict") -> str:
        return "".join(chr(token) for token in tokens)


def make_context() -> holdout.SearchContext:
    pages = []
    page_map: dict[str, dict[str, Any]] = {}
    source_order: dict[str, int] = {}
    vectors = []
    for index in range(24):
        source_id = f"source-{index:02}"
        text = f"topic page {index} with enough source text"
        unit_id = f"A:{source_id}:raw"
        pages.append(
            retrieval.Unit(
                unit_id=unit_id,
                text=text,
                sources=(source_id,),
                corpus="A",
                kind="raw_page",
            )
        )
        page_map[source_id] = {
            "source_id": source_id,
            "text": text,
            "book_id": "synthetic",
            "page": index,
            "lines": [text],
        }
        source_order[source_id] = index
        vectors.append([float(24 - index), float(index + 1)])
    return holdout.build_search_context(
        pages,
        [],
        page_map,
        source_order,
        {"events": {}, "code_to_events": {}},
        np.asarray(vectors, dtype=np.float32),
        CharacterEncoding(),
    )


def test_holdout_input_accepts_only_id_question(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(
        json.dumps({"id": "new-1", "question": "Where is the new record?"}) + "\n",
        encoding="utf-8",
    )

    questions = holdout.load_holdout_questions(path)

    assert questions == [holdout.HoldoutQuestion("new-1", "Where is the new record?")]

    path.write_text(
        json.dumps(
            {
                "id": "new-1",
                "question": "Where is the new record?",
                "reference": "must not be accepted",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly id and question"):
        holdout.load_holdout_questions(path)


def test_freeze_manifest_pin_and_schema() -> None:
    path = holdout.PROJECT_ROOT / holdout.POLICY_FREEZE_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert holdout.sha256_path(path) == holdout.POLICY_FREEZE_SHA256
    holdout.validate_policy_freeze_manifest(manifest)


def test_four_arms_share_retrieval_and_preserve_pair_rankings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = make_context()
    embedder = FakeEmbedder()
    reranker = FakeReranker()
    rerank_order: list[str] = []
    original = retrieval._rerank_condition

    def track_rerank(state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        rerank_order.append(state.condition)
        return original(state, *args, **kwargs)

    monkeypatch.setattr(retrieval, "_rerank_condition", track_rerank)
    questions = [
        holdout.HoldoutQuestion(f"q{index}", f"topic request {index}")
        for index in range(1, 11)
    ]

    rows, timings = holdout.run_questions(questions, context, embedder, reranker)

    assert [row["condition"] for row in rows] == list(holdout.CONDITIONS) * 10
    assert rerank_order == [
        condition
        for index in range(10)
        for condition in (("E", "F") if index % 2 == 0 else ("F", "E"))
    ]
    assert embedder.calls == 10
    assert reranker.calls == 20
    assert timings["query_retrieval_wall_ms"] >= 0
    assert timings["added_selection_wall_ms"] >= 0
    progress = capsys.readouterr().out.strip()
    assert progress.startswith(
        "evaluation progress questions=10/10 results=40 elapsed_s="
    )
    assert "q10" not in progress
    assert "topic" not in progress

    for question_number in range(1, 11):
        question_id = f"q{question_number}"
        paired = {row["condition"]: row for row in rows if row["id"] == question_id}
        expected_quota = relation_overlap.quota_candidates(
            paired["E"]["bm25_top50"], paired["E"]["dense_top50"]
        )
        assert paired["F"]["hybrid_unit_ids"] == expected_quota
        assert paired["F_coverage"]["hybrid_unit_ids"] == expected_quota
        assert paired["F"]["candidate_policy"] == "F quota then E relation fusion"
        expected_order = ["E", "F"] if question_number % 2 == 1 else ["F", "E"]
        assert (
            paired["E"]["timing_attribution"]["rerank_execution_order"]
            == expected_order
        )
        assert (
            paired["E"]["relation"]["event_ids"] == paired["F"]["relation"]["event_ids"]
        )
        for base_name, coverage_name in (
            ("E", "E_coverage"),
            ("F", "F_coverage"),
        ):
            base = paired[base_name]
            coverage = paired[coverage_name]
            for key in (
                "candidate_unit_ids",
                "reranked_unit_ids",
                "rerank_order",
            ):
                assert coverage[key] == base[key]
            assert len(base["candidate_unit_ids"]) == 20
            assert len(coverage["final_sources"]) <= 8
            assert coverage["context_tokens"] <= 8000
            assert coverage["added_selection_timing_ms"]["new_model_calls"] == 0


def test_output_guard_refuses_an_existing_path(tmp_path: Path) -> None:
    parser_args = holdout.build_parser().parse_args(
        ["--questions", "questions.jsonl", "--questions-sha256", "a" * 64]
    )
    assert parser_args.evaluation_kind == "existing_regression"
    assert holdout.resolve_output_dir(tmp_path, "existing_regression", None) == (
        tmp_path / holdout.DEFAULT_EXISTING_OUTPUT_DIR
    )
    assert holdout.resolve_output_dir(tmp_path, "new_holdout", None) == (
        tmp_path / holdout.DEFAULT_NEW_HOLDOUT_DIR
    )
    with pytest.raises(ValueError, match="dedicated evaluation root"):
        holdout.resolve_output_dir(tmp_path, "existing_regression", "outside/run")

    fresh = tmp_path / "fresh-run"
    assert holdout.ensure_fresh_output_dir(fresh) == fresh

    existing = tmp_path / "existing-run"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing overwrite"):
        holdout.ensure_fresh_output_dir(existing)
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_output_bundle_commits_complete_results_and_manifest(tmp_path: Path) -> None:
    output = tmp_path / "holdout-run"
    rows = [{"id": "q1", "condition": "E"}]

    committed = holdout.write_output_bundle(output, rows, {"format": "synthetic"})

    results_path = committed / "results.jsonl"
    manifest_path = committed / "run-manifest.json"
    assert results_path.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["results_rows"] == 1
    assert manifest["results_sha256"] == holdout.sha256_path(results_path)
    saved_row = json.loads(results_path.read_text(encoding="utf-8"))
    assert saved_row == rows[0]

    with pytest.raises(FileExistsError, match="refusing overwrite"):
        holdout.write_output_bundle(output, [{"id": "q2"}], {"format": "other"})
    assert json.loads(results_path.read_text(encoding="utf-8")) == rows[0]
