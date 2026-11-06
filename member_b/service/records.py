"""B 실행 결과를 C가 받는 평탄 로그로 조립한다. 검증·채점은 C가 맡는다."""
from dataclasses import asdict
from contextvars import ContextVar

REQUEST_RECORD: ContextVar[dict | None] = ContextVar("aden_request_record", default=None)


def create_run_record(question, mode, request_id, settings, config_hash):
    return {
        "experiment_id": "live", "question_id": request_id, "request_id": request_id,
        "dataset_id": settings.dataset, "config_hash": config_hash,
        "question": question, "retrieval_mode": mode,
        "retrieved_candidates": None, "final_context_chunks": None,
        "final_context_hash": None, "model_context_text": None, "model_context_hash": None,
        "status": "error", "answer": None, "reason": None, "citations": [],
        "latency_ms": None, "attempts": [], "langfuse_trace_id": None, "error_type": None,
    }


def context_record(candidates, context, context_hasher):
    final_chunks = [
        {**asdict(item.chunk), "source_id": f"S{index}"}
        for index, item in enumerate(context.final_chunks, 1)
    ]
    return {
        "retrieved_candidates": [
            {**asdict(item.chunk), "rank": item.rank, "score": item.score, "retriever": item.retriever}
            for item in candidates
        ],
        "final_context_chunks": final_chunks,
        "final_context_hash": context_hasher(final_chunks) if context_hasher else None,
        "model_context_text": context.text, "model_context_hash": context.context_hash,
        "model_context_token_count": context.token_count,
    }


def attach_last_model_messages(record):
    attempt = record["attempts"][-1] if record["attempts"] else {}
    record["model_input"] = attempt.get("model_input")
    record["model_output"] = attempt.get("model_output")
