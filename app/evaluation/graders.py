"""저장된 실행 기록 하나에 RAGAS Faithfulness와 LLM-as-a-Judge를 적용한다. 답변을 재생성하지 않는다.

평가 함수는 주입한다.
- judge_fn(prompt) -> {"text": str, "input_tokens": int|None, "output_tokens": int|None}
- faithfulness_fn(question, response, contexts) -> {"value": float, "input_tokens": ..., "output_tokens": ...}
실제 구현은 make_openai_judge(), make_ragas_faithfulness(). 테스트·샘플은 모의 함수를 쓴다.
평가 호출의 시간·토큰은 `grading_usage`에만 남기고 서비스 지표와 섞지 않는다.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.evaluation.metrics import SCORE_STATUSES, evidence_diagnostics, retrieval_metrics, score
from app.evaluation.evidence import chunk_text as chunk_text, final_evidence, labeled_contexts, sql_evidence

CRITERIA = ("fact", "condition", "evidence", "decision")  # 사실·조건·근거·판단
# v1: 항목별로 맞는 것만 받고 has_factual_claims는 선택. v2: 필수 필드·타입을 검증하고 검산 진단을 따로 받는다
JUDGE_SCHEMA_VERSION = "judge-output-v3-fact-trace"
CALCULATION_STATUSES = ("complete", "incomplete", "not_applicable", "unavailable")
# ragas 기본 1024는 실제 답변(문맥 8청크)의 주장 추출·검증 출력이 잘려 IncompleteOutputException이 난다
RAGAS_MAX_TOKENS = 32768
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

JudgeFn = Callable[[str], Mapping[str, Any]]
FaithfulnessFn = Callable[[str, str, list[str]], Mapping[str, Any]]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def response_text(record: Mapping[str, Any]) -> str:
    """사용자에게 보이는 출력 전체(answer + reason). 보류 사유 안의 사실 주장도 평가 대상이다."""
    parts = [record.get("answer") or "", record.get("reason") or ""]
    texts = []
    for part in parts:
        if part and part.strip():
            texts.append(part.strip())
    return "\n\n".join(texts)


def included_sql_contexts(record: Mapping[str, Any]) -> list[str]:
    """Only SQL evidence actually supplied to the final answer, with stable IDs."""
    return [f"[{key}]\n{text}" for key, text in sql_evidence(record).items()]


def _timed(fn: Callable[[], Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    start = time.perf_counter()
    error_details = None
    try:
        out = fn()
        err = out.get("error")
    except Exception as exc:
        # Changing the evaluation conditions mid-run must stop, not silently
        # turn the remaining questions into unavailable scores.
        if getattr(exc, "abort_evaluation", False):
            raise
        out, err = None, type(exc).__name__
        error_details = getattr(exc, "evaluation_details", None)
    metadata = out or error_details or {}
    usage = {
        "latency_ms": round((time.perf_counter() - start) * 1000),
        "input_tokens": metadata.get("input_tokens"),
        "output_tokens": metadata.get("output_tokens"),
        "error": err,
    }
    for key in ("reasoning_tokens", "cached_input_tokens", "logical_calls", "provider_attempts"):
        if key in metadata:
            usage[key] = metadata[key]
    details = (out or {}).get("details") or error_details
    if details is not None:
        usage["_details"] = details
    return out, usage


def build_judge_prompt(template: str, record: Mapping[str, Any],
                       reference: Mapping[str, Any]) -> str:
    context = "\n\n".join(labeled_contexts(record))
    ref = {}
    for field, field_value in reference.items():
        if field != "question":
            ref[field] = field_value
    values = {
        "question": record["question"], "status": record["status"],
        "answer": record.get("answer") or "", "reason": record.get("reason") or "",
        "citations": json.dumps(record.get("citations") or [], ensure_ascii=False),
        "final_context": context or "(최종 문맥 없음)",
        "reference": json.dumps(ref, ensure_ascii=False, indent=2),
        "retrieval_decision": json.dumps({
            key: (record.get("diagnostics") or {}).get(key)
            for key in ("finish_status", "termination_reason", "planner_conflicts")
        }, ensure_ascii=False),
    }
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda match: values.get(match[1], match[0]),
        template,
    )


def _criterion_errors(key: str, item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return [f"{key} 항목 없음"]
    errors = []
    status, value, reason = item.get("status"), item.get("score"), item.get("reason")
    if status not in SCORE_STATUSES:
        errors.append(f"{key}.status가 {'/'.join(SCORE_STATUSES)}가 아님: {status!r}")
    elif status == "scored":
        if isinstance(value, bool) or value not in (0, 1):  # True == 1이라 bool을 먼저 막는다
            errors.append(f"{key}.score는 scored일 때 0 또는 1: {value!r}")
    elif value is not None:
        errors.append(f"{key}.score는 {status}일 때 null: {value!r}")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"{key}.reason이 비어 있음")
    return errors


def _is_strings(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return False
    return True


def _calculation(status: str, reason: str, items: list | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "items": items or []}


def parse_calculation_checks(data: Any, source_ids: set[str]) -> dict[str, Any]:
    """검산 대상별 진단. 공식 네 점수와 따로 검증해, 진단이 틀려도 네 점수는 그대로 둔다.

    전체 상태: 대상이 없거나 모두 not_applicable이면 not_applicable, incomplete가 있으면 incomplete,
    unavailable이 있으면 unavailable, 그 외 complete. 구조가 틀리면 unavailable과 오류 사유다."""
    raw = data.get("calculation_checks") if isinstance(data, Mapping) else None
    if raw is None:
        return _calculation("unavailable", "Judge 출력에 calculation_checks 없음")
    if not isinstance(raw, list):
        return _calculation("unavailable", "검산 진단 구조 오류: calculation_checks는 배열이어야 함")
    items, errors = [], []
    for index, item in enumerate(raw):
        where = f"calculation_checks[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{where}가 객체가 아님")
            continue
        target, status, reason = item.get("target"), item.get("status"), item.get("reason")
        missing, supporting = item.get("missing_evidence"), item.get("supporting_source_ids")
        if not isinstance(target, str) or not target.strip():
            errors.append(f"{where}.target이 비어 있음")
        if status not in CALCULATION_STATUSES:
            errors.append(f"{where}.status가 {'/'.join(CALCULATION_STATUSES)}가 아님: {status!r}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{where}.reason이 비어 있음")
        if not _is_strings(missing) or not _is_strings(supporting):
            errors.append(f"{where}.missing_evidence·supporting_source_ids는 문자열 배열이어야 함")
            continue
        unknown = []
        for source_id in supporting:
            if source_id not in source_ids:
                unknown.append(source_id)
        if unknown:
            errors.append(f"{where}.supporting_source_ids가 실제 제공 문맥에 없음: {unknown}")
        if status == "complete" and (missing or not supporting):
            errors.append(f"{where}: complete는 부족 근거가 없고 문맥 근거 ID가 있어야 함")
        elif status == "incomplete" and not missing:
            errors.append(f"{where}: incomplete는 부족한 근거를 적어야 함")
        elif status == "not_applicable" and (missing or supporting):
            errors.append(f"{where}: not_applicable에는 부족 근거·근거 ID를 두지 않음")
        items.append({"target": target, "status": status, "missing_evidence": list(missing),
                      "supporting_source_ids": list(supporting), "reason": reason})
    if errors:
        return _calculation("unavailable", "검산 진단 구조 오류: " + "; ".join(errors))
    statuses = []
    for item in items:
        statuses.append(item["status"])
    if not statuses or set(statuses) == {"not_applicable"}:
        return _calculation("not_applicable", "검산을 요청한 대상 없음", items)
    if "incomplete" in statuses:
        overall = "incomplete"
    elif "unavailable" in statuses:
        overall = "unavailable"
    else:
        overall = "complete"
    return _calculation(overall, f"검산 대상 {len(items)}개", items)


def parse_judge_output(text: str, source_ids: set[str] | None = None
                       ) -> tuple[dict[str, dict[str, Any]], bool | None, dict[str, Any]]:
    """Judge JSON → (네 항목 점수, has_factual_claims, 검산 진단).

    네 항목과 has_factual_claims는 필수다. 하나라도 빠지거나 타입·status·score가 맞지 않으면
    네 항목을 모두 평가 불가로 두고 오류를 사유에 남긴다(일부만 골라 정상 점수로 쓰지 않는다)."""
    data = json.loads(text)
    errors = []
    if not isinstance(data, Mapping):
        data = {}
        errors.append("Judge 출력이 JSON 객체가 아님")
    claims = data.get("has_factual_claims")
    if not isinstance(claims, bool):
        errors.append(f"has_factual_claims는 boolean이어야 함: {claims!r}")
    criteria = data.get("criteria")
    if isinstance(criteria, Mapping):
        for key in CRITERIA:
            errors.extend(_criterion_errors(key, criteria.get(key)))
    else:
        errors.append("criteria 객체 없음")
    calculation = parse_calculation_checks(data, source_ids or set())
    if errors:
        un = score(None, "unavailable", "Judge 출력 구조 오류: " + "; ".join(errors))
        result = {}
        for key in CRITERIA:
            result[key] = un
        return result, None, calculation
    result = {}
    for key in CRITERIA:
        item = criteria[key]
        if item["status"] == "scored":
            result[key] = score(float(item["score"]), "scored", item["reason"].strip())
        else:
            result[key] = score(None, item["status"], item["reason"].strip())
    return result, claims, calculation


def grade_judge(record: Mapping[str, Any], reference: Mapping[str, Any], judge_fn: JudgeFn | None,
                template: str) -> tuple[dict, bool | None, dict | None, dict[str, Any]]:
    """반환: (네 항목 점수, has_factual_claims, Judge 사용량, 검산 진단)."""
    if record["status"] == "error":
        # 서비스 실패: 판단은 0, 답변이 없어 확인할 수 없는 항목은 평가 불가. Judge를 호출하지 않는다.
        un = score(None, "unavailable", f"서비스 실패({record.get('error_type')})로 평가할 답변 없음")
        return ({"fact": un, "condition": un, "evidence": un,
                 "decision": score(0.0, "scored", f"서비스 실패: {record.get('error_type')}")},
                None, None, _calculation("unavailable", "서비스 실패로 평가할 답변 없음"))
    if judge_fn is None:
        failure = "Judge 평가기 미설정"
        usage = None
    else:
        prompt = build_judge_prompt(template, record, reference)
        out, usage = _timed(lambda: judge_fn(prompt))
        if out is None:
            failure = f"Judge 호출 실패: {usage['error']}"
        else:
            source_ids = set(final_evidence(record))
            try:
                criteria, claims, calculation = parse_judge_output(out["text"], source_ids)
                usage["_details"] = {
                    **(usage.get("_details") or {}),
                    "prompt": prompt,
                    "output": json.loads(out["text"]),
                }
                return criteria, claims, usage, calculation
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                failure = f"Judge 출력 파싱 실패: {type(exc).__name__}"
    un = score(None, "unavailable", failure)
    unavailable_criteria = {}
    for criterion in CRITERIA:
        unavailable_criteria[criterion] = un
    return unavailable_criteria, None, usage, _calculation("unavailable", failure)


def grade_faithfulness(record: Mapping[str, Any], has_claims: bool | None,
                       faithfulness_fn: FaithfulnessFn | None) -> tuple[dict, dict | None]:
    """Faithfulness extracts its own claims; another Judge cannot suppress it."""
    if record["status"] == "error":
        return score(None, "unavailable", "서비스 실패로 평가할 답변 없음"), None
    response = response_text(record)
    if not response:
        return score(None, "not_applicable", "사용자에게 전달한 답변·설명이 없음"), None
    if faithfulness_fn is None:
        return score(None, "unavailable", "RAGAS 평가기 미설정"), None
    contexts = labeled_contexts(record)
    out, usage = _timed(lambda: faithfulness_fn(record["question"], response, contexts))
    if out is None:
        return score(None, "unavailable", f"RAGAS 호출 실패: {usage['error']}"), usage
    if out.get("status") == "not_applicable":
        return score(None, "not_applicable", out.get("reason") or "정상 추출 결과 사실 주장 없음"), usage
    if out.get("status") == "unavailable":
        return score(None, "unavailable", out.get("reason") or "RAGAS 판정 검증 실패"), usage
    value = out.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        return score(None, "unavailable", "RAGAS 점수가 유효한 0~1 값이 아님"), usage
    return score(float(value), "scored", "RAGAS Faithfulness"), usage


def grade_record(record: Mapping[str, Any], reference: Mapping[str, Any], *, k: int,
                 judge_fn: JudgeFn | None, faithfulness_fn: FaithfulnessFn | None,
                 judge_template: str | None = None,
                 diagnose_fn: JudgeFn | None = None) -> dict[str, Any]:
    if judge_template is not None:
        template = judge_template
    else:
        template = load_prompt("judge.txt")
    judge, claims, judge_usage, calculation = grade_judge(record, reference, judge_fn, template)
    faith, faith_usage = grade_faithfulness(record, claims, faithfulness_fn)
    judge_details = judge_usage.pop("_details", {}) if judge_usage else {}
    faith_details = faith_usage.pop("_details", {}) if faith_usage else {}
    from app.evaluation.attribution import attribute_evidence
    attribution = attribute_evidence(
        record, reference, judge_details.get("output"), judge_fn=diagnose_fn
    )
    candidate_usage = (attribution.get("candidate_diagnosis") or {}).get("usage")
    if candidate_usage is not None:
        candidate_usage = {key: value for key, value in candidate_usage.items()
                           if key not in {"details", "_details"}}
    fact_checks = attribution.get("fact_checks") or {}
    items = fact_checks.get("items") or []
    summary_items = [
        {key: item.get(key) for key in (
            "fact_id", "origin", "final_support", "answer_status", "attribution",
            "first_loss_stage", "failure_tags"
        )}
        for item in items
    ]
    return {
        "experiment_id": record["experiment_id"], "question_id": record["question_id"],
        "request_id": record["request_id"], "retrieval_mode": record["retrieval_mode"],
        "status": record["status"],
        "retrieval": retrieval_metrics(reference, record.get("final_context_chunks"), k),
        "evidence_diagnostics": evidence_diagnostics(reference, record, k),
        "faithfulness": faith, "judge": judge, "has_factual_claims": claims,
        "calculation_checks": calculation,
        "grading_usage": {"judge": judge_usage, "faithfulness": faith_usage,
                          "candidate_diagnosis": candidate_usage},
        "evidence_attribution": {
            "status": fact_checks.get("status", "unavailable"),
            "items": summary_items,
            "candidate_diagnosis_status": (attribution.get("candidate_diagnosis") or {}).get("status"),
        },
        "evaluation_details": {
            "judge": judge_details, "faithfulness": faith_details,
            "attribution": attribution,
        },
    }


def make_openai_judge(model: str, *, session=None, policy=None) -> JudgeFn:
    """The evaluator has its own explicit policy, separate from RAG generation."""
    from app.evaluation.model_runtime import EvaluationSession, load_evaluation_policy

    def judge(prompt: str) -> dict[str, Any]:
        if session is not None:
            return session.generate_json(prompt, model=model, stage="judge").as_mapping()
        with EvaluationSession(policy or load_evaluation_policy()) as owned:
            return owned.generate_json(prompt, model=model, stage="judge").as_mapping()
    return judge


def make_ragas_faithfulness(model: str, *, session=None, policy=None,
                           profile: str = "advanced") -> FaithfulnessFn:
    from app.evaluation.faithfulness import make_faithfulness
    return make_faithfulness(model, session=session, policy=policy, profile=profile)
