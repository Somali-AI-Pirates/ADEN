"""B·C 연결 검증용 자원 공급 함수. 기존 고정 근거 import는 공통 공급기로 연결하며, 메모리 복사는 개인 검증 도구다.

공통 자원 로더(app.service.resources.load_resources)가 환경변수의 '모듈:이름'으로 불러 쓴다.
API와 평가 CLI에 같은 값을 주면 둘이 같은 자원으로 실행된다.

- ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy
  Qdrant Cloud의 기존 컬렉션(벡터·payload) 전체를 메모리 Qdrant로 복사해 실제 검색을 실행한다
  (QDRANT_URL·QDRANT_API_KEY 필요). 프로세스가 끝나면 메모리 색인은 사라진다.
- ADEN_EVIDENCE_PROVIDER=experimental.Damsebi.evidence_sources:stored_evidence
  ADEN_EVIDENCE_RUNS=<runs.jsonl>에 저장된 후보를 (질문, 검색 방식)으로 재생한다. 임베더·Qdrant를
  만들지 않는다. ADEN_EVIDENCE_CONTEXT=1이면 최종 문맥까지 넘긴다(서비스가 문맥을 다시 고르지 않는 경로).
  고정 근거 B·C 검증이며 검색 방식의 성능 비교가 아니다.
"""
import os

from qdrant_client import QdrantClient, models

from app.service.evidence import stored_evidence, stored_provider  # noqa: F401 - 기존 호출 경로 호환
from shared.exceptions import ServiceError


def memory_copy(settings):
    """ADEN_QDRANT_CLIENT 대상. Qdrant Cloud의 기존 컬렉션(벡터·payload)을 메모리 Qdrant로 복사한다.
    반환한 클라이언트는 자원 로더가 검사하고 종료한다."""
    name = settings.qdrant_collection
    url, key = (os.environ.get(n, "").strip() for n in ("QDRANT_URL", "QDRANT_API_KEY"))
    if not (url and key):
        raise ServiceError("복사할 Qdrant Cloud의 QDRANT_URL·QDRANT_API_KEY 설정을 확인해 주세요.", 503)
    source = QdrantClient(url=url, api_key=key, timeout=20, check_compatibility=False)
    try:
        if not source.collection_exists(name):
            raise ServiceError(f"Qdrant Cloud에 복사할 컬렉션 '{name}'이 없습니다.", 503)
        vectors = source.get_collection(name).config.params.vectors
        memory = QdrantClient(location=":memory:")
        memory.create_collection(name, vectors_config=models.VectorParams(
            size=vectors.size, distance=vectors.distance))
        offset = None
        while True:
            points, offset = source.scroll(name, limit=256, offset=offset,
                                           with_payload=True, with_vectors=True)
            memory.upsert(name, points=[models.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                                        for p in points])
            if offset is None:
                return memory
    finally:
        source.close()
