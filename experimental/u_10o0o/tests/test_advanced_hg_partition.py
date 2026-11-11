from types import SimpleNamespace

import numpy as np
import pytest

from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_experiment_eval import score_one
from experimental.u_10o0o.advanced_hg_partition import (
    PartitionSearch,
    _VectorRows,
    attach_verified_sources,
    is_deferred_hg_page,
)
from experimental.u_10o0o.advanced_hg_partition_benchmark import full_fallback_result


CASE = "HG2028-00660"


def _page(book_id, number, text=None):
    return {
        "book_id": book_id,
        "file_name": f"{book_id}.pdf",
        "page": number,
        "text": text or f"{book_id} 쪽 {number} 원문",
    }


@pytest.mark.parametrize("previous_path", ["general", "hg_search", "clarify", "lookup"])
def test_full_fallback_reuses_complete_baseline_or_verified_lookup(previous_path):
    baseline = object()
    verified_lookup = object()
    result, route = full_fallback_result(
        "질문", previous_path, baseline, [], verified_lookup, None, {}, {}
    )

    if previous_path == "lookup":
        assert result is verified_lookup
        assert route == "lookup"
    else:
        assert result is baseline
        assert route == "full_fallback"


def test_peer_quote_group_accepts_an_alternate_citation_on_its_page():
    alternate = _page("AS052", 3, "대체 인용 근거")
    ranked = [(alternate, 1.0)]
    expanded, context, _ = attach_verified_sources(
        "근거 확인", ranked, ranked, SimpleNamespace(page_roles={}), {}
    )
    reference = {
        "evidence_groups": [
            [
                {"book_id": "AS051", "page": 3, "quote": "첫 번째 인용"},
                {"book_id": "AS052", "page": 3, "quote": "대체 인용 근거"},
            ]
        ]
    }
    retrieval = ranked, expanded, context, (), "general"
    assert score_one(reference, retrieval)["final_complete"] == 0
    metrics = score_one(reference, retrieval, peer=True)
    assert metrics["final_complete"] == 1
    assert metrics["doc_recall_at_8"] == metrics["page_recall_at_8"] == 0.5


class FakeSearch:
    def __init__(self, pages, vectors):
        self.pages = list(pages)
        self.vectors = vectors
        self.searches = 0
        self.reranks = 0
        self.embedder = SimpleNamespace(marker="shared-embedder")
        self.reranker = SimpleNamespace(marker="shared-reranker")

    def rank(self, questions, vectors, log):
        self.searches += len(questions)
        self.reranks += len(questions)
        pools = {}
        heads = {}
        for name in vectors:
            pools[name] = [[(page, 1.0) for page in self.pages] for _ in questions]
            heads[name] = [rows[:3] for rows in pools[name]]
        return pools, heads


def test_hg_range_is_removed_before_general_search_and_rows_stay_aligned():
    pages = [
        _page("AS056", 1),
        _page("AS057", 1),
        _page("AS090", 2),
        _page("AS120", 3),
        _page("AS121", 4),
        _page("MISC", 5),
    ]
    vectors = np.arange(12, dtype=float).reshape(6, 2)
    original_vectors = vectors.copy()
    hg_refs = {("AS056", 1), ("AS057", 1), ("AS090", 2), ("AS120", 3)}
    search = PartitionSearch(
        pages, vectors, hg_page_ids=hg_refs, search_factory=FakeSearch
    )

    assert is_deferred_hg_page(pages[1])
    assert is_deferred_hg_page(pages[3])
    assert not is_deferred_hg_page(pages[0])
    assert [page["book_id"] for page in search.general.pages] == [
        "AS056",
        "AS121",
        "MISC",
    ]
    assert [page["book_id"] for page in search.hg.pages] == [
        "AS056",
        "AS057",
        "AS090",
        "AS120",
    ]
    np.testing.assert_array_equal(
        search.general.vectors @ np.array([1.0, 0.0]), [0, 8, 10]
    )
    np.testing.assert_array_equal(
        search.hg.vectors @ np.array([1.0, 0.0]), [0, 2, 4, 6]
    )
    np.testing.assert_array_equal(vectors, original_vectors)
    assert search.pages[1] is pages[1]


