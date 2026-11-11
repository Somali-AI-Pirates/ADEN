"""Assemble multi-request retrieval candidates and their shared context."""

from app.generation.context import build_context
from experimental.u_10o0o.advanced_graph_benchmark import interleave, unique_pages
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
)
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _source_key,
)


def merge_request_candidates(base_pool, groups):
    """Prioritize request groups round-robin, then retain the base ranking."""
    base = list(unique_pages(base_pool))
    requests = [list(group) for group in groups if group]
    if not requests:
        return base[:CANDIDATES]
    return list(unique_pages([*interleave(requests), *base]))[:CANDIDATES]


def repack_request_context(retrieval, groups):
    """Reorder only existing candidates for multiple requests under the normal budget."""
    pool, expanded, _, cases, route = retrieval
    candidates = expanded[:CANDIDATES]
    by_source = {_source_key(row[0]): row for row in candidates}
    requests = []
    for group in groups:
        eligible = [
            by_source[_source_key(page)]
            for page, _ in group
            if _source_key(page) in by_source
        ]
        if eligible:
            requests.append(eligible)
    if len(requests) < 2:
        return retrieval

    ordered = list(unique_pages([*interleave(requests), *candidates]))[:CANDIDATES]
    context = build_context(
        _context_candidates(ordered), FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens
    )
    return pool, expanded, context, cases, f"{route}/request_round_robin"
