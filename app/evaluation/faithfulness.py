"""RAGAS Faithfulness with a source-verifiable advanced profile.

The implementation keeps Ragas 0.4.3's two-stage claim extraction and NLI
scoring. The advanced profile changes the prompts and the second-stage output
schema, then verifies every cited quote against the supplied context blocks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
FaithfulnessProfile = Literal["default", "advanced"]
T = TypeVar("T", bound=BaseModel)


class EvaluationSessionProtocol(Protocol):
    """The shared Responses API functionality needed by the Ragas adapter."""

    async def parse_json(
        self,
        prompt: str,
        response_model: type[T],
        *,
        model: str,
        stage: str,
    ) -> Any: ...


class SupportingQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="ID on the first line of a supplied context block")
    quote: str = Field(description="Verbatim contiguous quote from that source body")


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(description="Exact claim statement received for evaluation")
    verdict: Literal[0, 1] = Field(description="1 only when supplied context supports the claim")
    reason: str = Field(description="Brief evidence-based reason; no hidden reasoning")
    supporting_evidence: list[SupportingQuote] = Field(
        description="Supporting quote(s) with source IDs from the supplied context"
    )

    @field_validator("verdict", mode="before")
    @classmethod
    def reject_non_integer_verdict(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("verdict must be an integer 0 or 1")
        return value


class DetailedVerdicts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[ClaimVerdict]


class ExcludedNonfactual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Exact contiguous phrase copied from the evaluated response")
    reason: Literal[
        "greeting",
        "gratitude",
        "courtesy",
        "transition",
        "offer_to_help",
        "request_for_more_information",
        "other_nonfactual_filler",
    ] = Field(description="Short category for excluding non-factual conversational text")


class AdvancedStatementGeneratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[str] = Field(description="Factual claims from the answer, in answer order")
    excluded_nonfactual: list[ExcludedNonfactual] = Field(
        description="Non-factual conversational spans excluded from scoring"
    )


class FaithfulnessOutputError(ValueError):
    """Sanitized evaluator-output failure; never contains prompt or answer text."""

    def __init__(self, code: str, failures: list[dict[str, Any]], raw_verdicts: list[dict[str, Any]]):
        super().__init__(code)
        self.code = code
        self.failures = failures
        self.raw_verdicts = raw_verdicts


_SOURCE_HEADER = re.compile(r"^\[([^\]\n]+)\]$")
_GENERIC_BOILERPLATE = {
    "안녕하세요",
    "감사합니다",
    "도움이 필요하면 알려 주세요",
    "도움이 필요하면 말씀해 주세요",
    "추가 확인이 필요합니다",
    "확인해 보겠습니다",
    "확인하겠습니다",
    "필요한 정보가 있으면 알려 주세요",
    "please let me know if you need more information",
    "let me know if you need anything else",
    "i can help with that",
}


def _normalize_quote_text(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def _is_boilerplate(statement: str) -> bool:
    normalized = _normalize_quote_text(statement).strip(" .!?,。！？").casefold()
    return normalized in _GENERIC_BOILERPLATE


def _classify_claims(statements: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Exclude only empty text and generic conversational filler, with an audit trail."""
    claims: list[str] = []
    excluded: list[dict[str, str]] = []
    for statement in statements:
        normalized = statement.strip()
        if not normalized:
            excluded.append({"text": statement, "reason": "empty_text"})
        elif _is_boilerplate(normalized):
            excluded.append({"text": normalized, "reason": "generic_conversational_filler"})
        else:
            claims.append(normalized)
    return claims, excluded


def _validate_excluded_nonfactual(
    response: str,
    statements: list[str],
    excluded: list[ExcludedNonfactual],
) -> list[dict[str, str]]:
    """Require model-reported exclusions to be verbatim spans of the visible answer."""
    claim_texts = {_normalize_quote_text(statement) for statement in statements}
    validated: list[dict[str, str]] = []
    seen: set[str] = set()
    failures: list[dict[str, Any]] = []
    for index, item in enumerate(excluded):
        normalized = _normalize_quote_text(item.text)
        if not normalized or not _quote_is_present(item.text, response):
            failures.append({"index": index, "code": "excluded_text_not_present_in_response"})
            continue
        if normalized in claim_texts:
            failures.append({"index": index, "code": "text_marked_as_claim_and_exclusion"})
            continue
        if normalized not in seen:
            validated.append({"text": item.text.strip(), "reason": item.reason})
            seen.add(normalized)
    if failures:
        raise FaithfulnessOutputError("invalid_excluded_nonfactual", failures, [])
    return validated