def test_batched_rank_returns_pool_and_reranked_order_for_each_partition():
    pages = [_page("AS056", 1), _page("AS057", 1), _page("AS121", 1)]
    vectors = np.arange(6, dtype=float).reshape(3, 2)
    search = PartitionSearch(
        pages,
        vectors,
        hg_page_ids={("AS056", 1), ("AS057", 1), ("AS121", 1)},
        search_factory=FakeSearch,
    )
    questions = [
        {"id": "q1", "question": "첫 질문"},
        {"id": "q2", "question": "둘째 질문"},
    ]

    general = search.rank(questions, "general")
    hg = search.rank(questions, "hg")
    baseline = search.rank(questions, "baseline")

    assert [page["book_id"] for page, _ in general["q1"][0]] == ["AS056", "AS121"]
    assert [page["book_id"] for page, _ in hg["q1"][0]] == ["AS056", "AS057", "AS121"]
    assert [page["book_id"] for page, _ in baseline["q1"][0]] == [
        "AS056",
        "AS057",
        "AS121",
    ]
    assert search.general.embedder.marker == "shared-embedder"
    assert search.hg.reranker.marker == "shared-reranker"
    assert search.searches == 6
    assert search.reranks == 6


def test_default_hg_fallback_is_only_the_deferred_range():
    pages = [
        _page("AS056", 1),
        _page("AS057", 1),
        _page("AS120", 1),
        _page("AS121", 1),
        _page("MISC", 1),
    ]
    search = PartitionSearch(
        pages, np.zeros((len(pages), 2)), search_factory=FakeSearch
    )

    assert [page["book_id"] for page in search.hg.pages] == ["AS057", "AS120"]


