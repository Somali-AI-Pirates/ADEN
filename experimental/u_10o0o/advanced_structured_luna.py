"""Question-only GPT-6 Luna proposals in function or JSON-schema mode.

The function-call mode exposes a strict tool declaration, but this adapter
never executes the proposed lookup. Both response modes return the same local
Pydantic type so the router can validate and apply the proposal itself.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from dotenv import load_dotenv
from openai import APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
)

FUNCTION_NAME = "lookup_advanced_records"
FUNCTION_MODE = "tool"
JSON_SCHEMA_MODE = "json"
OUTPUT_MODES = (FUNCTION_MODE, JSON_SCHEMA_MODE)
TIMEOUT_SECONDS = 10.0

DOMAIN_INSTRUCTIONS = """You interpret questions for an import-logistics operations support team. Its source material includes HG reservation cases, HA charge and operational records, and IS work instructions and tables. Do not answer the user's operational question and do not invent a company name.

Return a structured lookup proposal using only information stated in the user's question. You cannot see source documents. Do not infer a reservation, date type, record role, or missing condition from general knowledge. Use route=unresolved when the target or request is unclear. Use route=documents for a general procedure question without a particular case; use route=lookup for one or two cases asking only for records; use route=mixed for a case question that also asks why, whether a condition applies, or about a general rule, exception, correction, or change. Documents and unresolved routes have no targets. When the function is available, do not call it for documents or unresolved routes. In a JSON response, return the appropriate route with an empty target list for these cases. If the function is unavailable and a case lookup cannot be proposed, leave lookup work to the local rules.

Make one target for each case and keep its request in that case's own clause. Each anchor must be one exact, continuous, uniquely occurring substring of the question. Set family to HG, HA, or IS only when the question supports that source family; if it does not, use an unresolved route with no targets. Copy only target-identifying filters into constraints, with their exact literal raw text contained in the anchor. For HG, use only these field labels: case_id, record_id, shipper, cargo, spec, contract_id, equipment_id, actual_discharge_date, actual_gate_out_date, actual_designated_return_date, scheduled_return_date, system_entry_date. Here case_id is an HG reservation key and record_id is an HG comparison record ID. For HA, constraints may use only case_id, the HA reservation key. For IS, constraints may use only record_id, the IS table key. Date field labels in constraints are only for a literal date value used as a filter, never for the date being requested. Use a date label only when that date type and a literal filter value are explicit in the same anchor. Use polarity=exclude only when the anchor explicitly excludes that literal; otherwise use include. Do not change the type of a date: scheduled, actual, and system-entry dates remain distinct. A constraint or target is only a proposal; the router will verify it locally.

For each target, put the information or records explicitly requested in fields as short, verbatim phrases copied from that target's anchor. Preserve multiple requested items separately. Do not replace the request with a guessed database field name or broaden it beyond the question. If the case or requested material cannot be safely assigned to an anchor, use unresolved rather than filling gaps. If no function call is appropriate, briefly identify the route as documents or unresolved; in JSON-schema mode, return the full required object. Never claim that a database lookup was executed or that a record was found."""

PROMPT_SHA256 = sha256(DOMAIN_INSTRUCTIONS.encode("utf-8")).hexdigest()


