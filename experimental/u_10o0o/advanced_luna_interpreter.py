"""GPT-6 Luna provider for the personal Advanced question-routing experiment.

Only the user's question and the fixed interpretation instructions are sent to
the provider. Retrieval candidates, PDF contents, and reference answers remain
local to the benchmark.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import APIStatusError, APITimeoutError, OpenAI

from experimental.u_10o0o.advanced_question_router import QuestionInterpretation

MODEL_ID = "gpt-6-luna"
API_BASE_URL = "https://api.openai.com/v1"
REASONING_EFFORT = "low"
MAX_OUTPUT_TOKENS = 1600

DOMAIN_INSTRUCTIONS = """You are a question interpreter for an import-logistics operations support team. The team reviews container reservations and identifiers, contract cost terms, actual handling events, approval or correction records, itemized charges, payments, unresolved handoffs, and IS operating procedures. Do not invent a company name or answer the operational question.

Your only task is to turn the user's question into the required structured fields. Use only words and facts explicitly present in that question. You do not have access to source documents. Do not infer a reservation, date type, record role, or missing condition from general knowledge. If the question does not support a safe interpretation, choose unresolved.

Choose route as follows: lookup for one or two identifiable cases asking only for named case records; documents for a general procedure or rule question without a case; mixed for a case question that also asks why, whether a condition applies, about an exception or change, or needs a general procedure; unresolved when the target or request is unclear. A documents or unresolved route has no targets.

For each target, copy anchor as one exact, continuous, uniquely occurring span from the question. Keep each case and its request in its own target; for two cases, never move a clue or requested role from one clause to the other. A target needs a supported case clue and at least one explicit record-role cue. Copy every constraint's raw value literally from that target's anchor; raw must be a substring of anchor. Use only these field names: case_id, contract_id, equipment_id, record_id, shipper, cargo, spec, actual_gate_out_date, actual_discharge_date, actual_designated_return_date, scheduled_return_date, system_entry_date. Use only a field whose type is explicit in the same anchor. Do not turn an unspecified date into a scheduled or actual date. Use polarity exclude only when the anchor explicitly excludes that literal value; otherwise use include. Do not use a generic clue as if it uniquely identified a case.

Use only these record roles, and include only roles explicitly requested in the target anchor:
- 비용 조건 약정: contract cost terms, rates, free periods
- 현장 사건 수신대장: actual pickup, discharge, or return event records
- 계약 조건 대조 회신: approval, review, correction, or change replies
- 비용 청구 명세: itemized charges, billed amounts, or charge calculations
- 수납 및 미결 인계: payments, refunds, receivables, or unresolved handoffs

If the question compares two cases, preserve each case's own requested roles. If it asks for a comparison but does not make the target-to-role relationship clear, choose unresolved. Return only the required object. Do not include explanations or hidden reasoning."""
PROMPT_SHA256 = sha256(DOMAIN_INSTRUCTIONS.encode("utf-8")).hexdigest()


class LunaInterpreter:
    """Call GPT-6 Luna once and keep only thread-local, redacted call telemetry."""

    def __init__(self, client: Any | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)
        if client is None:
            self.client = OpenAI(base_url=API_BASE_URL, max_retries=0)
        elif callable(getattr(client, "with_options", None)):
            # Pin the official endpoint and disable SDK retries on injected clients too.
            self.client = client.with_options(base_url=API_BASE_URL, max_retries=0)
        else:
            self.client = client
        self._local = threading.local()

    @property
    def last_call(self) -> dict[str, Any] | None:
        """Telemetry for this thread's most recent invocation; contains no text."""
        call = getattr(self._local, "last_call", None)
        return dict(call) if call is not None else None

    def invoke(
        self,
        question: str,
        *,
        timeout_seconds: float,
        rules_version: str | None,
    ) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must be a non-empty string.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number.")

        started = perf_counter()
        telemetry: dict[str, Any] = {
            "model": MODEL_ID,
            "reasoning_effort": REASONING_EFFORT,
            "rules_version": rules_version,
            "status": "started",
            "api_status_code": None,
            "latency_ms": None,
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "response_status": None,
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        self._local.last_call = telemetry

        try:
            response = self.client.responses.parse(
                model=MODEL_ID,
                instructions=DOMAIN_INSTRUCTIONS,
                input=question,
                text_format=QuestionInterpretation,
                reasoning={"effort": REASONING_EFFORT},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                tools=[],
                store=False,
                timeout=float(timeout_seconds),
            )
        except (TimeoutError, APITimeoutError):
            telemetry["status"] = "timeout"
            self._finish(telemetry, started)
            raise TimeoutError("GPT-6 Luna interpretation timed out.") from None
        except APIStatusError as exc:
            telemetry["status"] = "http_error"
            telemetry["api_status_code"] = exc.status_code
            self._finish(telemetry, started)
            raise RuntimeError("GPT-6 Luna interpretation failed.") from None
        except Exception:
            telemetry["status"] = "provider_error"
            self._finish(telemetry, started)
            raise RuntimeError("GPT-6 Luna interpretation failed.") from None

        self._record_response_usage(telemetry, response)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            telemetry["status"] = "no_parsed_output"
            self._finish(telemetry, started)
            raise RuntimeError("GPT-6 Luna returned no structured interpretation.")

        try:
            interpretation = QuestionInterpretation.model_validate(parsed)
        except Exception:
            telemetry["status"] = "invalid_structure"
            self._finish(telemetry, started)
            raise RuntimeError(
                "GPT-6 Luna returned an invalid interpretation."
            ) from None

        telemetry["status"] = "completed"
        self._finish(telemetry, started)
        return interpretation.model_dump(mode="json")

    @staticmethod
    def _record_response_usage(telemetry: dict[str, Any], response: Any) -> None:
        telemetry["response_status"] = getattr(response, "status", None)
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        telemetry["input_tokens"] = getattr(usage, "input_tokens", None)
        telemetry["output_tokens"] = getattr(usage, "output_tokens", None)
        input_details = getattr(usage, "input_tokens_details", None)
        if input_details is not None:
            telemetry["cached_input_tokens"] = getattr(
                input_details, "cached_tokens", None
            )
        details = getattr(usage, "output_tokens_details", None)
        if details is not None:
            telemetry["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)

    def _finish(self, telemetry: dict[str, Any], started: float) -> None:
        telemetry["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        self._local.last_call = dict(telemetry)


__all__ = [
    "DOMAIN_INSTRUCTIONS",
    "API_BASE_URL",
    "MAX_OUTPUT_TOKENS",
    "MODEL_ID",
    "PROMPT_SHA256",
    "REASONING_EFFORT",
    "LunaInterpreter",
]
