"""Hybrid 검색: Dense 후보와 BM25 후보를 RRF로 합친다.

두 점수는 크기와 의미가 달라 그대로 더하지 않는다. 각 목록의 순위만 써서
1 / (RRF_K + 순위)를 더하고, 같은 chunk_id는 한 번만 남긴다.
"""

from app.retrieval.dense import dense_search
from shared.contracts import RetrievedChunk

RRF_K = 60
CANDIDATES = 20  # Dense·BM25 각각의 후보 수 (Naive와 같은 20)


def rrf_merge(ranked_lists, top_n, k=RRF_K):
    """여러 순위 목록(각각 Chunk 목록)을 RRF 점수로 합친다. 동점은 chunk_id 순."""
    scores, chunk_of = {}, {}
    for chunks in ranked_lists:
        for rank, chunk in enumerate(chunks, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            chunk_of.setdefault(chunk.chunk_id, chunk)
    order = sorted(scores, key=lambda cid: (-scores[cid], cid))[:top_n]
    return [
        RetrievedChunk(
            chunk=chunk_of[cid], rank=rank, score=scores[cid], retriever="hybrid"
        )
        for rank, cid in enumerate(order, start=1)
    ]


def hybrid_search(question, resources, top_n, keyword_index, candidates=CANDIDATES):
    dense = [item.chunk for item in dense_search(question, resources, candidates)]
    keyword = [chunk for chunk, _ in keyword_index.search(question, candidates)]
    return rrf_merge([dense, keyword], top_n)