def _validate_program_exclusions(
    response: str,
    exclusions: list[dict[str, str]],
) -> None:
    failures = [
        {"index": index, "code": "excluded_text_not_present_in_response"}
        for index, item in enumerate(exclusions)
        if not _quote_is_present(item["text"], response)
    ]
    if failures:
        raise FaithfulnessOutputError("invalid_excluded_nonfactual", failures, [])


def _merge_excluded_claims(
    model_exclusions: list[dict[str, str]],
    program_exclusions: list[dict[str, str]],
) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*model_exclusions, *program_exclusions]:
        key = _normalize_quote_text(item["text"])
        if key not in seen:
            combined.append(item)
            seen.add(key)
    return combined


def _source_bodies(contexts: list[str]) -> dict[str, str]:
    """Read an ID only from a block's first line; body text cannot declare IDs."""
    sources: dict[str, str] = {}
    for context in contexts:
        first_line, separator, body = context.partition("\n")
        match = _SOURCE_HEADER.fullmatch(first_line)
        if not match or not separator:
            continue
        source_id = match.group(1)
        if source_id in sources:
            raise FaithfulnessOutputError("duplicate_source_id", [{"source_id": source_id}], [])
        sources[source_id] = body
    return sources


def _canonicalize_source_id(
    source_id: str, sources: Mapping[str, str]
) -> tuple[str, dict[str, str] | None]:
    """Unwrap exactly one displayed ID pair only when its inner ID is allowlisted."""
    if source_id in sources:
        return source_id, None
    if not (source_id.startswith("[") and source_id.endswith("]")):
        return source_id, None
    inner = source_id[1:-1]
    if not inner or "[" in inner or "]" in inner or inner not in sources:
        return source_id, None
    return inner, {
        "code": "source_id_bracket_wrapper_removed",
        "original_source_id": source_id,
        "canonical_source_id": inner,
    }


def _quote_is_present(quote: str, body: str) -> bool:
    normalized_quote = _normalize_quote_text(quote)
    return bool(normalized_quote) and normalized_quote in _normalize_quote_text(body)


