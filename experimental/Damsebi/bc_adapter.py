"""PR #40의 연결 경계(evidence_provider, 메모리 Qdrant)로 C 평가 CLI를 실행하는 어댑터. 개인 도구다.

    python -m app.evaluation.scripts.run_benchmark \
        --adapter experimental.Damsebi.bc_adapter:query_adapter --config configs/basic.yaml ...

근거 공급 방식은 환경변수로 고른다.
- ADEN_EVIDENCE_RUNS=<runs.jsonl>: 저장된 후보를 (질문, 검색 방식)으로 재생한다. 임베더·Qdrant를 만들지 않는다.
  고정 근거 B·C 검증이며 검색 방식의 성능 비교가 아니다.
- ADEN_EVIDENCE_CONTEXT=1: 재생할 때 최종 문맥까지 넘긴다(서비스가 문맥을 다시 고르지 않는 경로).
- ADEN_EVIDENCE_RUNS가 없으면 Qdrant Cloud 컬렉션을 메모리 Qdrant로 복사해 실제 검색을 실행한다
  (QDRANT_URL·QDRANT_API_KEY 필요). 프로세스가 끝나면 메모리 색인은 사라진다.
"""
import json
import os
from dataclasses import fields
from pathlib import Path

import tiktoken
from qdrant_client import QdrantClient, models

from app.generation.context import build_context
from app.service.bootstrap import ServiceBootstrap, _QueryAdapter
from app.service.resources import load_generation_resources, load_resources
from shared.contracts import Chunk, RetrievedChunk

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


def memory_copy(settings):
    """Qdrant Cloud의 기존 컬렉션(벡터·payload)을 메모리 Qdrant로 복사한다."""
    name = settings.qdrant_collection
    source = QdrantClient(url=os.environ["QDRANT_URL"].strip(),
                          api_key=os.environ["QDRANT_API_KEY"].strip(),
                          timeout=20, check_compatibility=False)
    try:
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


def _load(settings):
    runs_path = os.environ.get("ADEN_EVIDENCE_RUNS")
    if runs_path:
        provider = stored_provider(runs_path, settings,
                                   with_context=bool(os.environ.get("ADEN_EVIDENCE_CONTEXT")))
        return load_generation_resources(settings, evidence_provider=provider)
    return load_resources(settings, qdrant_client=memory_copy(settings))


_bootstrap = ServiceBootstrap(resources_loader=_load)
query_adapter = _QueryAdapter(_bootstrap)
configure_evaluation = _bootstrap.configure
