"""Naive 검색: 질문 임베딩과 가장 가까운 청크."""
from app.core.contracts import Chunk, RetrievedChunk
from app.indexing.vector_store import COLLECTION


def dense_search(question, resources, top_n):
    vector = resources.embedder.embed_query(question)
    points = resources.qdrant.query_points(
        COLLECTION, query=vector.tolist(), limit=top_n, with_payload=True).points
    return [RetrievedChunk(chunk=Chunk(**p.payload), rank=rank, score=p.score, retriever="dense")
            for rank, p in enumerate(points, start=1)]