class LookupConstraint(BaseModel):
    """One literal target clue; the router must verify it before use."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field: str = Field(min_length=1, max_length=64)
    raw: str = Field(min_length=1, max_length=160)
    polarity: Literal["include", "exclude"]


class LookupTarget(BaseModel):
    """One case and its locally-verifiable question span."""

    model_config = ConfigDict(extra="forbid", strict=True)

    family: Literal["HG", "HA", "IS"]
    anchor: str = Field(min_length=1, max_length=500)
    constraints: list[LookupConstraint] = Field(max_length=8)
    fields: list[str] = Field(max_length=8)


class LookupProposal(BaseModel):
    """Strict provider proposal. It is not a database result or tool output."""

    model_config = ConfigDict(extra="forbid", strict=True)

    route: Literal["lookup", "mixed", "documents", "unresolved"]
    targets: list[LookupTarget] = Field(max_length=2)


# This intentionally mirrors LookupProposal's Pydantic schema while keeping
# the wire schema flat and identical for Responses function and JSON-schema
# modes. Strict mode requires every listed property and forbids extras.
LOOKUP_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {
            "type": "string",
            "enum": ["lookup", "mixed", "documents", "unresolved"],
        },
        "targets": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "family": {"type": "string", "enum": ["HG", "HA", "IS"]},
                    "anchor": {"type": "string", "minLength": 1, "maxLength": 500},
                    "constraints": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                },
                                "raw": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 160,
                                },
                                "polarity": {
                                    "type": "string",
                                    "enum": ["include", "exclude"],
                                },
                            },
                            "required": ["field", "raw", "polarity"],
                            "additionalProperties": False,
                        },
                    },
                    "fields": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                },
                "required": ["family", "anchor", "constraints", "fields"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["route", "targets"],
    "additionalProperties": False,
}
SCHEMA_SHA256 = sha256(
    json.dumps(LOOKUP_PROPOSAL_SCHEMA, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
).hexdigest()

CallMode = Literal["tool", "json"]
CallStatus = Literal[
    "completed",
    "timeout",
    "api_error",
    "provider_error",
    "incomplete_response",
    "no_structured_output",
    "invalid_proposal",
]


@dataclass(frozen=True)
class LunaCallTelemetry:
    """Redacted call details; never contains question text or model output."""

    model: str
    mode: CallMode
    reasoning_effort: str
    attempted: bool
    api_status_code: int | None
    latency_ms: float | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    response_status: str | None
    started_at_utc: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LunaCallResult:
    """A proposal or a sanitized failure status for the local router."""

    status: CallStatus
    proposal: LookupProposal | None
    telemetry: LunaCallTelemetry
    tool_requested: bool = False


class _NoToolCall(Exception):
    """The auto-selected function was not used; the router should fall back."""


class StructuredLunaInterpreter:
    """Call Luna once using one of two strict, shape-equivalent output modes."""

    def __init__(self, client: Any | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)
        if client is None:
            self.client = OpenAI(base_url=API_BASE_URL, max_retries=0)
        elif callable(getattr(client, "with_options", None)):
            self.client = client.with_options(base_url=API_BASE_URL, max_retries=0)
        else:
            # Test doubles do not expose OpenAI client options.
            self.client = client
        self._local = threading.local()

    def invoke(
        self,
        question: str,
        *,
        mode: CallMode,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> LunaCallResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must be a non-empty string.")
        if mode not in OUTPUT_MODES:
            raise ValueError(f"Unsupported output mode: {mode}")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be positive and at most 10 seconds.")

        started = perf_counter()
        telemetry: dict[str, Any] = {
            "model": MODEL_ID,
            "mode": mode,
            "reasoning_effort": REASONING_EFFORT,
            "attempted": False,
            "api_status_code": None,
            "latency_ms": None,
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "response_status": None,
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            payload = self._request(question, mode, float(timeout_seconds))
            telemetry["attempted"] = True
            response = self.client.responses.create(**payload)
        except (TimeoutError, APITimeoutError):
            return self._result("timeout", None, telemetry, started)
        except APIStatusError as exc:
            telemetry["api_status_code"] = exc.status_code
            return self._result("api_error", None, telemetry, started)
        except Exception:  # noqa: BLE001 - provider text must stay sanitized
            return self._result("provider_error", None, telemetry, started)

        self._record_usage(telemetry, response)
        tool_requested = mode == FUNCTION_MODE and self._has_tool_call(response)
        if telemetry["response_status"] != "completed":
            return self._result(
                "incomplete_response", None, telemetry, started, tool_requested
            )
        try:
            proposal = self._decode(response, mode)
            proposal = LookupProposal.model_validate(proposal)
        except _NoToolCall:
            return self._result("no_tool_call", None, telemetry, started)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return self._result(
                "invalid_proposal", None, telemetry, started, tool_requested
            )
        except Exception:  # noqa: BLE001 - malformed SDK output stays local
            return self._result(
                "no_structured_output", None, telemetry, started, tool_requested
            )
        return self._result("completed", proposal, telemetry, started, tool_requested)

    @staticmethod
    def _request(
        question: str, mode: CallMode, timeout_seconds: float
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": MODEL_ID,
            "instructions": DOMAIN_INSTRUCTIONS,
            "input": question,
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "timeout": timeout_seconds,
        }
        if mode == FUNCTION_MODE:
            request.update(
                tools=[
                    {
                        "type": "function",
                        "name": FUNCTION_NAME,
                        "description": "Use for a lookup or mixed route. Return the structured lookup proposal; do not execute a lookup or claim that records were found. Do not use this function for documents or unresolved routes.",
                        "parameters": LOOKUP_PROPOSAL_SCHEMA,
                        "strict": True,
                    }
                ],
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        else:
            request.update(
                tools=[],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": FUNCTION_NAME,
                        "schema": LOOKUP_PROPOSAL_SCHEMA,
                        "strict": True,
                    }
                },
            )
        return request

    @staticmethod
    def _decode(response: Any, mode: CallMode) -> dict[str, Any]:
        if mode == FUNCTION_MODE:
            calls = [
                item
                for item in (getattr(response, "output", None) or ())
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                raise _NoToolCall
            if len(calls) != 1 or getattr(calls[0], "name", None) != FUNCTION_NAME:
                raise ValueError("Expected exactly one lookup proposal function call.")
            arguments = getattr(calls[0], "arguments", None)
            if not isinstance(arguments, str):
                raise TypeError("Function arguments must be a JSON string.")
            proposal = json.loads(arguments)
        else:
            output_text = getattr(response, "output_text", None)
            if not isinstance(output_text, str) or not output_text.strip():
                raise ValueError("Expected structured JSON output.")
            proposal = json.loads(output_text)
        if not isinstance(proposal, dict):
            raise TypeError("Lookup proposal must be a JSON object.")
        return proposal

    @staticmethod
    def _has_tool_call(response: Any) -> bool:
        return any(
            getattr(item, "type", None) == "function_call"
            for item in (getattr(response, "output", None) or ())
        )

    @staticmethod
    def _record_usage(telemetry: dict[str, Any], response: Any) -> None:
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
        output_details = getattr(usage, "output_tokens_details", None)
        if output_details is not None:
            telemetry["reasoning_tokens"] = getattr(
                output_details, "reasoning_tokens", None
            )

    @staticmethod
    def _result(
        status: CallStatus,
        proposal: LookupProposal | None,
        telemetry: dict[str, Any],
        started: float,
        tool_requested: bool = False,
    ) -> LunaCallResult:
        telemetry["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        call_telemetry = LunaCallTelemetry(**telemetry)
        return LunaCallResult(status, proposal, call_telemetry, tool_requested)


# The short name is the runner-facing interface; keep the descriptive class
# name available for standalone use and type annotations.
StructuredLuna = StructuredLunaInterpreter


__all__ = [
    "DOMAIN_INSTRUCTIONS",
    "FUNCTION_MODE",
    "FUNCTION_NAME",
    "JSON_SCHEMA_MODE",
    "LOOKUP_PROPOSAL_SCHEMA",
    "PROMPT_SHA256",
    "SCHEMA_SHA256",
    "LookupConstraint",
    "LookupProposal",
    "LookupTarget",
    "LunaCallResult",
    "LunaCallTelemetry",
    "StructuredLuna",
    "StructuredLunaInterpreter",
]
