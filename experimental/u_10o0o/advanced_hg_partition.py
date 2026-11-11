"""Keep the long AS057–AS120 records out of ordinary Advanced retrieval.

Scope contract: the original all-page ranking remains available as the baseline;
the ordinary BM25, dense, and KURE path receives only pages outside AS057–AS120;
an unresolved HG fallback can search every verified HG source page; verified
case-role source pages can be attached to ordinary search results and packed by
the existing selector under its 8-page/8,000-token limits. The module does not
delete or mutate page/vector data, invoke a model provider, or change a CLI.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from itertools import zip_longest
from typing import Any, Literal

import numpy as np

from app.generation.context import build_context
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_experiment_eval import Search
from experimental.u_10o0o.advanced_pair_context import pack_pair_context
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    retain_reranked_head,
)
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _source_key,
)

Partition = Literal["baseline", "general", "hg"]
_AS_BOOK_ID = re.compile(r"AS(\d{3})", re.IGNORECASE)
_MAX_TARGETS = 2


def is_deferred_hg_page(page: Mapping[str, Any]) -> bool:
    """Return whether a page belongs to the excluded AS057–AS120 range."""
    return _is_deferred_source(_source_key(page))


def _is_deferred_source(source: tuple[str, int]) -> bool:
    book_id, _ = source
    match = _AS_BOOK_ID.fullmatch(book_id)
    return match is not None and 57 <= int(match.group(1)) <= 120


class _VectorRows:
    """A read-only aligned row selection that avoids copying the vector matrix."""

    def __init__(self, vectors: Any, indices: Sequence[int]):
        self._vectors = vectors
        self._indices = tuple(indices)
        self._ranges = self._contiguous_ranges(self._indices)
        self.shape = (len(self._indices), *vectors.shape[1:])

    @staticmethod
    def _contiguous_ranges(indices: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
        if not indices:
            return ()
        ranges = []
        start = previous = indices[0]
        for index in indices[1:]:
            if index != previous + 1:
                ranges.append((start, previous + 1))
                start = index
            previous = index
        ranges.append((start, previous + 1))
        return tuple(ranges)

    def __matmul__(self, query: Any) -> np.ndarray:
        scores = [self._vectors[start:end] @ query for start, end in self._ranges]
        if not scores:
            return np.empty(0, dtype=float)
        if len(scores) == 1:
            return scores[0]
        return np.concatenate(scores)


class _SharedSearchResource:
    """Lazily forward a Search model property to the full-corpus Search."""

    def __init__(self, owner: Search, name: str):
        self._owner = owner
        self._name = name

    def __getattr__(self, name: str) -> Any:
        return getattr(getattr(self._owner, self._name), name)


def _default_search_factory(pages: Sequence[dict[str, Any]], vectors: Any) -> Search:
    return Search(pages, vectors)


class PartitionSearch:
    """Run the existing ``Search`` pipeline over baseline, general, and HG pages.

    ``general`` removes AS057–AS120 before PatternIndex/BM25, dense ranking, or
    KURE can see those pages. Without an explicit set, ``hg`` searches only that
    deferred range. Pass ``lookup.page_roles.keys()`` as ``hg_page_ids`` to let
    the dedicated fallback search every verified HG case-role source page.
    All three Search objects share the same lazy embedder and reranker instances.
    """

    def __init__(
        self,
        pages: Sequence[dict[str, Any]],
        vectors: Any,
        *,
        hg_page_ids: Sequence[tuple[str, int]] | set[tuple[str, int]] | None = None,
        search_factory: Callable[[Sequence[dict[str, Any]], Any], Any] | None = None,
    ) -> None:
        if not hasattr(vectors, "shape") or len(vectors.shape) != 2:
            raise ValueError("문서 벡터는 2차원 행렬이어야 합니다.")
        if vectors.shape[0] != len(pages):
            raise ValueError("페이지와 문서 벡터의 행 수가 다릅니다.")
        if not pages:
            raise ValueError("페이지 목록이 비어 있습니다.")

        self.pages = tuple(pages)
        page_keys = tuple(_source_key(page) for page in self.pages)
        if len(set(page_keys)) != len(page_keys):
            raise ValueError("원본 페이지 출처가 중복됩니다.")
        page_index = {key: index for index, key in enumerate(page_keys)}

        if hg_page_ids is None:
            hg_keys = {key for key in page_keys if _is_deferred_source(key)}
        else:
            hg_keys = set(hg_page_ids)
            unknown = hg_keys - page_index.keys()
            if unknown:
                raise ValueError("HG 페이지 참조가 전체 원본 페이지에 없습니다.")

        general_indices = tuple(
            index
            for index, page in enumerate(self.pages)
            if not is_deferred_hg_page(page)
        )
        hg_indices = tuple(
            index for index, key in enumerate(page_keys) if key in hg_keys
        )
        if not general_indices or not hg_indices:
            raise ValueError("일반 검색과 HG 전용 검색에는 페이지가 필요합니다.")

        factory = search_factory or _default_search_factory
        self.baseline = factory(self.pages, vectors)
        self.general = factory(
            tuple(self.pages[index] for index in general_indices),
            _VectorRows(vectors, general_indices),
        )
        self.hg = factory(
            tuple(self.pages[index] for index in hg_indices),
            _VectorRows(vectors, hg_indices),
        )
        self._searches = {
            "baseline": self.baseline,
            "general": self.general,
            "hg": self.hg,
        }
        for search in (self.general, self.hg):
            # Search uses cached properties. Installing a forwarding value in the
            # instance dictionary shares the owner's lazy resources without
            # initializing either model until the first actual ranking call.
            search.__dict__["embedder"] = _SharedSearchResource(
                self.baseline, "embedder"
            )
            search.__dict__["reranker"] = _SharedSearchResource(
                self.baseline, "reranker"
            )

        self.excluded_page_ids = frozenset(
            key
            for key, page in zip(page_keys, self.pages, strict=True)
            if is_deferred_hg_page(page)
        )
        self.hg_page_ids = frozenset(page_keys[index] for index in hg_indices)

    @property
    def searches(self) -> int:
        return sum(search.searches for search in self._searches.values())

    @property
    def reranks(self) -> int:
        return sum(search.reranks for search in self._searches.values())

    def rank(
        self,
        questions: Sequence[Mapping[str, Any]],
        partition: Partition,
        log: Callable[[str], None] | None = None,
    ) -> dict[
        str,
        tuple[list[tuple[dict[str, Any], float]], list[tuple[dict[str, Any], float]]],
    ]:
        """Batch-rank questions and return each ID's candidate pool and KURE order."""
        if partition not in self._searches:
            raise ValueError(f"알 수 없는 검색 분할입니다: {partition}")
        ids = [question.get("id") for question in questions]
        if any(not isinstance(qid, str) or not qid for qid in ids):
            raise ValueError("배치 검색 문항에는 비어 있지 않은 id가 필요합니다.")
        if len(ids) != len(set(ids)):
            raise ValueError("배치 검색 문항 id가 중복됩니다.")
        if not questions:
            return {}

        search = self._searches[partition]
        pools, reranked = search.rank(
            questions,
            {partition: search.vectors},
            log or (lambda _: None),
        )
        results = {}
        for index, qid in enumerate(ids):
            pool = pools[partition][index]
            order = retain_reranked_head(pool, reranked[partition][index])
            if partition == "general" and any(
                is_deferred_hg_page(page) for page, _ in (*pool, *order)
            ):
                raise RuntimeError(
                    "일반 검색 후보에 제외 대상 AS 페이지가 포함됐습니다."
                )
            results[qid] = pool, order
        return results

    def one(
        self,
        question: str,
        partition: Partition = "general",
        *,
        log: Callable[[str], None] | None = None,
    ) -> tuple[list[tuple[dict[str, Any], float]], list[tuple[dict[str, Any], float]]]:
        """Rank one question through the same batch path used for evaluation."""
        return self.rank(
            [{"id": "__single_question__", "question": question}],
            partition,
            log,
        )["__single_question__"]


