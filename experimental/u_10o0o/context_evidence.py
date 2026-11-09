"""Measure quoted-evidence coverage in the final, token-limited context.

This adapter deliberately keeps evaluation labels out of the selected context. It
reuses the production context builder so source metadata, separators, chunk order,
deduplication, and whole-block budget handling match normal generation.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import tiktoken

from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk

_CONTEXT_CHUNKS = 3
_TOKENIZER = "o200k_base"


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding(_TOKENIZER)


@lru_cache(maxsize=4096)
def _count_tokens(text: str) -> int:
    return len(_encoding().encode(text)) if text else 0


def _normalize(text: str) -> str:
    """Normalize compatibility forms and PDF whitespace, preserving other text."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "")
    return "".join(normalized.split())


def _source_key(value: Mapping[str, Any]) -> tuple[str, int]:
    book_id = value.get("book_id")
    if not isinstance(book_id, str) or not book_id.strip():
        file_name = value.get("file_name")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Evidence and context items require book_id or file_name.")
        book_id = Path(file_name).stem

    page = value.get("page")
    if isinstance(page, bool):
        raise TypeError("Evidence and context pages must be positive integers.")
    try:
        page_number = int(page)
    except TypeError as exc:
        raise TypeError(
            "Evidence and context pages must be positive integers."
        ) from exc
    except ValueError as exc:
        raise ValueError(
            "Evidence and context pages must be positive integers."
        ) from exc
    if page_number < 1:
        raise ValueError("Evidence and context pages must be positive integers.")
    return book_id, page_number


def _quote_groups(
    evidence_groups: Sequence[Sequence[Mapping[str, Any]]],
) -> tuple[list[set[tuple[str, int, str]]], set[tuple[str, int, str]]]:
    groups: list[set[tuple[str, int, str]]] = []
    all_quotes: set[tuple[str, int, str]] = set()
    for group in evidence_groups:
        group_quotes: set[tuple[str, int, str]] = set()
        if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
            for evidence in group:
                if not isinstance(evidence, Mapping):
                    continue
                quote = evidence.get("quote")
                if not isinstance(quote, str):
                    continue
                normalized_quote = _normalize(quote)
                if not normalized_quote:
                    continue
                book_id, page = _source_key(evidence)
                key = (book_id, page, normalized_quote)
                group_quotes.add(key)
                all_quotes.add(key)
            if group:
                groups.append(group_quotes)
    return groups, all_quotes


def _context_candidates(
    ranked: Sequence[tuple[Mapping[str, Any], float]],
) -> list[RetrievedChunk]:
    candidates: list[RetrievedChunk] = []
    for index, (item, score) in enumerate(ranked):
        book_id, page = _source_key(item)
        file_name = item.get("file_name")
        text = item.get("text")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Context items require a file_name.")
        if not isinstance(text, str):
            raise TypeError("Context items require text.")
        chunk_id = item.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            chunk_id = f"evaluation:{index}:{book_id}:{page}"
        chunk = Chunk(
            chunk_id=chunk_id,
            book_id=book_id,
            file_name=file_name,
            page=page,
            text=text,
            # Structural chunk text already includes any source heading supplied
            # by its builder. Do not add a second heading here.
            heading="",
        )
        candidates.append(
            RetrievedChunk(
                chunk=chunk,
                rank=index + 1,
                score=float(score),
                retriever="context-evidence-evaluation",
            )
        )
    return candidates


def score_context(
    ranked: Sequence[tuple[Mapping[str, Any], float]],
    evidence_groups: Sequence[Sequence[Mapping[str, Any]]],
    *,
    token_budget: int | None = 4000,
    top_k: int = _CONTEXT_CHUNKS,
) -> dict[str, int]:
    """Score quote coverage in the actual context assembled from ranked results.

    A quote counts only when it appears, after conservative normalization, in a
    single selected chunk with the same document and page as its gold label. Text
    from separate chunks is never joined for matching. The returned mapping has
    integer values only and contains no labels or source text.
    Pass None as the token budget to limit the context only by page count.
    """
    if token_budget is not None and (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget <= 0
    ):
        raise ValueError("token_budget must be None or a positive integer.")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer.")

    groups, expected = _quote_groups(evidence_groups)
    candidates = _context_candidates(ranked)
    context = build_context(
        candidates,
        k=top_k,
        token_budget=token_budget,
        count_tokens=_count_tokens,
    )

    found: set[tuple[str, int, str]] = set()
    for result in context.final_chunks:
        chunk = result.chunk
        normalized_text = _normalize(chunk.text)
        source = (chunk.book_id, chunk.page)
        for quote_key in expected:
            if quote_key[:2] == source and quote_key[2] in normalized_text:
                found.add(quote_key)

    return {
        "context_tokens": context.token_count,
        "context_chunks": len(context.final_chunks),
        "expected_quotes": len(expected),
        "found_quotes": len(found),
        "evidence_groups": len(groups),
        "groups_with_any_found_quote": sum(bool(group & found) for group in groups),
    }


def audit_evidence_matchability(
    pages: Sequence[Mapping[str, Any]],
    evidence_groups: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    """Count gold quotes that match a supplied text block on their source page.

    `pages` may contain only the canonical pages referenced by one question; the
    caller can pre-index the full restored-page collection to avoid rescanning it.
    This diagnostic returns aggregate counts only.
    """
    source_texts: dict[tuple[str, int], list[str]] = defaultdict(list)
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        text = page.get("text")
        if not isinstance(text, str):
            continue
        source_texts[_source_key(page)].append(_normalize(text))

    groups, expected = _quote_groups(evidence_groups)
    matchable = {
        key for key in expected if any(key[2] in text for text in source_texts[key[:2]])
    }
    return {
        "expected_quotes": len(expected),
        "matchable_quotes": len(matchable),
        "unmatched_quotes": len(expected - matchable),
        "evidence_groups": len(groups),
        "groups_with_any_matchable_quote": sum(
            bool(group & matchable) for group in groups
        ),
    }
