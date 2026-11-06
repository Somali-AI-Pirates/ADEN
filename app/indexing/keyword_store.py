"""같은 청크로 만드는 BM25 키워드 색인.

토큰화 (질문과 문서에 같은 규칙):
    한글      글자 2개씩 겹쳐 자른다(bigram). "무료기간" → 무료, 료기, 기간
              PDF 줄바꿈으로 띄어쓰기가 틀린 곳("준비상태")에서도 같은 조각이 나온다.
    영문·숫자 덩어리째 둔다. 예약번호·장비 코드(20GP)·날짜 숫자를 그대로 맞춘다.

색인은 따로 저장하지 않는다. Qdrant 컬렉션의 payload(청크 전체)를 읽어 메모리에 만든다.
그래서 Dense와 BM25가 항상 같은 청크를 본다. 620개 기준 1초 안팎이다.
"""
import math
import re
from collections import Counter

from app.ingestion.chunker import index_text
from shared.contracts import Chunk

TOKEN = re.compile(r"[가-힣]+|[a-z0-9]+")


def tokenize(text):
    tokens = []
    for part in TOKEN.findall(text.lower()):
        if part.isascii() or len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
    return tokens


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
            s = sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in terms if t in tf)
            if s > 0:
                scored.append((s, self.chunks[i].chunk_id, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(self.chunks[i], s) for s, _, i in scored[:top_n]]


def load_keyword_index(client, collection):
    """Qdrant 컬렉션의 청크 전체로 BM25 색인을 만든다."""
    chunks, offset = [], None
    while True:
        points, offset = client.scroll(collection, limit=256, offset=offset,
                                       with_payload=True, with_vectors=False)
        chunks.extend(Chunk(**p.payload) for p in points)
        if offset is None:
            break
    if not chunks:
        raise LookupError(f"'{collection}' 컬렉션에 청크가 없습니다.")
    return KeywordIndex(chunks)
