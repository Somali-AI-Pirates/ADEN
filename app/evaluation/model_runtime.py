from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Any, Generic, TypeVar

import httpx
from openai import APIStatusError, OpenAI
from pydantic import ValidationError

from shared.settings import (
    DEFAULT_CONFIG,
    EvaluationSettings,
    configured_path,
    read_config_mapping,
)

T = TypeVar("T")


@dataclass(frozen=True)
class EvaluationPolicy:
    """Explicit API policy shared by the RAGAS and Judge evaluators."""

    reasoning_effort: str = "max"
    fallback_reasoning_effort: str = "xhigh"
    max_output_tokens: int = 128000
    timeout_seconds: float = 600
    max_retries: int = 2

    def __post_init__(self) -> None:
        if self.reasoning_effort != "max" or self.fallback_reasoning_effort != "xhigh":
            raise ValueError(
                "Evaluation reasoning policy must be max with xhigh fallback"
            )
        if (
            self.max_output_tokens <= 0
            or self.timeout_seconds <= 0
            or self.max_retries < 0
        ):
            raise ValueError("Evaluation output, timeout, and retry limits are invalid")

    @classmethod
    def from_settings(cls, settings: EvaluationSettings | Any) -> EvaluationPolicy:
        """Accept either the evaluation subsection or the aggregate application settings."""
        source = getattr(settings, "evaluation", settings)
        if isinstance(source, Mapping):
            return cls(**source)
        return cls(
            reasoning_effort=source.reasoning_effort,
            fallback_reasoning_effort=source.fallback_reasoning_effort,
            max_output_tokens=source.max_output_tokens,
            timeout_seconds=source.timeout_seconds,
            max_retries=source.max_retries,
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)

    to_manifest = to_mapping


@dataclass
class EvaluationAttempt:
    reasoning_effort: str
    status: str
    latency_ms: int
    status_code: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_param: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationCallMetadata:
    provider: str
    api_surface: str
    model: str
    stage: str
    requested_reasoning_effort: str
    effective_reasoning_effort: str | None
    max_output_tokens: int
    timeout_seconds: float
    sdk_max_retries: int
    fallback_used: bool
    fallback_reason: str | None
    latency_ms: int
    attempts: list[EvaluationAttempt] = field(default_factory=list)
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_param: str | None = None
    status_code: int | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "api_surface": self.api_surface,
            "model": self.model,
            "stage": self.stage,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "effective_reasoning_effort": self.effective_reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "sdk_max_retries": self.sdk_max_retries,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "latency_ms": self.latency_ms,
            "attempts": [attempt.to_mapping() for attempt in self.attempts],
            "logical_calls": 1,
            "logical_attempts": len(self.attempts),
            "provider_attempts": None,
            "response_id": self.response_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "error_param": self.error_param,
            "status_code": self.status_code,
            "response_status": self.response_status,
            "incomplete_reason": self.incomplete_reason,
        }

    as_mapping = to_mapping


@dataclass(frozen=True)
class EvaluationCall(Generic[T]):
    output: T
    metadata: EvaluationCallMetadata

    def to_mapping(self) -> dict[str, Any]:
        output = self.output
        if hasattr(output, "model_dump"):
            output = output.model_dump(mode="json")
        result = {"output": output, **self.metadata.to_mapping()}
        if isinstance(output, str):
            result["text"] = output
        # Internal SDK retries are represented separately by sdk_max_retries.
        result["details"] = self.metadata.to_mapping()
        return result

    as_mapping = to_mapping


class EvaluationCallError(RuntimeError):
    """Safe evaluator failure; raw provider messages are never copied to metadata."""

    def __init__(
        self,
        message: str,
        metadata: EvaluationCallMetadata,
        *,
        abort_evaluation: bool = False,
    ) -> None:
        super().__init__(message)
        self.evaluation_details = metadata.to_mapping()
        self.abort_evaluation = abort_evaluation


