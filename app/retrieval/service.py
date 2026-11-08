"""retrieve() 단일 진입점.

resources에 필요한 속성 (app.service.resources.load_resources()가 채운다):
    embedder    app.indexing.embeddings.Embedder
    qdrant      app.indexing.vector_store.open_vector_index()가 돌려준 클라이언트
    collection  컬렉션 이름 (artifacts/basic/index_manifest.json의 vector_store.collection)
    retrieval   이 실행 자원이 소유하는 RetrievalResources.
                BM25·리랭커는 첫 사용 때 한 번 준비하고 종료 때 참조를 해제한다.

방식별 흐름 (docs/design/architecture.md):
    naive          Dense 상위 top_n
    hybrid         Dense 20 + BM25 20 → RRF → 상위 top_n
    hybrid_rerank  hybrid 후보 20 → 리랭커 재정렬 → 상위 top_n

동기 함수다. FastAPI는 이 함수를 사용하는 동기 /query 라우트를 작업 스레드에서 실행한다.
"""

from app.retrieval.dense import dense_search
from app.retrieval.hybrid import CANDIDATES, hybrid_search
from app.retrieval.reranker import rerank

MODES = ("naive", "hybrid", "hybrid_rerank")


def retrieve(question, mode, resources, top_n=20):
    """순서 있는 후보 RetrievedChunk 목록 (최대 top_n개).

    최종 k개 선택과 토큰 예산은 generation.build_context()가 맡는다.
    """
    if mode == "naive":
        return dense_search(question, resources, top_n)
    if mode == "hybrid":
        return hybrid_search(
            question,
            resources,
            top_n,
            resources.retrieval.keyword_index_for(
                resources.qdrant, resources.collection
            ),
        )
    if mode == "hybrid_rerank":
        candidates = hybrid_search(
            question,
            resources,
            CANDIDATES,
            resources.retrieval.keyword_index_for(
                resources.qdrant, resources.collection
            ),
        )
        return rerank(question, candidates, resources.retrieval.reranker_for(), top_n)
    raise ValueError(f"알 수 없는 검색 방식: {mode} (가능: {', '.join(MODES)})")
