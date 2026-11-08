from hashlib import sha256
from time import perf_counter
from typing import Any

from openai import APIError, APITimeoutError
from qdrant_client.http.exceptions import ApiException

from shared.contracts import QueryResponse, RetrievalMode
from shared.exceptions import ServiceError
from shared.settings import Settings
from app.retrieval.service import retrieve
from app.service.resources import Resources
from app.service.records import (
    attach_last_model_messages,
    candidate_records,
    context_record,
    create_run_record,
)
from app.generation.chain import DEFAULT_PROMPT, InvalidModelOutput, generate_answer
from app.generation.context import build_context


def prompt_hash() -> str:
    return sha256(DEFAULT_PROMPT.read_bytes()).hexdigest()


def experiment_config_hash(
    settings: Settings, *, prompt_digest: str | None = None
) -> str:
    if prompt_digest is None:
        prompt_digest = prompt_hash()
    return sha256((settings.model_dump_json() + prompt_digest).encode()).hexdigest()


def start_request_trace(
    resources: Resources,
    request_id: str,
    settings: Settings,
    *,
    question: str,
    retrieval_mode: RetrievalMode,
    prompt_digest: str | None = None,
):
    tracer = resources.tracer
    if tracer is None or not tracer.enabled:
        return None
    if prompt_digest is None:
        prompt_digest = prompt_hash()
    return tracer.start_request(
        request_id,
        question=question,
        retrieval_mode=retrieval_mode,
        dataset_id=settings.dataset,
        config_hash=experiment_config_hash(settings, prompt_digest=prompt_digest),
        metadata={"prompt_hash": prompt_digest},
    )


def answer_query(
    question: str,
    resources: Resources,
    request_id: str,
    settings: Settings,
) -> QueryResponse:
    """서버에 설정된 검색 방식으로 질문에 답한다."""
    response, _ = _run_query(
        question, resources, request_id, settings, mode=settings.retrieval.default_mode
    )
    assert response is not None
    return response


def record_query(
    question: str,
    resources: Resources,
    request_id: str,
    settings: Settings,
    *,
    retrieval_mode: RetrievalMode | None = None,
) -> dict[str, Any]:
    """질문을 실행하고 성공·실패를 평가 기록으로 반환한다."""
    _, record = _run_query(
        question,
        resources,
        request_id,
        settings,
        mode=retrieval_mode or settings.retrieval.default_mode,
        capture_record=True,
    )
    assert record is not None
    return record


def _run_query(
    question: str,
    resources: Resources,
    request_id: str,
    settings: Settings,
    *,
    mode: RetrievalMode,
    capture_record: bool = False,
) -> tuple[QueryResponse | None, dict[str, Any] | None]:
    started = perf_counter()
    record = None
    prompt_digest = None
    if capture_record:
        prompt_digest = prompt_hash()
        record = create_run_record(
            question,
            mode,
            request_id,
            settings,
            experiment_config_hash(settings, prompt_digest=prompt_digest),
        )
    trace = None
    response = None
    error_type = None
    reason = None
    try:
        trace = start_request_trace(
            resources,
            request_id,
            settings,
            question=question,
            retrieval_mode=mode,
            prompt_digest=prompt_digest,
        )
        if record is not None and trace is None and resources.tracer is not None:
            record.update(langfuse_status="disabled", langfuse_errors=[])
        response = _retrieve_and_answer(
            question, mode, resources, request_id, settings, trace, record
        )
        if record is not None:
            record.update(response.model_dump(mode="json"))
    except Exception as error:
        error_type = type(error.__cause__ or error).__name__
        reason = str(error) if isinstance(error, ServiceError) else "질문 처리 오류"
        if record is None:
            raise
        response = None
        record.update(
            status="error",
            answer=None,
            reason=reason,
            citations=[],
            error_type=error_type,
        )
    finally:
        elapsed = round((perf_counter() - started) * 1000)
        if record is not None:
            record.update(latency_ms=elapsed, latency_scope="service")
            attach_last_model_messages(record)
        if trace is not None:
            metadata = trace.finish(
                status=response.status if response else "error",
                answer=response.answer if response else None,
                reason=response.reason if response else reason,
                citations=[
                    citation.model_dump(mode="json") for citation in response.citations
                ]
                if response
                else [],
                latency_ms=elapsed,
                error_type=error_type,
            )
            if record is not None and isinstance(metadata, dict):
                record.update(metadata)
    return response, record


def _retrieve_and_answer(
    question, mode, resources, request_id, settings, trace: Any | None, record
):
    retrieval_started = perf_counter()
    try:
        candidates = retrieve(
            question, mode, resources, top_n=settings.retrieval.candidate_count
        )
    except NotImplementedError as error:
        raise ServiceError(
            "서버에 설정된 검색 방식을 사용할 수 없습니다.", 501
        ) from error
    except ApiException as error:
        raise ServiceError(
            "검색 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.", 503
        ) from error
    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
    retrieved_candidates = (
        candidate_records(candidates)
        if record is not None or trace is not None
        else None
    )
    if record is not None:
        record["retrieved_candidates"] = retrieved_candidates
    context = build_context(
        candidates,
        settings.retrieval.k,
        settings.retrieval.context_token_budget,
        resources.count_tokens,
    )
    if retrieved_candidates is not None:
        data = context_record(retrieved_candidates, context)
        if record is not None:
            record.update(data)
        if trace is not None:
            trace.record_retrieval(
                retrieved_candidates=retrieved_candidates,
                final_context_chunks=data["final_context_chunks"],
                final_context_hash=data["final_context_hash"],
                model_context_text=context.text,
                model_context_hash=context.context_hash,
                k=settings.retrieval.k,
                latency_ms=retrieval_ms,
            )
    if not context.final_chunks:
        return QueryResponse(
            request_id=request_id,
            status="abstained",
            answer="현재 제공된 문서에서는 질문에 답할 근거를 확인할 수 없습니다.",
            reason="질문과 관련된 검색 문맥을 확보하지 못했습니다.",
            citations=[],
        )
    try:
        generated = generate_answer(
            question,
            context,
            resources.model,
            trace=trace,
            attempt_log=record["attempts"] if record is not None else None,
            max_attempts=settings.generation_max_attempts,
            capture_attempts=False,
        )
    except APITimeoutError as error:
        raise ServiceError(
            "답변 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", 504
        ) from error
    except APIError as error:
        raise ServiceError(
            "답변 생성 서비스에 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 502
        ) from error
    except InvalidModelOutput as error:
        raise ServiceError(
            "답변의 형식이나 출처를 확인하지 못했습니다. 다시 시도해 주세요.", 502
        ) from error
    answer = generated.model_answer
    return QueryResponse(
        request_id=request_id,
        status=answer.status,
        answer=answer.answer,
        reason=answer.reason,
        citations=generated.citations,
    )
