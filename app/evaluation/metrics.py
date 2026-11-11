"""검색 지표(문서 단위 Hit@k·Recall@k), 점수 표현, 평균·운영 지표 집계."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.evaluation.dataset import evidence_books

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
    if final_context_chunks is None:
        delivered = None
    else:
        delivered = len(final_context_chunks)
    base = {"k": k, "delivered_chunks": delivered, "reference_books": gold}
    if not gold:
        na = score(None, "not_applicable", "참고 근거 문서가 없음")
        return {**base, "retrieved_books": None, "hit_at_k": na, "recall_at_k": na}
    if final_context_chunks is None:
        un = score(None, "unavailable", "서비스 실패로 최종 문맥이 기록되지 않음")
        return {**base, "retrieved_books": None, "hit_at_k": un, "recall_at_k": un}
    retrieved_book_ids = set()
    for chunk in final_context_chunks[:k]:
        retrieved_book_ids.add(chunk["book_id"])
    retrieved = sorted(retrieved_book_ids)
    found = set(gold) & set(retrieved)
    reason = f"정답 문서 {len(gold)}개 중 {len(found)}개 포함 (최종 문맥 {delivered}개 청크)"
    if found:
        hit_value = 1.0
    else:
        hit_value = 0.0
    return {**base, "retrieved_books": retrieved,
            "hit_at_k": score(hit_value, "scored", reason),
            "recall_at_k": score(len(found) / len(gold), "scored", reason)}


def aggregate(scores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """평균은 scored만 사용하고 해당 없음·평가 불가는 개수로 표시한다. 0점은 평균에 포함."""
    scores = list(scores)
    values = []
    for item in scores:
        if item["status"] == "scored":
            values.append(item["value"])
    not_applicable = 0
    for item in scores:
        if item["status"] == "not_applicable":
            not_applicable += 1
    unavailable = 0
    for item in scores:
        if item["status"] == "unavailable":
            unavailable += 1
    if values:
        mean = statistics.fmean(values)
    else:
        mean = None
    return {"mean": mean,
            "scored": len(values),
            "not_applicable": not_applicable,
            "unavailable": unavailable}


def operational_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """요청 단위 운영 지표. 정상 추가 확인·보류는 오류가 아니며 재시도는 별도 요청으로 세지 않는다."""
    n = len(records)
    errors = []
    for record in records:
        if record["status"] == "error":
            errors.append(record)
    ok = []
    for record in records:
        if record["status"] != "error":
            ok.append(record)

    def latency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        vals = []
        for row in rows:
            if row.get("latency_ms") is not None:
                vals.append(row["latency_ms"])
        if vals:
            mean_ms = statistics.fmean(vals)
        else:
            mean_ms = None
        if vals:
            p50_ms = statistics.median(vals)
        else:
            p50_ms = None
        return {"mean_ms": mean_ms,
                "p50_ms": p50_ms,
                "known": len(vals), "count": len(rows)}

    usage_status = []
    for record in records:
        usage_status.append(record["usage"]["status"])
    tokens = {}
    for key in ("input_tokens", "output_tokens"):
        known = []
        for record in records:
            if record["usage"][key] is not None:
                known.append(record["usage"][key])
        if known:
            tokens[key] = sum(known)
        else:
            tokens[key] = None
    error_types = set()
    for record in errors:
        error_types.add(record.get("error_type") or "unspecified")
    status_counts = {}
    for status in ("answered", "needs_clarification", "abstained", "error"):
        status_count = 0
        for record in records:
            if record["status"] == status:
                status_count += 1
        status_counts[status] = status_count
    retry_attempts = 0
    for record in records:
        retry_attempts += max(len(record["attempts"]) - 1, 0)
    if n:
        error_rate = len(errors) / n
    else:
        error_rate = None
    return {
        "requests": n,
        "errors": len(errors),
        "error_rate": error_rate,
        "error_types": sorted(error_types),
        "status_counts": status_counts,
        "latency_ok": latency(ok),
        "latency_error": latency(errors),
        "latency_all": latency(records),
        **tokens,
        "usage_complete": usage_status.count("complete"),
        "usage_partial": usage_status.count("partial"),
        "usage_unknown": usage_status.count("unknown") + usage_status.count("no_attempts"),
        "retry_attempts": retry_attempts,
    }


def evidence_diagnostics(reference, record, k):
    """페이지 포함과 인용문 보존은 다르며 예산 누락은 명시 ID만 사용한다."""
    candidates, final = record.get("retrieved_candidates"), record.get("final_context_chunks")
    gold_docs = set(evidence_books(reference))
    if not gold_docs:
        candidate_docs = score(None, "not_applicable", "참고 근거 문서가 없음")
    elif candidates is None:
        candidate_docs = score(None, "unavailable", "검색 후보가 기록되지 않음")
    else:
        found_docs = gold_docs & {c["book_id"] for c in candidates}
        candidate_docs = score(len(found_docs) / len(gold_docs), "scored",
                               f"정답 문서 {len(gold_docs)}개 중 {len(found_docs)}개 포함 (검색 후보 {len(candidates)}개)")
    groups = []
    for group in reference.get("evidence_groups") or []:
        pages = {(e["book_id"], e["page"]) for e in group
                 if e.get("book_id") and isinstance(e.get("page"), int)
                 and not isinstance(e["page"], bool) and e["page"] > 0}
        if pages:
            groups.append(pages)
    keys = ("candidate_page_recall", "final_page_recall", "context_lost_page_recall", "budget_lost_page_recall")
    if not groups:
        return {"candidate_doc_recall": candidate_docs,
                **{key: score(None, "not_applicable", "참고 근거 페이지가 없음") for key in keys}}

    def covered(chunks):
        if chunks is None:
            return None
        pages = {(c["book_id"], c["page"]) for c in chunks}
        return {i for i, group in enumerate(groups) if group & pages}

    def fraction(found, reason):
        if found is None:
            return score(None, "unavailable", reason + ": 실행 기록 없음")
        return score(len(found) / len(groups), "scored", reason + f" ({len(found)}/{len(groups)} 근거 묶음)")

    candidate_groups = covered(candidates)
    final_groups = covered(None if final is None else final[:k])
    lost = None if candidate_groups is None or final_groups is None else candidate_groups - final_groups
    excluded = record.get("budget_excluded_chunk_ids")
    budget_lost = None
    if lost is not None and isinstance(excluded, list):
        excluded_ids = set(excluded)
        budget_groups = covered([c for c in candidates if c["chunk_id"] in excluded_ids])
        budget_lost = lost & budget_groups
    return {
        "candidate_doc_recall": candidate_docs,
        "candidate_page_recall": fraction(candidate_groups, "전체 후보의 정답 페이지 포함"),
        "final_page_recall": fraction(final_groups, "최종 상위 k개의 정답 페이지 포함"),
        "context_lost_page_recall": fraction(lost, "후보에 있었지만 최종 문맥에서 빠진 근거"),
        "budget_lost_page_recall": fraction(budget_lost, "예산 제외 ID로 확인된 누락 근거"),
    }
