"""Alternative-aware fact coverage for the full Advanced reference set.

This evaluation-only adapter checks quoted evidence requirements against the
already packed context. It does not inspect question wording, answers, or fact
descriptions, and it never joins text from different source pages.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from experimental.u_10o0o.context_evidence import _normalize, _source_key


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    fields = {
        name: getattr(value, name, None)
        for name in ("book_id", "file_name", "page", "text")
    }
    return fields if any(item is not None for item in fields.values()) else None


def _context_pages(context: Any) -> dict[tuple[str, int], list[str]]:
    chunks = getattr(context, "final_chunks", context)
    if not isinstance(chunks, Sequence) or isinstance(chunks, (str, bytes)):
        raise TypeError("context must expose a sequence of final chunks")

    pages: dict[tuple[str, int], list[str]] = defaultdict(list)
    for item in chunks:
        chunk = (
            item.get("chunk", item)
            if isinstance(item, Mapping)
            else getattr(item, "chunk", item)
        )
        mapping = _as_mapping(chunk)
        if mapping is None:
            raise ValueError("context chunk is missing source metadata")
        text = mapping.get("text")
        if not isinstance(text, str):
            raise ValueError("context chunk is missing text")
        pages[_source_key(mapping)].append(_normalize(text))
    return pages


def _bundle_is_present(
    bundle: Any, pages: Mapping[tuple[str, int], Sequence[str]]
) -> bool:
    if (
        not isinstance(bundle, Sequence)
        or isinstance(bundle, (str, bytes))
        or not bundle
    ):
        return False

    for evidence in bundle:
        if not isinstance(evidence, Mapping):
            return False
        quote = evidence.get("quote")
        if not isinstance(quote, str):
            return False
        normalized_quote = _normalize(quote)
        if not normalized_quote:
            return False
        try:
            key = _source_key(evidence)
        except (KeyError, TypeError, ValueError):
            return False
        if not any(normalized_quote in text for text in pages.get(key, ())):
            return False
    return True


def fact_coverage(
    reference: Mapping[str, Any], context: Any
) -> dict[str, int | float | None]:
    """Score required evidence facts in packed context, honoring alternatives.

    A requirement is covered when every citation in its primary bundle is found
    on its own ``(book_id, page)`` or every citation in any one alternative
    bundle is found on its own source page. Multiple chunks from the same page
    can satisfy separate citations, but text is never combined across chunks.
    """
    if not isinstance(reference, Mapping):
        raise TypeError("reference must be a mapping")
    requirements = reference.get("evidence_requirements")
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
        raise ValueError("reference has no evidence_requirements list")

    pages = _context_pages(context)
    required = 0
    hit = 0
    primary_hits = 0
    alternative_hits = 0
    alternative_only_hits = 0

    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        required += 1
        primary_hit = _bundle_is_present(requirement.get("primary"), pages)
        alternatives = requirement.get("alternatives", [])
        if not isinstance(alternatives, Sequence) or isinstance(
            alternatives, (str, bytes)
        ):
            alternatives = []
        alternative_hit = any(
            _bundle_is_present(bundle, pages) for bundle in alternatives
        )

        primary_hits += int(primary_hit)
        alternative_hits += int(alternative_hit)
        alternative_only_hits += int(alternative_hit and not primary_hit)
        hit += int(primary_hit or alternative_hit)

    return {
        "required_facts": required,
        "required_facts_hit": hit,
        "primary_bundle_hits": primary_hits,
        "alternative_bundle_hits": alternative_hits,
        "alternative_only_hits": alternative_only_hits,
        "fact_recall": hit / required if required else None,
        "all_facts": int(required > 0 and hit == required),
    }
