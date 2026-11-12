"""Langfuse 5.x export-time masking for model and graph observations."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from langfuse.types import (
    MaskOtelSpansParams,
    MaskOtelSpansResult,
    OtelSpanPatch,
)

_INPUT = "langfuse.observation.input"
_OUTPUT = "langfuse.observation.output"
_METADATA_PREFIXES = (
    "langfuse.observation.metadata",
    "langfuse.trace.metadata",
)
_MODEL_PARAMETERS = "langfuse.observation.model.parameters"
_STATUS_MESSAGE = "langfuse.observation.status_message"
_REDACTED = "[REDACTED]"

_REASONING_TYPES = frozenset(
    {"analysis", "chain_of_thought", "cot", "reasoning", "thought", "thinking"}
)
_VISIBLE_CONTENT_TYPES = frozenset({"output_text", "refusal", "text"})
_HIDDEN_KEYS = frozenset(
    {
        "additionalkwargs",
        "analysis",
        "chainofthought",
        "errormessage",
        "exception",
        "exceptionmessage",
        "exceptiontext",
        "generationinfo",
        "llmoutput",
        "rawexception",
        "rawexceptiontext",
        "reasoning",
        "reasoningcontent",
        "responsemetadata",
        "stack",
        "stacktrace",
        "statusmessage",
        "thought",
        "thoughts",
        "traceback",
    }
)
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
_EXCEPTION_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"""(?i)\b(api[_ -]?key|secret(?:[_ -]?key)?|password|passwd|authorization)\s*(?:[:=]|\bis\b)\s*['"]?[^\s,;]+"""),
    re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,})\b"),
)


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _hidden_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized in _HIDDEN_KEYS:
        return True
    if normalized.endswith(("provider", "providerid", "providername", "providermetadata")):
        return True
    if "providermetadata" in normalized:
        return True
    if any(part in normalized for part in ("apikey", "authorization", "credential", "password", "secret")):
        return True
    if normalized in {"accesskey", "accesstoken", "authtoken", "bearer", "refreshtoken", "token"}:
        return True
    return False


def _hidden_reasoning_type(value: Any) -> bool:
    return isinstance(value, str) and value.casefold().replace("-", "_") in _REASONING_TYPES


def _scrub_string(value: str) -> str:
    result = value
    for match in _EXCEPTION_TOKEN.finditer(value):
        if not match.group().casefold().endswith(("error", "exception")):
            continue
        colon = match.end()
        while colon < len(value) and value[colon].isspace():
            colon += 1
        if colon < len(value) and value[colon] == ":" and colon + 1 < len(value):
            result = value[: match.start()] + _REDACTED
            break

    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub(lambda match: _REDACTED if match.lastindex is None else f"{match.group(1)}={_REDACTED}", result)
    return result


def _sanitize_error(value: Any) -> Any:
    """Keep normalized error codes, never raw exception text or response bodies."""
    if isinstance(value, str):
        stripped = value.strip()
        if _scrub_string(stripped) != stripped:
            return _DROP
        return stripped if _SAFE_ERROR_CODE.fullmatch(stripped) else _DROP
    if isinstance(value, Mapping):
        safe_fields = {"code", "error_class", "error_type", "status", "status_code", "type"}
        cleaned = {}
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in safe_fields:
                safe_item = _sanitize(item)
                if safe_item is not _DROP:
                    cleaned[key] = safe_item
        return cleaned if cleaned else _DROP
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _DROP


def _sanitize_content(value: Any) -> Any:
    """Keep visible message text while dropping non-text and reasoning blocks."""
    if isinstance(value, str):
        text = _scrub_string(value)
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return text
        if isinstance(decoded, (Mapping, list)):
            safe = _sanitize(decoded)
            if safe is _DROP:
                return _REDACTED
            return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        return text
    if not isinstance(value, list):
        return _sanitize(value)

    visible = []
    for block in value:
        if isinstance(block, str):
            visible.append(_scrub_string(block))
            continue
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if _hidden_reasoning_type(block_type) or block_type not in _VISIBLE_CONTENT_TYPES:
            continue
        cleaned = _sanitize(block)
        if cleaned is not _DROP:
            visible.append(cleaned)
    return visible


class _Drop:
    pass


