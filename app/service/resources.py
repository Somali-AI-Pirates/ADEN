import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken
from langchain_openai import ChatOpenAI
from openai import OpenAI
from qdrant_client import QdrantClient

from app.indexing.embeddings import Embedder
from app.observability.tracing import Tracer
from app.retrieval.resources import RetrievalResources
from shared.contracts import ContextBundle, RetrievedChunk
from shared.exceptions import ServiceError
from shared.settings import Settings

# (질문, 검색 방식) → (후보, 최종 문맥). 문맥이 None이면 서비스가 공통 build_context로 만든다.
EvidenceProvider = Callable[[str, str], tuple[list[RetrievedChunk], ContextBundle | None]]


@dataclass
class Resources:
    embedder: Embedder | None
    qdrant: QdrantClient | None
    collection: str
    model: ChatOpenAI
    count_tokens: Callable[[str], int]
    cleanup: ExitStack
    tracer: Tracer | None = None
    retrieval: RetrievalResources = field(default_factory=RetrievalResources)
    evidence_provider: EvidenceProvider | None = None

    def close(self) -> None:
        try:
            self.retrieval.clear()
        finally:
            self.cleanup.close()


def load_resources(settings: Settings, *, qdrant_client: QdrantClient | None = None) -> Resources:
    """로컬 검색과 생성을 준비한다. 전달한 Qdrant 클라이언트의 종료도 맡는다."""
    _require_environment()

    with ExitStack() as cleanup:
        if qdrant_client is not None:
            cleanup.callback(qdrant_client.close)
        tracer = Tracer.from_env()
        cleanup.callback(tracer.shutdown)
        embedder = Embedder(
            settings.embedding_model,
            device=settings.embedding_device,
        )
        qdrant = qdrant_client
        if qdrant is None:
            qdrant = _connect_qdrant(settings, embedder.dim, cleanup)
        else:
            _validate_qdrant(qdrant, settings, embedder.dim)
        model = _create_generation_model(settings, cleanup)
        count_tokens = _create_token_counter(settings.context_tokenizer)

        resources = Resources(
            embedder=embedder,
            qdrant=qdrant,
            collection=settings.qdrant_collection,
            model=model,
            count_tokens=count_tokens,
            cleanup=cleanup,
            tracer=tracer,
        )
        # 반환 객체의 조립까지 성공했을 때만 종료 책임을 넘긴다.
        resources.cleanup = cleanup.pop_all()
        return resources


def load_generation_resources(
    settings: Settings, *,
    evidence_provider: EvidenceProvider,
) -> Resources:
    """A를 호출하지 않고 준비된 후보(와 최종 문맥)로 생성과 평가를 연결한다."""
    if not callable(evidence_provider):
        raise ValueError("후보(와 최종 문맥)를 반환하는 evidence_provider가 필요합니다.")
    _require_environment()
    with ExitStack() as cleanup:
        tracer = Tracer.from_env()
        cleanup.callback(tracer.shutdown)
        model = _create_generation_model(settings, cleanup)
        count_tokens = _create_token_counter(settings.context_tokenizer)
        resources = Resources(
            embedder=None, qdrant=None, collection=settings.qdrant_collection,
            model=model, count_tokens=count_tokens, cleanup=cleanup,
            tracer=tracer, evidence_provider=evidence_provider,
        )
        resources.cleanup = cleanup.pop_all()
        return resources


def _require_environment() -> None:
    for name in ["OPENAI_API_KEY"]:
        if not os.environ.get(name, "").strip():
            raise ServiceError(f"서버의 {name} 설정을 확인해 주세요.", 503)


def _connect_qdrant(
    settings: Settings,
    embedding_dimension: int,
    cleanup: ExitStack,
) -> QdrantClient:
    """연결을 즉시 정리 대상으로 등록하고 기존 색인의 호환성을 검사한다."""
    qdrant = QdrantClient(location=":memory:")
    cleanup.callback(qdrant.close)
    _validate_qdrant(qdrant, settings, embedding_dimension)
    return qdrant


def _validate_qdrant(qdrant, settings, embedding_dimension):
    if not qdrant.collection_exists(settings.qdrant_collection):
        raise ServiceError(
            "로컬 검색 색인이 없습니다. 같은 실행에서 적재한 Qdrant 클라이언트나 준비된 문맥을 연결해 주세요.",
            503,
        )
    info = qdrant.get_collection(settings.qdrant_collection)
    if getattr(info.config.params.vectors, "size", None) != embedding_dimension:
        raise ServiceError("검색 색인과 질문 임베딩의 차원을 확인해 주세요.", 503)


def _create_generation_model(settings: Settings, cleanup: ExitStack) -> ChatOpenAI:
    """SDK 클라이언트를 생성 모델과 공유하고 재시도는 서비스 계층에 맡긴다."""
    openai_client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=settings.model_timeout_seconds,
        max_retries=0,
    )

    cleanup.callback(openai_client.close)

    return ChatOpenAI(
        model=settings.generation_model,
        api_key=os.environ["OPENAI_API_KEY"],
        client=openai_client.chat.completions,
        root_client=openai_client,
        timeout=settings.model_timeout_seconds,
        max_retries=0,
        max_tokens=settings.max_output_tokens,
    )


def _create_token_counter(tokenizer: str) -> Callable[[str], int]:
    """문맥 예산용 로컬 추정값. 실제 사용 토큰은 모델 응답에서 기록한다."""
    encoding = tiktoken.get_encoding(tokenizer)

    @lru_cache(maxsize=1024)
    def count_tokens(text: str) -> int:
        return len(encoding.encode(text)) if text else 0

    return count_tokens
