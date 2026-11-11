from contextlib import nullcontext
from hashlib import sha256
from time import perf_counter
from typing import Any

from openai import APIError, APITimeoutError

from shared.contracts import QueryRequest, QueryResponse
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
from app.generation.chain import DEFAULT_PROMPT, generate_answer
from app.generation.citations import resolve_citations
from app.generation.context import build_context, validate_context


def prompt_hash() -> str:
    return sha256(DEFAULT_PROMPT.read_bytes()).hexdigest()


def experiment_config_hash(settings: Settings) -> str:
    return sha256((settings.model_dump_json() + prompt_hash()).encode()).hexdigest()


def start_request_trace(resources, request, request_id, settings):
    if resources.tracer is None:
        return None
    return resources.tracer.start_request(
        request_id,
        question=request.question,
        retrieval_mode=request.retrieval_mode,
        dataset_id=settings.dataset,
        config_hash=experiment_config_hash(settings),
        metadata={"prompt_hash": prompt_hash()},
    )


def answer_query(
    request: QueryRequest,
    resources: Resources,
    request_id: str,
    settings: Settings,
    *,
    run_record: dict | None = None,
) -> QueryResponse:
    started = perf_counter()
    mode = request.retrieval_mode or settings.retrieval.default_mode
    request = request.model_copy(update={"retrieval_mode": mode})
    if run_record is not None:
        run_record.update(
            create_run_record(
                request.question,
                mode,
                request_id,
                settings,
                experiment_config_hash(settings),
            )
        )
    trace = None
    response = None
    error_type = None
    reason = None
    try:
        trace = start_request_trace(resources, request, request_id, settings)
        response = _answer_query(
            request, resources, request_id, settings, trace, run_record
        )
        if run_record is not None:
            run_record.update(response.model_dump(mode="json"))
        return response
    except Exception as error:
        error_type = type(error.__cause__ or error).__name__
        reason = str(error) if isinstance(error, ServiceError) else "질문 처리 오류"
        if run_record is not None:
            run_record.update(
                status="error",
                answer=None,
                reason=reason,
                citations=[],
                error_type=error_type,
            )
        raise
    finally:
        elapsed = round((perf_counter() - started) * 1000)
        if run_record is not None:
            run_record.update(latency_ms=elapsed, latency_scope="service")
            attach_last_model_messages(run_record)
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
            if run_record is not None and isinstance(metadata, dict):
                run_record.update(metadata)


def _answer_query(
    request, resources, request_id, settings, trace: Any | None, run_record
):
    try:
        candidates, context = prepare_evidence(
            request.question, request.retrieval_mode, resources, settings,
            trace=trace, run_record=run_record,
        )
        if not context.final_chunks:
            return QueryResponse(
                request_id=request_id,
                status="abstained",
                answer="현재 제공된 문서에서는 질문에 답할 근거를 확인할 수 없습니다.",
                reason="질문과 관련된 검색 문맥을 확보하지 못했습니다.",
                citations=[],
            )
        generated = generate_answer(
            request.question,
            context,
            resources.model,
            trace=trace,
            attempt_log=run_record["attempts"] if run_record is not None else None,
            max_attempts=settings.generation_max_attempts,
        )
        answer = generated.model_answer
        citations = resolve_citations(answer.source_ids, context.source_map)
    except APITimeoutError as error:
        raise ServiceError(
            "답변 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", 504
        ) from error
    except APIError as error:
        raise ServiceError(
            "답변 생성 서비스에 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 502
        ) from error
    except ValueError as error:
        raise ServiceError(
            "답변의 형식이나 출처를 확인하지 못했습니다. 다시 시도해 주세요.", 502
        ) from error
    return QueryResponse(
        request_id=request_id,
        status=answer.status,
        answer=answer.answer,
        reason=answer.reason,
        citations=citations,
    )


def prepare_evidence(question, mode, resources, settings, *, trace=None, run_record=None):
    """A 결과를 받아 생성에 넣을 최종 문맥을 확정한다.

    provider가 (후보, 최종 문맥)을 주면 문맥을 그대로 쓴다. (후보, None)을 주거나 provider 없이
    retrieve()를 쓰면 공통 build_context로 만든다. 어느 쪽이든 설정의 k·토큰 한도를 넘을 수 없다.
    """
    supplied = resources.evidence_provider is not None
    context = None
    started = perf_counter()
    try:
        stage = (trace.stage("retrieval", as_type="retriever",
                             input={"question": question, "retrieval_mode": mode})
                 if trace else nullcontext({}))
        with stage as observation:
            if supplied:
                candidates, context = resources.evidence_provider(question, mode)
            else:
                candidates = retrieve(question, mode, resources,
                                      top_n=settings.retrieval.candidate_count)
            flat = candidate_records(candidates)
            observation.update(output={"candidates": flat}, metadata={
                "provided_context": supplied, "candidate_count": len(flat),
                "latency_ms": round((perf_counter() - started) * 1000)})
            if run_record is not None:
                run_record.update(retrieved_candidates=flat, provided_context=supplied)
    except NotImplementedError as error:
        raise ServiceError("이 검색 방식은 준비 중입니다.", 501) from error
    except Exception as error:
        raise ServiceError("검색 자료를 준비하지 못했습니다. 연결 설정을 확인해 주세요.", 503) from error
    stage = trace.stage("final-context") if trace else nullcontext({})
    with stage:
        if context is None:
            context = build_context(candidates, settings.retrieval.k,
                                    settings.retrieval.context_token_budget, resources.count_tokens)
        try:
            validate_context(context, resources.count_tokens)
        except ValueError as error:
            raise ServiceError(
                "검색 문맥의 본문·출처·해시·토큰 수가 일치하지 않습니다. 문맥 제공 설정을 확인해 주세요.",
                503,
            ) from error
        if (len(context.final_chunks) > settings.retrieval.k
                or resources.count_tokens(context.text) > settings.retrieval.context_token_budget):
            raise ServiceError(
                "검색 문맥이 설정한 개수·토큰 한도를 넘었습니다. 문맥 제공 설정을 확인해 주세요.", 503)
        data = context_record(candidates, context)
        if run_record is not None:
            run_record.update(data)
        if trace is not None:
            trace.record_retrieval(
                retrieved_candidates=data["retrieved_candidates"],
                final_context_chunks=data["final_context_chunks"],
                final_context_hash=data["final_context_hash"],
                model_context_text=context.text, model_context_hash=context.context_hash,
                k=settings.retrieval.k,
                model_context_token_count=context.token_count,
                budget_excluded_chunk_ids=context.excluded_chunk_ids,
            )
    return candidates, context
