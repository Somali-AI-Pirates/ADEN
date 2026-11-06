"""검색 세 방식의 계약 확인. 실제 모델·Qdrant 없이 가짜 객체로 돈다.

    python -m pytest -q tests/retrieval
"""
import unittest
from types import SimpleNamespace

from app.indexing.keyword_store import KeywordIndex, tokenize
from app.retrieval.hybrid import rrf_merge
from app.retrieval.reranker import rerank
from app.retrieval.service import retrieve
from shared.contracts import Chunk, RetrievedChunk


def chunk(cid, text):
    book, page, _ = cid.split("-")
    return Chunk(chunk_id=cid, book_id=book, file_name=f"{book}.pdf", page=int(page[1:]), text=text)


CHUNKS = [
    chunk("DEMO1-p1-c1", "예약 접수번호는 예약 확정이 아니다."),
    chunk("DEMO1-p2-c1", "VGM 마감은 예약 확정 안내의 별도 마감을 따른다."),
    chunk("DEMO2-p1-c1", "20GP와 40GP 장비 안내. 무료기간과 보관료는 이 자료에 없다."),
]


class FakeEmbedder:
    def embed_query(self, text):
        return SimpleNamespace(tolist=lambda: [0.0])


class FakeQdrant:
    """Dense는 CHUNKS 순서를 그대로 돌려준다."""

    def query_points(self, collection, query, limit, with_payload):
        points = [SimpleNamespace(payload=c.__dict__, score=1.0 - i / 10) for i, c in enumerate(CHUNKS)]
        return SimpleNamespace(points=points[:limit])


class FakeReranker:
    """'보관료'가 들어 있으면 높은 점수."""

    def score(self, question, texts):
        return [1.0 if "보관료" in t else 0.0 for t in texts]


def resources(**extra):
    return SimpleNamespace(embedder=FakeEmbedder(), qdrant=FakeQdrant(), collection="demo",
                           keyword_index=KeywordIndex(CHUNKS), reranker=FakeReranker(), **extra)


class TokenizeTest(unittest.TestCase):
    def test_korean_bigrams_and_codes(self):
        self.assertEqual(tokenize("보관료 20GP"), ["보관", "관료", "20gp"])

    def test_same_pieces_with_or_without_space(self):
        self.assertTrue(set(tokenize("준비 상태")) <= set(tokenize("준비상태")))


class KeywordIndexTest(unittest.TestCase):
    def test_exact_terms_rank_first(self):
        top = KeywordIndex(CHUNKS).search("보관료는 얼마인가요", 3)
        self.assertEqual(top[0][0].chunk_id, "DEMO2-p1-c1")

    def test_no_overlap_returns_nothing(self):
        self.assertEqual(KeywordIndex(CHUNKS).search("zzz", 3), [])


class RrfTest(unittest.TestCase):
    def test_merges_by_rank_and_dedups(self):
        a, b, c = CHUNKS
        merged = rrf_merge([[a, b], [b, c]], top_n=10)
        self.assertEqual([m.chunk.chunk_id for m in merged], [b.chunk_id, a.chunk_id, c.chunk_id])
        self.assertEqual([m.rank for m in merged], [1, 2, 3])
        self.assertTrue(all(m.retriever == "hybrid" for m in merged))


class RerankTest(unittest.TestCase):
    def test_reorders_by_score_keeps_chunk(self):
        cands = [RetrievedChunk(chunk=c, rank=i, score=0.0, retriever="hybrid") for i, c in enumerate(CHUNKS, 1)]
        out = rerank("보관료", cands, FakeReranker(), top_n=2)
        self.assertEqual(out[0].chunk.chunk_id, "DEMO2-p1-c1")
        self.assertEqual([o.rank for o in out], [1, 2])
        self.assertEqual(out[0].retriever, "reranker")


class RetrieveTest(unittest.TestCase):
    def test_three_modes_return_nested_retrieved_chunks(self):
        for mode, name in [("naive", "dense"), ("hybrid", "hybrid"), ("hybrid_rerank", "reranker")]:
            out = retrieve("보관료", mode, resources(), top_n=3)
            self.assertTrue(out and all(isinstance(o, RetrievedChunk) for o in out), mode)
            self.assertEqual(out[0].retriever, name)
            self.assertEqual([o.rank for o in out], list(range(1, len(out) + 1)))

    def test_rerank_puts_relevant_first(self):
        self.assertEqual(retrieve("보관료", "hybrid_rerank", resources(), top_n=3)[0].chunk.chunk_id, "DEMO2-p1-c1")

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            retrieve("질문", "bm25_only", resources())


if __name__ == "__main__":
    unittest.main()
