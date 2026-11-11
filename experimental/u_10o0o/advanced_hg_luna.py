"""One-shot GPT-6 Luna proposals for locally resolving Advanced HG sources.

The model sees only the question and fixed instructions. This module exposes a
strict function proposal, but does not execute SQL, read PDFs, or send lookup
results back to the provider. The caller validates the proposal and performs
any source lookup locally.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from dotenv import load_dotenv
from openai import APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
)

FUNCTION_NAME = "lookup_hg_sources"
TIMEOUT_SECONDS = 10.0
HG_ROLES = (
    "비용 조건 약정",
    "현장 사건 수신대장",
    "계약 조건 대조 회신",
    "비용 청구 명세",
    "수납 및 미결 인계",
)
CallStatus = Literal[
    "completed",
    "timeout",
    "api_error",
    "provider_error",
    "incomplete_response",
    "no_tool_call",
    "invalid_proposal",
    "no_structured_output",
]

DOMAIN_INSTRUCTIONS = """You interpret questions for a Korean import-logistics operations team. Its HG records connect reservation cases to five kinds of original pages: agreed cost terms, an on-site event register, contract-condition replies, itemized charges, and payment or unresolved-handoff records. Do not invent a company name or answer the user's question.

Your task is to propose a local HG source lookup from the question only. You cannot see PDFs, database rows, search results, or reference answers. Do not invent a case, relationship between codes, date, record, or missing condition. Return only the strict function arguments when a specific HG case lookup is appropriate. This function call is a proposal; you do not execute a database lookup and must not claim that a record was found.

Use route=lookup for one or two identifiable HG cases when the user asks for case-specific records. Questions about the case's own approval, calculation, exception, or correction are still case lookups. Use route=mixed only when the case lookup is combined with a separate general policy or procedure question. Use route=documents for a general rule or procedure question with no specific case, and route=unresolved when the target or requested record is unclear. For documents or unresolved questions, do not call this function; leave the decision to the local router.

For every target, copy anchor as one exact, continuous span from the user's question that keeps that case's clues and its requested records together. Include any leading “YYYY-MM-DD HH:MM KST 기준” date in the anchor when it belongs to that case, even if another date in the anchor identifies an actual event. Do not combine separate cases into one target. Include one target per case, at most two. Each cue's raw value must be copied exactly from that target's anchor. Use cue kind=identifier for a literal case, contract, equipment, evidence, or register code; shipper, cargo, and date for those literal clues; and spec only for a broad equipment or cargo specification. For cue relation use unspecified unless the question explicitly states a relationship. For an explicit shortened name such as “20GP로 부르는 HL-20GP-0066”, mark the literal short name relation as short_alias; for an explicitly associated evidence or contract code, use linked_identifier. Never invent either relationship. Do not normalize, complete, split, or expand codes. A short term such as “20GP” may be a broad specification and can match many cases; never silently turn it into a longer code such as “HL-20GP-0066”. A code like “CT-006” or “HS-057” can also be shared by many records and is not proof of a unique reservation. Preserve every distinct literal clue that appears in the target anchor. The local program will check whether their combination identifies one case.

Use only the five exact role labels below, and include the roles the question asks to inspect. You may map a clear plain-language request to the closest role, but do not guess when the requested record kind is unclear. If a question needs all source records, include all five roles. If it asks for a broad rule without a specific case, use route=documents instead of returning every role.
- 비용 조건 약정: agreed contract terms, free periods, and rates
- 현장 사건 수신대장: recorded discharge, gate-out, return, and other on-site event details
- 계약 조건 대조 회신: contract checks, approvals, corrections, and replies
- 비용 청구 명세: billed charges and itemized cost calculations
- 수납 및 미결 인계: received payments, refunds, balances, and unresolved handoffs

For date cues, use relation=answer_as_of for a leading timestamp such as “YYYY-MM-DD HH:MM KST 기준” that asks what the case showed at that time. Use relation=identity_filter for a date attached to a concrete case event, such as “YYYY-MM-DD 양하 화물 건”. Do not confuse the two. Set target date_type to the meaning of the identity-filter date: actual for an event that happened, scheduled for a planned event, system_entry for when a value was entered, effective for when a correction took effect, and unspecified when its meaning is unclear. If there is only an answer-as-of date and no identity-filter date, use booking_status_as_of. If the anchor clearly has identity-filter dates of different types, use mixed. If no date appears, use unspecified. Never turn a scheduled date into an actual date or a case-status date into an event date.

