import re
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from shared.contracts import ContextBundle, GenerationResult, ModelAnswer
from app.generation.citations import resolve_citations

DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "shipping_qa.txt"
CITATION_MARK = re.compile(r"\[(S\d+)\]")


def _check_marked_sources(answer: ModelAnswer, context: ContextBundle) -> ModelAnswer:
    """답변 본문의 [S#] 표시가 최종 문맥에 있는지 확인하고, source_ids에서 빠진 ID를 보충한다."""
    marked = list(dict.fromkeys(CITATION_MARK.findall(answer.answer)))
    unknown = [source_id for source_id in marked if source_id not in context.source_map]
    if unknown:
        raise ValueError(f"답변 본문에 최종 검색 문맥에 없는 출처가 있습니다. {unknown}")
    missing = [source_id for source_id in marked if source_id not in answer.source_ids]
    if missing:
        answer = answer.model_copy(update={"source_ids": [*answer.source_ids, *missing]})
    return answer


def _validate(result: dict, context: ContextBundle) -> ModelAnswer:
    """모델 출력의 형식과 출처를 확인한다. 어긋나면 ValueError로 재생성 대상임을 알린다."""
    if result["parsing_error"] is not None or result["parsed"] is None:
        raise ValueError("모델 응답을 지정된 답변 형식으로 읽을 수 없습니다.")
    answer = _check_marked_sources(result["parsed"], context)
    if not answer.answer.strip() or not answer.reason.strip():
        raise ValueError("모델의 답변과 판단 이유는 비어 있을 수 없습니다.")
    resolve_citations(answer.source_ids, context.source_map)
    if answer.status == "answered" and not answer.source_ids:
        raise ValueError("답변 완료 응답에 근거 출처가 없습니다.")
    return answer


def generate_answer(
    question: str,
    context: ContextBundle,
    model: ChatOpenAI,
    *,
    prompt_path: Path = DEFAULT_PROMPT,
    defense: str = "",
    trace: Any | None = None,
    attempt_log: list[dict] | None = None,
    max_attempts: int = 1,
) -> GenerationResult:
    """이미 선택한 문맥으로 답변을 생성하고 실제 모델 입력·출력·사용량을 반환한다.

    출력 형식·출처 검증에 실패하면 같은 문맥으로 max_attempts까지 다시 생성하고
    모든 시도를 별도 기록으로 남긴다. 모델 호출 자체의 오류는 재시도하지 않고 그대로 올린다.
    """
    system_prompt = prompt_path.read_text(encoding="utf-8")
    if defense:
        system_prompt += "\n\n" + defense
    prompt = ChatPromptTemplate.from_messages([
        ("system", "{instructions}"),
        ("human", "질문\n{question}\n\n검색 자료\n{context}"),
    ])
    values = {"instructions": system_prompt, "question": question, "context": context.text}
    messages = prompt.invoke(values).to_messages()
    input_messages = [message.model_dump(mode="json") for message in messages]
    variant = "defense" if defense else "base"
    parameters = {"max_output_tokens": model.max_tokens}
    structured = model.with_structured_output(ModelAnswer, method="json_schema", include_raw=True)
    chain = prompt | structured

    def record(attempt: dict) -> None:
        if trace is not None:
            trace.record_attempt(**attempt)
        if attempt_log is not None:
            attempt_log.append(attempt)

    attempts: list[dict] = []
    last_error = ValueError("모델 응답을 확인하지 못했습니다.")
    for number in range(1, max_attempts + 1):
        base = {
            "attempt": number, "model": model.model_name, "model_input": input_messages,
            "prompt_variant": variant, "model_parameters": parameters,
        }
        stage = trace.stage(f"generation-attempt-{number}", as_type="generation",
                            model=model.model_name, input=input_messages) if trace else nullcontext({})
        with stage:
            started = perf_counter()
            try:
                result = chain.invoke(values)
                latency_ms = round((perf_counter() - started) * 1000)
            except Exception as error:
                record({
                    **base, "latency_ms": round((perf_counter() - started) * 1000),
                    "model_output": None, "input_tokens": None, "output_tokens": None,
                    "error": type(error).__name__,
                })
                raise

            raw = result["raw"]
            usage = raw.usage_metadata or {}
            attempt = {
                **base, "latency_ms": latency_ms, "model_output": raw.model_dump(mode="json"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "error": type(result["parsing_error"]).__name__ if result["parsing_error"] else None,
            }
            try:
                validation = trace.stage(f"validation-attempt-{number}") if trace else nullcontext({})
                with validation:
                    answer = _validate(result, context)
            except ValueError as error:
                attempt["error"] = attempt["error"] or type(error).__name__
                record(attempt)
                attempts.append(attempt)
                last_error = error
                continue
            record(attempt)
            attempts.append(attempt)
            return GenerationResult(model_answer=answer, attempts=attempts, usage=raw.usage_metadata)
    raise last_error
