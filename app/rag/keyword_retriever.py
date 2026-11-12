import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from langchain_core.documents import Document

from app.indexing.keyword_store import tokenize_for_bm25
from shared.table_rag_policy import (
    BM25_K1 as _BM25_K1,
    BM25_B as _BM25_B,
    HEADING_WEIGHT as _HEADING_WEIGHT,
    HEADING_POOL as _HEADING_POOL,
)

_BLOCKS = re.compile(r"\n\s*\n+")
_FOOTER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")


def _page_heading(text: str) -> str:
    blocks = [part.strip() for part in _BLOCKS.split(text.strip()) if part.strip()]
    if blocks and _FOOTER.fullmatch(blocks[-1]):
        blocks.pop()
    if len(blocks) > 1:
        selected = blocks[:3]
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and _FOOTER.fullmatch(lines[-1]):
            lines.pop()
        selected = [" ".join(lines[:6])]
    return "\n".join(selected)[:600]


class BM25Index:
    """Index body and heading terms for BM25 retrieval."""

    def __init__(
        self,
        documents: Sequence[Document],
        *,
        headings: Sequence[str] | None = None,
    ) -> None:
        self.documents = tuple(documents)
        self.doc_lengths: list[int] = []
        self.postings: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)

        for document_index, document in enumerate(self.documents):
            tokens = tokenize_for_bm25(document.page_content)
            self.doc_lengths.append(len(tokens))
            for term, frequency in Counter(tokens).items():
                self.postings[term].append((document_index, frequency))

        if not self.documents or not sum(self.doc_lengths):
            raise ValueError("BM25 requires at least one tokenized document.")

        if headings is not None and len(headings) != len(self.documents):
            raise ValueError("Headings must match the document count.")

        self.average_doc_length = sum(self.doc_lengths) / len(self.documents)
        self.length_norms = [
            _BM25_K1 * (_BM25_B * length / self.average_doc_length + 1 - _BM25_B)
            for length in self.doc_lengths
        ]
        document_count = len(self.documents)
        self.idf = {
            term: math.log1p(
                (document_count - len(postings) + 0.5) / (len(postings) + 0.5)
            )
            for term, postings in self.postings.items()
        }
        self.heading_index = self._build_heading_index(headings)

    def _build_heading_index(self, headings: Sequence[str] | None):
        if headings is None:
            return None

        heading_documents = [
            Document(
                page_content=heading,
                metadata={"document_index": index},
            )
            for index, heading in enumerate(headings)
            if tokenize_for_bm25(heading)
        ]
        if not heading_documents:
            return None
        return BM25Index(heading_documents)

    def search(self, question: str, *, top_k: int = 50) -> list[tuple[Document, float]]:
        if top_k <= 0:
            return []

        query_terms = set(tokenize_for_bm25(question)) & self.idf.keys()
        scores: defaultdict[int, float] = defaultdict(float)
        for term in sorted(query_terms):
            term_idf = self.idf[term]
            for document_index, frequency in self.postings[term]:
                length_norm = self.length_norms[document_index]
                scores[document_index] += (
                    term_idf * (_BM25_K1 + 1) * frequency / (frequency + length_norm)
                )

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [(self.documents[index], score) for index, score in ranked[:top_k]]

    def fielded_search(
        self,
        question: str,
        *,
        top_k: int = 50,
        field_pool: int = _HEADING_POOL,
        heading_weight: float = _HEADING_WEIGHT,
    ) -> list[tuple[Document, float]]:
        if top_k <= 0:
            return []

        body_ranked = self.search(question, top_k=max(field_pool, top_k))
        if not body_ranked or self.heading_index is None:
            return body_ranked[:top_k]

        document_indices = {
            id(document): index for index, document in enumerate(self.documents)
        }
        highest_body = body_ranked[0][1]
        scores = {
            document_indices[id(document)]: score / highest_body
            for document, score in body_ranked
        }
        heading_ranked = self.heading_index.search(question, top_k=field_pool)
        if heading_ranked:
            highest_heading = heading_ranked[0][1]
            for heading_document, score in heading_ranked:
                index = heading_document.metadata["document_index"]
                if index in scores:
                    scores[index] += heading_weight * score / highest_heading

        ranked_indices = sorted(scores, key=lambda index: (-scores[index], index))
        return [
            (self.documents[index], scores[index]) for index in ranked_indices[:top_k]
        ]
