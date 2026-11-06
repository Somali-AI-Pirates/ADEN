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
from shared.exceptions import ServiceError
from shared.settings import Settings


@dataclass
class Resources:
    embedder: Embedder
    qdrant: QdrantClient
    collection: str
    model: ChatOpenAI
    count_tokens: Callable[[str], int]
    cleanup: ExitStack
    tracer: Tracer | None = None
    retrieval: RetrievalResources = field(default_factory=RetrievalResources)

    def close(self) -> None:
        try:
            self.retrieval.clear()
        finally:
            self.cleanup.close()


def load_resources(settings: Settings) -> Resources:
    """설정 확인 → 관측 → 검색 → 생성 순서로 자원을 준비한다."""
    _require_environment()

    with ExitStack() as cleanup:
        tracer = Tracer.from_env()
        cleanup.callback(tracer.shutdown)
        embedder = Embedder(
            settings.embedding_model,
            device=settings.embedding_device,
        )
        qdrant = _connect_qdrant(settings, embedder.dim, cleanup)
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


def _require_environment() -> None:
    for name in ["OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY"]:
        if not os.environ.get(name, "").strip():
            raise ServiceError(f"서버의 {name} 설정을 확인해 주세요.", 503)


def _connect_qdrant(
    settings: Settings,
    embedding_dimension: int,
    cleanup: ExitStack,
) -> QdrantClient:
    """연결을 즉시 정리 대상으로 등록하고 기존 색인의 호환성을 검사한다."""
    qdrant = QdrantClient(
        url=os.environ["QDRANT_URL"].strip(),
        api_key=os.environ["QDRANT_API_KEY"].strip(),
        timeout=20,
        check_compatibility=False,
    )
    cleanup.callback(qdrant.close)
    if not qdrant.collection_exists(settings.qdrant_collection):
        raise ServiceError(
            "설정한 검색 색인이 없습니다. 색인 준비와 컬렉션 이름을 확인해 주세요.",
            503,
        )
    info = qdrant.get_collection(settings.qdrant_collection)
    if getattr(info.config.params.vectors, "size", None) != embedding_dimension:
        raise ServiceError("검색 색인과 질문 임베딩의 차원을 확인해 주세요.", 503)
    return qdrant


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
