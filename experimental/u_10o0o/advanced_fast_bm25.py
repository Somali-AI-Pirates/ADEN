"""A NumPy-backed adapter for the existing page-level BM25 index.

The adapter keeps BM25Index's tokenizer, scoring formula, term order, ranking
tie-break, and page objects. It only moves repeated posting-score calculation
from Python loops into precomputed float64 arrays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from experimental.u_10o0o.bm25 import K1

if TYPE_CHECKING:
    from experimental.u_10o0o.bm25 import BM25Index


class FastBM25:
    """Use an existing ``BM25Index`` with precomputed term contributions."""

    def __init__(self, original_index: BM25Index) -> None:
        self.pages = original_index.pages
        self.tokenizer = original_index.tokenizer
        self._term_contributions: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        for term, postings in original_index.postings.items():
            weight = original_index.idf[term] * (K1 + 1)
            page_indices = np.fromiter(
                (page_index for page_index, _ in postings),
                dtype=np.intp,
                count=len(postings),
            )
            contributions = np.fromiter(
                (
                    weight
                    * frequency
                    / (frequency + original_index.length_norms[page_index])
                    for page_index, frequency in postings
                ),
                dtype=np.float64,
                count=len(postings),
            )
            self._term_contributions[term] = (page_indices, contributions)

    def search(self, question: str, *, top_k: int = 20) -> list[tuple[dict, float]]:
        scores = np.zeros(len(self.pages), dtype=np.float64)
        matched = np.zeros(len(self.pages), dtype=np.bool_)
        query_terms = set(self.tokenizer(question))

        for term in sorted(query_terms & self._term_contributions.keys()):
            page_indices, contributions = self._term_contributions[term]
            # Each term appears at most once per page, so indexed addition is
            # safe and retains the original posting traversal order.
            scores[page_indices] += contributions
            matched[page_indices] = True

        indices = np.flatnonzero(matched)
        order = np.lexsort((indices, -scores[indices]))[:top_k]
        return [
            (self.pages[page_index], float(scores[page_index]))
            for page_index in indices[order]
        ]
