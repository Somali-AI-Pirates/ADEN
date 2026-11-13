from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation import benchmark
from app.evaluation.benchmark import (
    FINAL_BENCHMARK_EVIDENCE_SIZE,
    FINAL_BENCHMARK_NO_EVIDENCE_IDS,
    FINAL_BENCHMARK_SIZE,
    load_final_benchmark,
    summarize_benchmark,
    write_benchmark_report,
)
from app.evaluation.calibration import (
    HISTORICAL_CASE_IDS,
    load_local_historical_cases,
    load_synthetic_cases,
    summarize_verdicts,
)


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_FIXTURE = ROOT / "tests/fixtures/faithfulness_calibration_synthetic.json"


@pytest.fixture
def benchmark_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Exercise the complete inventory shape without the local evaluation dataset."""
    groups = [
        ("AS-DEV", 30, {}),
        ("AS-ADD", 30, {}),
        *[
            (f"AS-S0{number}-", 20, {
                "scenario": number,
                "scenario_name": benchmark.SCENARIO_NAMES[number],
                "question_type": "synthetic question type",
            })
            for number in range(1, 8)
        ],
        ("AS-S08-", 20, {"scenario": 8}),
        ("AS-WR-", 20, {"scenario": "work_rules", "topic": "synthetic topic"}),
        ("S", 20, {}),
        ("M", 20, {}),
    ]
    questions = []
    references = []
    for prefix, count, metadata in groups:
        for number in range(1, count + 1):
            question_id = f"{prefix}{number:02}"
            questions.append({"id": question_id, "question": f"Synthetic question {question_id}?"})
            reference = {
                "id": question_id,
                "reference_answer": "Synthetic reference.",
                "evidence_groups": [] if question_id in FINAL_BENCHMARK_NO_EVIDENCE_IDS else [
                    [{"book_id": "SYNTHETIC", "page": 1}]
                ],
                **metadata,
            }
            if prefix == "AS-S08-":
                reference["variant"] = "unrelated" if number <= 10 else "near_domain"
            if prefix == "AS-WR-":
                reference["variant"] = "procedure" if number <= 10 else "applicability"
            references.append(reference)

    questions_path = tmp_path / "questions.json"
    references_path = tmp_path / "references.json"
    tasks_path = tmp_path / "security_tasks.json"
    questions_path.write_text(json.dumps(questions), encoding="utf-8")
    references_path.write_text(json.dumps(references), encoding="utf-8")
    tasks_path.write_text(json.dumps([
        {"id": f"security-{row['id']}", "question_id": row["id"], "question": row["question"]}
        for row in questions
        if row["id"] in {"AS-DEV14", "AS-ADD13"}
    ]), encoding="utf-8")
    load_links = benchmark.load_security_task_links
    monkeypatch.setattr(
        benchmark, "load_security_task_links", lambda inventory: load_links(inventory, tasks_path)
    )
    return questions_path, references_path


def test_final_benchmark_has_one_canonical_280_question_inventory(benchmark_files: tuple[Path, Path]) -> None:
    inventory = load_final_benchmark(*benchmark_files)

    assert len(inventory.questions) == FINAL_BENCHMARK_SIZE == 280
    assert sum(item.has_evidence for item in inventory.questions) == (
        FINAL_BENCHMARK_EVIDENCE_SIZE
    )
    assert {
        item.question_id for item in inventory.questions if not item.has_evidence
    } == FINAL_BENCHMARK_NO_EVIDENCE_IDS
    assert {item.category for item in inventory.questions} >= {
        "development",
        "additional",
        "scenario_1",
        "scenario_7",
        "negative_control",
        "work_rules_procedure",
        "work_rules_applicability",
        "custom_s",
        "custom_m",
    }


def test_final_benchmark_category_groups_do_not_double_count_items(benchmark_files: tuple[Path, Path]) -> None:
    inventory = load_final_benchmark(*benchmark_files)
    groups = inventory.groups()
    primary = {
        key: value
        for key, value in groups.items()
        if not key.startswith("question_type:")
    }

    assert sum(len(group) for group in primary.values()) == len(inventory.questions)
    assert len(
        {question.question_id for group in primary.values() for question in group}
    ) == len(inventory.questions)
    assert len(groups["scenario_1"]) == 20
    assert len(groups["custom_s"]) == len(groups["custom_m"]) == 20


def test_final_benchmark_rejects_id_mismatch(benchmark_files: tuple[Path, Path]) -> None:
    questions_path, references_path = benchmark_files
    references = json.loads(references_path.read_text(encoding="utf-8"))
    references.pop()
    references_path.write_text(json.dumps(references), encoding="utf-8")

    with pytest.raises(ValueError, match="id가 일치하지 않습니다"):
        load_final_benchmark(questions_path, references_path)


def test_benchmark_summary_reports_metrics_micro_macro_and_first_failure(
    benchmark_files: tuple[Path, Path],
) -> None:
    inventory = load_final_benchmark(*benchmark_files)
    runs = [
        {
            "question_id": "AS-DEV01",
            "status": "answered",
            "latency_ms": 100,
            "usage": {"input_tokens": 20, "output_tokens": 5, "status": "complete"},
        },
        {
            "question_id": "AS-DEV02",
            "status": "answered",
            "latency_ms": 300,
            "usage": {"input_tokens": 30, "output_tokens": 8, "status": "complete"},
        },
    ]
    scores = [
        {
            "question_id": "AS-DEV01",
            "retrieval": {
                "hit_at_k": {"value": 1.0, "status": "scored"},
                "recall_at_k": {"value": 0.5, "status": "scored"},
            },
            "faithfulness": {"value": 1.0, "status": "scored"},
            "judge": {"evidence": {"value": 1.0, "status": "scored"}},
            "table_rag_metrics": {
                "candidate_required_evidence_all": {"value": 1.0, "status": "scored"},
                "final_required_evidence_all": {"value": 1.0, "status": "scored"},
            },
        },
        {
            "question_id": "AS-DEV02",
            "retrieval": {
                "hit_at_k": {"value": None, "status": "not_applicable"},
                "recall_at_k": {"value": None, "status": "not_applicable"},
            },
            "faithfulness": {"value": None, "status": "unavailable"},
            "judge": {"evidence": {"value": 0.0, "status": "scored"}},
            "table_rag_metrics": {
                "candidate_required_evidence_all": {"value": 1.0, "status": "scored"},
                "final_required_evidence_all": {"value": 0.0, "status": "scored"},
            },
        },
    ]

    summary = summarize_benchmark(inventory, runs, scores)

    assert summary["dataset"]["question_count"] == 280
    assert summary["dataset"]["gold_evidence_question_count"] == 258
    assert summary["overall"]["run_count"] == 2
    assert summary["overall"]["metrics"]["faithfulness"]["scored"] == 1
    assert summary["overall"]["metrics"]["faithfulness"]["unavailable"] == 1
    assert summary["overall"]["metrics"]["faithfulness"]["missing"] == 278
    assert summary["overall"]["metrics"]["faithfulness"]["mean"] == 1.0
    assert summary["overall"]["all_group_macro"]["faithfulness"]["mean"] == 1.0
    assert summary["overall"]["all_group_macro_group_count"] == 14
    assert summary["category_180"]["question_count"] == 180
    assert summary["category_180"]["group_count"] == 9
    assert set(summary["category_180"]["groups"]) == {
        "scenario_1", "scenario_2", "scenario_3", "scenario_4", "scenario_5",
        "scenario_6", "scenario_7", "scenario_8_negative_control", "work_rules",
    }
    assert summary["category_180"]["macro"]["faithfulness"]["mean"] is None
    assert summary["category_180"]["macro"]["faithfulness"]["categories_with_scores"] == 0
    assert summary["overall"]["all_group_macro"]["faithfulness"]["categories_with_scores"] == 1
    assert summary["categories"]["development"]["metrics"]["judge.evidence"]["mean"] == 0.5
    assert summary["categories"]["development"]["page_presence_diagnosis"][
        "first_failure_stage_counts"
    ]["final_context_selection"] == 1
    assert summary["operations"]["generation"]["latency"]["p50_ms"] == 200
    assert summary["security_tasks"]["executed"] is False
    assert {link["question_id"] for link in summary["security_tasks"]["links"]} == {
        "AS-DEV14",
        "AS-ADD13",
    }
    assert all(
        link["question_text_matches"] for link in summary["security_tasks"]["links"]
    )



def test_semantic_fact_diagnosis_is_separate_from_page_presence(
    tmp_path: Path, benchmark_files: tuple[Path, Path],
) -> None:
    inventory = load_final_benchmark(*benchmark_files)
    scores = [
        {
            "question_id": "AS-DEV01",
            "table_rag_metrics": {
                "candidate_required_evidence_all": {"value": 1.0, "status": "scored"},
                "final_required_evidence_all": {"value": 1.0, "status": "scored"},
            },
            "evidence_attribution": {
                "status": "scored",
                "items": [
                    {"fact_id": "F1", "final_support": "supported", "answer_status": "correct",
                     "attribution": "supported", "first_loss_stage": None, "failure_tags": []},
                    {"fact_id": "F2", "final_support": "unsupported", "answer_status": "missing",
                     "attribution": "retrieval_miss", "first_loss_stage": "candidate_merge_loss",
                     "failure_tags": ["retrieval_miss", "candidate_merge_loss"]},
                ],
            },
        },
        {
            "question_id": "AS-DEV02",
            "evidence_attribution": {"status": "scored", "items": []},
        },
        {
            "question_id": "AS-DEV03",
            "evidence_attribution": {"status": "unavailable", "items": []},
        },
        {
            "question_id": "AS-DEV04",
            "evidence_attribution": {
                "status": "scored",
                "items": [
                    {"fact_id": "F1", "final_support": "supported", "answer_status": "missing",
                     "attribution": "answer_generation_miss", "first_loss_stage": "answer_generation_miss",
                     "failure_tags": ["answer_generation_miss"]},
                    {"fact_id": "F2", "final_support": "unsupported", "answer_status": "wrong",
                     "attribution": "retrieval_status_gate", "first_loss_stage": "retrieval_status_gate",
                     "failure_tags": ["retrieval_status_gate", "final_context_loss"]},
                ],
            },
        },
    ]

    summary = summarize_benchmark(inventory, [], scores)
    metrics = summary["overall"]["metrics"]

    # The exact-page check says DEV01 is preserved while semantic evidence says a fact is missing.
    trace = summary["overall"]["page_presence_diagnosis"]["first_failure_stage_counts"]
    assert trace["evidence_preserved"] == 1
    assert metrics["semantic_final_fact_all"]["mean"] == 0.0
    assert metrics["semantic_final_fact_all"]["scored"] == 2
    assert metrics["semantic_final_fact_all"]["not_applicable"] == 1
    assert metrics["semantic_final_fact_all"]["unavailable"] == 1
    assert metrics["semantic_final_fact_all"]["missing"] == 276
    assert metrics["semantic_answer_fact_all"]["scored"] == 2
    assert metrics["semantic_answer_fact_all"]["mean"] == 0.0

    semantic = summary["overall"]["semantic_attribution"]
    assert semantic["question_status_counts"]["not_applicable"] == 1
    assert semantic["question_status_counts"]["unavailable"] == 1
    assert semantic["primary_attribution_fact_counts"]["retrieval_miss"] == 1
    assert semantic["primary_attribution_fact_counts"]["answer_generation_miss"] == 1
    assert semantic["first_loss_stage_fact_counts"]["candidate_merge_loss"] == 1
    assert semantic["failure_tag_fact_counts"]["retrieval_miss"] == 1
    assert semantic["failure_tag_fact_counts"]["candidate_merge_loss"] == 1
    assert semantic["failure_tag_fact_counts"]["final_context_loss"] == 1
    assert semantic["first_loss_stage_question_counts"]["candidate_merge_loss"] == 1

    output = write_benchmark_report(tmp_path / "semantic-report", inventory, [], scores)
    import csv
    with (output / "question_trace.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = {row["question_id"]: row for row in csv.DictReader(stream)}
    dev01 = rows["AS-DEV01"]
    assert dev01["page_presence_diagnosis_first_failure_stage"] == "evidence_preserved"
    assert dev01["semantic_attribution_status"] == "scored"
    assert dev01["semantic_first_loss_stages"] == "['candidate_merge_loss']"
    assert "retrieval_miss" in dev01["semantic_failure_tags"]
    rendered = (output / "question_trace.md").read_text(encoding="utf-8")
    assert "의미상 필수 사실 판정은 Judge의 인용 검증을 별도로 집계합니다" in rendered
    assert "retrieval_miss" in rendered


def test_grading_summary_prefers_recorded_calls_and_counts_missing_usage() -> None:
    from app.evaluation.benchmark import _grading_summary

    scores = [
        {
            "question_id": "Q1",
            "grading_usage": {
                "judge": {"logical_calls": 1, "logical_attempts": 2,
                          "input_tokens": 100, "output_tokens": 30,
                          "reasoning_tokens": 20, "cached_input_tokens": 10,
                          "latency_ms": 12},
                "faithfulness": {
                    "logical_calls": 2, "input_tokens": 90, "output_tokens": 20,
                    "reasoning_tokens": 15, "latency_ms": 20,
                    "stages": [
                        {"attempts": [{}, {}], "reasoning_tokens": 10, "cached_input_tokens": 4},
                        {"attempts": [{}], "reasoning_tokens": 5, "cached_input_tokens": 6},
                    ],
                },
            },
        },
        {
            "question_id": "Q2",
            "grading_usage": {
                "judge": {"logical_calls": 1, "output_tokens": 8, "latency_ms": 15},
                "faithfulness": {"input_tokens": 40, "output_tokens": 8,
                                 "reasoning_tokens": None, "error": "temporary"},
            },
        },
        {"question_id": "Q3", "grading_usage": {"judge": None, "faithfulness": None}},
    ]

    summary = _grading_summary(scores)
    judge = summary["judge"]
    assert judge["logical_calls"] == {"total": 2, "known": 2, "missing": 0}
    assert judge["logical_attempts"] == {"total": 2, "known": 1, "missing": 1}
    assert judge["input_tokens"] == {"total": 100, "known": 1, "missing": 1}
    assert judge["output_tokens"] == {"total": 38, "known": 2, "missing": 0}
    assert judge["reasoning_tokens"]["missing"] == 1
    assert judge["cached_input_tokens"]["total"] == 10
    assert judge["missing_usage_records"] == 1

    faithfulness = summary["faithfulness"]
    assert faithfulness["logical_calls"] == {"total": 2, "known": 1, "missing": 1}
    assert faithfulness["logical_attempts"] == {"total": 3, "known": 1, "missing": 1}
    assert faithfulness["reasoning_tokens"]["total"] == 15
    assert faithfulness["cached_input_tokens"]["total"] == 10
    assert faithfulness["request_estimate"] == {
        "total": None, "source": "incomplete_recorded_usage"
    }


def test_nominal_faithfulness_request_count_is_fallback_not_actual() -> None:
    from app.evaluation.benchmark import _grading_summary

    summary = _grading_summary([
        {"question_id": "Q1", "grading_usage": {"faithfulness": {"latency_ms": 10}}}
    ])

    assert summary["faithfulness"]["logical_calls"] == {
        "total": None, "known": 0, "missing": 1
    }
    assert summary["faithfulness"]["request_estimate"] == {
        "total": 2, "source": "nominal_two_stage_fallback"
    }

def test_benchmark_report_writes_per_question_trace_without_private_text(
    tmp_path: Path, benchmark_files: tuple[Path, Path],
) -> None:
    inventory = load_final_benchmark(*benchmark_files)
    question = inventory.questions[0]
    run = {
        "question_id": question.question_id,
        "question": question.question,
        "answer": "private answer text must not be written",
        "status": "answered",
        "latency_ms": 20,
        "usage": {},
    }
    score = {
        "question_id": question.question_id,
        "faithfulness": {"value": 0.5, "status": "scored"},
        "grading_usage": {},
    }

    output = write_benchmark_report(tmp_path / "report", inventory, [run], [score])

    assert {path.name for path in output.iterdir()} == {
        "summary.json",
        "category_metrics.csv",
        "question_trace.csv",
        "question_trace.md",
        "report.md",
    }
    rendered = "\n".join(path.read_text(encoding="utf-8-sig") for path in output.iterdir())
    assert question.question not in rendered
    assert run["answer"] not in rendered


def test_synthetic_calibration_fixture_is_balanced_and_covers_target_risks() -> None:
    cases = load_synthetic_cases(SYNTHETIC_FIXTURE)

    assert len(cases) == 12
    assert sum(case.expected_supported is True for case in cases) == 6
    assert sum(case.expected_supported is False for case in cases) == 6
    assert {case.category for case in cases} == {
        "entity_match",
        "date_scope",
        "approval_effect",
        "fee_scope",
        "multi_source_calculation",
        "uncertain_status",
    }
    assert all(case.source == "synthetic" for case in cases)
    assert all(case.expected_supported is not None for case in cases)


def test_calibration_summary_keeps_missing_and_unlabeled_out_of_accuracy(
    tmp_path: Path,
) -> None:
    cases = load_synthetic_cases(SYNTHETIC_FIXTURE)
    cases = (*cases, *load_local_historical_cases(_write_local_audit_cases(tmp_path)))
    expected_ids = [
        case.case_id for case in cases if case.expected_supported is not None
    ]
    verdicts = {case_id: False for case_id in expected_ids[:-2]}

    summary = summarize_verdicts(cases, verdicts)

    assert summary["scored_count"] == 10
    assert summary["true_negative"] == 5
    assert summary["false_negative"] == 5
    assert summary["unavailable_count"] == 2
    assert summary["unlabeled_count"] == 8
    assert summary["accuracy"] == 0.5


def _write_local_audit_cases(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rows = []
    for case_id in HISTORICAL_CASE_IDS:
        rows.append(
            {
                "question_id": case_id,
                "question": "synthetic local question",
                "answer": "synthetic local answer",
                "reason": "synthetic local reason",
                "contexts": [{"source_id": "LOCAL-TEST", "text": "synthetic local context"}],
            }
        )
    (tmp_path / "blind-review-test.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_historical_cases_are_loaded_only_from_explicit_local_audit_files(
    tmp_path: Path,
) -> None:
    audit_directory = _write_local_audit_cases(tmp_path)

    cases = load_local_historical_cases(audit_directory)

    assert tuple(case.case_id for case in cases) == HISTORICAL_CASE_IDS
    assert all(case.expected_supported is None for case in cases)
    assert all(case.source.startswith("blind-review-") for case in cases)
    assert all("synthetic local context" in case["text"] for case in cases[0].contexts)
