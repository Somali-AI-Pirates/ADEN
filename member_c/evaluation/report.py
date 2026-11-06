"""로컬 전체 기록(artifacts/)과 제출 결과(reports/<experiment_id>/) 파일 생성."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from member_c.observability.records import RETRIEVAL_MODES
from member_c.evaluation.graders import CRITERIA
from member_c.evaluation.metrics import aggregate, operational_summary

SAMPLE_BANNER = ("> **가상 샘플 결과** — 모의 서비스·모의 평가기로 만든 검증용 파일이다. "
                 "실제 Basic 데이터셋 성능이 아니며 발표·비교에 사용하지 않는다.")
JUDGE_LABELS = {"fact": "사실", "condition": "조건", "evidence": "근거", "decision": "판단"}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: 엑셀에서 한글 깨짐 방지
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(runs: Sequence[Mapping[str, Any]], scores: Sequence[Mapping[str, Any]]) -> dict:
    """방식별 평가 요약·운영 지표. 평가 호출 사용량은 grading으로 분리한다."""
    summary = {}
    for mode in [m for m in RETRIEVAL_MODES if any(r["retrieval_mode"] == m for r in runs)]:
        s = [x for x in scores if x["retrieval_mode"] == mode]
        grading = [u for x in s for u in x["grading_usage"].values() if u]
        summary[mode] = {
            "hit_at_k": aggregate(x["retrieval"]["hit_at_k"] for x in s),
            "recall_at_k": aggregate(x["retrieval"]["recall_at_k"] for x in s),
            "faithfulness": aggregate(x["faithfulness"] for x in s),
            **{f"judge_{c}": aggregate(x["judge"][c] for x in s) for c in CRITERIA},
            "operations": operational_summary([r for r in runs if r["retrieval_mode"] == mode]),
            "grading": {"calls": len(grading),
                        "failed_calls": sum(bool(u["error"]) for u in grading),
                        "latency_ms": sum(u["latency_ms"] for u in grading),
                        "input_tokens": sum(u["input_tokens"] or 0 for u in grading)
                        if any(u["input_tokens"] is not None for u in grading) else None,
                        "output_tokens": sum(u["output_tokens"] or 0 for u in grading)
                        if any(u["output_tokens"] is not None for u in grading) else None},
        }
    return summary


def comparison_rows(runs: Sequence[Mapping[str, Any]], scores: Sequence[Mapping[str, Any]],
                    data_kind: str) -> list[dict[str, Any]]:
    by_req = {s["request_id"]: s for s in scores}
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
        for c in CRITERIA:
            row[f"judge_{c}"] = s["judge"][c]["value"]
            row[f"judge_{c}_status"] = s["judge"][c]["status"]
            row[f"judge_{c}_reason"] = s["judge"][c]["reason"]
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
    return [{**{k: r.get(k) for k in keys},
             "final_context_chunk_ids": [c["chunk_id"] for c in r["final_context_chunks"] or []]}
            for r in runs]


def security_rows(rows: Sequence[Mapping[str, Any]], data_kind: str) -> list[dict[str, Any]]:
    return [{"data_kind": data_kind, "question_id": r["question_id"],
             "prompt_variant": r["prompt_variant"], "retrieval_mode": r["retrieval_mode"],
             "request_id": r.get("request_id"), "final_context_hash": r["final_context_hash"],
             "model_context_hash": r.get("model_context_hash"),
             "final_context_chunk_ids": ";".join(c["chunk_id"] for c in r["final_context_chunks"]),
             "status": r["status"], "error_type": r.get("error_type"),
             "attack_in_context": r["attack_in_context"],
             "attack_in_model_input": r["attack_in_model_input"],
             "followed_attack": r["followed_attack"]["value"],
             "answer_correct": r["answer_correct"]["value"],
             "over_refusal": r["over_refusal"]["value"], "outcome": r["outcome"],
             "defense_success": r["defense_success"], "answer": r.get("answer"),
             "judge_reasons": " | ".join(f"{k}: {r[k]['reason']}" for k in
                                         ("followed_attack", "answer_correct", "over_refusal")),
             "latency_ms": r.get("latency_ms"), "input_tokens": r["usage"]["input_tokens"],
             "output_tokens": r["usage"]["output_tokens"]}
            for r in rows]


def _fmt(agg: Mapping[str, Any]) -> str:
    mean = "-" if agg["mean"] is None else f"{agg['mean']:.3f}"
    return f"{mean} ({agg['scored']}/{agg['not_applicable']}/{agg['unavailable']})"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def render_readme(report_dir: Path) -> str:
    """폴더에 있는 결과 파일로 README를 만든다(일반 평가·보안 비교를 따로 실행해도 함께 표시)."""
    manifest = _read_json(report_dir / "manifest.json")
    summary = (_read_json(report_dir / "summary.json") or {}).get("modes")
    checks = _read_json(report_dir / "security_checks.json")
    sec_path = report_dir / "security_comparison.csv"
    security = list(csv.DictReader(io.StringIO(sec_path.read_text(encoding="utf-8-sig")))) if sec_path.exists() else None

    lines = [f"# 평가 결과 — {manifest['experiment_id']}", ""]
    if manifest.get("sample"):
        lines += [SAMPLE_BANNER, ""]
    lines += [f"- 데이터: `{manifest['dataset_id']}` · 설정 해시: `{manifest['config_hash']}`",
              f"- 실행 코드: `{manifest.get('git_commit')}` (미커밋 변경: {manifest.get('git_dirty')})"]
    bench = manifest.get("benchmark")
    if bench:
        lines += [f"- 일반 평가 k={bench['k']}, 방식: {', '.join(bench['modes'])}",
                  f"- 선택 질문({len(bench['question_ids'])}개): "
                  + ", ".join(f"`{q}`" for q in bench["question_ids"]),
                  f"- 선택 이유: {bench.get('selection_reason') or '(미기재)'}",
                  f"- 평가기: Judge={bench.get('judge')}, Faithfulness={bench.get('faithfulness')}",
                  f"- Langfuse 전송: `{bench.get('langfuse_flush')}` "
                  "(`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)",
                  f"- Langfuse 저장 확인(요청 수): `{bench.get('langfuse_verification')}` "
                  "(`verified`만 API에서 관측을 조회한 것, `None`은 추적 비활성·어댑터 관리)"]

    if summary:
        lines += ["", "## 방식별 품질 요약", "",
                  "값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.", "",
                  "| 지표 | " + " | ".join(summary) + " |",
                  "| --- |" + " --- |" * len(summary)]
        metrics = [("Hit@k", "hit_at_k"), ("Recall@k", "recall_at_k"),
                   ("Faithfulness", "faithfulness")] + [
            (f"Judge {JUDGE_LABELS[c]}", f"judge_{c}") for c in CRITERIA]
        for label, key in metrics:
            lines.append(f"| {label} | " + " | ".join(_fmt(summary[m][key]) for m in summary) + " |")
        lines += ["", "## 운영 지표 (서비스 요청만, 평가 호출 제외)", "",
                  "| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for mode, s in summary.items():
            o = s["operations"]
            mean = o["latency_ok"]["mean_ms"]
            lines.append(
                f"| {mode} | {o['requests']} | {o['error_rate']:.1%} ({o['errors']}) | "
                f"{'-' if mean is None else f'{mean:.0f} ms'} | {o['input_tokens']} | "
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
            lines += ["", "동일 문맥 확인(True 확인 / False 불일치 / None 확인 불가):", ""] + [
                f"- {q}: 로그 청크 {c['final_context_identical']}, 모델 문맥 문자열 "
                f"{c['model_context_identical']}, 모델 입력에 문맥 포함 {c['context_in_model_input']}, "
                f"생성 설정 {c['generation_settings_identical']}" for q, c in checks.items()]
        lines += ["", "공격 미노출(`not_exposed`)·생성 실패(`generation_failed`)·미평가는 방어 성공으로 세지 않는다."]

    lines += ["", "## Langfuse 기록 확인", "",
              "각 요청의 trace ID는 `answers.jsonl`·`comparison.csv`의 `langfuse_trace_id`다. "
              "trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만들므로 request_id로도 다시 계산할 수 있다.",
              "", "```bash", "python -m member_c.scripts.run_benchmark --verify-langfuse <trace_id>", "```", "",
              "Langfuse UI에서는 trace ID로 검색해 `rag-query` → `retrieval` → `final-context` → "
              "`generation-attempt-N` 순서를 확인한다.", "",
              "## 파일", "",
              "- `manifest.json`: 재현 설정 · `comparison.csv`: 질문×방식 점수·사유·운영값",
              "- `answers.jsonl`: 실제 답변·상태·인용·요청 ID · `scores.jsonl`: 평가 상세",
              "- `summary.json`: 방식별 요약 · `security_comparison.csv`: 보안 답변 4개", ""]
    return "\n".join(lines)


def write_artifacts(exp_dir: Path, manifest: Mapping[str, Any], *, runs=None, scores=None,
                    security=None) -> None:
    """로컬 전체 기록. 최종 문맥 텍스트·모델 입력까지 포함하므로 Git에 올리지 않는다."""
    _merge_manifest(exp_dir, manifest)
    if runs is not None:
        write_jsonl(exp_dir / "runs.jsonl", runs)
    if scores is not None:
        write_jsonl(exp_dir / "scores.jsonl", scores)
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
    data_kind = "SAMPLE" if merged.get("sample") else merged["dataset_id"]
    if runs is not None and scores is not None:
        write_json(report_dir / "summary.json",
                   {"data_kind": data_kind, "modes": summarize(runs, scores)})
        _write_csv(report_dir / "comparison.csv", comparison_rows(runs, scores, data_kind))
        write_jsonl(report_dir / "answers.jsonl", answer_rows(runs))
        write_jsonl(report_dir / "scores.jsonl", scores)
    if security is not None:
        _write_csv(report_dir / "security_comparison.csv", security_rows(security, data_kind))
        write_json(report_dir / "security_checks.json", security_checks or {})
    (report_dir / "README.md").write_text(render_readme(report_dir), encoding="utf-8")
    return report_dir
