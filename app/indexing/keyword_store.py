"""Legacy BM25 index over Qdrant chunk payloads.

The tokenizer lives in ``shared.bm25_tokens``; its original function names
remain importable here.
"""

import math
from collections import Counter

from app.ingestion.chunker import index_text
from shared.bm25_tokens import (
    tokenize as tokenize,
    tokenize_for_bm25 as tokenize_for_bm25,
)
from shared.contracts import Chunk


class KeywordIndex:
    """BM25 (Okapi). k1·b는 널리 쓰는 기본값이다."""

    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = list(chunks)
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(index_text(c))) for c in self.chunks]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lens) / len(self.lens)
        n = len(self.chunks)
        df = Counter(t for tf in self.tfs for t in tf)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query, top_n):
        """[(Chunk, 점수)]를 점수 순으로. 질문 단어가 하나도 겹치지 않는 청크는 뺀다."""
        terms = set(tokenize(query)) & self.idf.keys()
        scored = []
        for i, tf in enumerate(self.tfs):
            norm = self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg_len)
            s = sum(
                self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm)
                for t in terms
                if t in tf
            )
            if s > 0:
                scored.append((s, self.chunks[i].chunk_id, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(self.chunks[i], s) for s, _, i in scored[:top_n]]


def load_keyword_index(client, collection):
    """Qdrant 컬렉션의 청크 전체로 BM25 색인을 만든다."""
    chunks, offset = [], None
    while True:
        points, offset = client.scroll(
            collection, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        chunks.extend(Chunk(**p.payload) for p in points)
        if offset is None:
            break
    if not chunks:
        raise LookupError(f"'{collection}' 컬렉션에 청크가 없습니다.")
    return KeywordIndex(chunks)
