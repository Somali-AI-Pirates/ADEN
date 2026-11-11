"""The legacy adapter preserves its source completion helper and trace contract."""

from types import SimpleNamespace

import numpy as np

from experimental.u_10o0o import advanced_pair_context, advanced_source_lookup
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.pattern_retrieval import legacy_baseline


def _pages():
    return [
        {
            "book_id": f"AS{index:03d}",
            "file_name": f"AS{index:03d}.pdf",
            "page": index,
            "text": f"원본 페이지 AS{index:03d} 근거 문장입니다.",
        }
        for index in range(1, 31)
    ]


class _PatternIndex:
    def __init__(self, pages):
        self.pages = pages

    def fielded(self, question, *, top_k):
        del question
        return [
            (page, float(top_k - index))
            for index, page in enumerate(self.pages[:top_k])
        ]


class _Lookup:
    def __init__(self, pages):
        first_case = "HG2026-0001"
        second_case = "HG2026-0002"
        self.cases = {
            first_case: pages[20:25],
            second_case: pages[25:30],
        }
        self.profile_index = SimpleNamespace(
            candidate=lambda question: (None, "missing_terms")
        )
        self.id_to_cases = {case: {case} for case in self.cases}
        self.id_fields = {case: {"case_id"} for case in self.cases}
        self.page_roles = {
            (page["book_id"], page["page"]): (case, role)
            for case, case_pages in self.cases.items()
            for role, page in zip(ROLES, case_pages, strict=True)
        }
        self.rules = None

    @staticmethod
    def _matched_profile_values(question):
        del question
        return {"shipper": set(), "cargo": set(), "spec": set()}


class _Storage:
    @staticmethod
    def candidates(question):
        del question
        return ()


class _Embedder:
    @staticmethod
    def encode_query(questions, **kwargs):
        assert kwargs == {
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }
        return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(questions), 1))


class _Tokenizer:
    @staticmethod
    def __call__(questions, texts, *, truncation, verbose):
        assert truncation is False
        assert verbose is False
        return {
            "input_ids": [
                [1] * max(1, (len(question) + len(text)) // 2)
                for question, text in zip(questions, texts, strict=True)
            ]
        }


class _Reranker:
    tokenizer = _Tokenizer()

    @staticmethod
    def predict(pairs, *, batch_size, show_progress_bar):
        assert batch_size == 32
        assert show_progress_bar is False
        return np.arange(len(pairs), dtype=np.float32)


def test_batch_uses_real_complete_sources_and_preserves_lookup_pages(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "manifest.json").write_text(
        '{"identity": {"dataset": "advanced"}}', encoding="utf-8"
    )
    pages = _pages()
    vectors = np.zeros((len(pages), 2), dtype=np.float32)
    vectors[:20, 0] = np.linspace(1.0, 0.81, 20)
    vectors[20:, 1] = 1.0
    lookup = _Lookup(pages)
    logged = []

    monkeypatch.setattr(legacy_baseline, "load_index", lambda: (pages, vectors))
    monkeypatch.setattr(legacy_baseline, "PatternIndex", _PatternIndex)
    monkeypatch.setattr(
        legacy_baseline, "load_target_lookup", lambda *args, **kwargs: lookup
    )
    monkeypatch.setattr(
        legacy_baseline, "load_storage_lookup", lambda *args, **kwargs: _Storage()
    )
    monkeypatch.setattr(legacy_baseline, "load_model", lambda: _Embedder())
    monkeypatch.setattr(
        legacy_baseline, "CrossEncoder", lambda *args, **kwargs: _Reranker()
    )
    monkeypatch.setattr(legacy_baseline.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(advanced_source_lookup, "_count_tokens", len)
    monkeypatch.setattr(advanced_pair_context, "_count_tokens", len)

    runner = legacy_baseline.LegacyBaseline(curated=tmp_path)
    question = {
        "id": "pair-q",
        "question": "HG2026-0001의 근거를 확인해 주세요.",
    }
    results = runner.retrieve_batch([question], logged.append)

    assert len(results) == 1
    trace, context = results[0]
    assert trace["id"] == context["id"] == question["id"]
    assert trace["arm"] == context["arm"] == "baseline"
    assert "question" not in trace
    assert len(trace["pool"]) == 20
    assert len(trace["reranked_pool"]) == 20
    assert trace["work"]["rerank_pairs"] == 20
    assert trace["routes"]["hg"] == "explicit_hg"
    assert all(
        item["unit_id"] == f"baseline:{item['source_id']}" for item in trace["pool"]
    )

    original_ids = {item["source_id"] for item in trace["pool"]}
    reranked_ids = {item["source_id"] for item in trace["reranked_pool"]}
    final_ids = {item["source_id"] for item in trace["final_pages"]}
    assert "AS021:p0021" not in original_ids | reranked_ids
    assert "AS022:p0022" not in original_ids | reranked_ids
    assert {"AS021:p0021", "AS022:p0022"} <= final_ids
    assert {item["source_id"] for item in trace["ranked"]} >= {
        "AS021:p0021",
        "AS022:p0022",
    }
    assert context["pages"]
    assert all(page["text"].startswith("원본 페이지") for page in context["pages"])
    assert trace["context_sha256"]
    assert trace["context_tokens"] > 0
    assert logged
