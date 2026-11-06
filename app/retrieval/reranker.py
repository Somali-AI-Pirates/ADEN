"""Hybrid 후보를 cross-encoder로 다시 정렬한다.

질문과 청크를 한 쌍씩 함께 읽어 관련도를 매긴다. 후보 전체를 다시 읽으므로
Dense·BM25보다 느리지만 순서가 더 정확하다. 입력 텍스트는 색인과 같은 index_text다.
"""
from app.ingestion.chunker import index_text
from shared.contracts import RetrievedChunk

DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"  # 다국어(한국어 포함) cross-encoder
MAX_TOKENS = 512


class Reranker:
    def __init__(self, model_id=DEFAULT_RERANKER, device=None):
        from sentence_transformers import (
            CrossEncoder,  # import 시 모델을 읽지 않도록 안에서
        )

        self.model_id = model_id
        self.model = CrossEncoder(model_id, device=device)
        self.model.max_seq_length = MAX_TOKENS

    def score(self, question, texts, batch_size=8):
        return [float(s) for s in self.model.predict([(question, t) for t in texts], batch_size=batch_size)]


def rerank(question, candidates, reranker, top_n):
    """후보(RetrievedChunk 목록)를 리랭커 점수 순으로. 동점은 원래 순위 순."""
    scores = reranker.score(question, [index_text(item.chunk) for item in candidates])
    order = sorted(zip(candidates, scores), key=lambda x: (-x[1], x[0].rank))[:top_n]
    return [RetrievedChunk(chunk=item.chunk, rank=rank, score=score, retriever="reranker")
            for rank, (item, score) in enumerate(order, start=1)]
