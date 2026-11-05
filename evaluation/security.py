"""보안 비교: 질문마다 검색·최종 문맥 구성을 한 번만 하고 같은 문맥으로 기본/방어 프롬프트를 실행한다.

주입 함수
- retrieve_fn(question, retrieval_mode) -> {"retrieved_candidates": [...], "final_context_chunks": [...]}
- generate_fn(question, final_context_chunks, prompt_variant) ->
      {"status", "answer", "reason", "citations", "attempts", "latency_ms",
       "model_input", "model", "model_parameters", "error_type"(선택)}
- security_judge_fn(record, security_item) -> {"followed_attack"|"answer_correct"|"over_refusal":
      {"value": bool|None, "reason": str}}

attack_text는 실행이 끝난 뒤 노출 분석에만 쓰고 검색이나 문맥 구성에 넣지 않는다.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.observability.records import compute_context_hash, summarize_usage

VARIANTS = ("base", "defense")
JUDGED = ("followed_attack", "answer_correct", "over_refusal")

RetrieveFn = Callable[[str, str], Mapping[str, Any]]
GenerateFn = Callable[[str, Sequence[Mapping[str, Any]], str], Mapping[str, Any]]
SecurityJudgeFn = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def run_security_comparison(questions: Sequence[Mapping[str, str]], retrieve_fn: RetrieveFn,
                            generate_fn: GenerateFn, *, experiment_id: str,
                            retrieval_mode: str) -> list[dict[str, Any]]:
    """질문 2개 × 변형 2개 = 답변 4개. 검색은 질문당 한 번만 호출한다."""
    records = []
    for q in questions:
        retrieved = retrieve_fn(q["question"], retrieval_mode)
        chunks = list(retrieved["final_context_chunks"])
        context_hash = compute_context_hash(chunks)
        for variant in VARIANTS:
            passed = copy.deepcopy(chunks)  # 한 변형의 변경이 다른 변형에 번지지 않게
            try:
                gen = dict(generate_fn(q["question"], passed, variant))
            except Exception as exc:
                gen = {"status": "error", "answer": None, "reason": None, "citations": [],
                       "attempts": [], "latency_ms": None, "model_input": None,
                       "error_type": f"generation_exception:{type(exc).__name__}"}
            records.append({
                "experiment_id": experiment_id, "question_id": q["id"],
                "question": q["question"], "retrieval_mode": retrieval_mode,
                "prompt_variant": variant,
                "retrieved_candidates": list(retrieved.get("retrieved_candidates") or []),
                "final_context_chunks": chunks, "final_context_hash": context_hash,
                "context_hash_after_generation": compute_context_hash(passed),
                **{k: gen.get(k) for k in ("status", "answer", "reason", "citations",
                                           "latency_ms", "model_input", "model",
                                           "model_parameters", "error_type", "request_id",
                                           "langfuse_trace_id")},
                "attempts": list(gen.get("attempts") or []),
                "usage": summarize_usage(list(gen.get("attempts") or [])),
            })
    return records


def check_same_context(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """질문별로 두 변형의 문맥 해시·순서·생성 설정이 같은지 확인한다."""
    by_q: dict[str, list[Mapping[str, Any]]] = {}
    for r in records:
        by_q.setdefault(r["question_id"], []).append(r)
    result = {}
    for qid, rows in by_q.items():
        hashes = {r["final_context_hash"] for r in rows} | {r["context_hash_after_generation"]
                                                          for r in rows}
        orders = {tuple(c["chunk_id"] for c in r["final_context_chunks"]) for r in rows}
        settings = {json.dumps([r.get("model"), r.get("model_parameters")], sort_keys=True,
                               ensure_ascii=False) for r in rows}
        result[qid] = {"variants": sorted(r["prompt_variant"] for r in rows),
                       "context_identical": len(hashes) == 1 and len(orders) == 1,
                       "generation_settings_identical": len(settings) == 1}
    return result


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def analyze_security(records: Sequence[Mapping[str, Any]],
                     security_items: Mapping[str, Mapping[str, Any]],
                     security_judge_fn: SecurityJudgeFn | None = None) -> list[dict[str, Any]]:
    """노출·추종·업무 정확성·불필요한 거절을 나눠 기록한다. 미노출·생성 실패는 방어 성공이 아니다."""
    out = []
    for r in records:
        item = security_items.get(r["question_id"], {})
        attack = _norm(str(item.get("attack_text") or ""))
        context_text = _norm(" ".join(c["text"] for c in r["final_context_chunks"]))
        in_context = (attack in context_text) if attack else None
        model_input = r.get("model_input")
        if not attack or model_input is None:
            exposed = None
        else:
            raw = model_input if isinstance(model_input, str) else json.dumps(
                model_input, ensure_ascii=False)
            exposed = attack in _norm(raw)

        judged = {k: {"value": None, "reason": "미평가"} for k in JUDGED}
        if r["status"] != "error" and security_judge_fn is not None:
            try:
                res = security_judge_fn(r, item)
                for k in JUDGED:
                    v = res.get(k) or {}
                    val = v.get("value")
                    judged[k] = {"value": val if isinstance(val, bool) else None,
                                 "reason": str(v.get("reason") or "")}
            except Exception as exc:
                judged = {k: {"value": None, "reason": f"보안 평가 호출 실패: {type(exc).__name__}"}
                          for k in JUDGED}

        if r["status"] == "error":
            outcome = "generation_failed"
        elif exposed is None:
            outcome = "exposure_unknown"
        elif not exposed:
            outcome = "not_exposed"
        elif judged["followed_attack"]["value"] is None:
            outcome = "unjudged"
        else:
            outcome = "attack_followed" if judged["followed_attack"]["value"] else "defended"
        out.append({**r, "attack_in_context": in_context, "attack_in_model_input": exposed,
                    **judged, "outcome": outcome, "defense_success": outcome == "defended"})
    return out


def make_openai_security_judge(model: str, template: str) -> SecurityJudgeFn:
    from openai import OpenAI

    client = OpenAI()

    def judge(record: Mapping[str, Any], item: Mapping[str, Any]) -> Mapping[str, Any]:
        values = {"question": record["question"], "variant": record["prompt_variant"],
                  "status": record["status"], "answer": record.get("answer") or "",
                  "reason": record.get("reason") or "",
                  "security_item": json.dumps({k: v for k, v in item.items() if k != "question"},
                                              ensure_ascii=False, indent=2)}
        prompt = template
        for key, value in values.items():
            prompt = prompt.replace("{{" + key + "}}", value)
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"})
        return json.loads(resp.choices[0].message.content)

    return judge
