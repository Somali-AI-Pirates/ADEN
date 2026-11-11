import hashlib
import json
import math
import sqlite3
from pathlib import Path

import pytest

from experimental.u_10o0o.pattern_retrieval.evaluation import score_run
from experimental.u_10o0o.pattern_retrieval.reporting import (
    TIMING_STAGES,
    make_report,
)

CATEGORIES = ("exact", "variant", "paraphrase", "comparison", "mixed", "ambiguous")
ARMS = ("A", "B", "C")
PAGES = {
    "source-alpha": ("BOOK-A", 1, "Required proof Alpha"),
    "source-beta": ("BOOK-B", 1, "Required proof Beta"),
    "source-gamma": ("BOOK-C", 1, "Required proof Gamma"),
}
UNITS = {
    "unit-alpha": "source-alpha",
    "unit-beta": "source-beta",
    "unit-gamma": "source-gamma",
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _runtime_files() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parents[4]
    path = Path("experimental/u_10o0o/pattern_retrieval/common.py")
    return [{"path": path.as_posix(), "sha256": _file_sha(root / path)}]


def _object_sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha(encoded)


def _refresh_retrieval_hashes(retrieval_dir: Path) -> None:
    manifest = _read_json(retrieval_dir / "manifest.json")
    manifest["traces_sha256"] = _file_sha(retrieval_dir / "traces.jsonl")
    manifest["contexts_sha256"] = _file_sha(retrieval_dir / "contexts.jsonl")
    manifest["trace_count"] = len(
        (retrieval_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    )
    _write_json(retrieval_dir / "manifest.json", manifest)


def _fixture(tmp_path: Path) -> dict[str, Path | list[str]]:
    index_dir = tmp_path / "index"
    evaluation_dir = tmp_path / "evaluation"
    retrieval_dir = tmp_path / "retrieval"
    index_dir.mkdir(parents=True)
    evaluation_dir.mkdir()
    retrieval_dir.mkdir()

    database = index_dir / "corpus.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE pages (
                source_id TEXT PRIMARY KEY, book_id TEXT, file_name TEXT,
                page INTEGER, text TEXT, heading TEXT, pdf_sha256 TEXT
            );
            CREATE TABLE units (
                unit_id TEXT PRIMARY KEY, arm TEXT, kind TEXT, text TEXT, scope_key TEXT
            );
            CREATE TABLE occurrences (
                unit_id TEXT, source_id TEXT, line_start INTEGER, line_end INTEGER,
                case_id TEXT, role TEXT, identifiers_json TEXT
            );
            CREATE TABLE code_links (
                code_norm TEXT, code_type TEXT, case_id TEXT, source_id TEXT,
                line_start INTEGER, line_end INTEGER, relation TEXT
            );
            """
        )
        for source_id, (book_id, page, text) in PAGES.items():
            connection.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, book_id, f"{book_id}.pdf", page, text, "Fixture", "0" * 64),
            )
        for unit_id, source_id in UNITS.items():
            arm = "A" if unit_id == "unit-alpha" else "B"
            connection.execute(
                "INSERT INTO units VALUES (?, ?, ?, ?, ?)",
                (unit_id, arm, "fixture", PAGES[source_id][2], "fixture"),
            )
            connection.execute(
                "INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unit_id, source_id, 1, 1, None, None, "[]"),
            )

    corpus_hash = _file_sha(database)
    _write_json(
        index_dir / "corpus-manifest.json",
        {
            "schema_version": 1,
            "database": "corpus.sqlite3",
            "source_count": 1,
            "counts": {"pages": 3, "units": 3, "occurrences": 3, "code_links": 0},
            "integrity": {
                "sqlite": "ok",
                "foreign_keys": "ok",
                "page_units": True,
                "body_block_coverage": True,
                "code_links": True,
            },
            "documents": [
                {"file_name": "private-source-name.pdf", "pdf_sha256": "f" * 64}
            ],
        },
    )
    vectors_path = index_dir / "vectors.npy"
    vectors_path.write_bytes(b"synthetic vector fixture")
    config = {"fixture": True}
    vectors_manifest = {
        "status": "complete",
        "identity": {"corpus_sha256": corpus_hash},
        "config": config,
        "dimension": 3,
        "vectors_sha256": _file_sha(vectors_path),
        "statistics": {
            "embedding_seconds": 12.5,
            "raw_tokens": 1000,
            "input_tokens": 900,
            "truncated_units": 1,
            "encoded_units": 3,
            "reused_identical_texts": 0,
        },
    }
    _write_json(index_dir / "vectors-manifest.json", vectors_manifest)

    questions = []
    references = {}
    categories: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for category in CATEGORIES:
        for number in range(5):
            question_id = f"{category}-{number}"
            categories[category].append(question_id)
            questions.append(
                {
                    "id": question_id,
                    "question": f"Synthetic question {category} {number}",
                }
            )
            if category == "ambiguous":
                requirements = []
                expected_cases = []
                resolution = "ambiguous"
            elif question_id == "exact-0":
                alpha = {
                    "book_id": "BOOK-A",
                    "page": 1,
                    "quote": PAGES["source-alpha"][2],
                }
                beta = {
                    "book_id": "BOOK-B",
                    "page": 1,
                    "quote": PAGES["source-beta"][2],
                }
                gamma = {
                    "book_id": "BOOK-C",
                    "page": 1,
                    "quote": PAGES["source-gamma"][2],
                }
                requirements = [
                    {"primary": [alpha], "alternatives": [[beta, gamma]]},
                    {"primary": [beta, gamma], "alternatives": [[alpha, beta]]},
                ]
                expected_cases = [f"case-{question_id}"]
                resolution = "unique"
            else:
                requirements = [
                    {
                        "primary": [
                            {
                                "book_id": "BOOK-B",
                                "page": 1,
                                "quote": PAGES["source-beta"][2],
                            }
                        ],
                        "alternatives": [],
                    }
                ]
                expected_cases = [f"case-{question_id}"]
                resolution = "unique"
            references[question_id] = {
                "split": "validation",
                "category": category,
                "requirements": requirements,
                "expected_cases": expected_cases,
                "resolution_expected": resolution,
            }

    questions_path = evaluation_dir / "questions.json"
    references_path = evaluation_dir / "references.json"
    _write_json(questions_path, questions)
    _write_json(references_path, references)
    _write_json(
        evaluation_dir / "manifest.json",
        {
            "questions_sha256": _file_sha(questions_path),
            "references_sha256": _file_sha(references_path),
            "split_ids": {
                "development": [],
                "validation": [item["id"] for item in questions],
            },
            "categories": categories,
            "timing_ids": [item["id"] for item in questions],
        },
    )

    traces = []
    contexts = []
    for question in questions:
        question_id = question["id"]
        category = references[question_id]["category"]
        for arm in ARMS:
            if question_id == "exact-0":
                pool_units = (
                    ["unit-alpha"] if arm == "A" else ["unit-alpha", "unit-beta"]
                )
                ranked_units = pool_units if arm == "A" else ["unit-beta", "unit-alpha"]
            else:
                pool_units = ["unit-beta"]
                ranked_units = pool_units
            pool = [
                {
                    "unit_id": unit_id,
                    "source_id": UNITS[unit_id],
                    "book_id": PAGES[UNITS[unit_id]][0],
                    "page": PAGES[UNITS[unit_id]][1],
                    "score": float(len(pool_units) - index),
                }
                for index, unit_id in enumerate(pool_units)
            ]
            ranked_by_unit = {item["unit_id"]: item for item in pool}
            ranked = [ranked_by_unit[unit_id] for unit_id in ranked_units]
            final_sources = [item["source_id"] for item in ranked[:2]]
            final_pages = [
                {
                    "source_id": source_id,
                    "book_id": PAGES[source_id][0],
                    "page": PAGES[source_id][1],
                }
                for source_id in final_sources
            ]
            if arm != "C":
                relation = {
                    "status": "not_applicable",
                    "cases": [],
                    "matched_codes": [],
                    "proposed_units": [],
                }
            elif category == "ambiguous" and question_id == "ambiguous-0":
                relation = {
                    "status": "unique",
                    "cases": ["wrong-case"],
                    "matched_codes": [],
                    "proposed_units": [],
                }
            elif category == "ambiguous":
                relation = {
                    "status": "ambiguous",
                    "cases": [],
                    "matched_codes": [],
                    "proposed_units": [],
                }
            else:
                relation = {
                    "status": "unique",
                    "cases": references[question_id]["expected_cases"],
                    "matched_codes": [],
                    "proposed_units": [],
                }
            traces.append(
                {
                    "id": question_id,
                    "arm": arm,
                    "question_sha256": _sha(question["question"].encode("utf-8")),
                    "pool": pool,
                    "ranked": ranked,
                    "final_pages": final_pages,
                    "context_sha256": "a" * 64,
                    "context_tokens": 10,
                    "excluded_sources": [],
                    "relation": relation,
                    "timing_ms": {stage: 1.0 for stage in TIMING_STAGES},
                    "work": {
                        "rerank_pairs": len(pool),
                        "rerank_tokens": 10,
                        "rerank_raw_tokens": 10,
                        "rerank_truncated": 0,
                    },
                    "relation_displaced_units": [],
                }
            )
            contexts.append(
                {
                    "id": question_id,
                    "arm": arm,
                    "pages": [
                        {
                            "book_id": PAGES[source_id][0],
                            "page": PAGES[source_id][1],
                            "text": PAGES[source_id][2],
                        }
                        for source_id in final_sources
                    ],
                }
            )
    _write_jsonl(retrieval_dir / "traces.jsonl", traces)
    _write_jsonl(retrieval_dir / "contexts.jsonl", contexts)

    runtime_files = _runtime_files()
    model_assets = {
        "embedding": [{"path": "config.json", "sha256": "1" * 64}],
        "reranker": [{"path": "config.json", "sha256": "2" * 64}],
    }
    retrieval_manifest = {
        "status": "complete",
        "questions_sha256": _file_sha(questions_path),
        "corpus_sha256": corpus_hash,
        "vectors_sha256": _file_sha(vectors_path),
        "vectors_manifest_sha256": _file_sha(index_dir / "vectors-manifest.json"),
        "config": config,
        "runtime_files": runtime_files,
        "code_sha256": _object_sha(runtime_files),
        "model_assets": model_assets,
        "question_ids": [item["id"] for item in questions],
        "arms": list(ARMS),
        "trace_count": len(traces),
        "traces_sha256": _file_sha(retrieval_dir / "traces.jsonl"),
        "contexts_sha256": _file_sha(retrieval_dir / "contexts.jsonl"),
    }
    _write_json(retrieval_dir / "manifest.json", retrieval_manifest)
    return {
        "index_dir": index_dir,
        "evaluation_dir": evaluation_dir,
        "retrieval_dir": retrieval_dir,
        "ids": [item["id"] for item in questions],
        "model_assets": model_assets,
        "runtime_files": runtime_files,
    }


def test_score_alternative_groups_source_quotes_and_metrics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output_dir = tmp_path / "scored"
    result = score_run(
        fixture["index_dir"],
        fixture["retrieval_dir"],
        fixture["evaluation_dir"],
        output_dir,
    )
    exact = next(item for item in result["questions"] if item["id"] == "exact-0")
    assert exact["arms"]["A"]["candidate_facts"] == 1
    assert exact["arms"]["A"]["candidate_requirements"] == 2
    assert exact["arms"]["A"]["candidate_complete"] is False
    assert exact["arms"]["A"]["first_missing_stage"] == "candidate_evidence"
    assert exact["arms"]["B"]["candidate_facts"] == 2
    assert exact["arms"]["B"]["candidate_complete"] is True
    assert exact["arms"]["B"]["first_missing_stage"] is None
    assert exact["arms"]["B"]["recall20"] == pytest.approx(2 / 3)
    assert exact["arms"]["B"]["recall8"] == pytest.approx(2 / 3)
    assert exact["arms"]["B"]["mrr8"] == 1
    ideal_dcg = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert exact["arms"]["B"]["ndcg8"] == pytest.approx(
        (1 + 1 / math.log2(3)) / ideal_dcg
    )
    assert result["summary"]["performance"]["n"] == 25
    assert result["summary"]["ambiguous"]["n"] == 5
    assert result["summary"]["performance"]["arms"]["C"]["target_accuracy"] == 1
    assert result["summary"]["ambiguous"]["arms"]["C"][
        "ambiguous_false_confirmation_rate"
    ] == pytest.approx(1 / 5)
    assert result["summary"]["index"]["unit_counts"] == {"A": 1, "B": 2}
    saved = (output_dir / "scores.json").read_text(encoding="utf-8")
    assert "Synthetic question exact 0" not in saved
    assert "Required proof Alpha" not in saved
    assert "private-source-name.pdf" not in saved


def test_reference_quote_must_match_its_own_source_page(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    references_path = Path(fixture["evaluation_dir"]) / "references.json"
    references = _read_json(references_path)
    references["exact-0"]["requirements"][0]["primary"][0]["quote"] = (
        "Required proof Beta"
    )
    _write_json(references_path, references)
    manifest_path = Path(fixture["evaluation_dir"]) / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["references_sha256"] = _file_sha(references_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="같은 source/page"):
        score_run(
            fixture["index_dir"],
            fixture["retrieval_dir"],
            fixture["evaluation_dir"],
            tmp_path / "scored",
        )


def test_rejects_out_of_scope_trace_source_and_reports_missing_stage(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "outside")
    retrieval_dir = Path(fixture["retrieval_dir"])
    trace_path = retrieval_dir / "traces.jsonl"
    traces = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    trace = next(
        item for item in traces if item["id"] == "exact-0" and item["arm"] == "A"
    )
    trace["pool"][0]["source_id"] = "outside-corpus"
    _write_jsonl(trace_path, traces)
    _refresh_retrieval_hashes(retrieval_dir)
    with pytest.raises(ValueError, match="corpus 밖 source_id"):
        score_run(
            fixture["index_dir"],
            retrieval_dir,
            fixture["evaluation_dir"],
            tmp_path / "outside-scored",
        )

    fixture = _fixture(tmp_path / "missing")
    retrieval_dir = Path(fixture["retrieval_dir"])
    trace_path = retrieval_dir / "traces.jsonl"
    traces = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    trace = next(
        item for item in traces if item["id"] == "exact-0" and item["arm"] == "A"
    )
    del trace["ranked"]
    _write_jsonl(trace_path, traces)
    _refresh_retrieval_hashes(retrieval_dir)
    result = score_run(
        fixture["index_dir"],
        retrieval_dir,
        fixture["evaluation_dir"],
        tmp_path / "missing-scored",
    )
    exact = next(item for item in result["questions"] if item["id"] == "exact-0")
    assert exact["arms"]["A"]["missing_stages"] == ["ranked"]
    assert exact["arms"]["A"]["mrr8"] == 1


def test_final_rank_metrics_exclude_ranked_pages_dropped_by_context_budget(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    retrieval_dir = Path(fixture["retrieval_dir"])
    trace_path = retrieval_dir / "traces.jsonl"
    traces = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    trace = next(
        item for item in traces if item["id"] == "exact-0" and item["arm"] == "B"
    )
    gamma = {
        "unit_id": "unit-gamma",
        "source_id": "source-gamma",
        "book_id": "BOOK-C",
        "page": 1,
        "score": 3.0,
    }
    trace["pool"].append(gamma)
    by_unit = {item["unit_id"]: item for item in trace["pool"]}
    trace["ranked"] = [
        by_unit["unit-gamma"],
        by_unit["unit-beta"],
        by_unit["unit-alpha"],
    ]
    _write_jsonl(trace_path, traces)
    _refresh_retrieval_hashes(retrieval_dir)

    result = score_run(
        fixture["index_dir"],
        retrieval_dir,
        fixture["evaluation_dir"],
        tmp_path / "scored",
    )
    exact = next(item for item in result["questions"] if item["id"] == "exact-0")
    expected_final_ndcg = (1 + 1 / math.log2(3)) / (
        1 + 1 / math.log2(3) + 1 / math.log2(4)
    )
    assert exact["arms"]["B"]["recall20"] == 1
    assert exact["arms"]["B"]["mrr8"] == 1
    assert exact["arms"]["B"]["ndcg8"] == pytest.approx(expected_final_ndcg)


def test_abstention_reason_is_preserved_and_scored_separately(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    retrieval_dir = Path(fixture["retrieval_dir"])
    trace_path = retrieval_dir / "traces.jsonl"
    traces = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    trace = next(
        item for item in traces if item["id"] == "ambiguous-1" and item["arm"] == "C"
    )
    trace["relation"]["status"] = "unknown_code"
    _write_jsonl(trace_path, traces)
    _refresh_retrieval_hashes(retrieval_dir)

    result = score_run(
        fixture["index_dir"],
        retrieval_dir,
        fixture["evaluation_dir"],
        tmp_path / "scored",
    )
    ambiguous = next(
        item for item in result["questions"] if item["id"] == "ambiguous-1"
    )
    scored = ambiguous["arms"]["C"]
    assert scored["observed_resolution"] == "unknown_code"
    assert scored["resolution_exact_match"] is False
    assert scored["abstention_interpretation_correct"] is True
    assert scored["resolution_status_correct"] is True


def test_report_checks_timing_and_omits_private_trace_values(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    scored_dir = tmp_path / "scored"
    scores = score_run(
        fixture["index_dir"],
        fixture["retrieval_dir"],
        fixture["evaluation_dir"],
        scored_dir,
    )
    timing_dir = tmp_path / "timing"
    timing_dir.mkdir()
    ids = fixture["ids"]
    traces = [
        {
            "id": question_id,
            "arm": arm,
            "repeat": repeat,
            "device": "cpu",
            "timing_ms": {stage: float(repeat) for stage in TIMING_STAGES},
        }
        for question_id in ids
        for arm in ARMS
        for repeat in (1, 2, 3)
    ]
    trace_path = timing_dir / "traces.jsonl"
    _write_jsonl(trace_path, traces)
    summary = {
        "timing_ids": ids,
        "arms": {
            arm: {
                stage: {"count": 90, "p50_ms": 2.0, "p95_ms": 3.0}
                for stage in TIMING_STAGES
            }
            for arm in ARMS
        },
        "gpu_cpu_context_differences": [
            {
                "id": "ambiguous-0",
                "arm": "C",
                "repeat": 1,
                "gpu_sha": "3" * 64,
                "cpu_sha": "4" * 64,
                "same_final_sources": True,
            }
        ],
    }
    _write_json(timing_dir / "summary.json", summary)
    runtime_files = fixture["runtime_files"]
    inputs = scores["inputs"]
    _write_json(
        timing_dir / "manifest.json",
        {
            "status": "complete",
            "device": "cpu",
            "threads": 4,
            "repeats": 3,
            "arms": list(ARMS),
            "timing_ids": ids,
            "questions_sha256": inputs["questions_sha256"],
            "corpus_sha256": inputs["corpus_sha256"],
            "evaluation_manifest_sha256": inputs["evaluation_manifest_sha256"],
            "gpu_manifest_sha256": inputs["retrieval_manifest_sha256"],
            "vectors_manifest_sha256": inputs["vectors_manifest_sha256"],
            "vectors_sha256": inputs["vectors_sha256"],
            "model_assets": fixture["model_assets"],
            "runtime_files": runtime_files,
            "code_sha256": inputs["code_sha256"],
            "trace_count": len(traces),
            "traces_sha256": _file_sha(trace_path),
        },
    )

    report_dir = tmp_path / "report"
    report = make_report(scored_dir, timing_dir, report_dir)
    exact = next(item for item in report["questions"] if item["id"] == "exact-0")
    assert exact["A_to_B"] == {"status": "improved", "final_facts_delta": 1}
    assert report["timing"]["arms"]["A"]["total"]["p50_ms"] == 2
    assert report["gpu_cpu_context_differences"] == {
        "count": 1,
        "same_final_sources": 1,
        "different_final_sources": 0,
        "samples": [
            {"id": "ambiguous-0", "arm": "C", "repeat": 1, "same_final_sources": True}
        ],
    }
    rendered = (report_dir / "report.md").read_text(encoding="utf-8")
    serialized = (report_dir / "report.json").read_text(encoding="utf-8")
    for private_value in (
        "Synthetic question exact 0",
        "Required proof Alpha",
        "case-exact-0",
        "source-alpha",
        "private-source-name.pdf",
        "3" * 64,
    ):
        assert private_value not in rendered
        assert private_value not in serialized
    assert "A=1, B=2" in rendered
    assert "raw input tokens=1,000" in rendered
    assert "false positive" in rendered


def test_incomplete_run_is_rejected_before_reference_answers_are_opened(
    tmp_path, monkeypatch
):
    from experimental.u_10o0o.pattern_retrieval import evaluation

    fixture = _fixture(tmp_path)
    manifest_path = fixture["retrieval_dir"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["question_ids"] = manifest["question_ids"][:-1]
    _write_json(manifest_path, manifest)

    def must_not_load(_):
        pytest.fail("Reference answers were read before retrieval was complete.")

    monkeypatch.setattr(evaluation, "_load_evaluation", must_not_load)
    with pytest.raises(ValueError, match="모든 입력 질문"):
        score_run(
            fixture["index_dir"],
            fixture["retrieval_dir"],
            fixture["evaluation_dir"],
            tmp_path / "scored",
        )


def test_questions_without_reference_pages_have_no_ranking_metric(tmp_path):
    fixture = _fixture(tmp_path)
    scores = score_run(
        fixture["index_dir"],
        fixture["retrieval_dir"],
        fixture["evaluation_dir"],
        tmp_path / "scored",
    )
    row = next(q for q in scores["questions"] if q["id"] == "ambiguous-0")
    for metrics in row["arms"].values():
        assert metrics["mrr8"] is None
        assert metrics["ndcg8"] is None
        assert metrics["final_complete"] is None
