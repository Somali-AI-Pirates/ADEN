from types import SimpleNamespace

import numpy as np
import pytest
from langchain_core.documents import Document

from app.rag.corpus import TableRagCorpus
from app.rag.retrieval import TableRetriever, _rrf


class FakeEmbedder:
    def encode_query(self, question, **kwargs):
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)


class FakeTokenizer:
    def __call__(self, questions, texts, **kwargs):
        return {"input_ids": [[1, 2, 3] for _ in questions]}


class FakeReranker:
    def __init__(self):
        self.tokenizer = FakeTokenizer()
        self.calls = 0

    def predict(self, pairs, **kwargs):
        self.calls += 1
        return np.arange(len(pairs), dtype=np.float32)


def _doc(source_id, unit_id, text, *, table_id=None):
    book_id, page = source_id.split(":p")
    metadata = {
        "source_id": source_id,
        "unit_id": unit_id,
        "book_id": book_id,
        "page": int(page),
        "file_name": f"{book_id}.pdf",
    }
    if table_id:
        metadata["table_id"] = table_id
    return Document(page_content=text, metadata=metadata)


def _corpus():
    page1 = _doc("AS001:p0001", "AS001:p0001:page", "예약 비용 청구 조건 기록")
    page2 = _doc("AS001:p0002", "AS001:p0002:page", "예약 비용 수납 처리 내용")
    table = _doc("AS001:p0001", "chunk-1", "계약 조건 청구 금액", table_id="table-1")
    return TableRagCorpus(
        documents=(page1, page2, table),
        pages_by_source={
            page1.metadata["source_id"]: page1,
            page2.metadata["source_id"]: page2,
        },
        vectors=np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.float32),
        table_store=SimpleNamespace(
            by_page={("AS001", 1): ["table-1"], ("AS001", 2): ["table-2"]}
        ),
        embedding={
            "model_id": "test/embedder",
            "revision": "test-revision",
            "dimensions": 3,
            "query_prefix": "query: ",
            "query_max_length": 16,
        },
        page_count=2,
    )


def test_rrf_uses_5_5_constant_60_and_one_candidate_per_page():
    page1 = _doc("AS001:p0001", "AS001:p0001:page", "예약 비용 청구 조건 기록")
    table1 = _doc("AS001:p0001", "chunk-1", "계약 조건 청구 금액", table_id="table-1")
    page2 = _doc("AS001:p0002", "AS001:p0002:page", "수납 처리 내용")

    fused = _rrf(
        [(page1, 1.0), (page2, 0.5)],
        [(table1, 0.9), (page2, 0.4)],
    )

    assert len(fused) == 2
    assert len({document.metadata["source_id"] for document, _ in fused}) == 2
    scores_by_source = {
        document.metadata["source_id"]: score for document, score in fused
    }
    assert scores_by_source["AS001:p0002"] == pytest.approx(0.5 / 62 + 0.5 / 62)


def test_search_runs_one_rerank_and_returns_page_deduplicated_search_result():
    corpus = _corpus()
    reranker = FakeReranker()
    retriever = TableRetriever(
        corpus,
        embedder=FakeEmbedder(),
        reranker=reranker,
    )

    result = retriever.search("예약 비용 청구")

    assert len(result.pages) == 2
    assert len({page.metadata["source_id"] for page in result.pages}) == 2
    assert [page.metadata["source_id"] for page in result.pages] == [
        "AS001:p0002",
        "AS001:p0001",
    ]
    assert reranker.calls == 1
    assert result.diagnostics["reranker"]["pairs"] == 2
    assert result.diagnostics["ordered_source_ids"] == [
        "AS001:p0002",
        "AS001:p0001",
    ]
    assert set(result.table_ids) == {"table-1", "table-2"}
    assert retriever.manifest["retrieval"]["rrf"] == {
        "weights": [0.5, 0.5],
        "constant": 60,
    }
    assert retriever.manifest["retrieval"]["native_hg_enrichment"] is False


def test_remote_dense_provider_preserves_hybrid_and_reranking():
    from app.rag.retrieval import rank_dense

    corpus = _corpus()

    class RemoteDense:
        manifest = {"backend": "qdrant", "exact": True}
        closed = 0
        queries = []

        def search(self, vector, *, top_k):
            self.queries.append((vector.copy(), top_k))
            return rank_dense(corpus, vector, top_k=top_k)

        def close(self):
            self.closed += 1

    remote = RemoteDense()
    local_retriever = TableRetriever(
        corpus, embedder=FakeEmbedder(), reranker=FakeReranker(),
    )
    cloud_reranker = FakeReranker()
    cloud_retriever = TableRetriever(
        corpus, embedder=FakeEmbedder(), reranker=cloud_reranker, dense_index=remote,
    )
    local = local_retriever.search("예약 비용 청구")
    cloud = cloud_retriever.search("예약 비용 청구")
    assert cloud.pages == local.pages
    assert cloud.table_ids == local.table_ids
    for stage in ("bm25_top", "dense_top", "rrf_candidates", "reranker", "ordered_source_ids"):
        assert cloud.diagnostics[stage] == local.diagnostics[stage]
    assert cloud.diagnostics["dense_backend"] == remote.manifest
    assert cloud_reranker.calls == 1
    assert remote.queries[0][1] == 50
    cloud_retriever.close()
    cloud_retriever.close()
    assert remote.closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        cloud_retriever.search("예약 비용 청구")


def test_remote_dense_failure_does_not_fall_back_to_local_vectors():
    class UnavailableDense:
        manifest = {"backend": "qdrant", "exact": True}

        def search(self, vector, *, top_k):
            raise RuntimeError("Qdrant unavailable")

    reranker = FakeReranker()
    retriever = TableRetriever(
        _corpus(), embedder=FakeEmbedder(), reranker=reranker,
        dense_index=UnavailableDense(),
    )
    with pytest.raises(RuntimeError, match="Qdrant unavailable"):
        retriever.search("예약 비용 청구")
    assert reranker.calls == 0
