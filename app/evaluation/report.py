"""로컬 전체 기록(artifacts/)과 제출 결과(reports/<experiment_id>/) 파일 생성."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.observability.records import RETRIEVAL_MODES
from app.evaluation.graders import CRITERIA
from app.evaluation.metrics import aggregate, operational_summary, score

SAMPLE_BANNER = ("> **가상 샘플 결과** — 모의 서비스·모의 평가기로 만든 검증용 파일이다. "
                 "실제 Basic 데이터셋 성능이 아니며 발표·비교에 사용하지 않는다.")
JUDGE_LABELS = {"fact": "사실", "condition": "조건", "evidence": "근거", "decision": "판단"}


def write_json(path: Path, data: Any) -> None:
    from app.evaluation.checkpoints import atomic_json
    atomic_json(path, data)


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fields = list(rows[0])
    else:
        fields = []
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: 엑셀에서 한글 깨짐 방지
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(runs: Sequence[Mapping[str, Any]], scores: Sequence[Mapping[str, Any]]) -> dict:
    """방식별 평가 요약·운영 지표. 평가 호출 사용량은 grading으로 분리한다."""
    summary = {}
    present = {run["retrieval_mode"] for run in runs}
    modes_with_runs = [mode for mode in RETRIEVAL_MODES if mode in present]
    modes_with_runs.extend(sorted(present - set(RETRIEVAL_MODES)))
    for mode in modes_with_runs:
        s = []
        for score_row in scores:
            if score_row["retrieval_mode"] == mode:
                s.append(score_row)
        grading = []
        for score_row in s:
            for usage in score_row["grading_usage"].values():
                if usage:
                    grading.append(usage)

        hit_scores = []
        for score_row in s:
            hit_scores.append(score_row["retrieval"]["hit_at_k"])
        recall_scores = []
        for score_row in s:
            recall_scores.append(score_row["retrieval"]["recall_at_k"])
        faithfulness_scores = []
        for score_row in s:
            faithfulness_scores.append(score_row["faithfulness"])
        mode_summary = {
            "hit_at_k": aggregate(hit_scores),
            "recall_at_k": aggregate(recall_scores),
            "faithfulness": aggregate(faithfulness_scores),
        }
        for criterion in CRITERIA:
            judge_scores = []
            for score_row in s:
                judge_scores.append(score_row["judge"][criterion])
            mode_summary[f"judge_{criterion}"] = aggregate(judge_scores)
        mode_runs = []
        for run in runs:
            if run["retrieval_mode"] == mode:
                mode_runs.append(run)
        mode_summary["operations"] = operational_summary(mode_runs)

        table_metrics = [row["table_rag_metrics"] for row in s if "table_rag_metrics" in row]
        if table_metrics:
            mode_summary["table_rag_metrics"] = {
                name: aggregate(row[name] for row in table_metrics)
                for name in table_metrics[0]
            }
            for key in ("logical_calls", "provider_attempts", "missing_usage"):
                counts = [run["usage"].get(key) for run in mode_runs]
                known = [count for count in counts if count is not None]
                mode_summary["operations"][key] = sum(known) if known else None

        failed_calls = 0
        for usage in grading:
            if usage["error"]:
                failed_calls += 1
        grading_latency_ms = 0
        for usage in grading:
            grading_latency_ms += usage["latency_ms"]
        has_input_tokens = False
        for usage in grading:
            if usage["input_tokens"] is not None:
                has_input_tokens = True
                break
        grading_input_tokens = None
        if has_input_tokens:
            grading_input_tokens = 0
            for usage in grading:
                grading_input_tokens += usage["input_tokens"] or 0
        has_output_tokens = False
        for usage in grading:
            if usage["output_tokens"] is not None:
                has_output_tokens = True
                break
        grading_output_tokens = None
        if has_output_tokens:
            grading_output_tokens = 0
            for usage in grading:
                grading_output_tokens += usage["output_tokens"] or 0
        mode_summary["grading"] = {"calls": len(grading),
                                   "failed_calls": failed_calls,
                                   "latency_ms": grading_latency_ms,
                                   "input_tokens": grading_input_tokens,
                                   "output_tokens": grading_output_tokens}
        mode_summary["grading"]["logical_calls"] = sum(
            usage.get("logical_calls", 1) for usage in grading
        )
        attempts = [usage.get("provider_attempts") for usage in grading]
        mode_summary["grading"]["provider_attempts"] = (
            sum(attempts) if attempts and all(value is not None for value in attempts) else None
        )
        reasoning = [usage.get("reasoning_tokens") for usage in grading]
        mode_summary["grading"]["reasoning_tokens"] = (
            sum(reasoning) if reasoning and all(value is not None for value in reasoning) else None
        )
        mode_summary["grading"]["usage_incomplete_calls"] = sum(
            usage.get("input_tokens") is None or usage.get("output_tokens") is None for usage in grading
        )
        summary[mode] = mode_summary
    for mode, stats in summary.items():
        diagnostics = [s.get("evidence_diagnostics", {}) for s in scores if s["retrieval_mode"] == mode]
        keys = sorted({key for d in diagnostics for key in d})
        stats["evidence_diagnostics"] = {
            key: aggregate(d.get(key) or score(None, "unavailable", "기록 없음") for d in diagnostics)
            for key in keys
        }
        # 검산 진단은 점수가 아니라 상태별 건수로 둔다. 진단이 없는 이전 기록은 missing으로 센다
        calculation_counts = {}
        for score_row in scores:
            if score_row["retrieval_mode"] != mode:
                continue
            status = (score_row.get("calculation_checks") or {}).get("status", "missing")
            calculation_counts[status] = calculation_counts.get(status, 0) + 1
        stats["calculation_checks"] = calculation_counts
    return summary


def comparison_rows(runs: Sequence[Mapping[str, Any]], scores: Sequence[Mapping[str, Any]],
                    data_kind: str) -> list[dict[str, Any]]:
    by_req = {}
    for score_row in scores:
        by_req[score_row["request_id"]] = score_row
    rows = []
    for r in runs:
        s = by_req[r["request_id"]]
        ret = s["retrieval"]
        row = {"data_kind": data_kind, "question_id": r["question_id"],
               "retrieval_mode": r["retrieval_mode"], "request_id": r["request_id"],
               "status": r["status"], "error_type": r.get("error_type"),
               "k": ret["k"], "delivered_chunks": ret["delivered_chunks"],
               "reference_books": ";".join(ret["reference_books"]),
               "retrieved_books": ";".join(ret["retrieved_books"] or []),
               "hit_at_k": ret["hit_at_k"]["value"], "hit_status": ret["hit_at_k"]["status"],
               "recall_at_k": ret["recall_at_k"]["value"],
               "recall_status": ret["recall_at_k"]["status"],
               "faithfulness": s["faithfulness"]["value"],
               "faithfulness_status": s["faithfulness"]["status"],
               "faithfulness_reason": s["faithfulness"]["reason"]}
        for key, item in s.get("evidence_diagnostics", {}).items():
            row[key] = item["value"]
            row[f"{key}_status"] = item["status"]
            row[f"{key}_reason"] = item["reason"]
        row["langfuse_score_status_counts"] = json.dumps(s.get("langfuse_score_status_counts", {}))
        for c in CRITERIA:
            row[f"judge_{c}"] = s["judge"][c]["value"]
            row[f"judge_{c}_status"] = s["judge"][c]["status"]
            row[f"judge_{c}_reason"] = s["judge"][c]["reason"]
        calculation = s.get("calculation_checks") or {}
        row["calculation_status"] = calculation.get("status")
        row["calculation_reason"] = calculation.get("reason")
        row["calculation_items"] = json.dumps(calculation.get("items", []), ensure_ascii=False)
        row.update(latency_ms=r["latency_ms"], input_tokens=r["usage"]["input_tokens"],
                   output_tokens=r["usage"]["output_tokens"], usage_status=r["usage"]["status"],
                   attempts=len(r["attempts"]), langfuse_trace_id=r.get("langfuse_trace_id"),
                   langfuse_status=r.get("langfuse_status"))
        rows.append(row)
    return rows


def answer_rows(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keys = ("question_id", "retrieval_mode", "request_id", "question", "status", "answer",
            "reason", "citations", "error_type", "latency_ms", "usage", "final_context_hash",
            "model_context_hash", "langfuse_trace_id", "langfuse_status",
            "langfuse_verification")
    answers = []
    for r in runs:
        answer = {}
        for key in keys:
            answer[key] = r.get(key)
        chunk_ids = []
        for chunk in r["final_context_chunks"] or []:
            chunk_ids.append(chunk["chunk_id"])
        answer["final_context_chunk_ids"] = chunk_ids
        answers.append(answer)
    return answers


def security_rows(rows: Sequence[Mapping[str, Any]], data_kind: str) -> list[dict[str, Any]]:
    table = []
    for r in rows:
        chunk_ids = []
        for chunk in r["final_context_chunks"]:
            chunk_ids.append(chunk["chunk_id"])
        judge_reasons = []
        for judged_key in ("followed_attack", "answer_correct", "over_refusal"):
            judge_reasons.append(f"{judged_key}: {r[judged_key]['reason']}")
        table.append({"data_kind": data_kind, "question_id": r["question_id"],
                      "prompt_variant": r["prompt_variant"], "retrieval_mode": r["retrieval_mode"],
                      "request_id": r.get("request_id"), "final_context_hash": r["final_context_hash"],
                      "model_context_hash": r.get("model_context_hash"),
                      "final_context_chunk_ids": ";".join(chunk_ids),
                      "status": r["status"], "error_type": r.get("error_type"),
                      "attack_in_context": r["attack_in_context"],
                      "attack_in_model_input": r["attack_in_model_input"],
                      "followed_attack": r["followed_attack"]["value"],
                      "answer_correct": r["answer_correct"]["value"],
                      "over_refusal": r["over_refusal"]["value"], "outcome": r["outcome"],
                      "defense_success": r["defense_success"], "answer": r.get("answer"),
                      "judge_reasons": " | ".join(judge_reasons),
                      "latency_ms": r.get("latency_ms"), "input_tokens": r["usage"]["input_tokens"],
                      "output_tokens": r["usage"]["output_tokens"]})
    return table


def _fmt(agg: Mapping[str, Any]) -> str:
    if agg["mean"] is None:
        mean = "-"
    else:
        mean = f"{agg['mean']:.3f}"
    return f"{mean} ({agg['scored']}/{agg['not_applicable']}/{agg['unavailable']})"


def _read_json(path: Path) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    else:
        return None


def render_readme(report_dir: Path) -> str:
    """폴더에 있는 결과 파일로 README를 만든다(일반 평가·보안 비교를 따로 실행해도 함께 표시)."""
    manifest = _read_json(report_dir / "manifest.json")
    summary = (_read_json(report_dir / "summary.json") or {}).get("modes")
    checks = _read_json(report_dir / "security_checks.json")
    sec_path = report_dir / "security_comparison.csv"
    if sec_path.exists():
        security = list(csv.DictReader(io.StringIO(sec_path.read_text(encoding="utf-8-sig"))))
    else:
        security = None

    lines = [f"# 평가 결과 — {manifest['experiment_id']}", ""]
    if manifest.get("sample"):
        lines += [SAMPLE_BANNER, ""]
    lines += [f"- 데이터: `{manifest['dataset_id']}` · 설정 해시: `{manifest['config_hash']}`",
              f"- 실행 코드: `{manifest.get('git_commit')}` (미커밋 변경: {manifest.get('git_dirty')})"]
    bench = manifest.get("benchmark")
    if bench:
        question_labels = []
        for question_id in bench["question_ids"]:
            question_labels.append(f"`{question_id}`")
        lines += [f"- 일반 평가 k={bench['k']}, 방식: {', '.join(bench['modes'])}",
                  f"- 선택 질문({len(bench['question_ids'])}개): "
                  + ", ".join(question_labels),
                  f"- 선택 이유: {bench.get('selection_reason') or '(미기재)'}",
                  f"- 평가기: Judge={bench.get('judge')}, Faithfulness={bench.get('faithfulness')}, "
                  f"Judge 프롬프트 `{(bench.get('judge_prompt') or '-')[:19]}`, "
                  f"출력 스키마 `{bench.get('judge_schema') or 'judge-output-v1(기록 없음)'}`",
                  f"- Langfuse 전송: `{bench.get('langfuse_flush')}` "
                  "(`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)",
                  f"- Langfuse 저장 확인(요청 수): `{bench.get('langfuse_verification')}` "
                  "(`verified`만 기대한 관측이 모두 조회된 것, `partial`은 일부만 조회, "
                  "`None`은 추적 비활성·어댑터 관리)"]
    if manifest.get("langfuse_scores"):
        lines += [f"- Langfuse 평가 점수 게시: `{manifest['langfuse_scores']}` (전송과 서버 저장 확인은 별도)"]
    regrade = manifest.get("regrade")
    if regrade:
        lines += [f"- 재채점: 원본 `{regrade['source_experiment_id']}`의 저장 답변을 다시 채점했다"
                  f"(답변 재생성 없음). Judge 프롬프트 `{regrade['judge_prompt_file']}`, "
                  f"Faithfulness {regrade['faithfulness']}"]

    if summary:
        lines += ["", "## 방식별 품질 요약", "",
                  "값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.", "",
                  "| 지표 | " + " | ".join(summary) + " |",
                  "| --- |" + " --- |" * len(summary)]
        metrics = [("Hit@k", "hit_at_k"), ("Recall@k", "recall_at_k"),
                   ("Faithfulness", "faithfulness")]
        for criterion in CRITERIA:
            metrics.append((f"Judge {JUDGE_LABELS[criterion]}", f"judge_{criterion}"))
        for label, key in metrics:
            cells = []
            for mode_name in summary:
                cells.append(_fmt(summary[mode_name][key]))
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        diagnostic_keys = sorted({key for mode in summary.values() for key in mode.get("evidence_diagnostics", {})})
        if diagnostic_keys:
            lines += ["", "## 검색·문맥 누락 진단", "",
                      "페이지 포함은 인용문 전체 보존을 뜻하지 않는다. 예산 제외 ID로 확인된 누락만 예산 누락으로 센다.", "",
                      "| 지표 | " + " | ".join(summary) + " |", "| --- |" + " --- |" * len(summary)]
            for key in diagnostic_keys:
                lines.append(f"| {key} | " + " | ".join(_fmt(summary[m].get("evidence_diagnostics", {}).get(key, aggregate([]))) for m in summary) + " |")
        calculation_cells = []
        for mode_name in summary:
            counts = summary[mode_name].get("calculation_checks") or {}
            parts = []
            for status, count in sorted(counts.items()):
                parts.append(f"{status} {count}")
            calculation_cells.append(", ".join(parts) or "-")
        lines += ["", "## 검산 진단 (Judge, 공식 점수 아님)", "",
                  "complete 검산 완료 / incomplete 요청받은 검산 미완료 / not_applicable 검산 요청 없음 / "
                  "unavailable 진단 불가(구조 오류·호출 실패·서비스 실패) / missing 진단이 없는 이전 기록. "
                  "대상별 부족 근거는 `comparison.csv`의 `calculation_items`에 있다.", "",
                  "| 방식 | 문항 수(상태별) |", "| --- | --- |"]
        for mode_name, cell in zip(summary, calculation_cells):
            lines.append(f"| {mode_name} | {cell} |")
        lines += ["", "## 운영 지표 (서비스 요청만, 평가 호출 제외)", "",
                  "| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for mode, s in summary.items():
            o = s["operations"]
            mean = o["latency_ok"]["mean_ms"]
            if mean is None:
                latency_label = "-"
            else:
                latency_label = f"{mean:.0f} ms"
            lines.append(
                f"| {mode} | {o['requests']} | {o['error_rate']:.1%} ({o['errors']}) | "
                f"{latency_label} | {o['input_tokens']} | "
                f"{o['output_tokens']} | {o['usage_complete']}/{o['usage_partial']}/"
                f"{o['usage_unknown']} | {o['retry_attempts']} |")
        lines += ["", "정상적인 추가 확인·보류는 오류가 아니다. 토큰 합계는 확인된 값만 더한 것이며 부분·미확인 건수를 함께 본다.",
                  "평가 호출(RAGAS·Judge)의 시간·토큰은 `summary.json`의 `grading`에 따로 기록한다."]

    if security is not None:
        sec = manifest.get("security") or {}
        lines += ["", "## 보안 비교", "",
                  f"검색 방식 `{sec.get('retrieval_mode')}`에서 질문별 검색·문맥 구성을 한 번만 수행하고 "
                  "같은 문맥을 기본/방어 프롬프트에 재사용했다.", "",
                  "| 질문 | 변형 | 로그 문맥 해시 | 모델 문맥 해시 | 모델 입력 노출 | 공격 추종 | 업무 정확 | 불필요 거절 | 결과 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in security:
            lines.append(
                f"| {r['question_id']} | {r['prompt_variant']} | `{r['final_context_hash'][:19]}` | "
                f"`{(r['model_context_hash'] or '-')[:12]}` | "
                f"{r['attack_in_model_input'] or '-'} | {r['followed_attack'] or '-'} | "
                f"{r['answer_correct'] or '-'} | {r['over_refusal'] or '-'} | {r['outcome']} |")
        if checks:
            lines += ["", "동일 문맥 확인(True 확인 / False 불일치 / None 확인 불가):", ""]
            for checked_id, check in checks.items():
                lines.append(
                    f"- {checked_id}: 로그 청크 {check['final_context_identical']}, 모델 문맥 문자열 "
                    f"{check['model_context_identical']}, 모델 입력에 문맥 포함 {check['context_in_model_input']}, "
                    f"생성 설정 {check['generation_settings_identical']}")
        lines += ["", "공격 미노출(`not_exposed`)·생성 실패(`generation_failed`)·미평가는 방어 성공으로 세지 않는다."]

    lines += ["", "## Langfuse 기록 확인", "",
              "각 요청의 trace ID는 `answers.jsonl`·`comparison.csv`의 `langfuse_trace_id`다. "
              "trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만들므로 request_id로도 다시 계산할 수 있다.",
              "", "```bash", "python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id>", "```", "",
              "Langfuse UI에서는 trace ID로 검색해 `rag-query` → `retrieval` → `final-context` → "
              "`generation-attempt-N` 순서를 확인한다.", "",
              "## 파일", "",
              "- `manifest.json`: 재현 설정 · `comparison.csv`: 질문×방식 점수·사유·운영값",
              "- `answers.jsonl`: 실제 답변·상태·인용·요청 ID · `scores.jsonl`: 평가 상세",
              "- `summary.json`: 방식별 요약 · `security_comparison.csv`: 보안 답변 4개", ""]
    return "\n".join(lines)


def public_score(row: Mapping[str, Any]) -> dict[str, Any]:
    """Raw evaluator inputs and claim quotations stay in private local details."""
    return {key: value for key, value in row.items() if key != "evaluation_details"}


def write_artifacts(exp_dir: Path, manifest: Mapping[str, Any], *, runs=None, scores=None,
                    security=None) -> None:
    """로컬 전체 기록. 최종 문맥 텍스트·모델 입력까지 포함하므로 Git에 올리지 않는다."""
    _merge_manifest(exp_dir, manifest)
    if runs is not None:
        write_jsonl(exp_dir / "runs.jsonl", runs)
    if scores is not None:
        write_jsonl(exp_dir / "scores.jsonl", [public_score(row) for row in scores])
        details = [
            {"experiment_id": row["experiment_id"], "question_id": row["question_id"],
             "request_id": row["request_id"], **row["evaluation_details"]}
            for row in scores if row.get("evaluation_details")
        ]
        if details:
            write_jsonl(exp_dir / "evaluation-details.jsonl", details)
    if security is not None:
        write_jsonl(exp_dir / "security_runs.jsonl", security)


def _merge_manifest(directory: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """같은 실험 ID의 일반 평가·보안 비교 설정을 한 manifest에 합친다. 샘플과 실제는 섞지 않는다."""
    old = _read_json(directory / "manifest.json") or {}
    if old and bool(old.get("sample")) != bool(manifest.get("sample")):
        raise ValueError(f"{directory}: 샘플 결과와 실제 결과를 같은 실험 ID에 섞을 수 없다")
    merged = {**old, **manifest}
    write_json(directory / "manifest.json", merged)
    return merged


def write_report(report_dir: Path, manifest: Mapping[str, Any], *,
                 runs: Sequence[Mapping[str, Any]] | None = None,
                 scores: Sequence[Mapping[str, Any]] | None = None,
                 security: Sequence[Mapping[str, Any]] | None = None,
                 security_checks: Mapping[str, Any] | None = None) -> Path:
    merged = _merge_manifest(report_dir, manifest)
    if merged.get("sample"):
        data_kind = "SAMPLE"
    else:
        data_kind = merged["dataset_id"]
    if runs is not None and scores is not None:
        write_json(report_dir / "summary.json",
                   {"data_kind": data_kind, "modes": summarize(runs, scores)})
        _write_csv(report_dir / "comparison.csv", comparison_rows(runs, scores, data_kind))
        write_jsonl(report_dir / "answers.jsonl", answer_rows(runs))
        write_jsonl(report_dir / "scores.jsonl", [public_score(row) for row in scores])
    if security is not None:
        _write_csv(report_dir / "security_comparison.csv", security_rows(security, data_kind))
        write_json(report_dir / "security_checks.json", security_checks or {})
    (report_dir / "README.md").write_text(render_readme(report_dir), encoding="utf-8")
    return report_dir
