"""서비스 결과를 평가용 평탄 기록으로 조립한다."""

from dataclasses import asdict

from app.observability.records import compute_context_hash


def create_run_record(question, mode, request_id, settings, config_hash):
    return {
        "experiment_id": "live",
        "question_id": request_id,
        "request_id": request_id,
        "dataset_id": settings.dataset,
        "config_hash": config_hash,
        "question": question,
        "retrieval_mode": mode,
        "retrieved_candidates": None,
        "final_context_chunks": None,
        "final_context_hash": None,
        "model_context_text": None,
        "model_context_hash": None,
        "status": "error",
        "answer": None,
        "reason": None,
        "citations": [],
        "latency_ms": None,
        "attempts": [],
        "langfuse_trace_id": None,
        "error_type": None,
    }


def candidate_records(candidates):
    return [
        {
            **asdict(item.chunk),
            "rank": item.rank,
            "score": item.score,
            "retriever": item.retriever,
        }
        for item in candidates
    ]


def context_record(retrieved_candidates, context):
    final_chunks = [
        {**asdict(item.chunk), "source_id": f"S{index}"}
        for index, item in enumerate(context.final_chunks, 1)
    ]
    return {
        "retrieved_candidates": retrieved_candidates,
        "final_context_chunks": final_chunks,
        "final_context_hash": compute_context_hash(final_chunks),
        "model_context_text": context.text,
        "model_context_hash": context.context_hash,
        "model_context_token_count": context.token_count,
        "final_context_chunk_count": len(final_chunks),
        "budget_excluded_chunk_ids": context.excluded_chunk_ids,
    }


def attach_last_model_messages(record):
    attempt = record["attempts"][-1] if record["attempts"] else {}
    record["model_input"] = attempt.get("model_input")
    record["model_output"] = attempt.get("model_output")
