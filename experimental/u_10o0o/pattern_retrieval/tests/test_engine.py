"""Behavioral checks for exact identity, the single rerank, and source restoration."""

import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest
import tiktoken

from experimental.u_10o0o.bm25 import BM25Index
from experimental.u_10o0o.pattern_retrieval import engine
from experimental.u_10o0o.pattern_retrieval.common import codes_in, load_questions
from experimental.u_10o0o.pattern_retrieval.engine import Store, code_tokenize


def tiny_store(tmp_path):
    connection = sqlite3.connect(tmp_path / "corpus.sqlite3")
    connection.executescript("""
        CREATE TABLE pages(source_id TEXT,book_id TEXT,file_name TEXT,page INTEGER,
                           text TEXT,heading TEXT,pdf_sha256 TEXT);
        CREATE TABLE units(unit_id TEXT,arm TEXT,kind TEXT,text TEXT,scope_key TEXT);
        CREATE TABLE occurrences(unit_id TEXT,source_id TEXT,line_start INTEGER,
                                 line_end INTEGER,case_id TEXT,role TEXT,identifiers_json TEXT);
        CREATE TABLE code_links(code_norm TEXT,code_type TEXT,case_id TEXT,source_id TEXT,
                                line_start INTEGER,line_end INTEGER,relation TEXT);
    """)
    for i in range(12):
        sid = f"TEST:p{i + 1:04}"
        connection.execute(
            "INSERT INTO pages VALUES (?,?,?,?,?,?,?)",
            (sid, "TEST", "TEST.pdf", i + 1, f"원본 근거 {i + 1}", "", "0" * 64),
        )
        for arm in ["A", "B"]:
            uid = f"{arm}:{i:03}"
            connection.execute(
                "INSERT INTO units VALUES (?,?,?,?,?)",
                (uid, arm, "record", f"검색 표현 근거 {i + 1}", ""),
            )
            connection.execute(
                "INSERT INTO occurrences VALUES (?,?,?,?,?,?,?)",
                (uid, sid, 1, 1, "EVT-01" if i < 6 else "EVT-02", "기록", "[]"),
            )
    for code, case in [
        ("EVT-01", "EVT-01"),
        ("EQUIP-01", "EVT-01"),
        ("EVT-02", "EVT-02"),
        ("EQUIP-02", "EVT-02"),
        ("SHARED-01", "EVT-01"),
        ("SHARED-01", "EVT-02"),
    ]:
        connection.execute(
            "INSERT INTO code_links VALUES (?,?,?,?,?,?,?)",
            (code, "explicit", case, "TEST:p0001", 1, 1, "labeled"),
        )
    connection.commit()
    connection.close()
    return Store(tmp_path, vectors=False)


def test_code_normalization_preserves_identity():
    assert codes_in("ｅｖｔ－００１, EVT – 001-A") == ["EVT-001", "EVT-001-A"]
    assert "evt-001-a" in code_tokenize("EVT-001-A의 근거")
    assert codes_in("20GP, 2029-01-01, 001") == []
    assert code_tokenize("EVT-001 EVT-001").count("evt-001") == 2


def test_exact_relations_do_not_turn_partial_or_shared_codes_into_one_case(tmp_path):
    store = tiny_store(tmp_path)
    result = store.relation("equip-01 기록")
    assert result["cases"] == ["EVT-01"]
    assert all(uid.startswith("B:") for uid in result["proposed_units"])
    assert store.relation("SHARED-01 기록")["status"] == "ambiguous"
    assert store.relation("EQUIP-0 기록")["status"] == "unknown_code"
    assert store.relation("20GP 기록")["cases"] == []


def test_two_cases_need_explicit_comparison_and_do_not_merge_into_one(tmp_path):
    store = tiny_store(tmp_path)
    conflict = store.relation("EVT-01의 EQUIP-02 기록")
    assert conflict["status"] == "conflict"
    assert conflict["proposed_units"] == []
    comparison = store.relation("EVT-01과 EVT-02 기록 비교")
    assert comparison["status"] == "multiple_targets"
    assert comparison["cases"] == ["EVT-01", "EVT-02"]
    assert len(comparison["proposed_units"]) == 8
    assert comparison["proposed_units"][:2] == ["B:000", "B:006"]
    assert store.relation("EVT-01 말고 EVT-02")["proposed_units"] == []


@pytest.mark.parametrize("oversized", [False, True])
def test_one_rerank_and_full_original_pages_with_budget(
    tmp_path, monkeypatch, oversized
):
    store = tiny_store(tmp_path)
    if oversized:
        store.pages["TEST:p0012"]["text"] = "원본 근거 " * 10000
    store.arm_vectors = {
        arm: np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(ids), 1))
        for arm, ids in store.arm_indices.items()
    }
    calls = []

    def predict(model, question, texts, device):
        calls.append((question, len(texts)))
        return np.arange(len(texts), dtype=np.float32), {
            "rerank_pairs": len(texts),
            "rerank_tokens": 20,
            "rerank_truncated": 0,
        }

    monkeypatch.setattr(engine, "predict_scores", predict)
    retriever = engine.Engine.__new__(engine.Engine)
    retriever.device = "cpu"
    retriever.store = store
    retriever.keyword = {
        arm: BM25Index([store.units[i] for i in indices], tokenizer=code_tokenize)
        for arm, indices in store.arm_indices.items()
    }
    retriever.embedder = SimpleNamespace(
        encode_query=lambda *args, **kwargs: np.array([[1.0, 0.0]], dtype=np.float32),
        tokenizer=lambda texts, **kwargs: {"input_ids": [[1, 2] for _ in texts]},
    )
    retriever.reranker = None
    retriever.context_tokenizer = tiktoken.get_encoding("cl100k_base")
    trace, context = retriever.retrieve({"id": "t", "question": "근거"}, "B")
    assert calls == [("근거", 12)]
    assert len(trace["final_pages"]) == 8
    assert trace["context_tokens"] <= 8000
    assert len({p["source_id"] for p in trace["final_pages"]}) == 8
    assert all(p["text"].startswith("원본 근거") for p in context["pages"])
    assert not any("검색 표현" in p["text"] for p in context["pages"])
    if oversized:
        assert "TEST:p0012" in trace["excluded_sources"]
        assert all(p["page"] != 12 for p in context["pages"])


def test_retrieval_rejects_reference_fields(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text('[{"id":"q","question":"질문","answer":"정답"}]')
    with pytest.raises(ValueError, match="only"):
        load_questions(path)


def test_common_occurrence_prefers_verified_target_without_adding_rule_to_relations(
    tmp_path,
):
    store = tiny_store(tmp_path)
    store.occurrences["common"] = [
        {"source_id": "TEST:p0001", "case_id": None},
        {"source_id": "TEST:p0007", "case_id": None},
    ]
    assert store.occurrence("common", "규칙")["source_id"] == "TEST:p0001"
    assert store.occurrence("common", "규칙", ["EVT-02"])["source_id"] == "TEST:p0007"
    assert "common" not in store.relation("EVT-02")["proposed_units"]
