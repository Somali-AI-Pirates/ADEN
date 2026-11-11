import re
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from openai import LengthFinishReasonError

from shared.contracts import ContextBundle, GenerationResult, ModelAnswer
from app.generation.calendar import calendar_reference, validate_calendar_dates
from app.generation.citations import resolve_citations

DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "shipping_qa.txt"
CITATION_MARK = re.compile(r"\[(S\d+)\]")
TRUNCATION_FEEDBACK = (
    "\n\n재검증 피드백\n이전 응답이 출력 한도에서 잘렸습니다."
    " 질문에 필요한 사실·조건·출처만 간결하게 쓰고, 지정된 출력 형식의 전체 JSON을 끝까지 반환하세요."
)


def _check_marked_sources(answer: ModelAnswer, context: ContextBundle) -> ModelAnswer:
    """답변과 판단 이유의 [S#]를 확인하고 source_ids의 누락을 보충한다."""
    text = answer.answer + "\n" + answer.reason
    for mark in re.findall(r"\[[^\[\]]*\]?", text):
        if re.search(r"\b[Ss]\s*\d", mark) and not CITATION_MARK.fullmatch(mark):
            raise ValueError("출처 표기는 [S1][S2]처럼 자료 ID를 하나씩 표시해야 합니다.")
    marked = list(dict.fromkeys(CITATION_MARK.findall(text)))
    unknown = [source_id for source_id in marked if source_id not in context.source_map]
    if unknown:
        raise ValueError(f"답변 또는 판단 이유에 최종 검색 문맥에 없는 출처가 있습니다. {unknown}")
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
    if answer.status == "answered" and not CITATION_MARK.search(answer.answer):
        raise ValueError("답변 완료 응답의 본문에 근거 출처 표시가 없습니다.")
    return answer


def _retry_feedback(error: ValueError, context: ContextBundle) -> str:
    """검증 오류를 제한된 지시문으로 바꾼다. 모델 출력·오류 원문은 지시문에 복사하지 않는다."""
    message = str(error)
    if message.startswith("날짜 대조:"):
        reason = "무료·유료 날짜 행이 누락됐거나 날짜·요일·배정이 계산표와 다릅니다. 날짜 대조표의 행을 다시 확인하세요."
    elif message.startswith("출처 표기는"):
        reason = "자료 ID 표기가 잘못됐습니다. [S1][S2]처럼 하나씩 표시하세요."
    elif message.startswith("답변 완료"):
        reason = "답변 완료 응답의 본문에 유효한 자료 ID 인용을 표시하세요."
    else:
        reason = "JSON 필드·빈 답변·판단 이유·출처 목록을 확인하고 지정된 출력 형식으로 다시 작성하세요."
    unknown = list(dict.fromkeys(source for source in re.findall(r"\bS\d{1,6}\b", message)
                                if source not in context.source_map))[:8]
    invalid = f" 잘못된 자료 ID: {', '.join(unknown)}." if unknown else ""
    allowed = ", ".join(context.source_map)
    return ("\n\n재검증 피드백\n" + reason + invalid
            + f" 허용 자료 ID: {allowed}. 검색 자료와 질문은 동일합니다."
            " 사실을 추가하거나 출처를 만들지 말고, 검증 오류를 수정한 전체 JSON을 반환하세요.")


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

    출력 형식·출처·지원 날짜 검증에 실패하면 같은 문맥에 검증 피드백을 더해 max_attempts까지 다시 생성하고
    모든 시도를 별도 기록으로 남긴다. 출력 한도에서 잘린 응답(LengthFinishReasonError)도 같은 횟수 안에서
    간결하게 다시 쓰라는 피드백으로 재생성하고, 모두 잘리면 그 오류를 올린다.
    그 밖의 모델 호출 오류는 재시도하지 않고 그대로 올린다.
    """
    calendar_notes = calendar_reference(question, context)
    system_prompt = prompt_path.read_text(encoding="utf-8") + calendar_notes
    if defense:
        system_prompt += "\n\n" + defense
    prompt = ChatPromptTemplate.from_messages([
        ("system", "{instructions}"),
        ("human", "질문\n{question}\n\n검색 자료\n{context}"),
    ])
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
    feedback = ""
    for number in range(1, max_attempts + 1):
        values = {"instructions": system_prompt + feedback, "question": question, "context": context.text}
        messages = prompt.invoke(values).to_messages()
        input_messages = [message.model_dump(mode="json") for message in messages]
        base = {
            "attempt": number, "model": model.model_name, "model_input": input_messages,
            "prompt_variant": variant, "model_parameters": parameters,
            "calendar_reference": calendar_notes,
        }
        stage = trace.stage(f"generation-attempt-{number}", as_type="generation",
                            model=model.model_name, input=input_messages) if trace else nullcontext({})
        with stage:
            started = perf_counter()
            try:
                result = chain.invoke(values)
                latency_ms = round((perf_counter() - started) * 1000)
            except Exception as error:
                attempt = {
                    **base, "latency_ms": round((perf_counter() - started) * 1000),
                    "model_output": None, "input_tokens": None, "output_tokens": None,
                    "error": type(error).__name__,
                }
                if not isinstance(error, LengthFinishReasonError):
                    record(attempt)
                    raise
                usage = getattr(getattr(error, "completion", None), "usage", None)
                attempt["input_tokens"] = getattr(usage, "prompt_tokens", None)
                attempt["output_tokens"] = getattr(usage, "completion_tokens", None)
                record(attempt)
                attempts.append(attempt)
                last_error = error
                feedback = TRUNCATION_FEEDBACK
                continue

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
                    validate_calendar_dates(answer, question, context)
            except ValueError as error:
                attempt["error"] = attempt["error"] or type(error).__name__
                record(attempt)
                attempts.append(attempt)
                last_error = error
                feedback = _retry_feedback(error, context)
                continue
            record(attempt)
            attempts.append(attempt)
            return GenerationResult(model_answer=answer, attempts=attempts, usage=raw.usage_metadata)
    raise last_error
