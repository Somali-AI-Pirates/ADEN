"""B·C 연결 검증용 자원 공급 함수. 개인 검증 도구이며 공통 코드는 이 모듈에 의존하지 않는다.

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
import json
import os
from dataclasses import fields
from pathlib import Path

import tiktoken
from qdrant_client import QdrantClient, models

from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk
from shared.exceptions import ServiceError

CHUNK_FIELDS = [f.name for f in fields(Chunk)]


def stored_provider(runs_path, settings, *, with_context=False):
    """저장된 실행 기록의 후보를 그대로 돌려주는 evidence_provider."""
    lines = Path(runs_path).read_text(encoding="utf-8").splitlines()
    stored = {}
    for run in (json.loads(line) for line in lines if line.strip()):
        stored.setdefault(run["question"], {})[run["retrieval_mode"]] = run
    encoding = tiktoken.get_encoding(settings.context_tokenizer)

    def provider(question, mode):
        run = stored[question].get(mode)  # 저장되지 않은 질문은 KeyError → 서비스가 503으로 알린다
        if run is None or run.get("retrieved_candidates") is None:
            raise NotImplementedError(f"저장된 후보가 없는 검색 방식: {mode}")
        candidates = [
            RetrievedChunk(Chunk(**{k: c[k] for k in CHUNK_FIELDS if k in c}),
                           c["rank"], c["score"], c["retriever"])
            for c in run["retrieved_candidates"]
        ]
        if not with_context:
            return candidates, None
        by_id = {c.chunk.chunk_id: c for c in candidates}
        final = [by_id[c["chunk_id"]] for c in run["final_context_chunks"]]
        return candidates, build_context(final, settings.retrieval.k,
                                         settings.retrieval.context_token_budget,
                                         lambda text: len(encoding.encode(text)))

    return provider


def stored_evidence(settings):
    """ADEN_EVIDENCE_PROVIDER 대상. 재생할 기록은 ADEN_EVIDENCE_RUNS로 고른다."""
    runs_path = os.environ.get("ADEN_EVIDENCE_RUNS", "").strip()
    if not runs_path or not Path(runs_path).is_file():
        raise ServiceError("ADEN_EVIDENCE_RUNS에 재생할 실행 기록(runs.jsonl) 경로를 지정해 주세요.", 503)
    return stored_provider(runs_path, settings,
                           with_context=bool(os.environ.get("ADEN_EVIDENCE_CONTEXT")))


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
