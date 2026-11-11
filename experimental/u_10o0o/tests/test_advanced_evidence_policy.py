from app.generation.context import build_context
from experimental.u_10o0o.advanced_evidence_policy import (
    merge_request_candidates,
    repack_request_context,
)
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


def row(book_id, page, text="evidence"):
    return {
        "book_id": book_id,
        "page": page,
        "file_name": f"{book_id}.pdf",
        "chunk_id": f"{book_id}:{page}",
        "text": text,
    }


def keys(rows):
    return [_source_key(page) for page, _ in rows]


def test_merge_request_candidates_round_robins_and_caps_at_twenty():
    base = [(row("base", page), 0.0) for page in range(1, CANDIDATES + 1)]
    first, second, third = row("request-a", 1), row("request-b", 1), row("request-c", 1)
    merged = merge_request_candidates(
        base,
        [
            [(base[1][0], 1.0), (first, 0.9), (row("tail-a", 1), 0.8)],
            [(second, 0.9), (first, 0.8), (third, 0.7)],
        ],
    )

    assert len(merged) == CANDIDATES
    assert len(keys(merged)) == len(set(keys(merged)))
    assert keys(merged[:5]) == [
        _source_key(base[1][0]),
        _source_key(second),
        _source_key(first),
        ("tail-a", 1),
        _source_key(third),
    ]


def test_merge_request_candidates_prioritizes_one_group_before_base_tail():
    base = [(row("base", page), 0.0) for page in range(1, CANDIDATES + 1)]
    extra = row("extra", 1)
    merged = merge_request_candidates(base, [[(extra, 1.0)]])

    assert len(merged) == CANDIDATES
    assert keys(merged[:2]) == [_source_key(extra), _source_key(base[0][0])]


def test_merge_request_candidates_keeps_base_when_groups_are_empty():
    base = [(row("base", page), 0.0) for page in range(1, CANDIDATES + 1)]

    assert merge_request_candidates(base, [[], []]) == base


def test_repack_uses_only_existing_candidates_and_prioritizes_each_request():
    hg_pages = [row("HG", page) for page in range(1, 6)]
    ordinary = [row("ordinary", page) for page in range(1, 4)]
    policy = row("IS", 1)
    expanded = [(page, 0.0) for page in [*hg_pages, *ordinary, policy]]
    initial_context = build_context(
        _context_candidates(expanded), FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens
    )
    assert _source_key(policy) not in {
        (chunk.chunk.book_id, chunk.chunk.page)
        for chunk in initial_context.final_chunks
    }
    pool, cases = object(), object()
    retrieval = (pool, expanded, initial_context, cases, "hg_prepend")
    outside = row("outside", 1)

    result = repack_request_context(
        retrieval,
        [[(hg_pages[1], 0.0)], [(policy, 1.0), (outside, 2.0)]],
    )
    context_sources = [
        (chunk.chunk.book_id, chunk.chunk.page) for chunk in result[2].final_chunks
    ]

    assert result[0] is pool
    assert result[1] is expanded
    assert result[3] is cases
    assert result[4] == "hg_prepend/request_round_robin"
    assert context_sources[:2] == [_source_key(hg_pages[1]), _source_key(policy)]
    assert _source_key(policy) in context_sources
    assert _source_key(outside) not in context_sources
    assert set(context_sources) <= set(keys(expanded))
    assert len(context_sources) <= FINAL_K
    assert len(context_sources) == len(set(context_sources))
    assert result[2].token_count <= DEFAULT_CONTEXT_TOKENS


def test_repack_returns_original_retrieval_when_fewer_than_two_groups_survive():
    expanded = [(row("HG", page), 0.0) for page in range(1, 6)]
    retrieval = (object(), expanded, object(), object(), "original")

    assert (
        repack_request_context(retrieval, [[expanded[0]], [(row("outside", 1), 1.0)]])
        is retrieval
    )
    assert repack_request_context(retrieval, [[expanded[0]]]) is retrieval


def test_repack_respects_page_and_token_budgets_and_deduplicates_sources():
    long_text = "evidence " * 1400
    pages = [row("long", page, long_text) for page in range(1, CANDIDATES + 1)]
    expanded = [(page, 0.0) for page in pages]
    retrieval = (object(), expanded, object(), object(), "original")

    result = repack_request_context(
        retrieval,
        [[expanded[0], expanded[0]], [expanded[1], expanded[1]]],
    )
    context_sources = [
        (chunk.chunk.book_id, chunk.chunk.page) for chunk in result[2].final_chunks
    ]

    assert len(context_sources) < FINAL_K
    assert len(context_sources) == len(set(context_sources))
    assert result[2].token_count <= DEFAULT_CONTEXT_TOKENS
