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
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from member_Damsebi.evaluation.metrics import retrieval_metrics, score

CRITERIA = ("fact", "condition", "evidence", "decision")  # 사실·조건·근거·판단
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

JudgeFn = Callable[[str], Mapping[str, Any]]
FaithfulnessFn = Callable[[str, str, list[str]], Mapping[str, Any]]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def response_text(record: Mapping[str, Any]) -> str:
    """사용자에게 보이는 출력 전체(answer + reason). 보류 사유 안의 사실 주장도 평가 대상이다."""
    parts = [record.get("answer") or "", record.get("reason") or ""]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _timed(fn: Callable[[], Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    start = time.perf_counter()
    try:
        out = fn()
        err = None
    except Exception as exc:
        out, err = None, type(exc).__name__
    usage = {"latency_ms": round((time.perf_counter() - start) * 1000),
             "input_tokens": (out or {}).get("input_tokens"),
             "output_tokens": (out or {}).get("output_tokens"), "error": err}
    return out, usage


def build_judge_prompt(template: str, record: Mapping[str, Any],
                       reference: Mapping[str, Any]) -> str:
    context = "\n\n".join(f"[{c['source_id']}] {c['file_name']} p.{c['page']}\n{c['text']}"
                          for c in record.get("final_context_chunks") or [])
    ref = {k: v for k, v in reference.items() if k != "question"}
    values = {
        "question": record["question"], "status": record["status"],
        "answer": record.get("answer") or "", "reason": record.get("reason") or "",
        "citations": json.dumps(record.get("citations") or [], ensure_ascii=False),
        "final_context": context or "(최종 문맥 없음)",
        "reference": json.dumps(ref, ensure_ascii=False, indent=2),
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def parse_judge_output(text: str) -> tuple[dict[str, dict[str, Any]], bool | None]:
    """Judge JSON을 항목별 score로 바꾼다. 형식이 틀린 항목은 평가 불가로 둔다."""
    data = json.loads(text)
    criteria = data.get("criteria") or {}
    result = {}
    for key in CRITERIA:
        item = criteria.get(key)
        if not isinstance(item, Mapping):
            result[key] = score(None, "unavailable", "Judge 출력에 항목 없음")
            continue
        reason = str(item.get("reason") or "")
        status = item.get("status", "scored")
        value = item.get("score")
        if status == "scored" and value in (0, 1) and not isinstance(value, bool):
            result[key] = score(float(value), "scored", reason)
        elif status in ("not_applicable", "unavailable") and value is None:
            result[key] = score(None, status, reason)
        else:
            result[key] = score(None, "unavailable", f"Judge 출력 형식 오류: {item!r}")
    claims = data.get("has_factual_claims")
    return result, claims if isinstance(claims, bool) else None


def grade_judge(record: Mapping[str, Any], reference: Mapping[str, Any],
                judge_fn: JudgeFn | None, template: str) -> tuple[dict, bool | None, dict | None]:
    if record["status"] == "error":
        # 서비스 실패: 판단은 0, 답변이 없어 확인할 수 없는 항목은 평가 불가. Judge를 호출하지 않는다.
        un = score(None, "unavailable", f"서비스 실패({record.get('error_type')})로 평가할 답변 없음")
        return ({"fact": un, "condition": un, "evidence": un,
                 "decision": score(0.0, "scored", f"서비스 실패: {record.get('error_type')}")},
                None, None)
    if judge_fn is None:
        un = score(None, "unavailable", "Judge 평가기 미설정")
        return {k: un for k in CRITERIA}, None, None
    prompt = build_judge_prompt(template, record, reference)
    out, usage = _timed(lambda: judge_fn(prompt))
    if out is None:
        un = score(None, "unavailable", f"Judge 호출 실패: {usage['error']}")
        return {k: un for k in CRITERIA}, None, usage
    try:
        criteria, claims = parse_judge_output(out["text"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        un = score(None, "unavailable", f"Judge 출력 파싱 실패: {type(exc).__name__}")
        return {k: un for k in CRITERIA}, None, usage
    return criteria, claims, usage


def grade_faithfulness(record: Mapping[str, Any], has_claims: bool | None,
                       faithfulness_fn: FaithfulnessFn | None) -> tuple[dict, dict | None]:
    """질문·생성 답변·실제 최종 문맥만 사용한다. 참고 정답을 문맥에 덧붙이지 않는다."""
    if record["status"] == "error":
        return score(None, "unavailable", "서비스 실패로 평가할 답변 없음"), None
    response = response_text(record)
    if not response or has_claims is False:
        return score(None, "not_applicable", "사실 주장 없음(조건 확인·보류만)"), None
    contexts = [c["text"] for c in record.get("final_context_chunks") or []]
    if not contexts:
        if has_claims:
            return score(0.0, "scored", "최종 문맥 없이 사실 주장을 함"), None
        return score(None, "unavailable", "최종 문맥이 없고 사실 주장 여부를 확인하지 못함"), None
    if faithfulness_fn is None:
        return score(None, "unavailable", "RAGAS 평가기 미설정"), None
    out, usage = _timed(lambda: faithfulness_fn(record["question"], response, contexts))
    if out is None:
        return score(None, "unavailable", f"RAGAS 호출 실패: {usage['error']}"), usage
    value = out.get("value")
    if not isinstance(value, (int, float)) or math.isnan(value):
        return score(None, "unavailable", "RAGAS가 주장을 추출하지 못함(NaN)"), usage
    return score(float(value), "scored", "RAGAS Faithfulness"), usage


def grade_record(record: Mapping[str, Any], reference: Mapping[str, Any], *, k: int,
                 judge_fn: JudgeFn | None, faithfulness_fn: FaithfulnessFn | None,
                 judge_template: str | None = None) -> dict[str, Any]:
    template = judge_template if judge_template is not None else load_prompt("judge.txt")
    judge, claims, judge_usage = grade_judge(record, reference, judge_fn, template)
    faith, faith_usage = grade_faithfulness(record, claims, faithfulness_fn)
    return {
        "experiment_id": record["experiment_id"], "question_id": record["question_id"],
        "request_id": record["request_id"], "retrieval_mode": record["retrieval_mode"],
        "status": record["status"],
        "retrieval": retrieval_metrics(reference, record.get("final_context_chunks"), k),
        "faithfulness": faith, "judge": judge, "has_factual_claims": claims,
        "grading_usage": {"judge": judge_usage, "faithfulness": faith_usage},
    }


def make_openai_judge(model: str) -> JudgeFn:
    """OpenAI 호환 Chat Completions로 Judge 호출. OPENAI_API_KEY(필요 시 OPENAI_BASE_URL) 사용."""
    from openai import OpenAI

    client = OpenAI()

    def judge(prompt: str) -> dict[str, Any]:
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"})
        usage = resp.usage
        return {"text": resp.choices[0].message.content,
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None)}

    return judge


def make_ragas_faithfulness(model: str) -> FaithfulnessFn:
    """ragas 0.4 collections Faithfulness. 토큰 사용량은 SDK가 반환하지 않아 null로 둔다."""
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness

    def faithfulness(question: str, response: str, contexts: list[str]) -> dict[str, Any]:
        # 호출마다 새 비동기 클라이언트: score()가 매번 새 이벤트 루프를 만들기 때문
        metric = Faithfulness(llm=llm_factory(model, client=AsyncOpenAI()))
        result = metric.score(user_input=question, response=response, retrieved_contexts=contexts)
        return {"value": result.value, "input_tokens": None, "output_tokens": None}

    return faithfulness
