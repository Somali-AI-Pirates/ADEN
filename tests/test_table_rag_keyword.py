import pytest
from langchain_core.documents import Document

from app.rag.keyword_retriever import BM25Index, tokenize_for_bm25


def test_tokenizer_preserves_bigram_and_adds_normalized_identifier() -> None:
    text = "장비 hl ‑ 20gp − 0066 확인"

    assert tokenize_for_bm25(text) == [
        "장비",
        "hl",
        "20gp",
        "0066",
        "확인",
        "hl-20gp-0066",
    ]


def test_date_segments_are_not_joined_as_an_identifier() -> None:
    assert tokenize_for_bm25("예약일 2029-01-02") == [
        "예약",
        "약일",
        "2029",
        "01",
        "02",
    ]


def test_bm25_postings_track_document_frequency_and_term_frequency() -> None:
    documents = [
        Document(page_content="예약 예약"),
        Document(page_content="예약 청구"),
    ]

    index = BM25Index(documents)

    assert index.postings["예약"] == [(0, 2), (1, 1)]
    assert index.postings["청구"] == [(1, 1)]
    assert index.doc_lengths == [2, 2]


def test_bm25_ranks_documents_by_term_frequency() -> None:
    documents = [
        Document(page_content="예약 예약"),
        Document(page_content="예약 청구"),
    ]

    result = BM25Index(documents).search("예약")

    assert [document for document, _ in result] == documents
    assert [round(score, 4) for _, score in result] == [0.2605, 0.1823]


def test_bm25_returns_empty_for_unknown_terms_and_nonpositive_limit() -> None:
    index = BM25Index([Document(page_content="예약")])

    assert index.search("검역") == []
    assert index.search("예약", top_k=0) == []


def test_bm25_rejects_a_corpus_without_searchable_tokens() -> None:
    with pytest.raises(ValueError, match="tokenized document"):
        BM25Index([])

    with pytest.raises(ValueError, match="tokenized document"):
        BM25Index([Document(page_content=" -- ")])


def test_heading_bonus_reorders_a_matching_source_title() -> None:
    documents = [
        Document(page_content="예약"),
        Document(page_content="예약"),
    ]
    headings = ["일반 안내", "예약 확정 처리"]

    index = BM25Index(documents, headings=headings)
    body_order = [document for document, _ in index.search("예약")]
    fielded_order = [document for document, _ in index.fielded_search("예약")]

    assert body_order == documents
    assert fielded_order == list(reversed(documents))


def test_heading_count_must_match_documents() -> None:
    with pytest.raises(ValueError, match="Headings must match"):
        BM25Index([Document(page_content="예약")], headings=[])
