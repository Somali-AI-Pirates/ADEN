import hashlib

import pytest

from member_Damsebi.observability.records import (RunRecordError, compute_context_hash,
                                       normalize_run_record, summarize_usage)
from member_Damsebi.evaluation.dataset import evidence_books
from member_Damsebi.evaluation.metrics import aggregate, operational_summary, retrieval_metrics, score


def chunk(book, cid=None, page=1, sid="S1"):
    return {"chunk_id": cid or f"{book}-{page}", "book_id": book, "file_name": f"{book}.pdf",
            "page": page, "source_id": sid, "text": "t"}


REF = {"evidence_groups": [[{"book_id": "A"}], [{"book_id": "B"}, {"book_id": "A"}]]}


def test_evidence_books_dedup_across_groups():
    assert evidence_books(REF) == ["A", "B"]


def test_hit_recall_dedup_by_document():
    # A 청크 두 개는 문서 하나로 센다 → Recall 1/2
    m = retrieval_metrics(REF, [chunk("A", page=1), chunk("A", page=2)], k=5)
    assert m["hit_at_k"]["value"] == 1.0 and m["recall_at_k"]["value"] == 0.5
    assert m["delivered_chunks"] == 2 and m["k"] == 5


def test_only_top_k_final_chunks_count():
    m = retrieval_metrics(REF, [chunk("X"), chunk("A"), chunk("B")], k=2)
    assert m["recall_at_k"]["value"] == 0.5 and m["retrieved_books"] == ["A", "X"]


def test_no_reference_is_not_applicable_and_empty_retrieval_is_zero():
    na = retrieval_metrics({"evidence_groups": []}, [chunk("A")], k=5)
    assert na["hit_at_k"]["status"] == "not_applicable" and na["hit_at_k"]["value"] is None
    zero = retrieval_metrics(REF, [], k=5)
    assert zero["hit_at_k"] == {"value": 0.0, "status": "scored", "reason": zero["hit_at_k"]["reason"]}
    assert zero["recall_at_k"]["value"] == 0.0
    unknown = retrieval_metrics(REF, None, k=5)
    assert unknown["recall_at_k"]["status"] == "unavailable"


def test_aggregate_excludes_na_and_unavailable_but_keeps_zero():
    agg = aggregate([score(1.0, "scored", ""), score(0.0, "scored", ""),
                     score(None, "not_applicable", ""), score(None, "unavailable", "")])
    assert agg == {"mean": 0.5, "scored": 2, "not_applicable": 1, "unavailable": 1}
    with pytest.raises(ValueError):
        score(0.0, "unavailable", "평가 실패를 점수로 바꾸지 않는다")


def test_usage_missing_values_stay_null():
    assert summarize_usage([]) == {"input_tokens": None, "output_tokens": None,
                                   "status": "no_attempts"}
    assert summarize_usage([{"input_tokens": None, "output_tokens": None}])["status"] == "unknown"
    partial = summarize_usage([{"input_tokens": 10, "output_tokens": None},
                               {"input_tokens": 10, "output_tokens": 5}])
    assert partial == {"input_tokens": 20, "output_tokens": 5, "status": "partial"}


def att(n, **kw):
    return {"attempt": n, "model": "m", "model_input": [{"role": "user", "content": "q"}],
            "model_output": "out", **kw}


CTX = "[S1]\n내용: t"


def base_record(**kw):
    final = [chunk("A")]
    rec = {"model_context_text": CTX,
           "model_context_hash": hashlib.sha256(CTX.encode("utf-8")).hexdigest(), "experiment_id": "e", "question_id": "q", "request_id": "r", "dataset_id": "d",
           "retrieval_mode": "naive", "config_hash": "c", "question": "질문",
           "retrieved_candidates": [chunk("A"), chunk("B")], "final_context_chunks": final,
           "final_context_hash": compute_context_hash(final), "status": "answered",
           "answer": "a", "reason": "r", "citations": [], "latency_ms": 12.6,
           "attempts": [att(1, input_tokens=1, output_tokens=2)], "langfuse_trace_id": None}
    rec.update(kw)
    return rec


