"""실행 기록(RunRecord) 검증·정규화. 계약은 docs/design/contracts.md의 RunRecord 절.

A·B의 중첩 객체(RetrievedChunk.chunk)가 아니라 B 연결부가 평탄화한 로그 청크를 받는다.
문맥 해시는 두 종류다.
- final_context_hash: 평탄 로그 청크의 [chunk_id, source_id, text] 해시(`sha256:` 접두어). C가 계산식을 정한다.
- model_context_hash: B의 ContextBundle.context_hash, 실제 문맥 문자열의 SHA-256 hex(접두어 없음).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

RETRIEVAL_MODES = ("naive", "hybrid", "hybrid_rerank")
SUPPORTED_RETRIEVAL_MODES = RETRIEVAL_MODES + ("table_rag_a", "table_rag")
STATUSES = ("answered", "needs_clarification", "abstained", "error")
CANDIDATE_FIELDS = ("chunk_id", "book_id", "file_name", "page", "text")
FINAL_CHUNK_FIELDS = CANDIDATE_FIELDS + ("source_id",)

REQUIRED_FIELDS = (
    "experiment_id", "question_id", "request_id", "dataset_id", "retrieval_mode",
    "config_hash", "question", "retrieved_candidates", "final_context_chunks",
    "final_context_hash", "status", "answer", "reason", "citations", "latency_ms",
    "attempts", "langfuse_trace_id",
)


class RunRecordError(ValueError):
    """실행 기록이 연결 계약을 지키지 않을 때."""


def compute_context_hash(chunks: Sequence[Mapping[str, Any]]) -> str:
    """최종 문맥의 내용·순서 해시. 청크 ID·출처 ID·실제 텍스트를 순서대로 묶는다."""
    payload = []
    for chunk in chunks:
        payload.append([chunk.get("chunk_id"), chunk.get("source_id"), chunk.get("text")])
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RunRecordError(f"{field}는 0 이상의 정수 또는 null이어야 한다: {value!r}")
    return value


def summarize_usage(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """재시도를 포함한 시도별 사용량 합계. 미확인 값은 0으로 바꾸지 않는다.

    status: complete(모든 시도 확인) / partial(일부만 확인, 합계는 확인분만) /
    unknown(확인값 없음) / no_attempts(모델 시도 기록 없음)
    """
    if not attempts:
        return {"input_tokens": None, "output_tokens": None, "status": "no_attempts"}
    totals: dict[str, Any] = {}
    known_all = True
    known_any = False
    for key in ("input_tokens", "output_tokens"):
        values = []
        for attempt in attempts:
            values.append(attempt.get(key))
        known = []
        for value in values:
            if value is not None:
                known.append(value)
        if known:
            totals[key] = sum(known)
        else:
            totals[key] = None
        known_all &= len(known) == len(values)
        known_any |= bool(known)
    if known_all:
        totals["status"] = "complete"
    elif known_any:
        totals["status"] = "partial"
    else:
        totals["status"] = "unknown"
    return totals


def _normalize_chunk(raw: Any, where: str, *, need_source_id: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RunRecordError(f"{where}: 청크는 객체여야 한다")
    chunk = dict(raw)
    if isinstance(chunk.get("chunk"), Mapping) and "chunk_id" not in chunk:
        raise RunRecordError(f"{where}: 중첩 RetrievedChunk는 평탄 로그 청크로 변환해 넘겨야 한다")
    if need_source_id:
        required = FINAL_CHUNK_FIELDS
    else:
        required = CANDIDATE_FIELDS
    missing = []
    for field in required:
        if chunk.get(field) in (None, ""):
            missing.append(field)
    if missing:
        raise RunRecordError(f"{where}: 필수 필드 누락 {missing}")
    if isinstance(chunk["page"], bool) or not isinstance(chunk["page"], int) or chunk["page"] < 1:
        raise RunRecordError(f"{where}: page는 1부터 시작하는 정수여야 한다")
    return chunk


def _check_model_context(rec: dict[str, Any], *, called_model: bool) -> None:
    text = rec.setdefault("model_context_text", None)
    digest = rec.setdefault("model_context_hash", None)
    if text is None and digest is None and not called_model:
        return  # 생성 단계 전에 끝난 기록
    if not isinstance(text, str) or not isinstance(digest, str):
        raise RunRecordError("모델을 호출한 기록에는 model_context_text·model_context_hash가 있어야 한다")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RunRecordError("model_context_hash가 model_context_text의 SHA-256 hex와 다르다")


def normalize_run_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """실행 기록을 검증하고 C 모듈이 쓰는 형태로 정규화한다.

    - status=error인 기록은 검색·문맥 단계 전에 실패했을 수 있으므로
      retrieved_candidates/final_context_chunks/final_context_hash에 null을 허용한다.
      (null = 미확인, [] = 실제로 비어 있음)
    - 모델을 호출한 기록(attempts가 있음)은 model_context_text·model_context_hash가 필수다.
    - 루트 model_input/model_output이 없으면 마지막 시도의 값을 연결한다.
    - 추가 필드는 보존한다(error_type, heading 등).
    """
    if not isinstance(raw, Mapping):
        raise RunRecordError("실행 기록은 JSON 객체(Mapping)여야 한다")
    missing = []
    for required_field in REQUIRED_FIELDS:
        if required_field not in raw:
            missing.append(required_field)
    if missing:
        raise RunRecordError(f"필수 필드 누락: {missing}")
    rec = dict(raw)
    for f in ("experiment_id", "question_id", "request_id", "dataset_id", "config_hash", "question"):
        if not isinstance(rec[f], str) or not rec[f]:
            raise RunRecordError(f"{f}는 비어 있지 않은 문자열이어야 한다")
    if rec["retrieval_mode"] not in SUPPORTED_RETRIEVAL_MODES:
        raise RunRecordError(f"retrieval_mode는 {SUPPORTED_RETRIEVAL_MODES} 중 하나: {rec['retrieval_mode']!r}")
    if rec["status"] not in STATUSES:
        raise RunRecordError(f"status는 {STATUSES} 중 하나: {rec['status']!r}")
    is_error = rec["status"] == "error"

    for field, need_sid in (("retrieved_candidates", False), ("final_context_chunks", True)):
        value = rec[field]
        if value is None and is_error:
            continue
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise RunRecordError(f"{field}는 배열이어야 한다")
        normalized_chunks = []
        for index, chunk in enumerate(value):
            normalized_chunks.append(_normalize_chunk(chunk, f"{field}[{index}]", need_source_id=need_sid))
        rec[field] = normalized_chunks

    if rec["final_context_chunks"] is not None:
        expected = compute_context_hash(rec["final_context_chunks"])
        if rec["final_context_hash"] != expected:
            raise RunRecordError("final_context_hash가 compute_context_hash(final_context_chunks)와 "
                                 "다르다(ContextBundle.context_hash는 model_context_hash로 넘긴다)")
    elif rec["final_context_hash"] is not None:
        raise RunRecordError("final_context_chunks가 null이면 final_context_hash도 null이어야 한다")
    if not is_error and not (isinstance(rec["answer"], str) and isinstance(rec["reason"], str)):
        raise RunRecordError("정상 응답의 answer·reason은 문자열이어야 한다")
    if not isinstance(rec["citations"], list):
        raise RunRecordError("citations는 배열이어야 한다")
    if isinstance(rec["latency_ms"], float):
        rec["latency_ms"] = round(rec["latency_ms"])
    rec["latency_ms"] = _int_or_none(rec["latency_ms"], "latency_ms")
    if not isinstance(rec["attempts"], list):
        raise RunRecordError("attempts는 배열이어야 한다")
    attempts = []
    for i, a in enumerate(rec["attempts"]):
        if not isinstance(a, Mapping):
            raise RunRecordError(f"attempts[{i}]는 객체여야 한다")
        a = dict(a)
        if a.get("attempt") != i + 1:
            raise RunRecordError(f"attempts[{i}].attempt는 1부터 증가하는 번호여야 한다: {a.get('attempt')!r}")
        if not isinstance(a.get("model"), str) or not a["model"] or "model_input" not in a:
            raise RunRecordError(f"attempts[{i}]에 실제 model·model_input이 있어야 한다")
        a["input_tokens"] = _int_or_none(a.get("input_tokens"), f"attempts[{i}].input_tokens")
        a["output_tokens"] = _int_or_none(a.get("output_tokens"), f"attempts[{i}].output_tokens")
        for key in ("model_output", "error", "prompt_variant", "model_parameters"):
            a.setdefault(key, None)
        attempts.append(a)
    rec["attempts"] = attempts
    if attempts:
        last_model_input = attempts[-1]["model_input"]
    else:
        last_model_input = None
    rec.setdefault("model_input", last_model_input)
    if attempts:
        last_model_output = attempts[-1]["model_output"]
    else:
        last_model_output = None
    rec.setdefault("model_output", last_model_output)
    answer_call = any(a.get("role") == "answer" for a in attempts)
    legacy_call = any(a.get("role") is None for a in attempts)
    _check_model_context(rec, called_model=answer_call or legacy_call)
    raw_usage = rec.get("usage")
    usage = summarize_usage(attempts)
    if isinstance(raw_usage, Mapping):
        if not attempts:
            for key in ("input_tokens", "output_tokens"):
                value = raw_usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key] = value
            if raw_usage.get("status") in {
                "complete", "partial", "unknown", "no_attempts"
            }:
                usage["status"] = raw_usage["status"]
        for key in ("logical_calls", "provider_attempts", "missing_usage"):
            value = raw_usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
    rec["usage"] = usage
    rec.setdefault("error_type", None)
    if is_error and not rec["error_type"]:
        rec["error_type"] = "unspecified"
    return rec