class EvaluationConfigurationError(EvaluationCallError):
    """The selected reasoning policy cannot be held consistently for this evaluation."""


class EvaluationOutputError(EvaluationCallError):
    """The provider returned an incomplete or unusable evaluator output."""

    def __init__(
        self,
        message: str,
        metadata: EvaluationCallMetadata,
        *,
        validation_errors: list[dict[str, Any]] | None = None,
        raw_verdicts: list[dict[str, int]] | None = None,
    ) -> None:
        super().__init__(message, metadata)
        self.validation_errors = validation_errors or []
        self.raw_verdicts = raw_verdicts or []
        if self.validation_errors:
            self.evaluation_details["validation_errors"] = self.validation_errors
        if self.raw_verdicts:
            self.evaluation_details["raw_verdicts"] = self.raw_verdicts


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _safe_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(char for char in value[:80] if char.isalnum() or char in "._-")
    return cleaned or None


def _api_error_fields(
    error: BaseException,
) -> tuple[int | None, str | None, str | None]:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, Mapping) else {}
    if not isinstance(detail, Mapping):
        detail = {}
    code = _safe_label(detail.get("code") or getattr(error, "code", None))
    param = _safe_label(detail.get("param") or getattr(error, "param", None))
    return status if isinstance(status, int) else None, code, param


def _is_explicit_effort_denial(error: BaseException) -> bool:
    if not isinstance(error, APIStatusError):
        return False
    status, code, param = _api_error_fields(error)
    if status not in {400, 403} or code in {
        "rate_limit_exceeded",
        "insufficient_quota",
    }:
        return False
    normalized_param = (param or "").lower().replace("_", ".")
    if normalized_param == "reasoning.effort":
        return True

    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, Mapping) else {}
    message = detail.get("message", "") if isinstance(detail, Mapping) else ""
    text = message.lower() if isinstance(message, str) else ""
    effort_reference = "reasoning effort" in text or "reasoning.effort" in text
    denial = any(
        phrase in text
        for phrase in (
            "not allowed",
            "not permitted",
            "not authorized",
            "permission",
            "unsupported",
            "not supported",
            "not available",
            "access denied",
            "does not support",
            "only supports",
        )
    )
    return (
        effort_reference
        and denial
        and code not in {"rate_limit_exceeded", "insufficient_quota"}
    )


def _is_structured_output_error(error: BaseException) -> bool:
    return isinstance(error, ValidationError) or type(error).__name__ in {
        "LengthFinishReasonError",
        "ContentFilterFinishReasonError",
        "InvalidResponseFormatError",
    }


def _validation_diagnostics(
    error: BaseException,
) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    if not isinstance(error, ValidationError):
        return [], []
    safe_errors = []
    raw_verdicts = []
    for item in error.errors(include_input=True):
        location = [
            _safe_label(part) if isinstance(part, str) else part
            for part in item.get("loc", ())
        ]
        safe_errors.append(
            {"location": location, "error_type": _safe_label(item.get("type"))}
        )
        raw_value = item.get("input")
        if "verdict" in location and type(raw_value) is int:
            raw_verdicts.append({"verdict": raw_value})
    return safe_errors, raw_verdicts


_RESPONSE_STATUSES = {"completed", "failed", "incomplete", "in_progress", "queued", "cancelled"}
_INCOMPLETE_REASONS = {"max_output_tokens", "content_filter"}


