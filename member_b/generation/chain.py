import re
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from shared.contracts import ContextBundle, GenerationResult, ModelAnswer
from member_b.generation.citations import resolve_citations

DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "shipping_qa.txt"
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


def generate_answer(
    question: str,
    context: ContextBundle,
    model: ChatOpenAI,
    *,
    prompt_path: Path = DEFAULT_PROMPT,
    defense: str = "",
    trace: Any | None = None,
    attempt_log: list[dict] | None = None,
) -> GenerationResult:
    """이미 선택한 문맥으로 답변을 생성하고 실제 모델 입력·출력·사용량을 반환한다."""
    system_prompt = prompt_path.read_text(encoding="utf-8")
    if defense:
        system_prompt += "\n\n" + defense
    prompt = ChatPromptTemplate.from_messages([
        ("system", "{instructions}"),
        ("human", "질문\n{question}\n\n검색 자료\n{context}"),
    ])
    messages = prompt.format_messages(
        instructions=system_prompt,
        question=question,
        context=context.text,
    )
    input_messages = [message.model_dump(mode="json") for message in messages]
    try:
        result = model.with_structured_output(
            ModelAnswer, method="json_schema", include_raw=True,
        ).invoke(messages)
    except Exception as error:
        failed_attempt = {
            "attempt": 1, "model": model.model_name, "model_input": input_messages,
            "model_output": None, "input_tokens": None, "output_tokens": None,
            "error": type(error).__name__, "prompt_variant": "defense" if defense else "base",
            "model_parameters": {"max_output_tokens": model.max_tokens},
        }
        if attempt_log is not None:
            attempt_log.append(failed_attempt)
        if trace is not None:
            trace.record_attempt(**failed_attempt)
        raise

    raw = result["raw"]
    usage = raw.usage_metadata or {}
    attempt = {
        "attempt": 1, "model": model.model_name, "model_input": input_messages,
        "model_output": raw.model_dump(mode="json"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "prompt_variant": "defense" if defense else "base",
        "model_parameters": {"max_output_tokens": model.max_tokens},
        "error": type(result["parsing_error"]).__name__ if result["parsing_error"] else None,
    }
    if trace is not None:
        trace.record_attempt(**attempt)
    if attempt_log is not None:
        attempt_log.append(attempt)
    if result["parsing_error"] is not None or result["parsed"] is None:
        raise ValueError("모델 응답을 지정된 답변 형식으로 읽을 수 없습니다.")
    answer = _check_marked_sources(result["parsed"], context)
    if not answer.answer.strip() or not answer.reason.strip():
        raise ValueError("모델의 답변과 판단 이유는 비어 있을 수 없습니다.")
    resolve_citations(answer.source_ids, context.source_map)
    if answer.status == "answered" and not answer.source_ids:
        raise ValueError("답변 완료 응답에 근거 출처가 없습니다.")
    return GenerationResult(
        model_answer=answer,
        attempts=[attempt],
        usage=raw.usage_metadata,
    )
