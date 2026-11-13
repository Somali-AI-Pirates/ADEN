"""Final Advanced benchmark inventory, aggregation, and local report writing."""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.metrics import aggregate


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS_PATH = (
    REPOSITORY_ROOT / "data/advanced/evaluation/final_benchmark_questions.json"
)
DEFAULT_REFERENCES_PATH = (
    REPOSITORY_ROOT / "data/advanced/evaluation/final_benchmark.json"
)
DEFAULT_SECURITY_TASKS_PATH = (
    REPOSITORY_ROOT / "data/advanced/evaluation/security_tasks.json"
)

# These counts describe the reviewed final set; generic aggregation below accepts
# any inventory and does not assume this size.
FINAL_BENCHMARK_COUNTS = {
    "development": 30,
    "additional": 30,
    **{f"scenario_{number}": 20 for number in range(1, 8)},
    "negative_control": 20,
    "work_rules_procedure": 10,
    "work_rules_applicability": 10,
    "custom_s": 20,
    "custom_m": 20,
}
FINAL_BENCHMARK_SIZE = sum(FINAL_BENCHMARK_COUNTS.values())
FINAL_BENCHMARK_EVIDENCE_SIZE = 258
FINAL_BENCHMARK_NO_EVIDENCE_IDS = frozenset(
    {"AS-DEV12", "AS-ADD12"}
    | {f"AS-S08-{number:02}" for number in range(1, 21)}
)
SCENARIO_NAMES = {
    1: "직접 조회",
    2: "번호 없이 한 사건 식별",
    3: "한 사건의 여러 기록 연결",
    4: "서로 다른 사건 비교",
    5: "적용되는 기록·조건 판정",
    6: "대상이 모호해 추가 확인 필요",
    7: "대상은 명확하지만 확정 근거 부족",
}

PAGE_PRESENCE_DIAGNOSIS_LABELS = {
    "candidate_retrieval": "정답 페이지가 후보에 없음",
    "final_context_selection": "후보에는 있지만 최종 문맥에서 빠짐",
    "evidence_preserved": "최종 문맥에 정답 페이지 존재",
    "no_gold_evidence": "페이지 정답 없음",
    "unavailable": "페이지 진단 불가",
}
SEMANTIC_FIRST_LOSS_ORDER = (
    "retrieval_miss", "planner_exposure_loss", "candidate_merge_loss",
    "final_context_loss", "retrieval_status_gate", "answer_generation_miss", "unknown",
)
SEMANTIC_FIRST_LOSS_LABELS = {
    "retrieval_miss": "검색기에서 근거를 찾지 못함",
    "planner_exposure_loss": "검색 결과가 플래너에 노출되지 않음",
    "candidate_merge_loss": "플래너 관측 뒤 후보 병합에서 빠짐",
    "final_context_loss": "후보에는 있지만 최종 문맥에서 빠짐",
    "retrieval_status_gate": "검색 상태 판정이 답변을 막음",
    "answer_generation_miss": "최종 문맥에 있지만 답변에서 누락·오답",
    "unknown": "최초 누락 단계 불명확",
}
SEMANTIC_METRIC_PATHS = {"semantic_final_fact_all", "semantic_answer_fact_all"}


@dataclass(frozen=True)
class BenchmarkQuestion:
    """One uniquely identified question and its post-run grading reference."""

    question_id: str
    question: str
    reference: Mapping[str, Any]
    category: str
    category_detail: str
    has_evidence: bool


@dataclass(frozen=True)
class BenchmarkInventory:
    """Canonical questions, references, and unique category groups."""

    questions: tuple[BenchmarkQuestion, ...]

    def groups(self) -> dict[str, tuple[BenchmarkQuestion, ...]]:
        """Return unique primary groups and overlapping question-type breakdowns."""
        grouped: dict[str, list[BenchmarkQuestion]] = defaultdict(list)
        for question in self.questions:
            grouped[question.category].append(question)
            if question.category.startswith("scenario_"):
                grouped[f"question_type:{question.category_detail}"].append(question)
        return {name: tuple(rows) for name, rows in sorted(grouped.items())}

    def primary_groups(self) -> dict[str, tuple[BenchmarkQuestion, ...]]:
        """Return disjoint groups whose union contains each benchmark item once."""
        return {
            name: rows
            for name, rows in self.groups().items()
            if not name.startswith("question_type:")
        }


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"벤치마크 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"벤치마크 파일은 객체 배열이어야 합니다: {path}")
    return value