def test_normalize_valid_and_error_records():
    rec = normalize_run_record(base_record())
    assert rec["latency_ms"] == 13 and rec["usage"]["status"] == "complete"
    # 루트 model_input/model_output은 마지막 실제 시도의 값으로 연결
    assert rec["model_output"] == "out" and rec["model_input"] == rec["attempts"][-1]["model_input"]
    err = normalize_run_record(base_record(status="error", answer=None, reason=None,
                                           retrieved_candidates=None, final_context_chunks=None,
                                           final_context_hash=None, attempts=[],
                                           model_context_text=None, model_context_hash=None))
    assert err["error_type"] == "unspecified" and err["model_input"] is None


@pytest.mark.parametrize("change", [
    {"retrieval_mode": "bm25"},
    {"status": "ok"},
    {"final_context_chunks": [{"chunk_id": "x", "book_id": "A", "file_name": "A.pdf",
                               "page": 1, "text": "t"}]},  # source_id 누락
    {"final_context_chunks": [dict(chunk("A"), page=0)]},
    {"final_context_chunks": None},  # 정상 응답인데 최종 문맥 미기록
    {"attempts": [att(1, input_tokens=-1)]},
    {"attempts": [att(2)]},  # 시도 번호는 1부터
    {"attempts": [{"attempt": 1, "model": "m"}]},  # 실제 모델 입력 누락
    {"reason": None},  # 정상 응답의 reason은 문자열
    {"retrieved_candidates": [{"chunk_id": "x", "book_id": "A", "file_name": "A.pdf", "page": 1}]},
    {"final_context_hash": "0" * 64},  # ContextBundle.context_hash를 복사한 경우
    {"model_context_hash": "0" * 64},  # 문맥 문자열과 해시 불일치
    {"model_context_text": None, "model_context_hash": None},  # 모델 호출했는데 문맥 문자열 없음
])
def test_normalize_rejects_contract_violations(change):
    with pytest.raises(RunRecordError):
        normalize_run_record(base_record(**change))


def test_nested_retrieved_chunk_is_rejected_with_reason():
    nested = {"chunk": {k: v for k, v in chunk("A").items() if k != "source_id"},
              "rank": 1, "score": 0.8, "retriever": "dense"}
    with pytest.raises(RunRecordError, match="평탄"):
        normalize_run_record(base_record(retrieved_candidates=[nested]))


def test_abstain_without_model_call_needs_no_model_context():
    rec = normalize_run_record(base_record(status="abstained", final_context_chunks=[],
                                           final_context_hash=compute_context_hash([]),
                                           attempts=[], model_context_text=None,
                                           model_context_hash=None))
    assert rec["usage"]["status"] == "no_attempts"


def test_missing_required_field():
    rec = base_record()
    del rec["final_context_hash"]
    with pytest.raises(RunRecordError, match="final_context_hash"):
        normalize_run_record(rec)


def test_context_hash_depends_on_order_and_text():
    a, b = chunk("A", sid="S1"), chunk("B", sid="S2")
    assert compute_context_hash([a, b]) != compute_context_hash([b, a])
    assert compute_context_hash([a]) != compute_context_hash([dict(a, text="u")])


def test_ops_separates_errors_from_clarification():
    recs = [normalize_run_record(base_record(status="needs_clarification", latency_ms=100)),
            normalize_run_record(base_record(status="abstained", latency_ms=200,
                                             attempts=[att(1, input_tokens=None)])),
            normalize_run_record(base_record(status="error", answer=None, latency_ms=900,
                                             error_type="model_timeout",
                                             attempts=[att(1, input_tokens=5, error="t"),
                                                       att(2, input_tokens=5, error="t")]))]
    ops = operational_summary(recs)
    assert ops["errors"] == 1 and ops["error_rate"] == pytest.approx(1 / 3)
    assert ops["latency_ok"]["mean_ms"] == 150 and ops["latency_error"]["mean_ms"] == 900
    assert ops["input_tokens"] == 11 and ops["usage_unknown"] == 1
    assert ops["retry_attempts"] == 1 and ops["error_types"] == ["model_timeout"]