If a code, shorthand, shipper, cargo, or date is not stated in the question, do not add it. If the clues may point to multiple cases, do not choose one; use route=unresolved and no targets. For a mixed question, keep the case-specific request in its target and leave rule or explanation retrieval to the local search path. Never include source text, database contents, tool results, or a natural-language answer."""
PROMPT_SHA256 = sha256(DOMAIN_INSTRUCTIONS.encode("utf-8")).hexdigest()


class HGCue(BaseModel):
    """One literal clue in a case-bound question span."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["identifier", "shipper", "cargo", "spec", "date"]
    raw: str = Field(min_length=1, max_length=160)
    relation: Literal[
        "unspecified",
        "short_alias",
        "linked_identifier",
        "answer_as_of",
        "identity_filter",
    ]


class HGTarget(BaseModel):
    """One proposed case and the original-record roles requested for it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    anchor: str = Field(min_length=1, max_length=500)
    cues: list[HGCue] = Field(min_length=1, max_length=12)
    roles: list[
        Literal[
            "비용 조건 약정",
            "현장 사건 수신대장",
            "계약 조건 대조 회신",
            "비용 청구 명세",
            "수납 및 미결 인계",
        ]
    ] = Field(min_length=1, max_length=5)
    date_type: Literal[
        "booking_status_as_of",
        "actual",
        "scheduled",
        "system_entry",
        "effective",
        "mixed",
        "unspecified",
    ]

    @model_validator(mode="after")
    def cues_belong_to_anchor_and_roles_are_unique(self) -> HGTarget:
        if any(cue.raw not in self.anchor for cue in self.cues):
            raise ValueError("Every cue must be a literal substring of its anchor.")
        if any(
            cue.relation in {"answer_as_of", "identity_filter"} and cue.kind != "date"
            for cue in self.cues
        ):
            raise ValueError("Date relations can only be used with date cues.")
        if any(
            cue.relation == "short_alias" and cue.kind not in {"identifier", "spec"}
            for cue in self.cues
        ):
            raise ValueError("short_alias can only describe an identifier or spec cue.")
        if any(
            cue.relation == "linked_identifier" and cue.kind != "identifier"
            for cue in self.cues
        ):
            raise ValueError("linked_identifier can only describe an identifier cue.")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("Requested HG roles must be unique.")
        return self


class HGProposal(BaseModel):
    """Strict model proposal; local code owns all matching and retrieval."""

    model_config = ConfigDict(extra="forbid", strict=True)

    route: Literal["lookup", "mixed", "documents", "unresolved"]
    targets: list[HGTarget] = Field(max_length=2)

    @model_validator(mode="after")
    def route_has_matching_target_shape(self) -> HGProposal:
        if self.route in {"documents", "unresolved"} and self.targets:
            raise ValueError("documents and unresolved routes cannot have targets.")
        if self.route in {"lookup", "mixed"} and not self.targets:
            raise ValueError("lookup and mixed routes require at least one target.")
        return self


LOOKUP_HG_SOURCES_SCHEMA: dict[str, Any] = {
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
                    "anchor": {"type": "string", "minLength": 1, "maxLength": 500},
                    "cues": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "identifier",
                                        "shipper",
                                        "cargo",
                                        "spec",
                                        "date",
                                    ],
                                },
                                "raw": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 160,
                                },
                                "relation": {
                                    "type": "string",
                                    "enum": [
                                        "unspecified",
                                        "short_alias",
                                        "linked_identifier",
                                        "answer_as_of",
                                        "identity_filter",
                                    ],
                                },
                            },
                            "required": ["kind", "raw", "relation"],
                            "additionalProperties": False,
                        },
                    },
                    "roles": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {"type": "string", "enum": list(HG_ROLES)},
                    },
                    "date_type": {
                        "type": "string",
                        "enum": [
                            "booking_status_as_of",
                            "actual",
                            "scheduled",
                            "system_entry",
                            "effective",
                            "mixed",
                            "unspecified",
                        ],
                    },
                },
                "required": ["anchor", "cues", "roles", "date_type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["route", "targets"],
    "additionalProperties": False,
}
SCHEMA_SHA256 = sha256(
    json.dumps(LOOKUP_HG_SOURCES_SCHEMA, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
).hexdigest()


@dataclass(frozen=True)
class HGLunaTelemetry:
    """Redacted call details; no question or proposal values are retained."""

    model: str
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
class HGLunaCallResult:
    """One sanitized call status and, if valid, its local lookup proposal."""

    status: CallStatus
    proposal: HGProposal | None
    telemetry: HGLunaTelemetry
    tool_requested: bool


class _NoToolCall(Exception):
    """The model chose not to request a local source lookup."""


class HGLuna:
    """Ask Luna once for a strict HG lookup proposal; never execute its tool."""

    def __init__(self, client: Any | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)
        if client is None:
            self.client = OpenAI(base_url=API_BASE_URL, max_retries=0)
        elif callable(getattr(client, "with_options", None)):
            self.client = client.with_options(base_url=API_BASE_URL, max_retries=0)
        else:
            self.client = client

    def invoke(
        self,
        question: str,
        *,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> HGLunaCallResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must be a non-empty string.")
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
            request = self._request(question, float(timeout_seconds))
            telemetry["attempted"] = True
            response = self.client.responses.create(**request)
        except (TimeoutError, APITimeoutError):
            return self._result("timeout", None, telemetry, started)
        except APIStatusError as exc:
            telemetry["api_status_code"] = exc.status_code
            return self._result("api_error", None, telemetry, started)
        except Exception:  # noqa: BLE001 - provider detail may include user data
            return self._result("provider_error", None, telemetry, started)

        self._record_usage(telemetry, response)
        tool_requested = self._has_tool_call(response)
        if telemetry["response_status"] != "completed":
            return self._result(
                "incomplete_response", None, telemetry, started, tool_requested
            )
        try:
            raw = self._decode(response)
            proposal = HGProposal.model_validate(raw)
            if any(question.count(target.anchor) != 1 for target in proposal.targets):
                raise ValueError(
                    "Every target anchor must occur exactly once in the question."
                )
        except _NoToolCall:
            return self._result("no_tool_call", None, telemetry, started)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return self._result(
                "invalid_proposal", None, telemetry, started, tool_requested
            )
        except Exception:  # noqa: BLE001 - malformed SDK output stays sanitized
            return self._result(
                "no_structured_output", None, telemetry, started, tool_requested
            )
        return self._result("completed", proposal, telemetry, started, tool_requested)

    @staticmethod
    def _request(question: str, timeout_seconds: float) -> dict[str, Any]:
        return {
            "model": MODEL_ID,
            "instructions": DOMAIN_INSTRUCTIONS,
            "input": question,
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "timeout": timeout_seconds,
            "tools": [
                {
                    "type": "function",
                    "name": FUNCTION_NAME,
                    "description": (
                        "Propose case-bound HG original-source roles for a specific "
                        "reservation question. This proposal is not a lookup result; "
                        "do not execute the function or claim records were found. "
                        "Do not call it for a general rule question or unclear target."
                    ),
                    "parameters": LOOKUP_HG_SOURCES_SCHEMA,
                    "strict": True,
                }
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

    @staticmethod
    def _decode(response: Any) -> dict[str, Any]:
        calls = [
            item
            for item in (getattr(response, "output", None) or ())
            if getattr(item, "type", None) == "function_call"
        ]
        if not calls:
            raise _NoToolCall
        if len(calls) != 1 or getattr(calls[0], "name", None) != FUNCTION_NAME:
            raise ValueError("Expected exactly one HG source proposal function call.")
        arguments = getattr(calls[0], "arguments", None)
        if not isinstance(arguments, str):
            raise TypeError("Function arguments must be a JSON string.")
        proposal = json.loads(arguments)
        if not isinstance(proposal, dict):
            raise TypeError("HG source proposal must be a JSON object.")
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
        proposal: HGProposal | None,
        telemetry: dict[str, Any],
        started: float,
        tool_requested: bool = False,
    ) -> HGLunaCallResult:
        telemetry["latency_ms"] = round((perf_counter() - started) * 1000, 3)
        return HGLunaCallResult(
            status, proposal, HGLunaTelemetry(**telemetry), tool_requested
        )


__all__ = [
    "DOMAIN_INSTRUCTIONS",
    "FUNCTION_NAME",
    "HGProposal",
    "HG_ROLES",
    "HGTarget",
    "HGCue",
    "HGLuna",
    "HGLunaCallResult",
    "HGLunaTelemetry",
    "LOOKUP_HG_SOURCES_SCHEMA",
    "PROMPT_SHA256",
    "SCHEMA_SHA256",
    "TIMEOUT_SECONDS",
]