def _safe_enum(value: Any, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _token_value(usage: Any, name: str) -> int | None:
    value = _value(usage, name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_fields(response: Any, api_surface: str) -> dict[str, int | None]:
    usage = _value(response, "usage")
    if usage is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "cached_input_tokens": None,
        }
    if api_surface.startswith("responses"):
        input_tokens = _token_value(usage, "input_tokens")
        output_tokens = _token_value(usage, "output_tokens")
        input_details = _value(usage, "input_tokens_details")
        output_details = _value(usage, "output_tokens_details")
        reasoning = _token_value(output_details, "reasoning_tokens")
        cached = _token_value(input_details, "cached_tokens")
    else:
        input_tokens = _token_value(usage, "prompt_tokens")
        output_tokens = _token_value(usage, "completion_tokens")
        input_details = _value(usage, "prompt_tokens_details")
        output_details = _value(usage, "completion_tokens_details")
        reasoning = _token_value(output_details, "reasoning_tokens")
        cached = _token_value(input_details, "cached_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "cached_input_tokens": cached,
    }


def _responses_output_text(response: Any) -> str:
    if _value(response, "status") != "completed":
        raise ValueError("incomplete response")
    texts = []
    for item in _value(response, "output", []) or []:
        if _value(item, "type") != "message":
            continue
        if _value(item, "status") not in {None, "completed"}:
            raise ValueError("incomplete output message")
        if _value(item, "refusal"):
            raise ValueError("refusal output")
        for content in _value(item, "content", []) or []:
            content_type = _value(content, "type")
            if content_type == "refusal" or _value(content, "refusal"):
                raise ValueError("refusal output")
            if content_type == "output_text":
                text = _value(content, "text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
    if not texts:
        output_text = _value(response, "output_text")
        if isinstance(output_text, str) and output_text.strip():
            texts.append(output_text)
    if not texts:
        raise ValueError("missing output text")
    return "\n".join(texts)


def load_evaluation_policy(config_path: str | Path | None = None) -> EvaluationPolicy:
    # Resolve explicit paths relative to the repository root; otherwise honor ADEN_CONFIG.
    if config_path is None:
        path = configured_path()
    else:
        path = Path(config_path)
        if not path.is_absolute():
            path = DEFAULT_CONFIG.parents[1] / path
    raw = read_config_mapping(path)
    evaluator_settings = EvaluationSettings.model_validate(raw.get("evaluation", {}))
    return EvaluationPolicy.from_settings(evaluator_settings)


class EvaluationSession:
    """One lazily-created OpenAI client and one pinned reasoning policy per grading run."""

    def __init__(
        self,
        policy: EvaluationPolicy | EvaluationSettings | Any,
        *,
        client: OpenAI | None = None,
    ):
        self.policy = (
            policy
            if isinstance(policy, EvaluationPolicy)
            else EvaluationPolicy.from_settings(policy)
        )
        self._client = client
        self._owns_client = client is None
        self._lock = threading.RLock()
        self._pinned_effort: str | None = None
        self._fallback_used = False
        self._fallback_reason: str | None = None
        self._failed = False
        self._closed = False

    def __enter__(self) -> EvaluationSession:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        with self._lock:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_client and self._client is not None:
                self._client.close()

    @property
    def effective_reasoning_effort(self) -> str | None:
        return self._pinned_effort

    def to_manifest(self) -> dict[str, Any]:
        """Current policy decision for the evaluation manifest; contains no credentials."""
        return {
            **self.policy.to_mapping(),
            "effective_reasoning_effort": self._pinned_effort,
            "fallback_used": self._fallback_used,
            "fallback_reason": self._fallback_reason,
        }

    manifest = to_manifest

    def restore_effective_effort(self, effort: str) -> None:
        # Restore the manifest's selected effort before resumed grading begins.
        with self._lock:
            if self._pinned_effort is not None:
                raise RuntimeError("Evaluation effort is already pinned")
            if effort not in {
                self.policy.reasoning_effort,
                self.policy.fallback_reasoning_effort,
            }:
                raise ValueError(
                    "Manifest effort is outside the configured evaluator policy"
                )
            self._pinned_effort = effort
            if effort == self.policy.fallback_reasoning_effort:
                self._fallback_used = True
                self._fallback_reason = "restored_from_manifest"

    def _openai(self) -> OpenAI:
        if self._closed:
            raise RuntimeError("EvaluationSession is closed")
        if self._client is None:
            timeout = httpx.Timeout(
                self.policy.timeout_seconds,
                connect=min(self.policy.timeout_seconds, 10.0),
            )
            self._client = OpenAI(timeout=timeout, max_retries=self.policy.max_retries)
        return self._client

    def generate_json(
        self,
        prompt: str,
        *,
        model: str,
        stage: str,
    ) -> EvaluationCall[str]:
        # Responses JSON mode is supported with Luna's max reasoning effort.
        def request(effort: str) -> Any:
            return self._openai().responses.create(
                model=model,
                input=prompt,
                reasoning={"effort": effort},
                max_output_tokens=self.policy.max_output_tokens,
                text={"format": {"type": "json_object"}},
                timeout=self.policy.timeout_seconds,
            )

        return self._execute(
            "responses.json", model, stage, request, _responses_output_text
        )

    async def parse_json(
        self,
        prompt: str,
        response_model: type[T],
        *,
        model: str,
        stage: str,
    ) -> EvaluationCall[T]:
        """Parse RAGAS structured output with Responses API in a worker thread."""
        return await asyncio.to_thread(
            self._parse_json_sync, prompt, response_model, model=model, stage=stage
        )

    def _parse_json_sync(
        self,
        prompt: str,
        response_model: type[T],
        *,
        model: str,
        stage: str,
    ) -> EvaluationCall[T]:
        def request(effort: str) -> Any:
            return self._openai().responses.parse(
                model=model,
                input=prompt,
                reasoning={"effort": effort},
                max_output_tokens=self.policy.max_output_tokens,
                text_format=response_model,
                timeout=self.policy.timeout_seconds,
            )

        def output(response: Any) -> T:
            status = _value(response, "status")
            if status != "completed":
                raise ValueError("response was not completed")
            parsed = _value(response, "output_parsed")
            if parsed is None:
                raise ValueError("missing parsed output")
            return parsed

        call = self._execute("responses.parse", model, stage, request, output)
        return call  # type: ignore[return-value]

    def _execute(
        self,
        api_surface: str,
        model: str,
        stage: str,
        request: Any,
        parse_output: Any,
    ) -> EvaluationCall[Any]:
        started = time.perf_counter()
        attempts: list[EvaluationAttempt] = []
        with self._lock:
            if self._failed:
                metadata = self._metadata(
                    api_surface,
                    model,
                    stage,
                    attempts,
                    started,
                    status="configuration_error",
                )
                raise EvaluationConfigurationError(
                    "Evaluator session is unusable after a reasoning configuration failure.",
                    metadata,
                    abort_evaluation=True,
                )
            initial_effort = self._pinned_effort or self.policy.reasoning_effort
            effort = initial_effort
            while True:
                attempt_started = time.perf_counter()
                try:
                    response = request(effort)
                except Exception as error:
                    if api_surface == "responses.parse" and _is_structured_output_error(
                        error
                    ):
                        if self._pinned_effort is None:
                            self._pinned_effort = effort
                        attempts.append(
                            self._attempt(
                                effort, error, attempt_started, "output_error"
                            )
                        )
                        metadata = self._metadata(
                            api_surface,
                            model,
                            stage,
                            attempts,
                            started,
                            status="output_error",
                            error=error,
                        )
                        validation_errors, raw_verdicts = _validation_diagnostics(error)
                        raise EvaluationOutputError(
                            "Evaluator returned incomplete or invalid structured output.",
                            metadata,
                            validation_errors=validation_errors,
                            raw_verdicts=raw_verdicts,
                        ) from None
                    attempts.append(
                        self._attempt(effort, error, attempt_started, "request_error")
                    )
                    if _is_explicit_effort_denial(error):
                        if self._pinned_effort is not None or self._fallback_used:
                            self._failed = True
                            metadata = self._metadata(
                                api_surface,
                                model,
                                stage,
                                attempts,
                                started,
                                status="configuration_error",
                                error=error,
                            )
                            raise EvaluationConfigurationError(
                                "The pinned evaluator reasoning effort was denied; abort this evaluation.",
                                metadata,
                                abort_evaluation=True,
                            ) from None
                        self._fallback_used = True
                        self._fallback_reason = "requested_reasoning_effort_denied"
                        self._pinned_effort = self.policy.fallback_reasoning_effort
                        effort = self.policy.fallback_reasoning_effort
                        continue
                    if self._pinned_effort is not None:
                        # A non-effort rejection does not change the pinned policy.
                        pass
                    elif not isinstance(error, APIStatusError):
                        # A transport or SDK parse failure may follow an accepted request;
                        # pin conservatively so later scores cannot silently mix policies.
                        self._pinned_effort = effort
                    metadata = self._metadata(
                        api_surface,
                        model,
                        stage,
                        attempts,
                        started,
                        status="request_error",
                        error=error,
                    )
                    raise EvaluationCallError(
                        "Evaluator API request failed.", metadata
                    ) from None

                if self._pinned_effort is None:
                    self._pinned_effort = effort
                try:
                    parsed = parse_output(response)
                except Exception as error:
                    attempts.append(
                        self._attempt(effort, error, attempt_started, "output_error")
                    )
                    metadata = self._metadata(
                        api_surface,
                        model,
                        stage,
                        attempts,
                        started,
                        status="output_error",
                        error=error,
                        response=response,
                    )
                    raise EvaluationOutputError(
                        "Evaluator returned incomplete or invalid structured output.",
                        metadata,
                    ) from None

                attempts.append(
                    EvaluationAttempt(
                        reasoning_effort=effort,
                        status="completed",
                        latency_ms=round(
                            (time.perf_counter() - attempt_started) * 1000
                        ),
                    )
                )
                metadata = self._metadata(
                    api_surface,
                    model,
                    stage,
                    attempts,
                    started,
                    status="completed",
                    response=response,
                )
                return EvaluationCall(parsed, metadata)

    def _attempt(
        self, effort: str, error: BaseException, started: float, status: str
    ) -> EvaluationAttempt:
        status_code, error_code, error_param = _api_error_fields(error)
        return EvaluationAttempt(
            reasoning_effort=effort,
            status=status,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status_code=status_code,
            error_type=type(error).__name__,
            error_code=error_code,
            error_param=error_param,
        )

    def _metadata(
        self,
        api_surface: str,
        model: str,
        stage: str,
        attempts: list[EvaluationAttempt],
        started: float,
        *,
        status: str,
        error: BaseException | None = None,
        response: Any = None,
    ) -> EvaluationCallMetadata:
        response_usage = _usage_fields(
            response, "responses" if api_surface.startswith("responses") else "chat"
        )
        status_code, error_code, error_param = (
            _api_error_fields(error) if error else (None, None, None)
        )
        return EvaluationCallMetadata(
            provider="openai",
            api_surface=api_surface,
            model=model,
            stage=stage,
            requested_reasoning_effort=self.policy.reasoning_effort,
            effective_reasoning_effort=self._pinned_effort,
            max_output_tokens=self.policy.max_output_tokens,
            timeout_seconds=self.policy.timeout_seconds,
            sdk_max_retries=self.policy.max_retries,
            fallback_used=self._fallback_used,
            fallback_reason=self._fallback_reason,
            latency_ms=round((time.perf_counter() - started) * 1000),
            attempts=list(attempts),
            response_id=_value(response, "id"),
            input_tokens=response_usage["input_tokens"],
            output_tokens=response_usage["output_tokens"],
            reasoning_tokens=response_usage["reasoning_tokens"],
            cached_input_tokens=response_usage["cached_input_tokens"],
            error_type=type(error).__name__
            if error
            else (status if status != "completed" else None),
            error_code=error_code,
            error_param=error_param,
            status_code=status_code,
            response_status=_safe_enum(
                _value(response, "status"), _RESPONSE_STATUSES
            ),
            incomplete_reason=_safe_enum(
                _value(_value(response, "incomplete_details"), "reason"),
                _INCOMPLETE_REASONS,
            ),
        )
