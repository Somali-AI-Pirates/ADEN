"""검색 지표(문서 단위 Hit@k·Recall@k), 점수 표현, 평균·운영 지표 집계."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from evaluation.dataset import evidence_books

SCORE_STATUSES = ("scored", "not_applicable", "unavailable")


def score(value: float | None, status: str, reason: str) -> dict[str, Any]:
    """평가값 하나. scored만 value를 가진다(해당 없음·평가 불가를 숫자로 바꾸지 않음)."""
    if status not in SCORE_STATUSES:
        raise ValueError(f"알 수 없는 평가 상태: {status}")
    if (status == "scored") != (value is not None):
        raise ValueError("scored만 value를 가져야 한다")
    return {"value": value, "status": status, "reason": reason}


def retrieval_metrics(reference: Mapping[str, Any],
                      final_context_chunks: Sequence[Mapping[str, Any]] | None,
                      k: int) -> dict[str, Any]:
    """생성 직전 최종 문맥 상위 k개 청크의 book_id로 계산한다. 검색 후보 전체는 쓰지 않는다."""
    gold = evidence_books(reference)
    delivered = None if final_context_chunks is None else len(final_context_chunks)
    base = {"k": k, "delivered_chunks": delivered, "reference_books": gold}
    if not gold:
        na = score(None, "not_applicable", "참고 근거 문서가 없음")
        return {**base, "retrieved_books": None, "hit_at_k": na, "recall_at_k": na}
    if final_context_chunks is None:
        un = score(None, "unavailable", "서비스 실패로 최종 문맥이 기록되지 않음")
        return {**base, "retrieved_books": None, "hit_at_k": un, "recall_at_k": un}
    retrieved = sorted({c["book_id"] for c in final_context_chunks[:k]})
    found = set(gold) & set(retrieved)
    reason = f"정답 문서 {len(gold)}개 중 {len(found)}개 포함 (최종 문맥 {delivered}개 청크)"
    return {**base, "retrieved_books": retrieved,
            "hit_at_k": score(1.0 if found else 0.0, "scored", reason),
            "recall_at_k": score(len(found) / len(gold), "scored", reason)}


def aggregate(scores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """평균은 scored만 사용하고 해당 없음·평가 불가는 개수로 표시한다. 0점은 평균에 포함."""
    scores = list(scores)
    values = [s["value"] for s in scores if s["status"] == "scored"]
    return {"mean": statistics.fmean(values) if values else None,
            "scored": len(values),
            "not_applicable": sum(s["status"] == "not_applicable" for s in scores),
            "unavailable": sum(s["status"] == "unavailable" for s in scores)}


def operational_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """요청 단위 운영 지표. 정상 추가 확인·보류는 오류가 아니며 재시도는 별도 요청으로 세지 않는다."""
    n = len(records)
    errors = [r for r in records if r["status"] == "error"]
    ok = [r for r in records if r["status"] != "error"]

    def latency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        vals = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        return {"mean_ms": statistics.fmean(vals) if vals else None,
                "p50_ms": statistics.median(vals) if vals else None,
                "known": len(vals), "count": len(rows)}

    usage_status = [r["usage"]["status"] for r in records]
    tokens = {}
    for key in ("input_tokens", "output_tokens"):
        known = [r["usage"][key] for r in records if r["usage"][key] is not None]
        tokens[key] = sum(known) if known else None
    return {
        "requests": n,
        "errors": len(errors),
        "error_rate": len(errors) / n if n else None,
        "error_types": sorted({r.get("error_type") or "unspecified" for r in errors}),
        "status_counts": {s: sum(r["status"] == s for r in records)
                          for s in ("answered", "needs_clarification", "abstained", "error")},
        "latency_ok": latency(ok),
        "latency_error": latency(errors),
        "latency_all": latency(records),
        **tokens,
        "usage_complete": usage_status.count("complete"),
        "usage_partial": usage_status.count("partial"),
        "usage_unknown": usage_status.count("unknown") + usage_status.count("no_attempts"),
        "retry_attempts": sum(max(len(r["attempts"]) - 1, 0) for r in records),
    }
