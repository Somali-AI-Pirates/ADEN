"""Normalization boundaries shared by ingestion and retrieval."""

from app.indexing.keyword_store import tokenize_for_bm25
from app.rag.records import codes
from shared.text_normalization import normalize_text


def test_bigram_tokens_preserve_complete_code_in_addition_to_parts():
    assert tokenize_for_bm25("무료기간 AB – 123 20GP") == [
        "무료", "료기", "기간", "ab", "123", "20gp", "ab-123",
    ]
    assert tokenize_for_bm25("HG2029-00660 HL-20GP-0066") == [
        "hg2029", "00660", "hl", "20gp", "0066",
        "hg2029-00660", "hl-20gp-0066",
    ]


def test_keyword_and_record_code_normalization_keep_distinct_supported_ranges():
    # BM25 historically lowercases ASCII; record lookup normalizes full-width IDs.
    assert tokenize_for_bm25("ＨＬ－２０ＧＰ－００６６") == []
    assert codes("ＨＬ－２０ＧＰ－００６６") == ("HL-20GP-0066",)
    assert tokenize_for_bm25("LONGNAMECODE-123") == ["longnamecode", "123"]


def test_record_value_normalization_preserves_nfkc_casefold_and_spacing():
    assert normalize_text(" ＨＬ　 A \nß ") == "hl a ss"
