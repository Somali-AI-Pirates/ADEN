from collections import Counter

import pytest

from experimental.u_10o0o import advanced_field_roles
from experimental.u_10o0o.bm25 import tokenize, tokenize_bigram_id
from experimental.u_10o0o.bm25_code_normalization import (
    normalized_index,
    previous_index,
    score,
    tokenize_normalized_code,
)


@pytest.mark.parametrize("code", ["IS03-003-01", "AB-12344", "HL-20GP-0066", "ABC-DEF"])
def test_existing_complete_codes_and_base_terms_are_unchanged(code):
    text = f"기존 코드 {code} 조회"
    assert tokenize_normalized_code(text) == tokenize_bigram_id(text)


@pytest.mark.parametrize(
    "code", ["is03-003-01", "IS03 - 003 - 01", "Is03–003−01", "IS03\t-\t003-01"]
)
def test_spelling_variants_add_the_same_full_code_without_replacing_base_terms(code):
    assert tokenize_normalized_code(code) == tokenize(code) + ["is03-003-01"]


def test_occurrence_counts_and_separate_lines_are_preserved():
    tokens = tokenize_normalized_code("IS03-003-01 is03-003-01")
    assert Counter(tokens)["is03-003-01"] == 2
    assert "is03-003-01" not in tokenize_normalized_code("IS03 -\n003-01")
    assert tokenize_normalized_code("예약 확인 3일") == tokenize("예약 확인 3일")


def test_index_normalization_recovers_lowercase_query_and_restores_loader():
    pages = [
        {"file_name": "A.pdf", "page": 1, "text": "IS03-003-01 확인"},
        {"file_name": "B.pdf", "page": 1, "text": "is03 003 01"},
    ]
    original_loader = advanced_field_roles.load_tokenizer
    baseline = previous_index(pages)
    normalized = normalized_index(pages)
    assert advanced_field_roles.load_tokenizer is original_loader
    assert baseline.fielded("is03-003-01")[0][0]["file_name"] == "B.pdf"
    assert normalized.fielded("is03-003-01")[0][0]["file_name"] == "A.pdf"
    assert pages[0]["text"] == "IS03-003-01 확인"
    current = advanced_field_roles.PatternIndex(pages)
    assert current.fielded("is03-003-01") == normalized.fielded("is03-003-01")
    assert current.config["tokenizer"]["tokenizer"] == "bigram-id-normalized"


def test_incomplete_results_cannot_open_references(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text('{"status":"running"}', encoding="utf-8")

    def unexpected(*args):
        pytest.fail("References were opened before all search results were saved")

    monkeypatch.setattr(
        "experimental.u_10o0o.bm25_code_normalization.load_references", unexpected
    )
    with pytest.raises(ValueError, match="Both retrievals must complete"):
        score(tmp_path, tmp_path / "scores")