def test_verified_roles_are_attached_and_mixed_context_keeps_general_search_page():
    role_pages = {
        role: _page(f"AS{60 + index:03}", index + 1, f"원문 {role}의 검증된 정보")
        for index, role in enumerate(ROLES)
    }
    page_roles = {
        (page["book_id"], page["page"]): (CASE, role)
        for role, page in role_pages.items()
    }
    lookup = SimpleNamespace(page_roles=page_roles)
    general = [(_page("AS001", 1), 0.9), (_page("AS002", 1), 0.8)]

    expanded, context, route = attach_verified_sources(
        "이 사건의 청구 기록과 적용 규정을 알려줘",
        general,
        general,
        lookup,
        {CASE: role_pages},
        requested_roles={CASE: (ROLES[3],)},
        mixed=True,
    )

    final_sources = {
        (chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks
    }
    requested = role_pages[ROLES[3]]
    assert (requested["book_id"], requested["page"]) in final_sources
    assert all(
        (page["book_id"], page["page"]) in final_sources for page in role_pages.values()
    )
    assert any(book_id == "AS001" for book_id, _ in final_sources)
    assert len(context.final_chunks) <= 8
    assert context.token_count <= 8000
    assert len(expanded) == 7
    assert route.startswith("repacked") or route == "covered"


def test_mixed_single_case_with_all_roles_keeps_one_true_general_page():
    role_pages = {
        role: _page(f"AS{60 + index:03}", index + 1, f"원문 {role} 내용")
        for index, role in enumerate(ROLES)
    }
    lookup = SimpleNamespace(
        page_roles={
            (page["book_id"], page["page"]): (CASE, role)
            for role, page in role_pages.items()
        }
    )
    general = [(_page("AS001", 1), 0.9), (_page("AS002", 1), 0.8)]

    expanded, context, route = attach_verified_sources(
        "이 사건 다섯 기록과 적용 예외를 함께 보여줘",
        general,
        general,
        lookup,
        {CASE: role_pages},
        requested_roles={CASE: ROLES},
        mixed=True,
    )

    final_sources = {
        (chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks
    }
    assert len(final_sources) == 7
    assert any(book_id == "AS001" for book_id, _ in final_sources)
    assert len(expanded) == 7
    assert len(context.final_chunks) <= 8
    assert context.token_count <= 8000
    assert route in {"repacked", "covered"} or route.endswith("_with_general")


def test_supplemental_case_role_does_not_satisfy_mixed_general_reservation():
    role_pages = {
        role: _page(f"AS{60 + index:03}", index + 1, f"원문 {role} 내용")
        for index, role in enumerate(ROLES)
    }
    role_pages[ROLES[0]] = _page("AS050", 1, "별도 사건의 계약 원문")
    page_roles = {
        (page["book_id"], page["page"]): (CASE, role)
        for role, page in role_pages.items()
    }
    lookup = SimpleNamespace(page_roles=page_roles)
    ranked = [(role_pages[ROLES[0]], 0.9)]

    expanded, context, route = attach_verified_sources(
        "이 사건의 청구 기록과 적용 규정을 알려줘",
        ranked,
        ranked,
        lookup,
        {CASE: role_pages},
        requested_roles={CASE: (ROLES[3],)},
        mixed=True,
    )

    assert context.final_chunks
    assert route.endswith("_missing_general")
    assert len(expanded) == 5


def test_verified_source_attachment_rejects_unfiltered_general_candidates():
    restricted = _page("AS057", 1)
    lookup = SimpleNamespace(page_roles={})
    with pytest.raises(ValueError, match="AS057–AS120"):
        attach_verified_sources(
            "질문",
            [(restricted, 1.0)],
            [(restricted, 1.0)],
            lookup,
            {},
        )


def test_full_corpus_mixed_assembly_keeps_deferred_search_page_with_db_sources():
    role_pages = {
        role: _page(f"AS{60 + index:03}", index + 1, f"원문 {role}의 검증된 정보")
        for index, role in enumerate(ROLES)
    }
    page_roles = {
        (page["book_id"], page["page"]): (CASE, role)
        for role, page in role_pages.items()
    }
    lookup = SimpleNamespace(page_roles=page_roles)
    # This is the full-corpus ranking: it contains AS057–AS120 pages alongside
    # ordinary pages. AS057 is not one of the separately attached DB sources.
    full_ranked = [
        (_page("AS057", 12, "정책 근거 원문"), 0.99),
        *[
            (_page(f"AS{book:03}", book, f"전체 검색 결과 {book}"), 0.9 - i / 100)
            for i, book in enumerate(range(130, 148))
        ],
    ]

    expanded, context, route = attach_verified_sources(
        "이 사건 청구 기록과 적용 규정을 알려줘",
        full_ranked,
        full_ranked,
        lookup,
        {CASE: role_pages},
        requested_roles={CASE: (ROLES[3],)},
        mixed=True,
        allow_deferred_search_pages=True,
    )

    final_sources = {
        (chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks
    }
    assert (
        role_pages[ROLES[3]]["book_id"],
        role_pages[ROLES[3]]["page"],
    ) in final_sources
    assert ("AS057", 12) in final_sources
    assert len(context.final_chunks) <= 8
    assert context.token_count <= 8000
    assert len(expanded) <= 20
    assert route in {"covered", "repacked_with_general"} or route.startswith("repacked")


def test_vector_rows_validate_alignment_and_unknown_hg_references():
    with pytest.raises(ValueError, match="행 수"):
        PartitionSearch(
            [_page("AS001", 1)], np.zeros((2, 2)), search_factory=FakeSearch
        )
    with pytest.raises(ValueError, match="전체 원본 페이지"):
        PartitionSearch(
            [_page("AS001", 1)],
            np.zeros((1, 2)),
            hg_page_ids={("AS999", 1)},
            search_factory=FakeSearch,
        )

    vectors = np.arange(12, dtype=float).reshape(6, 2)
    subset = _VectorRows(vectors, (0, 1, 4, 5))
    assert subset.shape == (4, 2)
    np.testing.assert_array_equal(subset @ np.array([0.0, 1.0]), [1, 3, 9, 11])
