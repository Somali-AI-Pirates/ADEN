import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI
from qdrant_client import QdrantClient

from app.core.exceptions import ServiceError
from app.core.settings import DEFAULT_CONFIG, Settings
from app.indexing.embeddings import Embedder
from app.observability.tracing import Tracer


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
        qdrant = QdrantClient(
            url=os.environ["QDRANT_URL"].strip(),
            api_key=os.environ["QDRANT_API_KEY"].strip(),
            timeout=20,
            check_compatibility=False,
        )
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

        @lru_cache(maxsize=128)
        def count_tokens(text: str) -> int:
            if not text:
                return 0
            return counter.responses.input_tokens.count(
                model=settings.generation_model, input=text,
            ).input_tokens

        return Resources(
            embedder=embedder,
            qdrant=qdrant,
            collection=settings.qdrant_collection,
            model=model,
            count_tokens=count_tokens,
            cleanup=cleanup.pop_all(),
            tracer=tracer,
        )
