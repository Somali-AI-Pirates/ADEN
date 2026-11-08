import re
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from shared.contracts import Citation, ContextBundle, GenerationResult, ModelAnswer
from app.generation.citations import resolve_citations

DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "shipping_qa.txt"
CITATION_MARK = re.compile(r"\[(S\d+)\]")


class InvalidModelOutput(ValueError):
    """모델 출력 형식이나 출처를 재시도 후에도 검증하지 못했다."""


def _check_marked_sources(answer: ModelAnswer, context: ContextBundle) -> ModelAnswer:
    """답변 본문의 [S#] 표시가 최종 문맥에 있는지 확인하고, source_ids에서 빠진 ID를 보충한다."""
    marked = list(dict.fromkeys(CITATION_MARK.findall(answer.answer)))
    unknown = [source_id for source_id in marked if source_id not in context.source_map]
    if unknown:
        raise ValueError(
            f"답변 본문에 최종 검색 문맥에 없는 출처가 있습니다. {unknown}"
        )
    missing = [source_id for source_id in marked if source_id not in answer.source_ids]
    if missing:
        answer = answer.model_copy(
            update={"source_ids": [*answer.source_ids, *missing]}
        )
    return answer


def _validate(
    result: dict, context: ContextBundle
) -> tuple[ModelAnswer, list[Citation]]:
    """모델 출력의 형식과 출처를 확인한다. 어긋나면 ValueError로 재생성 대상임을 알린다."""
    if result["parsing_error"] is not None or result["parsed"] is None:
        raise ValueError("모델 응답을 지정된 답변 형식으로 읽을 수 없습니다.")
    answer = _check_marked_sources(result["parsed"], context)
    if not answer.answer.strip() or not answer.reason.strip():
        raise ValueError("모델의 답변과 판단 이유는 비어 있을 수 없습니다.")
    citations = resolve_citations(answer.source_ids, context.source_map)
    if answer.status == "answered" and not answer.source_ids:
        raise ValueError("답변 완료 응답에 근거 출처가 없습니다.")
    return answer, citations


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
    capture_attempts: bool = True,
) -> GenerationResult:
    """이미 선택한 문맥으로 답변을 생성하고 실제 모델 입력·출력·사용량을 반환한다.

    출력 형식·출처 검증에 실패하면 같은 문맥으로 max_attempts까지 다시 생성하고
    요청된 경우 각 시도를 기록한다. 모델 호출 자체의 오류는 재시도하지 않고 그대로 올린다.
    """
    system_prompt = prompt_path.read_text(encoding="utf-8")
    if defense:
        system_prompt += "\n\n" + defense
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{instructions}"),
            ("human", "질문\n{question}\n\n검색 자료\n{context}"),
        ]
    )
    messages = prompt.format_messages(
        instructions=system_prompt,
        question=question,
        context=context.text,
    )
    input_messages = (
        [message.model_dump(mode="json") for message in messages]
        if capture_attempts or trace is not None or attempt_log is not None
        else None
    )
    structured = model.with_structured_output(
        ModelAnswer, method="json_schema", include_raw=True
    )
    attempts: list[dict] = []

    def record_attempt(number: int, raw: AIMessage | None, error: str | None) -> None:
        if input_messages is None:
            return
        usage = (raw.usage_metadata or {}) if raw is not None else {}
        attempt = {
            "attempt": number,
            "model": model.model_name,
            "model_input": input_messages,
            "prompt_variant": "defense" if defense else "base",
            "model_parameters": {"max_output_tokens": model.max_tokens},
            "model_output": raw.model_dump(mode="json") if raw is not None else None,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "error": error,
        }
        attempts.append(attempt)
        if trace is not None:
            trace.record_attempt(**attempt)
        if attempt_log is not None:
            attempt_log.append(attempt)

    last_error = None
    for number in range(1, max_attempts + 1):
        try:
            result = structured.invoke(messages)
        except Exception as error:
            record_attempt(number, None, type(error).__name__)
            raise

        raw = result["raw"]
        try:
            answer, citations = _validate(result, context)
        except ValueError as error:
            record_attempt(number, raw, type(result["parsing_error"] or error).__name__)
            last_error = error
            continue
        record_attempt(number, raw, None)
        return GenerationResult(
            model_answer=answer,
            citations=citations,
            attempts=attempts,
            usage=raw.usage_metadata,
        )

    if last_error is not None:
        raise InvalidModelOutput(str(last_error)) from last_error
    raise ValueError("최대 모델 호출 횟수는 양수여야 합니다.")
