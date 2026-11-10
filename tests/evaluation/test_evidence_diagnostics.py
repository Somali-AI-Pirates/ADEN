from app.evaluation.metrics import evidence_diagnostics

REF = {"evidence_groups": [[{"book_id": "A", "page": 1}], [{"book_id": "B", "page": 2}]]}


def chunk(book, page, cid):
    return {"book_id": book, "page": page, "chunk_id": cid, "file_name": book+".pdf", "text": "SAMPLE"}


def test_budget_loss_only_counts_explicitly_excluded_pages():
    a, b = chunk("A", 1, "a"), chunk("B", 2, "b")
    base = {"retrieved_candidates": [a,b], "final_context_chunks": [a]}
    d = evidence_diagnostics(REF, base | {"budget_excluded_chunk_ids": ["b"]}, 8)
    assert d["candidate_doc_recall"]["value"] == 1
    assert "검색 후보 2개" in d["candidate_doc_recall"]["reason"]
    assert d["candidate_page_recall"]["value"] == 1
    assert d["final_page_recall"]["value"] == .5
    assert d["context_lost_page_recall"]["value"] == .5
    assert d["budget_lost_page_recall"]["value"] == .5
    other = evidence_diagnostics(REF, base | {"budget_excluded_chunk_ids": []}, 8)
    assert other["context_lost_page_recall"]["value"] == .5
    assert other["budget_lost_page_recall"]["value"] == 0
    assert evidence_diagnostics(REF, base, 8)["budget_lost_page_recall"]["status"] == "unavailable"


def test_unknown_and_empty_candidates_are_different():
    d = evidence_diagnostics(REF, {"retrieved_candidates": None, "final_context_chunks": None}, 8)
    assert d["candidate_doc_recall"]["status"] == "unavailable"
    assert d["candidate_page_recall"]["status"] == "unavailable"
    d = evidence_diagnostics(REF, {"retrieved_candidates": [], "final_context_chunks": [],
                                   "budget_excluded_chunk_ids": []}, 8)
    assert d["candidate_doc_recall"]["value"] == d["budget_lost_page_recall"]["value"] == 0


def test_missing_reference_pages_are_not_zero():
    d = evidence_diagnostics({"evidence_groups": [[{"book_id": "A"}]]},
                            {"retrieved_candidates": [], "final_context_chunks": []}, 8)
    assert d["candidate_doc_recall"]["value"] == 0
    assert d["candidate_page_recall"]["status"] == "not_applicable"


def test_final_top_k_and_alternative_pages_are_respected():
    a, b = chunk("A", 1, "a"), chunk("B", 2, "b")
    ref = {"evidence_groups": [[{"book_id": "A", "page": 1}, {"book_id": "A", "page": 3}],
                               [{"book_id": "B", "page": 2}]]}
    d = evidence_diagnostics(ref, {"retrieved_candidates": [a,b], "final_context_chunks": [a,b],
                                   "budget_excluded_chunk_ids": []}, 1)
    assert d["candidate_page_recall"]["value"] == 1
    assert d["final_page_recall"]["value"] == .5
    assert d["context_lost_page_recall"]["value"] == .5
    assert d["budget_lost_page_recall"]["value"] == 0
