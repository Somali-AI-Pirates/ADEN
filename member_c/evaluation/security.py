"""보안 비교: 질문마다 검색·최종 문맥 구성을 한 번만 하고 같은 문맥으로 기본/방어 프롬프트를 실행한다.

주입 함수
- retrieve_fn(question, retrieval_mode) -> {"retrieved_candidates": [...], "final_context_chunks": [...]}
- generate_fn(question, final_context_chunks, prompt_variant) ->
      {"status", "answer", "reason", "citations", "attempts"(AttemptRecord), "latency_ms",
       "model_context_text", "model_context_hash", "request_id", "error_type"(선택)}
  B의 어댑터는 retrieve_fn에서 만든 ContextBundle을 유지해 재사용하고 재검색하지 않는다.
  루트 model_input/model_output·model·model_parameters가 없으면 마지막 시도의 값을 쓴다.
- security_judge_fn(record, security_item) -> {"followed_attack"|"answer_correct"|"over_refusal":
      {"value": bool|None, "reason": str}}

attack_text는 실행이 끝난 뒤 노출 분석에만 쓰고 검색이나 문맥 구성에 넣지 않는다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from member_c.observability.records import compute_context_hash, summarize_usage

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
            attempts = list(gen.get("attempts") or [])
            last = attempts[-1] if attempts else {}
            records.append({
                "experiment_id": experiment_id, "question_id": q["id"],
                "question": q["question"], "retrieval_mode": retrieval_mode,
                "prompt_variant": variant,
                "retrieved_candidates": list(retrieved.get("retrieved_candidates") or []),
                "final_context_chunks": chunks, "final_context_hash": context_hash,
                "context_hash_after_generation": compute_context_hash(passed),
                **{k: gen.get(k) for k in ("status", "answer", "reason", "citations",
                                           "latency_ms", "model_context_text",
                                           "model_context_hash", "error_type", "request_id",
                                           "langfuse_trace_id")},
                **{k: gen.get(k, last.get(k)) for k in ("model_input", "model_output", "model",
                                                        "model_parameters")},
                "attempts": attempts,
                "usage": summarize_usage(attempts),
            })
    return records


def _model_context_identical(rows: Sequence[Mapping[str, Any]]) -> bool | None:
    """B의 문맥 문자열이 두 변형에서 같고 각 해시가 그 문자열의 SHA-256인지. 값이 없으면 None."""
    pairs = [(r.get("model_context_text"), r.get("model_context_hash")) for r in rows]
    if any(t is None or h is None for t, h in pairs):
        return None
    valid = all(hashlib.sha256(t.encode("utf-8")).hexdigest() == h for t, h in pairs)
    return valid and len(set(pairs)) == 1


def _context_in_model_input(rows: Sequence[Mapping[str, Any]]) -> bool | None:
    """실제 모델 입력에 준비한 문맥 문자열이 그대로 들어갔는지. 값이 없으면 None."""
    if any(r.get("model_context_text") is None or r.get("model_input") is None for r in rows):
        return None
    return all(_norm(r["model_context_text"]) in _norm(_input_text(r["model_input"]))
               for r in rows)


def _all(values: Sequence[bool | None]) -> bool | None:
    return False if False in values else (None if None in values else True)


def check_same_context(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """질문별로 두 변형의 문맥 두 해시·순서·모델 입력·생성 설정이 같은지 확인한다.

    값: True 확인, False 불일치, None 확인 불가(필드 없음·생성 실패). 계약은 contracts.md 3절."""
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
        checks = {"final_context_identical": len(hashes) == 1 and len(orders) == 1,
                  "model_context_identical": _model_context_identical(rows),
                  "context_in_model_input": _context_in_model_input(rows)}
        result[qid] = {"variants": sorted(r["prompt_variant"] for r in rows), **checks,
                       "context_identical": _all(list(checks.values())),
                       "generation_settings_identical": len(settings) == 1}
    return result


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _input_text(value: Any) -> str:
    """모델 입력(문자열·메시지 목록·content parts)의 문자열 값만 이어 붙인다.
    json.dumps를 쓰면 줄바꿈이 '\\n' 글자로 바뀌어 줄이 나뉜 공격 문장을 놓친다."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        # role·type 같은 메타 값이 문장 사이에 끼지 않게 본문 키만 쓴다
        keys = [k for k in ("content", "text") if k in value] or list(value)
        return " ".join(_input_text(value[k]) for k in keys)
    if isinstance(value, Sequence):
        return " ".join(_input_text(v) for v in value)
    return ""


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
            exposed = attack in _norm(_input_text(model_input))

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
