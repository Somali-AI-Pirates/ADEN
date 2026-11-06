"""C의 일반·보안 평가 실행기에 주입하는 B 측 호출 함수."""
from dataclasses import asdict
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from shared.contracts import QueryRequest
from shared.exceptions import ServiceError
from member_b.generation.chain import generate_answer
from member_b.generation.citations import resolve_citations
from member_b.generation.context import build_context
from member_b.service.query import answer_query, experiment_config_hash, start_request_trace
from member_b.service.records import attach_last_model_messages, context_record, create_run_record


def make_query_adapter(resources, settings):
    if resources.context_hasher is None:
        raise ServiceError("평가 실행 전 C의 문맥 해시 함수를 연결해 주세요.", 503)

    def adapter(question, retrieval_mode, request_id):
        request = QueryRequest(question=question, retrieval_mode=retrieval_mode)
        record = {}
        try:
            answer_query(request, resources, request_id, settings, run_record=record)
        except Exception:
            if record.get("status") != "error":
                raise
        record["latency_scope"] = "in_process_adapter"
        return record

    return adapter


def make_security_adapters(resources, settings, *, defense):
    if resources.context_hasher is None:
        raise ServiceError("보안 비교 전 C의 문맥 해시 함수를 연결해 주세요.", 503)
    cached = None

    def retrieve_fn(question, mode):
        nonlocal cached
        cached = None
        request = QueryRequest(question=question, retrieval_mode=mode)
        mode = request.retrieval_mode or settings.retrieval.default_mode
        request = request.model_copy(update={"retrieval_mode": mode})
        candidates = resources.retriever(request.question, mode, resources, top_n=settings.retrieval.candidate_count)
        context = build_context(candidates, settings.retrieval.k, settings.retrieval.context_token_budget, resources.count_tokens)
        cached = (request, candidates, context)
        return context_record(candidates, context, resources.context_hasher)

    def generate_fn(question, final_context_chunks, prompt_variant):
        if cached is None:
            raise ValueError("보안 비교 문맥을 먼저 준비해 주세요.")
        question = QueryRequest(question=question).question
        request, candidates, context = cached
        expected = [{**asdict(item.chunk), "source_id": f"S{index}"}
                    for index, item in enumerate(context.final_chunks, 1)]
        if question != request.question or final_context_chunks != expected:
            raise ValueError("보안 비교의 질문 또는 고정 문맥이 변경되었습니다.")
        if prompt_variant not in ("base", "defense") or (prompt_variant == "defense" and not defense.strip()):
            raise ValueError("프롬프트 변형과 방어 문장을 확인해 주세요.")
        started = perf_counter()
        request_id = uuid4().hex
        record = create_run_record(question, request.retrieval_mode, request_id, settings, experiment_config_hash(settings))
        record.update(context_record(candidates, context, resources.context_hasher))
        record.update(prompt_variant=prompt_variant, defense_hash=sha256(defense.encode()).hexdigest() if prompt_variant == "defense" else None)
        trace = None
        try:
            trace = start_request_trace(resources, request, request_id, settings)
            if trace is not None:
                trace.record_retrieval(
                    retrieved_candidates=record["retrieved_candidates"], final_context_chunks=record["final_context_chunks"],
                    final_context_hash=record["final_context_hash"], model_context_text=context.text,
                    model_context_hash=context.context_hash, k=settings.retrieval.k,
                )
            if not context.final_chunks:
                record.update(status="abstained", answer="현재 제공된 자료에서는 근거를 확인할 수 없습니다.", reason="검색 문맥이 없습니다.")
            else:
                result = generate_answer(
                    question, context, resources.model, defense=defense if prompt_variant == "defense" else "",
                    trace=trace, attempt_log=record["attempts"],
                    max_attempts=settings.generation_max_attempts,
                )
                answer = result.model_answer
                record.update(status=answer.status, answer=answer.answer, reason=answer.reason,
                              citations=[item.model_dump(mode="json") for item in resolve_citations(answer.source_ids, context.source_map)])
        except Exception as error:
            record.update(status="error", answer=None, reason="고정 문맥의 답변 생성에 실패했습니다.",
                          error_type=type(error.__cause__ or error).__name__)
        finally:
            record.update(latency_ms=round((perf_counter() - started) * 1000), latency_scope="fixed_context_generation")
            attach_last_model_messages(record)
            if trace is not None:
                metadata = trace.finish(status=record["status"], answer=record["answer"], reason=record["reason"],
                                        citations=record["citations"], latency_ms=record["latency_ms"], error_type=record["error_type"])
                if isinstance(metadata, dict):
                    record.update(metadata)
        return record

    return retrieve_fn, generate_fn
