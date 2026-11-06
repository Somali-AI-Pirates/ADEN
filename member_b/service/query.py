from dataclasses import asdict
from hashlib import sha256
from time import perf_counter

from openai import APIError, APITimeoutError

from shared.contracts import QueryRequest, QueryResponse
from shared.exceptions import ServiceError
from member_b.service.resources import Resources
from shared.settings import Settings
from member_b.generation.chain import DEFAULT_PROMPT, generate_answer
from member_b.generation.citations import resolve_citations
from member_b.generation.context import build_context
from member_a.retrieval.service import retrieve
from member_c.observability.tracing import RequestTrace, Tracer
from member_c.observability.records import compute_context_hash


def prompt_hash() -> str:
    """답변 프롬프트 파일 내용의 해시. 프롬프트가 바뀌면 실험 구분 값도 바뀐다."""
    return sha256(DEFAULT_PROMPT.read_bytes()).hexdigest()


def experiment_config_hash(settings: Settings) -> str:
    """공통 설정과 답변 프롬프트를 함께 반영한 실험 구분 해시."""
    return sha256((settings.model_dump_json() + prompt_hash()).encode()).hexdigest()


def answer_query(
    request: QueryRequest,
    resources: Resources,
    request_id: str,
    settings: Settings,
) -> QueryResponse:
    started = perf_counter()
    tracer = getattr(resources, "tracer", None) or Tracer()
    trace = tracer.start_request(
        request_id,
        question=request.question,
        retrieval_mode=request.retrieval_mode or settings.retrieval.default_mode,
        dataset_id=settings.dataset,
        config_hash=experiment_config_hash(settings),
        metadata={"prompt_hash": prompt_hash()},
    )
    try:
        response = _answer_query(request, resources, request_id, settings, trace)
    except Exception as error:
        trace.finish(
            status="error", answer=None,
            reason=str(error) if isinstance(error, ServiceError) else "질문 처리 오류",
            citations=[], latency_ms=round((perf_counter() - started) * 1000),
            error_type=type(error).__name__,
        )
        raise
    trace.finish(
        status=response.status, answer=response.answer, reason=response.reason,
        citations=[citation.model_dump(mode="json") for citation in response.citations],
        latency_ms=round((perf_counter() - started) * 1000),
    )
    return response


def _answer_query(
    request: QueryRequest,
    resources: Resources,
    request_id: str,
    settings: Settings,
    trace: RequestTrace,
) -> QueryResponse:
    mode = request.retrieval_mode or settings.retrieval.default_mode
    retrieval_started = perf_counter()
    try:
        candidates = retrieve(
            request.question, mode, resources,
            top_n=settings.retrieval.candidate_count,
        )
    except NotImplementedError as error:
        raise ServiceError(
            "이 검색 방식은 준비 중입니다. 현재는 naive를 선택해 주세요.",
            501,
        ) from error
    except Exception as error:
        raise ServiceError("검색 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.", 503) from error
    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)

    try:
        context = build_context(
            candidates,
            settings.retrieval.k,
            settings.retrieval.context_token_budget,
            resources.count_tokens,
        )
        retrieved_candidates = [
            {**asdict(item.chunk), "rank": item.rank, "score": item.score,
             "retriever": item.retriever}
            for item in candidates
        ]
        final_context_chunks = [
            {**asdict(item.chunk), "source_id": f"S{index}"}
            for index, item in enumerate(context.final_chunks, 1)
        ]
        trace.record_retrieval(
            retrieved_candidates=retrieved_candidates,
            final_context_chunks=final_context_chunks,
            final_context_hash=compute_context_hash(final_context_chunks),
            model_context_text=context.text,
            model_context_hash=context.context_hash,
            k=settings.retrieval.k,
            latency_ms=retrieval_ms,
        )
        if not context.final_chunks:
            return QueryResponse(
                request_id=request_id, status="abstained",
                answer="현재 제공된 문서에서는 질문에 답할 근거를 확인할 수 없습니다.",
                reason="질문과 관련된 검색 문맥을 확보하지 못했습니다.",
                citations=[],
            )
        generated = generate_answer(request.question, context, resources.model, trace=trace)
        answer = generated.model_answer
        citations = resolve_citations(answer.source_ids, context.source_map)
    except APITimeoutError as error:
        raise ServiceError("답변 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", 504) from error
    except APIError as error:
        raise ServiceError("답변 생성 서비스에 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", 502) from error
    except ValueError as error:
        raise ServiceError("답변의 형식이나 출처를 확인하지 못했습니다. 다시 시도해 주세요.", 502) from error

    return QueryResponse(
        request_id=request_id, status=answer.status, answer=answer.answer,
        reason=answer.reason, citations=citations,
    )
