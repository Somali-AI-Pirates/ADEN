"""Write a sanitized comparison report for scored retrieval and CPU timing runs."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .evaluation import (
    ARMS,
    _fail,
    _object_sha,
    _read_json,
    _read_jsonl,
    _runtime_fingerprint,
    _sha256_file,
    _write_json,
)

TIMING_STAGES = (
    "query_embedding",
    "bm25",
    "dense",
    "relation",
    "rerank",
    "restore",
    "total",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise _fail("CPU timing에 표본이 없습니다.")
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _timing_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm in ARMS:
        records = [trace for trace in traces if trace["arm"] == arm]
        arm_summary: dict[str, Any] = {"n": len(records)}
        for stage in TIMING_STAGES:
            values = [float(trace["timing_ms"][stage]) for trace in records]
            arm_summary[stage] = {
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
            }
        result[arm] = arm_summary
    return result


def _close(left: Any, right: float) -> bool:
    return isinstance(left, (int, float)) and math.isclose(
        float(left), right, rel_tol=1e-8, abs_tol=1e-6
    )


def _validate_summary(
    summary: Any, computed: dict[str, Any], timing_ids: list[str]
) -> list[dict[str, Any]]:
    if not isinstance(summary, dict) or not isinstance(summary.get("arms"), dict):
        raise _fail("timing summary 형식이 잘못됐습니다.")
    if summary.get("timing_ids") != timing_ids:
        raise _fail("timing summary의 timing_ids가 timing manifest와 다릅니다.")
    reported_arms = summary["arms"]
    if set(reported_arms) != set(ARMS):
        raise _fail("timing summary가 A/B/C arms를 모두 포함하지 않습니다.")
    for arm in ARMS:
        if not isinstance(reported_arms[arm], dict):
            raise _fail("timing summary arm 형식이 잘못됐습니다.")
        for stage in TIMING_STAGES:
            reported = reported_arms[arm].get(stage)
            expected = computed[arm][stage]
            if (
                not isinstance(reported, dict)
                or not _close(reported.get("p50_ms"), expected["p50_ms"])
                or not _close(reported.get("p95_ms"), expected["p95_ms"])
            ):
                raise _fail("timing summary percentiles가 traces.jsonl과 다릅니다.")
            if reported.get("count") != computed[arm]["n"]:
                raise _fail("timing summary count가 traces.jsonl과 다릅니다.")
    differences = summary.get("gpu_cpu_context_differences")
    if not isinstance(differences, list):
        raise _fail("timing summary의 GPU/CPU 비교 형식이 잘못됐습니다.")
    difference_keys: set[tuple[str, str, int]] = set()
    for item in differences:
        if (
            not isinstance(item, dict)
            or item.get("id") not in timing_ids
            or item.get("arm") not in ARMS
            or item.get("repeat") not in (1, 2, 3)
            or not isinstance(item.get("gpu_sha"), str)
            or not isinstance(item.get("cpu_sha"), str)
            or not isinstance(item.get("same_final_sources"), bool)
        ):
            raise _fail("timing summary의 GPU/CPU 차이 항목이 잘못됐습니다.")
        key = (item["id"], item["arm"], item["repeat"])
        if key in difference_keys:
            raise _fail("timing summary에 중복 GPU/CPU 차이 항목이 있습니다.")
        difference_keys.add(key)
        if any(
            len(item[key]) != 64 or any(c not in "0123456789abcdef" for c in item[key])
            for key in ("gpu_sha", "cpu_sha")
        ):
            raise _fail("timing summary에 잘못된 context hash가 있습니다.")
    return differences


def _validate_timing(
    timing_dir: Path, scores: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest, _ = _read_json(timing_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise _fail("timing manifest가 completed 상태가 아닙니다.")
    score_inputs = scores.get("inputs", {})
    for key in (
        "questions_sha256",
        "corpus_sha256",
        "evaluation_manifest_sha256",
    ):
        if manifest.get(key) != score_inputs.get(key):
            raise _fail(f"timing manifest의 {key}가 scores.json과 다릅니다.")
    if manifest.get("gpu_manifest_sha256") != score_inputs.get(
        "retrieval_manifest_sha256"
    ):
        raise _fail(
            "timing manifest의 GPU/retrieval manifest 해시가 scores.json과 다릅니다."
        )
    for key in ("vectors_manifest_sha256", "vectors_sha256"):
        if manifest.get(key) != score_inputs.get(key):
            raise _fail(f"timing manifest의 {key}가 scores.json과 다릅니다.")
    if _object_sha(manifest.get("model_assets")) != score_inputs.get(
        "model_assets_sha256"
    ):
        raise _fail("timing과 retrieval의 model_assets가 다릅니다.")
    code_hash = _runtime_fingerprint(
        manifest.get("runtime_files"), manifest.get("code_sha256")
    )
    if code_hash != score_inputs.get("code_sha256"):
        raise _fail("timing과 retrieval의 runtime code hash가 다릅니다.")
    timing_ids = manifest.get("timing_ids")
    if (
        not isinstance(timing_ids, list)
        or len(timing_ids) != 30
        or len(set(timing_ids)) != 30
        or timing_ids != score_inputs.get("timing_ids")
    ):
        raise _fail("timing manifest의 timing_ids가 frozen evaluation과 다릅니다.")
    if (
        manifest.get("device") != "cpu"
        or manifest.get("threads") != 4
        or manifest.get("repeats") != 3
        or not isinstance(manifest.get("arms"), list)
        or set(manifest["arms"]) != set(ARMS)
    ):
        raise _fail("timing manifest의 CPU/repeat/arm 조건이 계약과 다릅니다.")

    traces = _read_jsonl(timing_dir / "traces.jsonl")
    if manifest.get("traces_sha256") != _sha256_file(timing_dir / "traces.jsonl"):
        raise _fail("timing manifest의 traces_sha256가 traces.jsonl과 다릅니다.")
    if (
        manifest.get("trace_count") != 30 * len(ARMS) * 3
        or len(traces) != 30 * len(ARMS) * 3
    ):
        raise _fail("timing traces.jsonl의 레코드 수가 30×A/B/C×3과 다릅니다.")
    expected_keys = {
        (question_id, arm, repeat)
        for question_id in timing_ids
        for arm in ARMS
        for repeat in (1, 2, 3)
    }
    found: set[tuple[str, str, int]] = set()
    for trace in traces:
        question_id, arm, repeat = (
            trace.get("id"),
            trace.get("arm"),
            trace.get("repeat"),
        )
        if (
            question_id not in timing_ids
            or arm not in ARMS
            or repeat not in (1, 2, 3)
            or trace.get("device") != "cpu"
        ):
            raise _fail("timing trace에 범위 밖 ID/arm/repeat 또는 device가 있습니다.")
        key = (question_id, arm, repeat)
        if key in found:
            raise _fail("timing traces.jsonl에 중복 ID/arm/repeat가 있습니다.")
        found.add(key)
        times = trace.get("timing_ms")
        if not isinstance(times, dict) or any(
            not isinstance(times.get(stage), (int, float))
            or isinstance(times.get(stage), bool)
            or not math.isfinite(float(times[stage]))
            or float(times[stage]) < 0
            for stage in TIMING_STAGES
        ):
            raise _fail("timing trace의 timing_ms 값이 잘못됐습니다.")
    if found != expected_keys:
        raise _fail(
            "timing traces.jsonl이 timing ID×arm×repeat를 모두 포함하지 않습니다."
        )

    computed = _timing_summary(traces)
    summary, _ = _read_json(timing_dir / "summary.json")
    differences = _validate_summary(summary, computed, timing_ids)
    return manifest, {"arms": computed}, differences


def _comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if before["final_requirements"] == 0 or after["final_requirements"] == 0:
        return {"status": "not_applicable", "final_facts_delta": None}
    if before["final_facts"] is None or after["final_facts"] is None:
        return {"status": "missing_stage", "final_facts_delta": None}
    delta = after["final_facts"] - before["final_facts"]
    status = "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
    return {"status": status, "final_facts_delta": delta}


def _question_comparisons(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for question in questions:
        arms = question["arms"]
        first_missing = {arm: arms[arm]["first_missing_stage"] for arm in ARMS}
        result.append(
            {
                "id": question["id"],
                "split": question["split"],
                "category": question["category"],
                "final_facts": {arm: arms[arm]["final_facts"] for arm in ARMS},
                "final_complete": {arm: arms[arm]["final_complete"] for arm in ARMS},
                "A_to_B": _comparison(arms["A"], arms["B"]),
                "B_to_C": _comparison(arms["B"], arms["C"]),
                "C_classification": {
                    "expected": arms["C"]["expected_resolution"],
                    "observed": arms["C"]["observed_resolution"],
                    "correct": arms["C"]["resolution_status_correct"],
                    "exact_match": arms["C"]["resolution_exact_match"],
                    "abstention_interpreted": arms["C"][
                        "abstention_interpretation_correct"
                    ],
                    "false_confirmation": arms["C"]["false_confirmation"],
                    "false_positive": arms["C"]["false_positive"],
                    "abstain": arms["C"]["abstain"],
                },
                "first_missing_stage": first_missing,
            }
        )
    return result


def _pct(value: Any) -> str:
    return "N/A" if value is None else f"{100 * float(value):.1f}%"


def _num(value: Any, digits: int = 3) -> str:
    return "N/A" if value is None else f"{float(value):.{digits}f}"


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _comparison_label(value: str) -> str:
    return {
        "improved": "개선",
        "regressed": "하락",
        "unchanged": "변화 없음",
        "missing_stage": "단계 누락",
        "not_applicable": "해당 없음",
    }.get(value, value)


def _metric_table(summary: dict[str, Any], title: str) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = summary["arms"][arm]
        lines.append(
            "| {arm} | {n} | {r20} | {r8} | {mrr} | {ndcg} | {cf} | {cc} | {ff} | {fc} |".format(
                arm=arm,
                n=row["n"],
                r20=_pct(row["recall20"]),
                r8=_pct(row["recall8"]),
                mrr=_num(row["mrr8"]),
                ndcg=_num(row["ndcg8"]),
                cf=_pct(row["candidate_fact_coverage"]),
                cc=_pct(row["candidate_complete_rate"]),
                ff=_pct(row["final_fact_coverage"]),
                fc=_pct(row["final_complete_rate"]),
            )
        )
    lines.append("")
    return lines


def _classification_table(summary: dict[str, Any]) -> list[str]:
    lines = [
        "| Arm | Target accuracy (N) | Status accuracy (N) | Exact status match (N) | Safe abstention mapping (N) | False confirmation (N) | False positive (N) | Abstain (N) | Ambiguous false confirmation (N) | Ambiguous false positive (N) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = summary["arms"][arm]
        if arm != "C":
            lines.append(
                f"| {arm} | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |"
            )
            continue
        lines.append(
            "| C | {target} ({target_n}) | {status} ({status_n}) | {exact} ({exact_n}) | {safe_abstain} ({safe_abstain_n}) | {false_rate} ({false_n}) | {fp_rate} ({fp_n}) | {abstain_rate} ({abstain_n}) | {ambiguous_rate} ({ambiguous_n}) | {ambiguous_fp_rate} ({ambiguous_fp_n}) |".format(
                target=_pct(row["target_accuracy"]),
                target_n=row["target_accuracy_n"],
                status=_pct(row["resolution_status_accuracy"]),
                status_n=row["resolution_status_accuracy_n"],
                exact=_pct(row["resolution_exact_match_rate"]),
                exact_n=row["resolution_exact_match_n"],
                safe_abstain=_pct(row["abstention_interpretation_rate"]),
                safe_abstain_n=row["abstention_interpretation_n"],
                false_rate=_pct(row["false_confirmation_rate"]),
                false_n=row["false_confirmation_n"],
                fp_rate=_pct(row["false_positive_rate"]),
                fp_n=row["false_positive_n"],
                abstain_rate=_pct(row["abstain_rate"]),
                abstain_n=row["abstain_n"],
                ambiguous_rate=_pct(row["ambiguous_false_confirmation_rate"]),
                ambiguous_n=row["ambiguous_false_confirmation_n"],
                ambiguous_fp_rate=_pct(row["ambiguous_false_positive_rate"]),
                ambiguous_fp_n=row["ambiguous_false_positive_n"],
            )
        )
    lines.append("")
    return lines


def _render_report(
    scores: dict[str, Any],
    timing: dict[str, Any],
    differences: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> str:
    lines = [
        "# 패턴 검색 평가 보고서",
        "",
        "이 보고서에는 문항 ID와 집계 지표만 있습니다. 질문 원문, 정답 인용, 사건 ID, 출처 ID, 검색된 필드 값은 포함하지 않습니다.",
        "",
        "일반 검색 지표는 `ambiguous` category를 제외합니다. requirements가 비어 있으면 N/A로 보고합니다. Recall@20은 candidate pool의 고유 페이지, Recall@8은 최종 페이지를 사용합니다. MRR/nDCG@8은 리랭킹·중복 제거·문맥 예산을 적용한 실제 최종 페이지 순서를 사용합니다.",
        "",
        "## 검색 품질",
        "",
    ]
    lines.extend(
        _metric_table(scores["summary"]["performance"], "전체 (ambiguous 제외)")
    )
    for split in ("development", "validation"):
        lines.extend(
            _metric_table(
                scores["summary"]["by_split"][split],
                "개발" if split == "development" else "검증",
            )
        )
    for split in ("development", "validation"):
        for category in (
            "exact",
            "variant",
            "paraphrase",
            "comparison",
            "mixed",
            "ambiguous",
        ):
            lines.extend(
                _metric_table(
                    scores["summary"]["by_split_category"][split][category],
                    f"{split} / {category}",
                )
            )
    lines.extend(
        _metric_table(scores["summary"]["ambiguous"], "모호성 문항 (별도 집계)")
    )
    index = scores["summary"]["index"]
    lines.extend(
        [
            "## 검색 index 및 임베딩",
            "",
            f"출처 문서 수: {index['source_count']}; 원문 페이지: {index['page_count']}; occurrences: {index['occurrence_count']}; code links: {index['code_link_count']}.",
            f"Units: A={index['unit_counts']['A']}, B={index['unit_counts']['B']}; corpus.sqlite3={index['database_bytes']:,} bytes; vectors.npy={index['vectors_bytes']:,} bytes; vector dimension={index['vector_dimension']}.",
            f"Embedding time={index['embedding_seconds']:.2f}s; raw input tokens={index['raw_tokens']:,}; unit input tokens (identical-text reuse included)={index['input_tokens']:,}; truncated units={index['truncated_units']:,}.",
            "",
        ]
    )
    lines.extend(
        [
            "## 조건별 검색량",
            "",
            "워밍업을 제외한 측정 요청 기준이다. 후보 단위 수가 같아도 고유 원본 페이지 수와 입력 토큰 수가 같지는 않다. 모든 외부 API 호출은 0회다.",
            "",
            "| 세트 | 조건 | 요청/리랭크 호출 | 리랭크 쌍 | 리랭크 토큰 | 절단 쌍 | 후보 쪽 평균 | 최종 쪽 평균 | 문맥 토큰 평균 | 관계 발동 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for split, arms in scores["summary"]["work"].items():
        for arm, work in arms.items():
            lines.append(
                f"| {split} | {arm} | {work['requests']} | {work['rerank_pairs']} | {work['rerank_tokens']:,} | {work['rerank_truncated']} | {_num(work['mean_candidate_pages'], 2)} | {_num(work['mean_final_pages'], 2)} | {_num(work['mean_context_tokens'], 0)} | {work['relation_triggered']} |"
            )
    lines.append("")
    lines.extend(
        [
            "## C target 및 오확정 점검",
            "",
            "Target accuracy는 unique 또는 multiple target이 기대된 문항에서 case 집합까지 정확히 일치해야 합니다. Exact status match와 `none`/`unknown_code`/`unsupported_exclusion`/`too_many_targets` 등을 모호성·충돌·비적용에 대한 안전한 보류로 해석한 비율을 분리했습니다. False confirmation은 잘못된 단일 target 확정, false positive는 잘못된 target 주장을 뜻합니다. Abstain은 target이 기대됐지만 보류 status로 응답한 비율입니다. ambiguous 전용 false-confirmation/false-positive는 별도 집계합니다.",
            "",
        ]
    )
    lines.extend(_classification_table(scores["summary"]["performance"]))
    lines.extend(_classification_table(scores["summary"]["ambiguous"]))
    lines.extend(
        [
            "## CPU 지연 시간",
            "",
            "각 수치는 고정된 검증 문항 30개, 3회 반복, CPU 스레드 4개 기준입니다.",
            "",
            "| Arm | Stage | N | p50 (ms) | p95 (ms) |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for arm in ARMS:
        row = timing["arms"][arm]
        for stage in TIMING_STAGES:
            values = row[stage]
            lines.append(
                f"| {arm} | {stage} | {row['n']} | {values['p50_ms']:.2f} | {values['p95_ms']:.2f} |"
            )
    same_final = sum(item["same_final_sources"] for item in differences)
    different_final = len(differences) - same_final
    lines.extend(
        [
            "",
            f"GPU/CPU context hash 차이: {len(differences)}건; 최종 출처 순서 동일: {same_final}건; 상이: {different_final}건.",
            "",
            "## 문항별 A→B 및 B→C 변화",
            "",
            "`final facts`는 충족한 reference requirement 수입니다. `first missing`은 arm별 가장 먼저 생략된 검색 단계입니다. requirement가 N/A면 complete로 세지 않습니다.",
            "",
            "| ID | Split | Category | Final facts A/B/C | A→B | B→C | C class expected/observed | C correct | Exact match | Safe abstention | False confirmation | False positive | Abstain | First missing A/B/C |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in comparisons:
        cclass = item["C_classification"]
        missing = item["first_missing_stage"]
        final_facts = item["final_facts"]
        lines.append(
            "| {id} | {split} | {category} | {facts} | {ab} ({ab_delta}) | {bc} ({bc_delta}) | {expected}/{observed} | {correct} | {exact} | {safe_abstention} | {false} | {fp} | {abstain} | {missing} |".format(
                id=_md_cell(item["id"]),
                split=item["split"],
                category=item["category"],
                facts="/".join(
                    "N/A" if final_facts[arm] is None else str(final_facts[arm])
                    for arm in ARMS
                ),
                ab=_comparison_label(item["A_to_B"]["status"]),
                ab_delta="N/A"
                if item["A_to_B"]["final_facts_delta"] is None
                else f"{item['A_to_B']['final_facts_delta']:+d}",
                bc=_comparison_label(item["B_to_C"]["status"]),
                bc_delta="N/A"
                if item["B_to_C"]["final_facts_delta"] is None
                else f"{item['B_to_C']['final_facts_delta']:+d}",
                expected=cclass["expected"],
                observed=cclass["observed"] or "missing",
                correct="N/A"
                if cclass["correct"] is None
                else str(cclass["correct"]).lower(),
                exact="N/A"
                if cclass["exact_match"] is None
                else str(cclass["exact_match"]).lower(),
                safe_abstention="N/A"
                if cclass["abstention_interpreted"] is None
                else str(cclass["abstention_interpreted"]).lower(),
                false="N/A"
                if cclass["false_confirmation"] is None
                else str(cclass["false_confirmation"]).lower(),
                fp="N/A"
                if cclass["false_positive"] is None
                else str(cclass["false_positive"]).lower(),
                abstain="N/A"
                if cclass["abstain"] is None
                else str(cclass["abstain"]).lower(),
                missing="/".join(missing[arm] or "—" for arm in ARMS),
            )
        )
    lines.append("")
    return "\n".join(lines)


def make_report(
    scored_dir: str | Path, timing_dir: str | Path, output_dir: str | Path
) -> dict[str, Any]:
    """Validate score/timing artifacts and write a sanitized Markdown report."""
    scored_dir = Path(scored_dir)
    timing_dir = Path(timing_dir)
    output_dir = Path(output_dir)
    scores, _ = _read_json(scored_dir / "scores.json")
    if (
        not isinstance(scores, dict)
        or scores.get("schema_version") != "pattern-retrieval-scores-v1"
    ):
        raise _fail("scores.json schema_version이 지원되지 않습니다.")
    questions = scores.get("questions")
    if not isinstance(questions, list) or any(
        not isinstance(item, dict) for item in questions
    ):
        raise _fail("scores.json의 questions 형식이 잘못됐습니다.")
    seen_ids: set[str] = set()
    for question in questions:
        if (
            not isinstance(question.get("id"), str)
            or question["id"] in seen_ids
            or question.get("split") not in ("development", "validation")
            or question.get("category")
            not in (
                "exact",
                "variant",
                "paraphrase",
                "comparison",
                "mixed",
                "ambiguous",
            )
            or not isinstance(question.get("arms"), dict)
            or set(question["arms"]) != set(ARMS)
        ):
            raise _fail("scores.json에 유효하지 않은 문항 지표가 있습니다.")
        seen_ids.add(question["id"])

    if scores["inputs"].get("scorer_sha256") != _sha256_file(
        Path(__file__).with_name("evaluation.py")
    ):
        raise _fail("채점 코드가 scores 생성 이후 바뀌었습니다.")
    _, timing, differences = _validate_timing(timing_dir, scores)
    comparisons = _question_comparisons(questions)
    report = {
        "schema_version": "pattern-retrieval-report-v1",
        "implementation_sha256": {
            "evaluation": scores["inputs"]["scorer_sha256"],
            "reporting": _sha256_file(Path(__file__)),
        },
        "summary": scores["summary"],
        "timing": timing,
        "gpu_cpu_context_differences": {
            "count": len(differences),
            "same_final_sources": sum(
                item["same_final_sources"] for item in differences
            ),
            "different_final_sources": sum(
                not item["same_final_sources"] for item in differences
            ),
            "samples": [
                {
                    key: item[key]
                    for key in ("id", "arm", "repeat", "same_final_sources")
                }
                for item in differences
            ],
        },
        "questions": comparisons,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "report.json", report)
    (output_dir / "report.md").write_text(
        _render_report(scores, timing, differences, comparisons), encoding="utf-8"
    )
    return report
