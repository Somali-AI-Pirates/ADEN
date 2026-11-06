import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache

import tiktoken
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI
from qdrant_client import QdrantClient

from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG, Settings
from member_a.indexing.embeddings import Embedder
from member_a.indexing.vector_store import open_vector_index
from member_c.observability.tracing import Tracer


@dataclass
class Resources:
    embedder: Embedder
    qdrant: QdrantClient
    collection: str
    model: ChatOpenAI
    count_tokens: Callable[[str], int]
    cleanup: ExitStack
    tracer: Tracer

    def close(self):
        self.cleanup.close()


def load_resources(settings: Settings) -> Resources:
    load_dotenv(DEFAULT_CONFIG.parent.parent / ".env", override=False)
    for name in ["OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY"]:
        if not os.environ.get(name, "").strip():
            raise ServiceError(f"서버의 {name} 설정을 확인해 주세요.", 503)

    with ExitStack() as cleanup:
        tracer = Tracer.from_env()
        cleanup.callback(tracer.shutdown)
        embedder = Embedder(settings.embedding_model, device=settings.embedding_device)
        try:
            qdrant = open_vector_index(
                settings.qdrant_collection, timeout=20, check_compatibility=False,
            )
        except LookupError as error:
            raise ServiceError("설정한 검색 색인이 없습니다. A의 색인 준비와 컬렉션 이름을 확인해 주세요.", 503) from error
        cleanup.callback(qdrant.close)
        info = qdrant.get_collection(settings.qdrant_collection)
        if getattr(info.config.params.vectors, "size", None) != embedder.dim:
            raise ServiceError("검색 색인과 질문 임베딩의 차원을 확인해 주세요.", 503)
        counter = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        )
        cleanup.callback(counter.close)
        model = ChatOpenAI(
            model=settings.generation_model,
            api_key=os.environ["OPENAI_API_KEY"],
            client=counter.chat.completions,
            root_client=counter,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
            max_tokens=settings.max_output_tokens,
        )

        # 문맥 예산 확인용 로컬 토큰 계산. 요청마다 원격 API를 반복 호출하지 않는다.
        # 생성 모델의 실제 토큰 수와 약간 다를 수 있으며, 실제 사용량은 모델 응답의 usage로 기록한다.
        encoding = tiktoken.get_encoding("o200k_base")

        @lru_cache(maxsize=1024)
        def count_tokens(text: str) -> int:
            return len(encoding.encode(text)) if text else 0

        return Resources(
            embedder=embedder,
            qdrant=qdrant,
            collection=settings.qdrant_collection,
            model=model,
            count_tokens=count_tokens,
            cleanup=cleanup.pop_all(),
            tracer=tracer,
        )