_DROP = _Drop()


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        if _hidden_reasoning_type(value.get("type")):
            return _DROP
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if _hidden_key(key):
                continue
            if key.casefold() == "error":
                safe_item = _sanitize_error(item)
            elif key.casefold() == "content":
                safe_item = _sanitize_content(item)
            else:
                safe_item = _sanitize(item)
            if safe_item is not _DROP:
                cleaned[key] = safe_item
        return cleaned
    if isinstance(value, (list, tuple)):
        cleaned = []
        for item in value:
            safe_item = _sanitize(item)
            if safe_item is not _DROP:
                cleaned.append(safe_item)
        return cleaned
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _sanitize_payload(raw: Any) -> str:
    """Sanitize one serialized Langfuse input/output value."""
    if not isinstance(raw, str):
        return _REDACTED
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # The SDK serializes plain prompt/completion strings without JSON quotes.
        return _scrub_string(raw)
    safe = _sanitize(decoded)
    if safe is _DROP:
        safe = _REDACTED
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def _sanitize_metadata(raw: Any) -> str:
    if not isinstance(raw, str):
        return _REDACTED
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _scrub_string(raw)
    safe = _sanitize(decoded)
    if safe is _DROP:
        safe = _REDACTED
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def _is_metadata_attribute(key: str) -> bool:
    return any(key == prefix or key.startswith(prefix + ".") for prefix in _METADATA_PREFIXES)


def _is_payload_attribute(key: str) -> bool:
    lowered = key.casefold()
    return key in {_INPUT, _OUTPUT, _MODEL_PARAMETERS} or lowered.startswith(
        ("gen_ai.prompt", "gen_ai.completion", "llm.prompts", "llm.completions")
    )


def _should_delete_attribute(key: str) -> bool:
    lowered = key.casefold()
    if key == _STATUS_MESSAGE or _hidden_key(key):
        return True
    if _is_metadata_attribute(key):
        metadata_key = key.rsplit(".", 1)[-1].casefold()
        if metadata_key.startswith(("ls_", "lc_")) or metadata_key == "is_langchain_root":
            return True
        if _hidden_key(metadata_key):
            return True
    if any(part in lowered for part in ("exception.message", "exception.stacktrace", "traceback")):
        return True
    return False


def _fallback_deletes(attributes: Mapping[str, Any]) -> tuple[str, ...]:
    """On a filtering error, remove all attributes that can carry payload text."""
    deletes = []
    for key in attributes:
        lowered = key.casefold()
        if (
            key in {_INPUT, _OUTPUT, _MODEL_PARAMETERS, _STATUS_MESSAGE}
            or _is_metadata_attribute(key)
            or _is_payload_attribute(key)
            or _should_delete_attribute(key)
            or any(part in lowered for part in ("prompt", "completion", "input", "output", "content"))
        ):
            deletes.append(key)
    return tuple(deletes)


def mask_langfuse_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
    """Remove hidden reasoning and sensitive metadata from Langfuse export batches.

    Visible text, model inputs, outputs, tool calls, and usage remain available.
    If one span cannot be sanitized, its payload attributes are removed so a
    malformed object never makes the batch export unmasked content.
    """
    patches = {}
    for identifier, span in params.spans.items():
        try:
            updates = {}
            deletes = []
            for key, value in span.attributes.items():
                if _should_delete_attribute(key):
                    deletes.append(key)
                elif key in {_INPUT, _OUTPUT} or key.startswith(
                    ("gen_ai.prompt", "gen_ai.completion", "llm.prompts", "llm.completions")
                ):
                    updates[key] = _sanitize_payload(value)
                elif _is_metadata_attribute(key) or key == _MODEL_PARAMETERS:
                    updates[key] = _sanitize_metadata(value)
                elif isinstance(value, str):
                    updates[key] = _scrub_string(value)
            if updates or deletes:
                patches[identifier] = OtelSpanPatch(
                    set_attributes=updates,
                    delete_attributes=tuple(deletes),
                )
        except Exception:
            try:
                patches[identifier] = OtelSpanPatch(
                    set_attributes={
                        key: _REDACTED
                        for key in (_INPUT, _OUTPUT)
                        if key in span.attributes
                    },
                    delete_attributes=_fallback_deletes(span.attributes),
                )
            except Exception:
                # Langfuse drops the entire export batch when this hook raises.
                raise RuntimeError("Langfuse masking failed") from None
    return MaskOtelSpansResult(span_patches=patches)