def _validate_advanced_verdicts(
    expected_statements: list[str],
    verdicts: DetailedVerdicts,
    sources: Mapping[str, str],
    normalization_events: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    events = normalization_events if normalization_events is not None else []
    raw = [
        {"claim_index": index, "verdict": item.verdict}
        for index, item in enumerate(verdicts.statements)
    ]
    failures: list[dict[str, Any]] = []
    if len(verdicts.statements) != len(expected_statements):
        failures.append({"code": "verdict_count_mismatch"})
    for index, (expected, item) in enumerate(zip(expected_statements, verdicts.statements)):
        if item.statement != expected:
            failures.append({"claim_index": index, "code": "claim_order_or_text_mismatch"})
        if type(item.verdict) is not int or item.verdict not in (0, 1):
            failures.append({"claim_index": index, "code": "invalid_verdict_type_or_value"})
        for evidence_index, quote in enumerate(item.supporting_evidence):
            source_id, normalization = _canonicalize_source_id(quote.source_id, sources)
            if normalization:
                events.append({
                    "claim_index": index,
                    "evidence_index": evidence_index,
                    **normalization,
                })
            body = sources.get(source_id)
            if body is None:
                failures.append({
                    "claim_index": index,
                    "code": "unknown_source_id",
                    "source_id": quote.source_id,
                })
            elif not _quote_is_present(quote.quote, body):
                failures.append({
                    "claim_index": index,
                    "code": "quote_not_present_in_source",
                    "source_id": source_id,
                })
        if item.verdict == 1 and not item.supporting_evidence:
            failures.append({"claim_index": index, "code": "supporting_quote_required"})
    if failures:
        raise FaithfulnessOutputError("invalid_faithfulness_output", failures, raw)

    details = [
        {
            "statement": item.statement,
            "verdict": item.verdict,
            "reason": item.reason.strip(),
            "supporting_evidence": [quote.model_dump() for quote in item.supporting_evidence],
            "evidence_validation": "verified",
        }
        for item in verdicts.statements
    ]
    return details, [item.verdict for item in verdicts.statements]


def _advanced_prompt(filename: str, payload: Mapping[str, Any]) -> str:
    instruction = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    return instruction + "\n\n입력 데이터(JSON):\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _metadata_mapping(call: Any) -> Mapping[str, Any]:
    if isinstance(call, Mapping):
        nested = call.get("metadata")
        return nested if isinstance(nested, Mapping) else call
    if callable(getattr(call, "to_mapping", None)):
        mapped = call.to_mapping()
        if isinstance(mapped, Mapping) and isinstance(mapped.get("metadata"), Mapping):
            return mapped["metadata"]
    metadata = getattr(call, "metadata", None)
    if callable(getattr(metadata, "to_mapping", None)):
        return metadata.to_mapping()
    if callable(getattr(metadata, "model_dump", None)):
        return metadata.model_dump(mode="json")
    return metadata if isinstance(metadata, Mapping) else {}


def _usage(calls: list[Any]) -> dict[str, Any]:
    entries = [_metadata_mapping(call) for call in calls]
    totals: dict[str, Any] = {}
    for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
        values = [entry.get(field) for entry in entries]
        totals[field] = sum(value for value in values if isinstance(value, int)) if any(
            isinstance(value, int) for value in values
        ) else None
    retained_fields = (
        "provider", "api_surface", "model", "stage", "requested_reasoning_effort",
        "effective_reasoning_effort", "sdk_max_retries", "fallback_used", "fallback_reason",
        "max_output_tokens", "timeout_seconds", "latency_ms", "attempts", "response_id",
        "input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens",
        "error_type", "error_code", "error_param", "status_code", "response_status",
        "incomplete_reason",
    )
    totals["stages"] = [
        {key: entry[key] for key in retained_fields if key in entry}
        for entry in entries
    ]
    totals["logical_calls"] = len(entries)
    return totals


def _default_session(policy: Any | None = None) -> Any:
    from app.evaluation.model_runtime import EvaluationSession, load_evaluation_policy

    return EvaluationSession(policy if policy is not None else load_evaluation_policy())


def make_faithfulness(
    model: str,
    *,
    session: EvaluationSessionProtocol | None = None,
    profile: FaithfulnessProfile = "default",
    policy: Any | None = None,
) -> Any:
    """Return `(question, response, contexts) -> value/details/usage` grader.

    `default` retains Ragas 0.4.3's stock prompt and schema. `advanced` adds
    Korean 업무기록 guidance and verified quote attribution per claim. Neither
    profile receives reference answers; neither exposes model reasoning.
    """
    if profile not in ("default", "advanced"):
        raise ValueError(f"Unsupported Faithfulness profile: {profile}")
    try:
        from ragas.llms.base import InstructorBaseRagasLLM
        from ragas.metrics.collections import Faithfulness
        from ragas.metrics.collections.faithfulness.util import (
            StatementGeneratorInput,
            StatementGeneratorOutput,
        )
        from ragas.metrics.result import MetricResult
        from ragas.prompt.metrics.base_prompt import BasePrompt
    except ImportError as exc:
        raise RuntimeError("Install the project eval extra to use RAGAS Faithfulness") from exc

    class ResponsesAdapter(InstructorBaseRagasLLM):
        def __init__(self, active_session: EvaluationSessionProtocol):
            self.session = active_session
            self.calls: list[Any] = []
            self.inputs: list[dict[str, Any]] = []
            self.failure: dict[str, Any] | None = None

        def generate(self, prompt: str, response_model: type[T]) -> T:
            raise RuntimeError("The Faithfulness grader uses the asynchronous Ragas path")

        async def agenerate(self, prompt: str, response_model: type[T]) -> T:
            if response_model in {StatementGeneratorOutput, AdvancedStatementGeneratorOutput}:
                stage = "faithfulness_claim_extraction"
            elif response_model.__name__ in {"NLIStatementOutput", "DetailedVerdicts"}:
                stage = "faithfulness_claim_verification"
            else:
                raise TypeError(f"Unexpected Faithfulness output model: {response_model.__name__}")
            input_record = {
                "stage": stage,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt": prompt,
                "response_model": response_model.__name__,
            }
            self.inputs.append(input_record)
            try:
                call = await self.session.parse_json(
                    prompt, response_model, model=model, stage=stage
                )
            except Exception as exc:
                failure_metadata = getattr(exc, "evaluation_details", None)
                if getattr(exc, "abort_evaluation", False):
                    if isinstance(failure_metadata, Mapping):
                        self.calls.append({"metadata": dict(failure_metadata)})
                    raise
                failure_metadata = (
                    failure_metadata if isinstance(failure_metadata, Mapping) else {}
                )
                failure_status = failure_metadata.get("status")
                is_output_error = (
                    failure_status == "output_error"
                    or type(exc).__name__ in {"EvaluationOutputError", "ValidationError"}
                )
                code = "structured_output_invalid" if is_output_error else "evaluation_call_failed"
                safe_failures: list[dict[str, Any]] = [{
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "status": failure_status,
                }]
                validation_errors = getattr(exc, "validation_errors", None)
                if isinstance(validation_errors, list):
                    safe_failures.extend(
                        {"stage": stage, **error}
                        for error in validation_errors
                        if isinstance(error, Mapping)
                    )
                raw_verdicts = getattr(exc, "raw_verdicts", None)
                if not isinstance(raw_verdicts, list):
                    raw_verdicts = failure_metadata.get("raw_verdicts")
                errors = getattr(exc, "errors", None)
                if callable(errors):
                    for error in errors():
                        location = error.get("loc", ())
                        if "verdict" not in location:
                            continue
                        safe_failures.append({
                            "stage": stage,
                            "location": [str(part) for part in location],
                            "error_type": str(error.get("type") or "validation_error"),
                        })
                        raw_value = error.get("input")
                        if type(raw_value) is int:
                            if not isinstance(raw_verdicts, list):
                                raw_verdicts = []
                            raw_verdicts.append({"verdict": raw_value})
                if not isinstance(raw_verdicts, list):
                    raw_verdicts = []
                self.failure = {
                    "code": code,
                    "error_type": type(exc).__name__,
                    "failures": safe_failures,
                    "raw_verdicts": [
                        item for item in raw_verdicts
                        if isinstance(item, Mapping)
                        and all(type(value) is int for key, value in item.items() if key == "verdict")
                        and "verdict" in item
                    ],
                }
                if failure_metadata:
                    self.calls.append({"metadata": dict(failure_metadata)})
                raise FaithfulnessOutputError(
                    code, safe_failures, self.failure["raw_verdicts"]
                ) from None
            self.calls.append(call)
            output = getattr(call, "output", None)
            if output is None:
                self.failure = {"code": "empty_structured_output", "failures": [{"stage": stage}]}
                raise FaithfulnessOutputError("empty_structured_output", [{"stage": stage}], [])
            # Private structured-output audit only; no raw Response or reasoning payload.
            input_record["parsed_output"] = output.model_dump(mode="json")
            return output

    if profile == "advanced":
        class AdvancedStatementPrompt(BasePrompt[StatementGeneratorInput, AdvancedStatementGeneratorOutput]):
            input_model = StatementGeneratorInput
            output_model = AdvancedStatementGeneratorOutput
            instruction = ""
            examples: list[Any] = []

            def to_string(self, data: StatementGeneratorInput) -> str:
                return _advanced_prompt(
                    "faithfulness_advanced_statements.txt",
                    {"question": data.question, "response": data.answer},
                )

        class DetailedVerdictInput(BaseModel):
            context: str
            statements: list[str]

        class DetailedVerdictPrompt(BasePrompt[DetailedVerdictInput, DetailedVerdicts]):
            input_model = DetailedVerdictInput
            output_model = DetailedVerdicts
            instruction = ""
            examples: list[Any] = []

            def to_string(self, data: DetailedVerdictInput) -> str:
                return _advanced_prompt(
                    "faithfulness_advanced_verdicts.txt",
                    {"context": data.context, "statements": data.statements},
                )

        class AdvancedFaithfulness(Faithfulness):
            def __init__(self, llm: InstructorBaseRagasLLM):
                super().__init__(llm=llm)
                self.statement_generator_prompt = AdvancedStatementPrompt()
                self.nli_statement_prompt = DetailedVerdictPrompt()
                self.details: dict[str, Any] = {}
                self.model_exclusions: list[dict[str, str]] = []

            async def _create_statements(self, question: str, response: str) -> list[str]:
                input_data = StatementGeneratorInput(question=question, answer=response)
                prompt = self.statement_generator_prompt.to_string(input_data)
                extracted = await self.llm.agenerate(prompt, AdvancedStatementGeneratorOutput)
                self.model_exclusions = _validate_excluded_nonfactual(
                    response, extracted.statements, extracted.excluded_nonfactual
                )
                return extracted.statements

            async def ascore(self, user_input: str, response: str, retrieved_contexts: list[str]) -> MetricResult:
                if not response:
                    raise ValueError("response is missing")
                if not user_input:
                    raise ValueError("user_input is missing")
                excluded_claims: list[dict[str, str]] = []
                source_id_normalizations: list[dict[str, Any]] = []
                try:
                    statements, program_exclusions = _classify_claims(
                        await self._create_statements(user_input, response)
                    )
                    excluded_claims = _merge_excluded_claims(
                        self.model_exclusions, program_exclusions
                    )
                    _validate_program_exclusions(response, program_exclusions)
                    if not statements:
                        self.details = {
                            "profile": profile,
                            "status": "not_applicable",
                            "claims": [],
                            "excluded_claims": excluded_claims,
                            "source_attribution": "verified_quote_per_claim",
                        }
                        return MetricResult(value=None, reason="유효한 사실 주장 없음")
                    verdicts = await self._create_verdicts(statements, "\n".join(retrieved_contexts))
                    claims, values = _validate_advanced_verdicts(
                        statements,
                        verdicts,
                        _source_bodies(retrieved_contexts),
                        normalization_events=source_id_normalizations,
                    )
                except FaithfulnessOutputError as exc:
                    self.details = {
                        "profile": profile,
                        "status": "unavailable",
                        "reason": exc.code,
                        "failed_validation": exc.failures,
                        "raw_verdicts": exc.raw_verdicts,
                        "excluded_claims": excluded_claims,
                        "source_id_normalizations": source_id_normalizations,
                        "error_type": (getattr(self.llm, "failure", None) or {}).get("error_type"),
                        "source_attribution": "verified_quote_per_claim",
                    }
                    return MetricResult(value=None, reason=exc.code)
                self.details = {
                    "profile": profile,
                    "status": "scored",
                    "claim_count": len(claims),
                    "claims": claims,
                    "excluded_claims": excluded_claims,
                    "source_id_normalizations": source_id_normalizations,
                    "source_attribution": "verified_quote_per_claim",
                }
                return MetricResult(value=sum(values) / len(values))

            async def _create_verdicts(self, statements: list[str], context: str) -> DetailedVerdicts:
                input_data = self.nli_statement_prompt.input_model(context=context, statements=statements)
                return await self.llm.agenerate(
                    self.nli_statement_prompt.to_string(input_data), DetailedVerdicts
                )

    else:
        class StockFaithfulness(Faithfulness):
            def __init__(self, llm: InstructorBaseRagasLLM):
                super().__init__(llm=llm)
                self.details: dict[str, Any] = {}

            async def ascore(self, user_input: str, response: str, retrieved_contexts: list[str]) -> MetricResult:
                if not response:
                    raise ValueError("response is missing")
                if not user_input:
                    raise ValueError("user_input is missing")
                excluded_claims: list[dict[str, str]] = []
                try:
                    statements, excluded_claims = _classify_claims(
                        await self._create_statements(user_input, response)
                    )
                    _validate_program_exclusions(response, excluded_claims)
                    if not statements:
                        self.details = {
                            "profile": profile,
                            "status": "not_applicable",
                            "claims": [],
                            "excluded_claims": excluded_claims,
                            "source_attribution": "not_collected_by_stock_schema",
                        }
                        return MetricResult(value=None, reason="유효한 사실 주장 없음")
                    verdicts = await self._create_verdicts(statements, "\n".join(retrieved_contexts))
                    if len(verdicts.statements) != len(statements):
                        raise FaithfulnessOutputError("verdict_count_mismatch", [{"stage": "verification"}], [])
                    details, scores = [], []
                    for index, (expected, item) in enumerate(zip(statements, verdicts.statements, strict=True)):
                        if item.statement != expected or type(item.verdict) is not int or item.verdict not in (0, 1):
                            raise FaithfulnessOutputError(
                                "invalid_faithfulness_output",
                                [{"claim_index": index, "code": "claim_or_verdict_mismatch"}],
                                [{"claim_index": index, "verdict": item.verdict}],
                            )
                        scores.append(item.verdict)
                        details.append({
                            "statement": expected,
                            "verdict": item.verdict,
                            "reason": item.reason.strip(),
                            "supporting_evidence": None,
                            "evidence_validation": "not_collected_by_stock_schema",
                        })
                except FaithfulnessOutputError as exc:
                    self.details = {
                        "profile": profile,
                        "status": "unavailable",
                        "reason": exc.code,
                        "failed_validation": exc.failures,
                        "raw_verdicts": exc.raw_verdicts,
                        "excluded_claims": excluded_claims,
                        "error_type": (getattr(self.llm, "failure", None) or {}).get("error_type"),
                        "source_attribution": "not_collected_by_stock_schema",
                    }
                    return MetricResult(value=None, reason=exc.code)
                self.details = {
                    "profile": profile,
                    "status": "scored",
                    "claim_count": len(details),
                    "claims": details,
                    "excluded_claims": excluded_claims,
                    "source_attribution": "not_collected_by_stock_schema",
                }
                return MetricResult(value=sum(scores) / len(scores))

    def run_with(
        active_session: EvaluationSessionProtocol,
        question: str,
        response: str,
        contexts: list[str],
    ) -> dict[str, Any]:
        adapter = ResponsesAdapter(active_session)
        metric = AdvancedFaithfulness(adapter) if profile == "advanced" else StockFaithfulness(adapter)

        async def evaluate() -> tuple[Any, dict[str, Any]]:
            result = await metric.ascore(
                user_input=question,
                response=response,
                retrieved_contexts=contexts,
            )
            return result, metric.details

        result, details = asyncio.run(evaluate())
        usage = _usage(adapter.calls)
        usage["logical_calls"] = len(adapter.inputs)
        details["model_calls"] = adapter.inputs
        if adapter.failure:
            details = {
                "profile": profile,
                "status": "unavailable",
                "reason": adapter.failure["code"],
                "failed_validation": adapter.failure["failures"],
                "raw_verdicts": adapter.failure.get("raw_verdicts", []),
                "error_type": adapter.failure.get("error_type"),
                "excluded_claims": details.get("excluded_claims", []),
                "source_attribution": (
                    "verified_quote_per_claim" if profile == "advanced"
                    else "not_collected_by_stock_schema"
                ),
            }
        else:
            details = dict(details)
        details["model_calls"] = adapter.inputs
        details["usage"] = usage
        return {
            "value": result.value,
            "status": details["status"],
            "details": details,
            "usage": usage,
            "logical_calls": usage["logical_calls"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
            "error": details.get("error_type"),
        }

    def faithfulness(question: str, response: str, contexts: list[str]) -> dict[str, Any]:
        if session is not None:
            return run_with(session, question, response, contexts)
        with _default_session(policy) as active_session:
            return run_with(active_session, question, response, contexts)

    faithfulness.model = model  # type: ignore[attr-defined]
    faithfulness.profile = profile  # type: ignore[attr-defined]
    faithfulness.session = session  # type: ignore[attr-defined]
    return faithfulness
