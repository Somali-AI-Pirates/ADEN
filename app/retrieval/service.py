"""retrieve() 단일 진입점.

resources에 필요한 속성 (B의 core.load_resources()가 채운다):
    embedder    app.indexing.embeddings.Embedder
    qdrant      app.indexing.vector_store.open_vector_index()가 돌려준 클라이언트
    collection  컬렉션 이름 (artifacts/basic/index_manifest.json의 vector_store.collection)

동기 함수다. 비동기 API에서는 asyncio.to_thread(retrieve, ...)로 호출해 이벤트 루프를 막지 않는다.
"""
from app.retrieval.dense import dense_search

MODES = ("naive", "hybrid", "hybrid_rerank")


def retrieve(question, mode, resources, top_n=20):
    """순서 있는 후보 RetrievedChunk 목록 (최대 top_n개).

    최종 k개 선택과 토큰 예산은 generation.build_context()가 맡는다.
    """
    if mode == "naive":
        return dense_search(question, resources, top_n)
    if mode in MODES:
        raise NotImplementedError(f"{mode}는 다음 PR에서 추가합니다")
    raise ValueError(f"알 수 없는 검색 방식: {mode} (가능: {', '.join(MODES)})")
