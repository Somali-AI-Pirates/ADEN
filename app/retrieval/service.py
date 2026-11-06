"""retrieve() 단일 진입점.

resources에 필요한 속성 (B의 app.service.resources.load_resources()가 채운다):
    embedder    app.indexing.embeddings.Embedder
    qdrant      app.indexing.vector_store.open_vector_index()가 돌려준 클라이언트
    collection  컬렉션 이름 (artifacts/basic/index_manifest.json의 vector_store.collection)
선택 속성 (없으면 처음 쓸 때 A가 만들어 프로세스 안에 보관한다):
    keyword_index  app.indexing.keyword_store.KeywordIndex  (hybrid, hybrid_rerank)
                   없으면 Qdrant 컬렉션의 청크로 만든다. 1초 안팎
    reranker       app.retrieval.reranker.Reranker          (hybrid_rerank)
                   없으면 BAAI/bge-reranker-v2-m3를 읽는다. 처음 한 번 수십 초 걸리므로
                   서버 시작 때 미리 넣어 두거나 retrieve()를 한 번 불러 두면 좋다.

방식별 흐름 (docs/design/architecture.md 6장):
    naive          Dense 상위 top_n
    hybrid         Dense 20 + BM25 20 → RRF → 상위 top_n
    hybrid_rerank  hybrid 후보 20 → 리랭커 재정렬 → 상위 top_n

동기 함수다. 현재 비동기 API는 이 함수를 호출하는 answer_query() 전체를
asyncio.to_thread로 실행해 이벤트 루프를 막지 않는다.
"""
import threading

from app.indexing.keyword_store import load_keyword_index
from app.retrieval.dense import dense_search
from app.retrieval.hybrid import CANDIDATES, hybrid_search
from app.retrieval.reranker import Reranker, rerank

MODES = ("naive", "hybrid", "hybrid_rerank")

_lock = threading.Lock()
_cache = {}


def _cached(key, make):
    with _lock:  # 여러 요청이 동시에 처음 부를 때 한 번만 만든다
        if key not in _cache:
            _cache[key] = make()
        return _cache[key]


def keyword_index_for(resources):
    index = getattr(resources, "keyword_index", None)
    if index is not None:
        return index
    return _cached(("bm25", resources.collection),
                   lambda: load_keyword_index(resources.qdrant, resources.collection))


def reranker_for(resources):
    model = getattr(resources, "reranker", None)
    if model is not None:
        return model
    return _cached(("reranker",), Reranker)


def retrieve(question, mode, resources, top_n=20):
    """순서 있는 후보 RetrievedChunk 목록 (최대 top_n개).

    최종 k개 선택과 토큰 예산은 generation.build_context()가 맡는다.
    """
    if mode == "naive":
        return dense_search(question, resources, top_n)
    if mode == "hybrid":
        return hybrid_search(question, resources, top_n, keyword_index_for(resources))
    if mode == "hybrid_rerank":
        candidates = hybrid_search(question, resources, CANDIDATES, keyword_index_for(resources))
        return rerank(question, candidates, reranker_for(resources), top_n)
    raise ValueError(f"알 수 없는 검색 방식: {mode} (가능: {', '.join(MODES)})")