def _index_unique(
    rows: Sequence[dict[str, Any]], source_name: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = row.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(f"{source_name}에 비어 있거나 잘못된 id가 있습니다.")
        if question_id in indexed:
            raise ValueError(f"{source_name}에 중복 id가 있습니다: {question_id}")
        indexed[question_id] = row
    return indexed


def _classify(question_id: str, reference: Mapping[str, Any]) -> tuple[str, str]:
    if match := re.fullmatch(r"AS-DEV(\d{2})", question_id):
        return "development", f"AS-DEV{match.group(1)}"
    if match := re.fullmatch(r"AS-ADD(\d{2})", question_id):
        return "additional", f"AS-ADD{match.group(1)}"
    if match := re.fullmatch(r"AS-S0([1-7])-(\d{2})", question_id):
        number = int(match.group(1))
        if reference.get("scenario") != number:
            raise ValueError(f"{question_id}의 scenario metadata가 id와 다릅니다.")
        detail = reference.get("question_type")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError(f"{question_id}에 question_type metadata가 없습니다.")
        if reference.get("scenario_name") != SCENARIO_NAMES[number]:
            raise ValueError(f"{question_id}의 scenario_name이 예상과 다릅니다.")
        return f"scenario_{number}", detail
    if match := re.fullmatch(r"AS-S08-(\d{2})", question_id):
        if reference.get("scenario") != 8:
            raise ValueError(f"{question_id}의 scenario metadata가 id와 다릅니다.")
        variant = reference.get("variant")
        if variant not in {"unrelated", "near_domain"}:
            raise ValueError(f"{question_id}의 negative-control variant가 없습니다.")
        return "negative_control", str(variant)
    if match := re.fullmatch(r"AS-WR-(\d{2})", question_id):
        if reference.get("scenario") != "work_rules":
            raise ValueError(f"{question_id}의 work-rules metadata가 없습니다.")
        variant = reference.get("variant")
        if variant not in {"procedure", "applicability"}:
            raise ValueError(f"{question_id}의 work-rules variant가 잘못됐습니다.")
        return f"work_rules_{variant}", str(reference.get("topic", ""))
    if match := re.fullmatch(r"([SM])(\d{2})", question_id):
        return f"custom_{match.group(1).lower()}", f"custom_{match.group(1)}"
    raise ValueError(f"최종 벤치마크에 분류할 수 없는 id가 있습니다: {question_id}")


def load_final_benchmark(
    questions_path: Path = DEFAULT_QUESTIONS_PATH,
    references_path: Path = DEFAULT_REFERENCES_PATH,
) -> BenchmarkInventory:
    """Load and validate the one canonical Advanced benchmark.

    Only the explicitly paired question/reference files are used. This final-set
    preflight is intentionally strict; the summary helpers remain size agnostic.
    """
    question_rows = _index_unique(_read_json_list(questions_path), "질문 파일")
    reference_rows = _index_unique(_read_json_list(references_path), "참고 파일")
    if question_rows.keys() != reference_rows.keys():
        only_questions = sorted(question_rows.keys() - reference_rows.keys())
        only_references = sorted(reference_rows.keys() - question_rows.keys())
        raise ValueError(
            "질문과 참고 파일의 id가 일치하지 않습니다 "
            f"(참고 없음={only_questions[:5]}, 질문 없음={only_references[:5]})."
        )

    result: list[BenchmarkQuestion] = []
    category_counts: dict[str, int] = defaultdict(int)
    no_evidence_ids: set[str] = set()
    for question_id, question_row in question_rows.items():
        question = question_row.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{question_id}에 질문 본문이 없습니다.")
        reference = reference_rows[question_id]
        if not isinstance(reference.get("reference_answer"), str):
            raise ValueError(f"{question_id}에 reference_answer가 없습니다.")
        evidence_groups = reference.get("evidence_groups")
        if not isinstance(evidence_groups, list):
            raise ValueError(f"{question_id}의 evidence_groups가 배열이 아닙니다.")
        category, category_detail = _classify(question_id, reference)
        category_counts[category] += 1
        if not evidence_groups:
            no_evidence_ids.add(question_id)
        result.append(
            BenchmarkQuestion(
                question_id=question_id,
                question=question.strip(),
                reference=reference,
                category=category,
                category_detail=category_detail,
                has_evidence=bool(evidence_groups),
            )
        )

    if category_counts != FINAL_BENCHMARK_COUNTS:
        raise ValueError(
            "최종 벤치마크 category별 문항 수가 고정 구성과 다릅니다: "
            f"{dict(sorted(category_counts.items()))}"
        )
    if len(result) != FINAL_BENCHMARK_SIZE:
        raise ValueError(f"최종 벤치마크 문항 수가 {FINAL_BENCHMARK_SIZE}개가 아닙니다.")
    if no_evidence_ids != FINAL_BENCHMARK_NO_EVIDENCE_IDS:
        raise ValueError("근거가 없는 22개 문항 구성이 예상과 다릅니다.")
    if sum(question.has_evidence for question in result) != FINAL_BENCHMARK_EVIDENCE_SIZE:
        raise ValueError("최종 벤치마크 근거 보유 문항 수가 258개가 아닙니다.")
    return BenchmarkInventory(tuple(result))


def load_security_task_links(
    inventory: BenchmarkInventory,
    tasks_path: Path = DEFAULT_SECURITY_TASKS_PATH,
) -> tuple[dict[str, Any], ...]:
    """Validate security cases' reused questions without executing those cases."""
    tasks = _read_json_list(tasks_path)
    question_by_id = {row.question_id: row.question for row in inventory.questions}
    result = []
    for task in tasks:
        question_id = task.get("question_id")
        task_id = task.get("id")
        if question_id not in question_by_id or not isinstance(task_id, str):
            raise ValueError("보안 과제의 연결 문항 id가 최종 벤치마크에 없습니다.")
        if task.get("question") != question_by_id[question_id]:
            raise ValueError(f"{task_id}의 보안 질문이 기존 질문과 다릅니다.")
        result.append(
            {
                "task_id": task_id,
                "question_id": question_id,
                "type": task.get("type"),
                "question_text_matches": task.get("question") == question_by_id[question_id],
                "executed": False,
            }
        )
    return tuple(result)


def _collect_score_paths(
    value: Any, prefix: tuple[str, ...] = ()
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    if "status" in value and "value" in value:
        return {".".join(prefix): value}
    found: dict[str, Mapping[str, Any]] = {}
    for key, child in value.items():
        found.update(_collect_score_paths(child, (*prefix, str(key))))
    return found


def _index_records(
    rows: Sequence[Mapping[str, Any]], label: str
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError(f"{label} 행에 question_id가 없습니다.")
        if question_id in indexed:
            raise ValueError(f"{label}에 중복 question_id가 있습니다: {question_id}")
        indexed[question_id] = row
    return indexed


def _semantic_question_diagnosis(score_row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize validated semantic fact attribution for one scored question."""
    if score_row is None:
        return {"status": "missing", "items": []}
    attribution = score_row.get("evidence_attribution")
    if not isinstance(attribution, Mapping):
        return {"status": "unavailable", "items": []}
    status, raw_items = attribution.get("status"), attribution.get("items")
    if status == "not_applicable":
        return {"status": "not_applicable", "items": []}
    if status == "scored" and isinstance(raw_items, list) and not raw_items:
        return {"status": "not_applicable", "items": []}
    if status != "scored" or not isinstance(raw_items, list):
        return {"status": "unavailable", "items": []}
    normalized = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            normalized.append({"final_support": "uncertain", "answer_status": "uncertain",
                               "attribution": "unknown", "first_loss_stage": "unknown",
                               "failure_tags": ["unknown"]})
            continue
        final_support = item.get("final_support")
        answer_status = item.get("answer_status")
        if final_support not in {"supported", "unsupported", "uncertain"}:
            final_support = "uncertain"
        if answer_status not in {"correct", "missing", "wrong", "uncertain"}:
            answer_status = "uncertain"
        raw_primary = item.get("attribution")
        primary = raw_primary.strip() if isinstance(raw_primary, str) and raw_primary.strip() else "unknown"
        raw_stage = item.get("first_loss_stage")
        stage = None
        if raw_stage is not None:
            stage = raw_stage if raw_stage in SEMANTIC_FIRST_LOSS_ORDER else "unknown"
        elif final_support != "supported" or answer_status != "correct":
            stage = "unknown"
        raw_tags = item.get("failure_tags")
        if isinstance(raw_tags, list):
            tags = list(dict.fromkeys(
                tag.strip() if isinstance(tag, str) and tag.strip() else "unknown"
                for tag in raw_tags
            ))
        elif final_support != "supported" or answer_status != "correct":
            tags = ["unknown"]
        else:
            tags = []
        normalized.append({"final_support": final_support, "answer_status": answer_status,
                           "attribution": primary, "first_loss_stage": stage,
                           "failure_tags": tags})
    return {"status": "scored", "items": normalized}


def _semantic_fact_metrics(score_row: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    diagnosis = _semantic_question_diagnosis(score_row)
    status, items = diagnosis["status"], diagnosis["items"]
    if status == "missing":
        return {}
    if status == "not_applicable":
        return {name: {"value": None, "status": "not_applicable",
                       "reason": "필수 사실이 없는 질문"} for name in SEMANTIC_METRIC_PATHS}
    if status != "scored":
        reason = "의미상 필수 사실 판정이 없거나 불완전함"
        return {name: {"value": None, "status": "unavailable", "reason": reason}
                for name in SEMANTIC_METRIC_PATHS}
    if not items:
        return {name: {"value": None, "status": "not_applicable",
                       "reason": "검증된 필수 사실이 비어 있음"} for name in SEMANTIC_METRIC_PATHS}
    final_values = [item["final_support"] for item in items]
    if "unsupported" in final_values:
        final = {"value": 0.0, "status": "scored",
                 "reason": "필수 사실 중 최종 문맥이 뒷받침하지 않는 사실이 있음"}
    elif "uncertain" in final_values:
        final = {"value": None, "status": "unavailable",
                 "reason": "최종 문맥의 의미상 지지가 불명확함"}
    else:
        final = {"value": 1.0, "status": "scored",
                 "reason": "모든 필수 사실이 최종 문맥에서 지지됨"}
    answer_values = [item["answer_status"] for item in items]
    if any(value in {"missing", "wrong"} for value in answer_values):
        answer = {"value": 0.0, "status": "scored",
                  "reason": "필수 사실 중 답변에서 빠지거나 틀린 사실이 있음"}
    elif "uncertain" in answer_values:
        answer = {"value": None, "status": "unavailable",
                  "reason": "필수 사실의 답변 정확성이 불명확함"}
    else:
        answer = {"value": 1.0, "status": "scored",
                  "reason": "모든 필수 사실을 답변에서 정확히 제시함"}
    return {"semantic_final_fact_all": final, "semantic_answer_fact_all": answer}


def _benchmark_score_paths(score_row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    paths = _collect_score_paths(score_row)
    paths.update(_semantic_fact_metrics(score_row))
    return paths


def _aggregate_metrics(question_ids: Sequence[str],
                       scores_by_id: Mapping[str, Mapping[str, Any]],
                       all_paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    output = {}
    for path in all_paths:
        present = []
        for question_id in question_ids:
            score_row = scores_by_id.get(question_id)
            if score_row is None:
                continue
            value = _benchmark_score_paths(score_row).get(path)
            if value is not None:
                present.append(value)
        result = aggregate(present)
        result["missing"] = len(question_ids) - len(present)
        output[path] = result
    return output


def _latency_summary(values: Sequence[float | int]) -> dict[str, Any]:
    numeric = sorted(float(value) for value in values if value is not None)
    if not numeric:
        return {"count": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None}

    def percentile(fraction: float) -> float:
        index = max(0, math.ceil(fraction * len(numeric)) - 1)
        return numeric[index]

    return {
        "count": len(numeric),
        "mean_ms": statistics.fmean(numeric),
        "p50_ms": statistics.median(numeric),
        "p95_ms": percentile(0.95),
    }


def _sum_known(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    return {
        "total": sum(values) if values else None,
        "known": len(values),
        "missing": len(rows) - len(values),
    }


def _generation_summary(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(run.get("status", "missing")) for run in runs)
    usages = [run.get("usage") or {} for run in runs]
    result: dict[str, Any] = {
        "requests": len(runs),
        "status_counts": dict(sorted(statuses.items())),
        "latency": _latency_summary(
            [run.get("latency_ms") for run in runs if run.get("latency_ms") is not None]
        ),
    }
    for key in ("input_tokens", "output_tokens", "logical_calls", "provider_attempts"):
        result[key] = _sum_known(usages, key)
    result["missing_usage"] = _sum_known(usages, "missing_usage")
    return result


def _usage_counter_value(row: Mapping[str, Any], field: str) -> int | None:
    """Read a recorded counter, deriving it from Ragas stage metadata if available."""
    value = row.get(field)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    stages = row.get("stages")
    if not isinstance(stages, list) or not stages or any(not isinstance(x, Mapping) for x in stages):
        return None
    if field == "logical_calls":
        return len(stages)
    if field == "logical_attempts":
        totals = []
        for stage in stages:
            attempts = stage.get("logical_attempts")
            if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0:
                totals.append(attempts)
            elif isinstance(stage.get("attempts"), list):
                totals.append(len(stage["attempts"]))
            else:
                return None
        return sum(totals)
    values = [stage.get(field) for stage in stages]
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in values):
        return None
    return sum(values)


def _sum_usage_counter(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    values = [_usage_counter_value(row, field) for row in rows]
    known = [value for value in values if value is not None]
    return {"total": sum(known) if known else None, "known": len(known),
            "missing": len(values) - len(known)}


def _grading_summary(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grader_names = sorted({str(grader) for row in scores
                           for grader in (row.get("grading_usage") or {})})
    result = {}
    for grader in grader_names:
        all_rows = [(row.get("grading_usage") or {}).get(grader) for row in scores]
        rows = [usage for usage in all_rows if isinstance(usage, Mapping)]
        if not rows:
            continue
        logical_calls = _sum_usage_counter(rows, "logical_calls")
        estimate = {"total": logical_calls["total"],
                    "source": "recorded_logical_calls" if logical_calls["known"] else "unavailable"}
        if grader == "faithfulness" and logical_calls["known"] == 0:
            estimate = {"total": 2 * len(rows), "source": "nominal_two_stage_fallback"}
        elif logical_calls["known"] < len(rows):
            estimate = {"total": None, "source": "incomplete_recorded_usage"}
        result[grader] = {
            "score_count": len(scores),
            "recorded_calls": len(rows),
            "missing_usage_records": len(scores) - len(rows),
            "failed_calls": sum(usage.get("error") is not None for usage in rows),
            "latency": _latency_summary([usage.get("latency_ms") for usage in rows
                                         if usage.get("latency_ms") is not None]),
            "input_tokens": _sum_usage_counter(rows, "input_tokens"),
            "output_tokens": _sum_usage_counter(rows, "output_tokens"),
            "reasoning_tokens": _sum_usage_counter(rows, "reasoning_tokens"),
            "cached_input_tokens": _sum_usage_counter(rows, "cached_input_tokens"),
            "logical_calls": logical_calls,
            "logical_attempts": _sum_usage_counter(rows, "logical_attempts"),
            "request_estimate": estimate,
        }
    return result


def _page_presence_first_failure_stage(
    question: BenchmarkQuestion, score_row: Mapping[str, Any] | None
) -> str:
    """Diagnose page-ID presence only; this is not semantic fact attribution."""
    if not question.has_evidence:
        return "no_gold_evidence"
    if score_row is None:
        return "unavailable"
    table_metrics = score_row.get("table_rag_metrics")
    if not isinstance(table_metrics, Mapping):
        return "unavailable"
    candidate = table_metrics.get("candidate_required_evidence_all")
    final = table_metrics.get("final_required_evidence_all")
    if not isinstance(candidate, Mapping) or not isinstance(final, Mapping):
        return "unavailable"
    if candidate.get("status") != "scored" or final.get("status") != "scored":
        return "unavailable"
    if candidate.get("value") is None or final.get("value") is None:
        return "unavailable"
    if candidate["value"] < 1:
        return "candidate_retrieval"
    if final["value"] < 1:
        return "final_context_selection"
    return "evidence_preserved"


def _ordered_counter(counter: Counter, order: Sequence[str]) -> dict[str, int]:
    result = {name: counter.get(name, 0) for name in order}
    result.update({name: counter[name] for name in sorted(counter) if name not in result})
    return result


def _page_presence_diagnosis_summary(
    questions: Sequence[BenchmarkQuestion], scores_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    stages = Counter(_page_presence_first_failure_stage(q, scores_by_id.get(q.question_id))
                     for q in questions)
    return {"question_count": len(questions),
            "first_failure_stage_counts": _ordered_counter(
                stages, tuple(PAGE_PRESENCE_DIAGNOSIS_LABELS))}


def _semantic_attribution_summary(
    questions: Sequence[BenchmarkQuestion], scores_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    statuses = Counter()
    primary_fact = Counter()
    primary_question = Counter()
    first_fact = Counter()
    first_question = Counter()
    tag_fact = Counter()
    tag_question = Counter()
    fact_count = 0
    for question in questions:
        diagnosis = _semantic_question_diagnosis(scores_by_id.get(question.question_id))
        statuses[diagnosis["status"]] += 1
        if diagnosis["status"] != "scored":
            continue
        items = diagnosis["items"]
        fact_count += len(items)
        q_primary, q_first, q_tags = set(), set(), set()
        for item in items:
            primary = item["attribution"]
            primary_fact[primary] += 1
            q_primary.add(primary)
            first = item.get("first_loss_stage")
            if first is not None:
                first = first if first in SEMANTIC_FIRST_LOSS_ORDER else "unknown"
                first_fact[first] += 1
                q_first.add(first)
            for tag in set(item.get("failure_tags") or []):
                tag_fact[tag] += 1
                q_tags.add(tag)
        primary_question.update(q_primary)
        first_question.update(q_first)
        tag_question.update(q_tags)
    return {
        "question_count": len(questions), "fact_count": fact_count,
        "question_status_counts": _ordered_counter(
            statuses, ("scored", "not_applicable", "unavailable", "missing")),
        "primary_attribution_fact_counts": dict(sorted(primary_fact.items())),
        "primary_attribution_question_counts": dict(sorted(primary_question.items())),
        "first_loss_stage_fact_counts": _ordered_counter(first_fact, SEMANTIC_FIRST_LOSS_ORDER),
        "first_loss_stage_question_counts": _ordered_counter(first_question, SEMANTIC_FIRST_LOSS_ORDER),
        "failure_tag_fact_counts": dict(sorted(tag_fact.items())),
        "failure_tag_question_counts": dict(sorted(tag_question.items())),
    }


def _calculation_status_summary(
    question_ids: Sequence[str],
    scores_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    statuses = Counter()
    for question_id in question_ids:
        row = scores_by_id.get(question_id)
        calculation = (row or {}).get("calculation_checks") or {}
        statuses[str(calculation.get("status", "missing"))] += 1
    return dict(sorted(statuses.items()))


def _metric_summary_for_ids(
    question_ids: Sequence[str],
    scores_by_id: Mapping[str, Mapping[str, Any]],
    all_paths: Sequence[str],
) -> dict[str, Any]:
    return {
        "question_count": len(question_ids),
        "metric_count": len(all_paths),
        "metrics": _aggregate_metrics(question_ids, scores_by_id, all_paths),
    }


def _category_180_groups(
    groups: Mapping[str, Sequence[BenchmarkQuestion]],
) -> dict[str, tuple[BenchmarkQuestion, ...]]:
    """Return scenario 1–8 plus combined work rules, without dev/add/custom."""
    result = {
        f"scenario_{number}": tuple(groups[f"scenario_{number}"])
        for number in range(1, 8)
        if f"scenario_{number}" in groups
    }
    if "negative_control" in groups:
        result["scenario_8_negative_control"] = tuple(groups["negative_control"])
    work_rules = tuple(sorted(
        (*groups.get("work_rules_procedure", ()),
         *groups.get("work_rules_applicability", ())),
        key=lambda question: question.question_id,
    ))
    if work_rules:
        result["work_rules"] = work_rules
    return result


def _macro_metrics(
    groups: Mapping[str, Sequence[BenchmarkQuestion]],
    scores_by_id: Mapping[str, Mapping[str, Any]],
    all_paths: Sequence[str],
) -> dict[str, Any]:
    means: dict[str, list[float]] = defaultdict(list)
    for questions in groups.values():
        metrics = _aggregate_metrics(
            [question.question_id for question in questions], scores_by_id, all_paths
        )
        for path, item in metrics.items():
            if item["mean"] is not None:
                means[path].append(item["mean"])
    return {
        path: {
            "mean": statistics.fmean(means[path]) if means[path] else None,
            "categories_with_scores": len(means[path]),
            "categories_without_scores": len(groups) - len(means[path]),
        }
        for path in all_paths
    }


def summarize_benchmark(
    inventory: BenchmarkInventory,
    runs: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize page presence, semantic facts, and all evaluation metrics."""
    runs_by_id = _index_records(runs, "실행 기록")
    scores_by_id = _index_records(scores, "채점 기록")
    known_ids = {row.question_id for row in inventory.questions}
    extras = (runs_by_id.keys() | scores_by_id.keys()) - known_ids
    if extras:
        raise ValueError(f"최종 벤치마크에 없는 question_id가 있습니다: {sorted(extras)[:5]}")

    all_paths = sorted(
        SEMANTIC_METRIC_PATHS
        | {path for row in scores for path in _benchmark_score_paths(row)}
    )
    question_ids = [row.question_id for row in inventory.questions]
    primary_groups = inventory.primary_groups()
    categories = {}
    for category, questions in primary_groups.items():
        ids = [question.question_id for question in questions]
        categories[category] = {
            "question_count": len(questions),
            "run_count": sum(question_id in runs_by_id for question_id in ids),
            "metric_count": len(all_paths),
            "metrics": _aggregate_metrics(ids, scores_by_id, all_paths),
            "page_presence_diagnosis": _page_presence_diagnosis_summary(
                questions, scores_by_id
            ),
            "semantic_attribution": _semantic_attribution_summary(
                questions, scores_by_id
            ),
            "calculation_checks": _calculation_status_summary(ids, scores_by_id),
        }

    question_type_groups = {
        name.removeprefix("question_type:"): questions
        for name, questions in inventory.groups().items()
        if name.startswith("question_type:")
    }
    question_types = {
        question_type: _metric_summary_for_ids(
            [question.question_id for question in questions],
            scores_by_id,
            all_paths,
        )
        for question_type, questions in sorted(question_type_groups.items())
    }
    overall_metrics = _aggregate_metrics(question_ids, scores_by_id, all_paths)
    all_group_macro = _macro_metrics(primary_groups, scores_by_id, all_paths)

    category_180_groups = _category_180_groups(primary_groups)
    category_180_questions = tuple(
        question
        for group in category_180_groups.values()
        for question in group
    )
    category_180_ids = [question.question_id for question in category_180_questions]
    if len(category_180_ids) != len(set(category_180_ids)):
        raise ValueError("카테고리 180 그룹 간 중복 문항이 있습니다.")
    category_180_scores = _aggregate_metrics(category_180_ids, scores_by_id, all_paths)
    category_180_group_rows = {}
    for name, questions in category_180_groups.items():
        ids = [question.question_id for question in questions]
        category_180_group_rows[name] = {
            "question_count": len(questions),
            "run_count": sum(question_id in runs_by_id for question_id in ids),
            "metrics": _aggregate_metrics(ids, scores_by_id, all_paths),
            "page_presence_diagnosis": _page_presence_diagnosis_summary(
                questions, scores_by_id
            ),
            "semantic_attribution": _semantic_attribution_summary(
                questions, scores_by_id
            ),
        }
    category_180 = {
        "question_count": len(category_180_ids),
        "group_count": len(category_180_groups),
        "groups": category_180_group_rows,
        "metrics": category_180_scores,
        "macro": _macro_metrics(category_180_groups, scores_by_id, all_paths),
    }

    evidence_questions = [row for row in inventory.questions if row.has_evidence]
    no_evidence_questions = [row for row in inventory.questions if not row.has_evidence]
    security_links = load_security_task_links(inventory)
    return {
        "dataset": {
            "dataset_id": "advanced-final-benchmark",
            "question_count": len(inventory.questions),
            "unique_question_count": len(known_ids),
            "gold_evidence_question_count": len(evidence_questions),
            "no_gold_evidence_question_count": len(no_evidence_questions),
            "component_files_are_included_in_final_total": True,
        },
        "overall": {
            "question_count": len(question_ids),
            "run_count": sum(question_id in runs_by_id for question_id in question_ids),
            "score_count": sum(question_id in scores_by_id for question_id in question_ids),
            "metrics": overall_metrics,
            "all_group_macro": all_group_macro,
            "all_group_macro_group_count": len(primary_groups),
            "all_group_macro_groups": sorted(primary_groups),
            "page_presence_diagnosis": _page_presence_diagnosis_summary(
                inventory.questions, scores_by_id
            ),
            "semantic_attribution": _semantic_attribution_summary(
                inventory.questions, scores_by_id
            ),
        },
        "category_180": category_180,
        "gold_evidence": _metric_summary_for_ids(
            [row.question_id for row in evidence_questions], scores_by_id, all_paths
        ),
        "no_gold_evidence": _metric_summary_for_ids(
            [row.question_id for row in no_evidence_questions], scores_by_id, all_paths
        ),
        "categories": categories,
        "question_types": question_types,
        "calculation_checks": _calculation_status_summary(question_ids, scores_by_id),
        "operations": {
            "generation": _generation_summary(
                [runs_by_id[question_id] for question_id in question_ids
                 if question_id in runs_by_id]
            ),
            "grading_by_metric": _grading_summary(scores),
        },
        "security_tasks": {"executed": False, "links": list(security_links)},
    }


def _trace_rows(
    inventory: BenchmarkInventory,
    runs: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    runs_by_id = _index_records(runs, "실행 기록")
    scores_by_id = _index_records(scores, "채점 기록")
    rows = []
    for question in inventory.questions:
        run = runs_by_id.get(question.question_id, {})
        score_row = scores_by_id.get(question.question_id)
        metric_paths = _benchmark_score_paths(score_row) if score_row is not None else {}
        semantic = _semantic_question_diagnosis(score_row)
        items = semantic["items"]
        primary = sorted({item["attribution"] for item in items})
        stages = sorted(
            {item["first_loss_stage"] for item in items if item.get("first_loss_stage")},
            key=lambda value: SEMANTIC_FIRST_LOSS_ORDER.index(value)
            if value in SEMANTIC_FIRST_LOSS_ORDER else len(SEMANTIC_FIRST_LOSS_ORDER),
        )
        tags = sorted({tag for item in items for tag in item.get("failure_tags", [])})
        trace = {
            "question_id": question.question_id,
            "category": question.category,
            "category_detail": question.category_detail,
            "has_gold_evidence": question.has_evidence,
            "run_status": run.get("status", "missing"),
            "page_presence_diagnosis_first_failure_stage": (
                _page_presence_first_failure_stage(question, score_row)
            ),
            "semantic_attribution_status": semantic["status"],
            "semantic_fact_count": len(items),
            "semantic_primary_attributions": primary,
            "semantic_first_loss_stages": stages,
            "semantic_failure_tags": tags,
            "calculation_status": (
                (score_row or {}).get("calculation_checks", {}).get("status", "missing")
            ),
            "generation_latency_ms": run.get("latency_ms"),
            "generation_input_tokens": (run.get("usage") or {}).get("input_tokens"),
            "generation_output_tokens": (run.get("usage") or {}).get("output_tokens"),
        }
        for metric_path, value in metric_paths.items():
            trace[f"{metric_path}.value"] = value.get("value")
            trace[f"{metric_path}.status"] = value.get("status")
        rows.append(trace)
    return rows


def _trace_metric_display(row: Mapping[str, Any], path: str) -> str:
    status = row.get(f"{path}.status", "missing")
    value = row.get(f"{path}.value")
    return f"{value if value is not None else '—'} ({status})"


def _render_question_trace_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# 질문별 평가 추적",
        "",
        "페이지 진단은 정답 페이지 ID의 후보·최종 문맥 포함 여부만 봅니다. "
        "의미상 필수 사실 판정은 Judge의 인용 검증을 별도로 집계합니다.",
        "질문·답변·문맥 원문은 포함하지 않습니다.",
        "",
        "| ID | 카테고리 | 실행 | 페이지 존재 진단 | 필수 사실 상태 | 최종 문맥 전체 | 답변 전체 | 주요 귀속 | 최초 손실 | 다중 태그 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['question_id']} | {row['category']} | {row['run_status']} | "
            f"{PAGE_PRESENCE_DIAGNOSIS_LABELS[row['page_presence_diagnosis_first_failure_stage']]} | "
            f"{row['semantic_attribution_status']} ({row['semantic_fact_count']} facts) | "
            f"{_trace_metric_display(row, 'semantic_final_fact_all')} | "
            f"{_trace_metric_display(row, 'semantic_answer_fact_all')} | "
            f"{', '.join(row['semantic_primary_attributions']) or '—'} | "
            f"{', '.join(row['semantic_first_loss_stages']) or '—'} | "
            f"{', '.join(row['semantic_failure_tags']) or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _metric_display(metrics: Mapping[str, Mapping[str, Any]], name: str) -> str:
    item = metrics.get(name, {})
    mean = item.get("mean")
    if mean is None:
        return "—"
    return (
        f"{mean:.3f} "
        f"(채점 {item.get('scored', 0)}, 해당 없음 {item.get('not_applicable', 0)}, "
        f"평가 불가 {item.get('unavailable', 0)}, 누락 {item.get('missing', 0)})"
    )


def _counter_display(value: Mapping[str, Any]) -> str:
    total = value.get("total")
    shown = str(total) if total is not None else "—"
    return f"{shown} (기록 {value.get('known', 0)}, 누락 {value.get('missing', 0)})"


def _count_table_rows(
    fact_counts: Mapping[str, int], question_counts: Mapping[str, int],
    labels: Mapping[str, str] | None = None,
) -> list[tuple[str, int, int]]:
    names = list(dict.fromkeys([*fact_counts, *question_counts]))
    return [
        ((labels or {}).get(name, name), fact_counts.get(name, 0), question_counts.get(name, 0))
        for name in names
    ]


def _render_benchmark_markdown(summary: Mapping[str, Any]) -> str:
    overall = summary["overall"]
    metrics = overall["metrics"]
    key_metrics = [
        ("faithfulness", "Ragas Faithfulness"),
        ("judge.fact", "Judge 사실"),
        ("judge.condition", "Judge 조건"),
        ("judge.evidence", "Judge 근거"),
        ("judge.decision", "Judge 판단"),
        ("retrieval.hit_at_k", "최종 문서 Hit@k"),
        ("retrieval.recall_at_k", "최종 문서 Recall@k"),
        ("table_rag_metrics.candidate_page_recall", "후보 페이지 Recall"),
        ("table_rag_metrics.final_page_recall", "최종 페이지 Recall"),
        ("table_rag_metrics.candidate_required_evidence_all", "후보 전체 필수 근거 충족"),
        ("table_rag_metrics.final_required_evidence_all", "최종 전체 필수 근거 충족"),
        ("semantic_final_fact_all", "의미상 최종 문맥 전체 필수 사실 지지"),
        ("semantic_answer_fact_all", "의미상 답변 전체 필수 사실 정답"),
        ("table_rag_metrics.final_sql_expected", "최종 SQL 기대값 일치"),
    ]
    lines = [
        "# Advanced 최종 벤치 평가", "",
        f"- 고유 문항: {summary['dataset']['unique_question_count']}",
        ("- 페이지 정답 보유 문항: "
         f"{summary['dataset']['gold_evidence_question_count']} / "
         f"{summary['dataset']['question_count']}"),
        f"- 페이지 정답이 없는 문항: {summary['dataset']['no_gold_evidence_question_count']}",
        f"- 답변 실행 / 채점 결과: {overall['run_count']} / {overall['score_count']}",
        "- 합본의 Development·Additional·Custom 문항은 각 구성 세트에 다시 더하지 않음",
        "",
        "## 전체 고유 문항 micro", "",
        "| 평가 항목 | 평균 (채점; 해당 없음; 평가 불가; 누락) |", "| --- | --- |",
    ]
    for path, label in key_metrics:
        if path in metrics:
            lines.append(f"| {label} | {_metric_display(metrics, path)} |")

    all_macro = overall["all_group_macro"]
    lines.extend([
        "", "## 전체 primary-group macro", "",
        (f"동일 가중치 평균, {overall['all_group_macro_group_count']}개 분리 그룹. "
         "Development·Additional·Custom도 포함하므로 카테고리 180 macro와 별도 지표다."),
        "", "| 평가 항목 | 그룹 평균 | 점수 있는 그룹 / 전체 그룹 |", "| --- | ---: | ---: |",
    ])
    for path, label in key_metrics:
        item = all_macro.get(path)
        if item is not None:
            mean = item["mean"]
            lines.append(
                f"| {label} | {mean if mean is not None else '—'} | "
                f"{item['categories_with_scores']} / {overall['all_group_macro_group_count']} |"
            )

    category_180 = summary["category_180"]
    lines.extend([
        "", "## 카테고리 180", "",
        (f"총 {category_180['question_count']}문항, {category_180['group_count']}개 동일 가중치 그룹: "
         + ", ".join(
             f"{name} {row['question_count']}문항"
             for name, row in category_180["groups"].items()
         )),
        "Development·Additional·Custom은 포함하지 않는다. 아래 micro는 180문항 전체 기준, "
        "macro는 위 9개 그룹을 같은 가중치로 계산한다.",
        "", "| 평가 항목 | category 180 micro | category 180 macro |", "| --- | --- | --- |",
    ])
    for path, label in key_metrics:
        micro = category_180["metrics"].get(path)
        macro = category_180["macro"].get(path)
        if micro is not None and macro is not None:
            lines.append(
                f"| {label} | {_metric_display(category_180['metrics'], path)} | "
                f"{macro['mean'] if macro['mean'] is not None else '—'} "
                f"({macro['categories_with_scores']}/{category_180['group_count']} 그룹) |"
            )

    page = overall["page_presence_diagnosis"]
    lines.extend([
        "", "## 정답 페이지 존재 진단", "",
        "이 표는 페이지 ID가 후보·최종 문맥에 있었는지만 봅니다. 의미상 사실 인식이나 답변 정확도를 대신하지 않습니다.",
        "", "| 첫 페이지 누락 단계 | 문항 수 |", "| --- | ---: |",
    ])
    for stage, count in page["first_failure_stage_counts"].items():
        lines.append(f"| {PAGE_PRESENCE_DIAGNOSIS_LABELS[stage]} | {count} |")

    semantic = overall["semantic_attribution"]
    lines.extend([
        "", "## 의미상 필수 사실 진단", "",
        "Judge가 검수한 필수 사실별로 최종 문맥의 인용 지지와 답변의 정확성을 분리합니다. "
        "Primary attribution은 사실마다 하나, failure tags는 사실마다 여러 개일 수 있으며, first-loss stage는 최초 손실 위치만 셉니다.",
        "", "### 질문 판정 상태", "", "| 상태 | 질문 수 |", "| --- | ---: |",
    ])
    for status, count in semantic["question_status_counts"].items():
        lines.append(f"| {status} | {count} |")

    for title, fact_key, question_key, label_map in (
        ("Primary attribution", "primary_attribution_fact_counts", "primary_attribution_question_counts", None),
        ("최초 손실 단계", "first_loss_stage_fact_counts", "first_loss_stage_question_counts", SEMANTIC_FIRST_LOSS_LABELS),
        ("다중 failure tags", "failure_tag_fact_counts", "failure_tag_question_counts", SEMANTIC_FIRST_LOSS_LABELS),
    ):
        lines.extend(["", f"### {title}", "", "| 분류 | 사실 항목 수 | 포함된 질문 수 |", "| --- | ---: | ---: |"])
        for label, facts, questions in _count_table_rows(
            semantic[fact_key], semantic[question_key], label_map
        ):
            lines.append(f"| {label} | {facts} | {questions} |")

    lines.extend([
        "", "## 모든 primary group 결과", "",
        (f"{overall['all_group_macro_group_count']}개 분리 그룹이며 Development·Additional·Custom을 포함한다. "
         "카테고리 180과 혼동하지 않도록 별도 표로 둔다."),
        "", "| 그룹 | 문항 | 실행 | Faithfulness | Judge 근거 | 최종 전체 페이지 근거 | 최종 문맥 전체 사실 | 답변 전체 사실 |",
        "| --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ])
    for category, row in summary["categories"].items():
        group_metrics = row["metrics"]
        lines.append(
            f"| {category} | {row['question_count']} | {row['run_count']} | "
            f"{_metric_display(group_metrics, 'faithfulness')} | "
            f"{_metric_display(group_metrics, 'judge.evidence')} | "
            f"{_metric_display(group_metrics, 'table_rag_metrics.final_required_evidence_all')} | "
            f"{_metric_display(group_metrics, 'semantic_final_fact_all')} | "
            f"{_metric_display(group_metrics, 'semantic_answer_fact_all')} |"
        )

    lines.extend([
        "", "## 카테고리 180의 9개 그룹 결과", "",
        "| 그룹 | 문항 | 실행 | 최종 페이지 근거 | 최종 문맥 전체 사실 | 답변 전체 사실 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ])
    for name, row in category_180["groups"].items():
        group_metrics = row["metrics"]
        lines.append(
            f"| {name} | {row['question_count']} | {row['run_count']} | "
            f"{_metric_display(group_metrics, 'table_rag_metrics.final_required_evidence_all')} | "
            f"{_metric_display(group_metrics, 'semantic_final_fact_all')} | "
            f"{_metric_display(group_metrics, 'semantic_answer_fact_all')} |"
        )

    lines.extend([
        "", "## 세부 질문 유형", "", "| 유형 | 문항 | Faithfulness | 최종 전체 페이지 근거 |", "| --- | ---: | --- | --- |",
    ])
    for question_type, row in summary["question_types"].items():
        lines.append(
            f"| {question_type} | {row['question_count']} | "
            f"{_metric_display(row['metrics'], 'faithfulness')} | "
            f"{_metric_display(row['metrics'], 'table_rag_metrics.final_required_evidence_all')} |"
        )

    lines.extend(["", "## 검산 상태", "", "| 상태 | 문항 수 |", "| --- | ---: | --- |"])
    for status, count in summary["calculation_checks"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## 답변·평가 사용량과 지연", ""])
    generation = summary["operations"]["generation"]
    latency = generation["latency"]
    lines.append(
        f"- 답변 실행 {generation['requests']}건, p50 {latency['p50_ms']}, "
        f"p95 {latency['p95_ms']} ms; 입력 {generation['input_tokens']['total']}, "
        f"출력 {generation['output_tokens']['total']} 토큰, "
        f"모델 호출 {generation['logical_calls']['total']}"
    )
    for grader, row in summary["operations"]["grading_by_metric"].items():
        latency = row["latency"]
        estimate = row["request_estimate"]
        estimate_note = (
            f", 요청 추정 {estimate['total']} ({estimate['source']})"
            if estimate.get("total") is not None else ""
        )
        lines.append(
            f"- {grader}: 기록 {row['recorded_calls']}건, 미기록 {row['missing_usage_records']}건, "
            f"실패 {row['failed_calls']}건, p50 {latency['p50_ms']}, p95 {latency['p95_ms']} ms; "
            f"논리 호출 {_counter_display(row['logical_calls'])}; "
            f"논리 시도 {_counter_display(row['logical_attempts'])}; "
            f"입력 토큰 {_counter_display(row['input_tokens'])}; "
            f"출력 토큰 {_counter_display(row['output_tokens'])}; "
            f"reasoning 토큰 {_counter_display(row['reasoning_tokens'])}; "
            f"캐시 입력 {_counter_display(row['cached_input_tokens'])}{estimate_note}"
        )
    if summary["security_tasks"]["links"]:
        lines.extend(["", "보안 과제 두 건은 기존 질문을 재사용하며 이 보고서에서 실행하거나 일반 점수에 섞지 않았습니다."])
    lines.extend([
        "", "질문별 태그와 페이지·의미 진단은 question_trace.csv와 question_trace.md에 있습니다. "
        "집계 JSON에는 전체, 카테고리, category 180 micro/macro, 모든 primary-group macro가 별도 저장됩니다. "
        "질문·답변·문맥 원문은 보고서에 복사하지 않습니다.", "",
    ])
    return "\n".join(lines)


def write_benchmark_report(
    directory: Path,
    inventory: BenchmarkInventory,
    runs: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
) -> Path:
    """Write local-only summary/report tables without answer or source text."""
    directory.mkdir(parents=True, exist_ok=True)
    summary = summarize_benchmark(inventory, runs, scores)
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    category_rows = []
    for metric_path, metric in summary["overall"]["metrics"].items():
        category_rows.append(
            {
                "group_type": "overall",
                "group": "all_unique_questions",
                "question_count": summary["overall"]["question_count"],
                "metric": metric_path,
                **metric,
            }
        )
    for metric_path, metric in summary["overall"]["all_group_macro"].items():
        category_rows.append(
            {
                "group_type": "all_group_macro",
                "group": "equal_weight_all_primary_groups",
                "group_count": summary["overall"]["all_group_macro_group_count"],
                "question_count": summary["overall"]["question_count"],
                "metric": metric_path,
                "mean": metric["mean"],
                "scored": metric["categories_with_scores"],
                "not_applicable": None,
                "unavailable": None,
                "missing": metric["categories_without_scores"],
            }
        )
    for metric_path, metric in summary["category_180"]["metrics"].items():
        category_rows.append(
            {
                "group_type": "category_180_micro",
                "group": "scenario_1_to_8_plus_work_rules",
                "group_count": summary["category_180"]["group_count"],
                "question_count": summary["category_180"]["question_count"],
                "metric": metric_path,
                **metric,
            }
        )
    for metric_path, metric in summary["category_180"]["macro"].items():
        category_rows.append(
            {
                "group_type": "category_180_macro",
                "group": "equal_weight_nine_groups",
                "group_count": summary["category_180"]["group_count"],
                "question_count": summary["category_180"]["question_count"],
                "metric": metric_path,
                "mean": metric["mean"],
                "scored": metric["categories_with_scores"],
                "not_applicable": None,
                "unavailable": None,
                "missing": metric["categories_without_scores"],
            }
        )
    for category, row in summary["category_180"]["groups"].items():
        for metric_path, metric in row["metrics"].items():
            category_rows.append(
                {
                    "group_type": "category_180_group",
                    "group": category,
                    "group_count": summary["category_180"]["group_count"],
                    "question_count": row["question_count"],
                    "metric": metric_path,
                    **metric,
                }
            )
    for category, row in summary["categories"].items():
        for metric_path, metric in row["metrics"].items():
            category_rows.append(
                {
                    "group_type": "category",
                    "group": category,
                    "question_count": row["question_count"],
                    "metric": metric_path,
                    **metric,
                }
            )
    for question_type, row in summary["question_types"].items():
        for metric_path, metric in row["metrics"].items():
            category_rows.append(
                {
                    "group_type": "question_type",
                    "group": question_type,
                    "question_count": row["question_count"],
                    "metric": metric_path,
                    **metric,
                }
            )
    _write_csv(directory / "category_metrics.csv", category_rows)
    trace_rows = _trace_rows(inventory, runs, scores)
    _write_csv(directory / "question_trace.csv", trace_rows)
    (directory / "question_trace.md").write_text(
        _render_question_trace_markdown(trace_rows), encoding="utf-8"
    )
    (directory / "report.md").write_text(
        _render_benchmark_markdown(summary), encoding="utf-8"
    )
    return directory
