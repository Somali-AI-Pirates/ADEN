import importlib
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

# '모듈:이름' 팩터리. settings를 받아 색인을 적재한 QdrantClient / evidence_provider를 반환한다.
QDRANT_CLIENT_ENV = "ADEN_QDRANT_CLIENT"
EVIDENCE_PROVIDER_ENV = "ADEN_EVIDENCE_PROVIDER"


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
    """API와 평가 CLI가 함께 쓰는 자원 준비. 전달받거나 팩터리가 만든 Qdrant 클라이언트의 종료도 맡는다.

    클라이언트를 직접 넘기지 않으면 환경변수로 지정한 연결을 쓴다.
    - ADEN_QDRANT_CLIENT: 그 메모리 Qdrant 색인으로 실제 검색과 생성을 실행한다.
    - ADEN_EVIDENCE_PROVIDER: 검색 자원 없이 근거 주입 경로를 실행한다.
    둘 다 없으면 빈 메모리 Qdrant라 색인 없음(503)으로 알린다.
    """
    _require_environment()
    if qdrant_client is None:
        provider_spec, client_spec = (os.environ.get(name, "").strip()
                                      for name in (EVIDENCE_PROVIDER_ENV, QDRANT_CLIENT_ENV))
        if provider_spec and client_spec:
            raise ServiceError(
                f"{QDRANT_CLIENT_ENV}와 {EVIDENCE_PROVIDER_ENV}는 하나만 지정해 주세요.", 503)
        if provider_spec:
            provider = _prepare(EVIDENCE_PROVIDER_ENV, provider_spec, settings)
            if not callable(provider):
                raise ServiceError(
                    f"{EVIDENCE_PROVIDER_ENV}={provider_spec}가 근거 공급 함수를 반환하지 않았습니다.", 503)
            return load_generation_resources(settings, evidence_provider=provider)
        if client_spec:
            qdrant_client = _prepare(QDRANT_CLIENT_ENV, client_spec, settings)

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


def _prepare(name: str, spec: str, settings: Settings):
    """환경변수의 '모듈:이름' 팩터리를 불러 settings로 호출한다. 실패하면 어느 지정이 문제인지 알린다."""
    module, _, attr = spec.partition(":")
    try:
        prepared = getattr(importlib.import_module(module), attr)(settings)
    except ServiceError:
        raise
    except Exception as error:
        # 오류 본문에는 경로·주소가 들어갈 수 있어 API 응답에는 종류만 알린다. 원인은 예외 연결로 남는다
        raise ServiceError(
            f"{name}={spec} 준비에 실패했습니다({type(error).__name__}). "
            "'모듈:이름' 형식과 그 함수가 쓰는 자료·환경변수를 확인해 주세요.", 503) from error
    if prepared is None:
        raise ServiceError(f"{name}={spec}가 준비한 대상을 반환하지 않았습니다.", 503)
    return prepared


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
            f"검색 색인 '{settings.qdrant_collection}'이 없습니다. {QDRANT_CLIENT_ENV}로 색인을 적재한 "
            f"Qdrant 클라이언트를, 또는 {EVIDENCE_PROVIDER_ENV}로 준비된 근거를 연결해 주세요.",
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
