"""C 측 실행 기록(RunRecord) 연결부.

app/core/contracts.py의 공통 타입이 아직 없어 JSON/Mapping 입력을 검증·정규화한다.
공통 타입이 생기면 그 타입을 dict로 바꿔(`model_dump()` 등) 이 함수에 넘기면 된다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

RETRIEVAL_MODES = ("naive", "hybrid", "hybrid_rerank")
STATUSES = ("answered", "needs_clarification", "abstained", "error")
CHUNK_FIELDS = ("chunk_id", "book_id", "file_name", "page", "source_id", "text")

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
    payload = [[c.get("chunk_id"), c.get("source_id"), c.get("text")] for c in chunks]
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
        values = [a.get(key) for a in attempts]
        known = [v for v in values if v is not None]
        totals[key] = sum(known) if known else None
        known_all &= len(known) == len(values)
        known_any |= bool(known)
    totals["status"] = "complete" if known_all else ("partial" if known_any else "unknown")
    return totals


def _normalize_chunk(raw: Any, where: str, *, need_source_id: bool) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RunRecordError(f"{where}: 청크는 객체여야 한다")
    chunk = dict(raw)
    required = CHUNK_FIELDS if need_source_id else ("chunk_id", "book_id", "file_name", "page")
    missing = [f for f in required if chunk.get(f) in (None, "")]
    if missing:
        raise RunRecordError(f"{where}: 필수 필드 누락 {missing}")
    if isinstance(chunk["page"], bool) or not isinstance(chunk["page"], int) or chunk["page"] < 1:
        raise RunRecordError(f"{where}: page는 1부터 시작하는 정수여야 한다")
    return chunk


def normalize_run_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """실행 기록을 검증하고 C 모듈이 쓰는 형태로 정규화한다.

    - status=error인 기록은 검색·문맥 단계 전에 실패했을 수 있으므로
      retrieved_candidates/final_context_chunks/final_context_hash에 null을 허용한다.
      (null = 미확인, [] = 실제로 비어 있음)
    - 추가 필드는 보존한다(error_type, model_input 등).
    """
    if not isinstance(raw, Mapping):
        raise RunRecordError("실행 기록은 JSON 객체(Mapping)여야 한다")
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise RunRecordError(f"필수 필드 누락: {missing}")
    rec = dict(raw)
    for f in ("experiment_id", "question_id", "request_id", "dataset_id", "config_hash", "question"):
        if not isinstance(rec[f], str) or not rec[f]:
            raise RunRecordError(f"{f}는 비어 있지 않은 문자열이어야 한다")
    if rec["retrieval_mode"] not in RETRIEVAL_MODES:
        raise RunRecordError(f"retrieval_mode는 {RETRIEVAL_MODES} 중 하나: {rec['retrieval_mode']!r}")
    if rec["status"] not in STATUSES:
        raise RunRecordError(f"status는 {STATUSES} 중 하나: {rec['status']!r}")
    is_error = rec["status"] == "error"

    for field, need_sid in (("retrieved_candidates", False), ("final_context_chunks", True)):
        value = rec[field]
        if value is None and is_error:
            continue
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise RunRecordError(f"{field}는 배열이어야 한다")
        rec[field] = [_normalize_chunk(c, f"{field}[{i}]", need_source_id=need_sid)
                      for i, c in enumerate(value)]

    if rec["final_context_chunks"] is not None:
        if not isinstance(rec["final_context_hash"], str) or not rec["final_context_hash"]:
            raise RunRecordError("final_context_chunks가 있으면 final_context_hash도 있어야 한다")
    if not is_error and not isinstance(rec["answer"], str):
        raise RunRecordError("정상 응답의 answer는 문자열이어야 한다")
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
        a["input_tokens"] = _int_or_none(a.get("input_tokens"), f"attempts[{i}].input_tokens")
        a["output_tokens"] = _int_or_none(a.get("output_tokens"), f"attempts[{i}].output_tokens")
        a.setdefault("error", None)
        attempts.append(a)
    rec["attempts"] = attempts
    rec["usage"] = summarize_usage(attempts)
    rec.setdefault("error_type", None)
    if is_error and not rec["error_type"]:
        rec["error_type"] = "unspecified"
    return rec