def attach_verified_sources(
    question: str,
    pool: Sequence[tuple[Mapping[str, Any], float]],
    order: Sequence[tuple[Mapping[str, Any], float]],
    lookup: Any,
    role_pages_by_case: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    requested_roles: Mapping[str, Sequence[str]] | None = None,
    mixed: bool = False,
    allow_deferred_search_pages: bool = False,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
) -> tuple[list[tuple[Mapping[str, Any], float]], Any, str]:
    """Attach verified role pages to a general result and pack the final context.

    ``role_pages_by_case`` maps each verified case to its source pages by role.
    Requested roles receive first priority; other verified roles follow and may
    be retained if the shared selector's page and token budget permits. For a
    mixed source-and-policy question, ``mixed=True`` reserves room for a general
    search page when the candidate pool makes that possible. Set
    ``allow_deferred_search_pages`` only when ``pool`` and ``order`` came from
    the full-corpus baseline search; it allows AS057–AS120 candidates to join
    verified database pages in the assembled context. Its default keeps the
    partitioned-search boundary intact.
    """
    if not 0 < context_tokens <= DEFAULT_CONTEXT_TOKENS:
        raise ValueError(
            f"context_tokens는 1부터 {DEFAULT_CONTEXT_TOKENS} 사이여야 합니다."
        )
    if len(role_pages_by_case) > _MAX_TARGETS:
        raise ValueError("한 번에 검증할 수 있는 대상은 두 사건까지입니다.")
    if not allow_deferred_search_pages and any(
        is_deferred_hg_page(page) for page, _ in (*pool, *order)
    ):
        raise ValueError("일반 검색 결과에 AS057–AS120 쪽을 넘길 수 없습니다.")

    source_pages: dict[tuple[str, int], tuple[str, str, Mapping[str, Any]]] = {}
    normalized_roles: dict[str, tuple[str, ...]] = {}
    page_roles = getattr(lookup, "page_roles", {})
    if requested_roles is not None and not set(requested_roles) <= set(
        role_pages_by_case
    ):
        raise ValueError("요청 역할에 대응하는 검증된 사건 원문이 없습니다.")
    for case_id, role_pages in role_pages_by_case.items():
        if not isinstance(case_id, str) or not case_id or not role_pages:
            raise ValueError("사건별 원문 역할과 출처가 필요합니다.")
        wanted = (
            tuple(requested_roles.get(case_id, ()))
            if requested_roles is not None
            else tuple(role for role in ROLES if role in role_pages)
        )
        if (
            not wanted
            or len(wanted) != len(set(wanted))
            or any(role not in ROLES or role not in role_pages for role in wanted)
        ):
            raise ValueError("요청 역할을 사건의 검증된 원문 쪽에서 찾을 수 없습니다.")
        normalized_roles[case_id] = tuple(role for role in ROLES if role in wanted)
        for role, page in role_pages.items():
            if role not in ROLES:
                raise ValueError("알 수 없는 HG 기록 역할입니다.")
            source = _source_key(page)
            if page_roles.get(source) != (case_id, role):
                raise ValueError("HG 원문 쪽과 검증된 사건·역할 연결이 다릅니다.")
            previous = source_pages.get(source)
            if previous is not None and previous[:2] != (case_id, role):
                raise ValueError("한 원문 쪽이 서로 다른 역할에 연결됐습니다.")
            source_pages[source] = (case_id, role, page)

    baseline_sources = {_source_key(page) for page, _ in pool}
    ordered_sources = {_source_key(page) for page, _ in order}
    if baseline_sources != ordered_sources:
        raise ValueError("후보와 리랭크 결과의 페이지 집합이 다릅니다.")

    requested_rows = []
    requested_groups = [
        [
            (case_id, role, role_pages_by_case[case_id][role])
            for role in normalized_roles[case_id]
        ]
        for case_id in normalized_roles
    ]
    for row in zip_longest(*requested_groups):
        requested_rows.extend(item for item in row if item is not None)
    supplemental_rows = [
        (case_id, role, role_pages[role])
        for case_id, role_pages in role_pages_by_case.items()
        for role in ROLES
        if role in role_pages and role not in normalized_roles[case_id]
    ]

    ranked_sources: list[tuple[Mapping[str, Any], float]] = []
    seen: set[tuple[str, int]] = set()
    for case_id, role, page in (*requested_rows, *supplemental_rows):
        source = _source_key(page)
        if source not in source_pages or source in seen:
            continue
        seen.add(source)
        ranked_sources.append((page, 0.0))
    for page, score in order:
        source = _source_key(page)
        if source not in seen:
            seen.add(source)
            ranked_sources.append((page, float(score)))
    if len(ranked_sources) > CANDIDATES:
        # Verified target roles have priority; all their pages fit before general
        # candidates because the interpreter is limited to two targets/five roles.
        ranked_sources = ranked_sources[:CANDIDATES]

    baseline = build_context(
        _context_candidates(order[:FINAL_K]),
        FINAL_K,
        context_tokens,
        _count_tokens,
    )
    expanded, context, route = pack_pair_context(
        question,
        ranked_sources,
        lookup,
        baseline,
        targets=normalized_roles,
        context_tokens=context_tokens,
        reserve_general=False,
    )

    if mixed:
        requested = {
            (case_id, role)
            for case_id, roles in normalized_roles.items()
            for role in roles
        }
        general_rows = [
            (page, float(score))
            for page, score in order
            if _source_key(page) not in source_pages
        ]
        if not _has_general_source(context, general_rows):
            priority = [(page, 0.0) for _, _, page in requested_rows]
            supplemental = [(page, 0.0) for _, _, page in supplemental_rows]
            original_general_order = [
                (page, float(score))
                for page, score in order
                if _source_key(page) not in source_pages
            ]
            complete_with_general = False
            for general in general_rows:
                general_source = _source_key(general[0])
                forced = (
                    priority
                    + [general]
                    + supplemental
                    + [
                        item
                        for item in original_general_order
                        if _source_key(item[0]) != general_source
                    ]
                )
                forced = _deduplicate_ranked(forced)[:CANDIDATES]
                candidate_context = build_context(
                    _context_candidates(forced),
                    FINAL_K,
                    context_tokens,
                    _count_tokens,
                )
                if requested <= _context_roles(
                    candidate_context, page_roles
                ) and general_source in _context_sources(candidate_context):
                    expanded, context = forced, candidate_context
                    route = "repacked_with_general"
                    complete_with_general = True
                    break
            if not complete_with_general:
                route = f"{route}_missing_general"
    if len(context.final_chunks) > FINAL_K or context.token_count > context_tokens:
        raise RuntimeError("최종 HG 문맥이 페이지 또는 토큰 상한을 넘었습니다.")
    return expanded, context, route


def _context_sources(context: Any) -> set[tuple[str, int]]:
    return {(chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks}


def _context_roles(context: Any, page_roles: Mapping[Any, Any]) -> set[tuple[str, str]]:
    return {
        role
        for source in _context_sources(context)
        if (role := page_roles.get(source)) is not None
    }


def _has_general_source(
    context: Any, general_rows: Sequence[tuple[Any, float]]
) -> bool:
    return bool(
        _context_sources(context) & {_source_key(page) for page, _ in general_rows}
    )


def _deduplicate_ranked(
    ranked: Sequence[tuple[Mapping[str, Any], float]],
) -> list[tuple[Mapping[str, Any], float]]:
    seen = set()
    result = []
    for page, score in ranked:
        source = _source_key(page)
        if source not in seen:
            seen.add(source)
            result.append((page, score))
    return result
