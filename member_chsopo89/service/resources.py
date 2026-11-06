import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Any

import tiktoken
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI
from qdrant_client import QdrantClient

from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG, Settings


@dataclass
class Resources:
    embedder: Any
    qdrant: QdrantClient
    collection: str
    model: ChatOpenAI
    count_tokens: Callable[[str], int]
    cleanup: ExitStack
    retriever: Callable
    tracer: Any | None = None
    context_hasher: Callable[[list[dict]], str] | None = None

    def close(self):
        self.cleanup.close()


def load_resources(settings: Settings) -> Resources:
    load_dotenv(DEFAULT_CONFIG.parent.parent / ".env", override=False)
    try:
        embeddings = import_module("member_ysl727727.indexing.embeddings")
        retrieval = import_module("member_ysl727727.retrieval.service")
    except ModuleNotFoundError as error:
        if error.name and error.name.split(".", 1)[0] == "member_ysl727727":
            raise ServiceError("A의 검색 모듈을 연결해야 실제 질문을 처리할 수 있습니다. B 개발용 API 문서는 이용할 수 있습니다.", 503) from error
        raise
    for name in ["OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY"]:
        if not os.environ.get(name, "").strip():
            raise ServiceError(f"서버의 {name} 설정을 확인해 주세요.", 503)

    with ExitStack() as cleanup:
        tracer = None
        context_hasher = None
        try:
            tracing = import_module("member_Damsebi.observability.tracing")
            records = import_module("member_Damsebi.observability.records")
        except ModuleNotFoundError as error:
            if not error.name or error.name.split(".", 1)[0] != "member_Damsebi":
                raise
        else:
            tracer = tracing.Tracer.from_env()
            cleanup.callback(tracer.shutdown)
            context_hasher = records.compute_context_hash
        embedder = embeddings.Embedder(settings.embedding_model, device=settings.embedding_device)
        qdrant = QdrantClient(
            url=os.environ["QDRANT_URL"].strip(), api_key=os.environ["QDRANT_API_KEY"].strip(),
            timeout=20, check_compatibility=False,
        )
        cleanup.callback(qdrant.close)
        if not qdrant.collection_exists(settings.qdrant_collection):
            raise ServiceError("설정한 검색 색인이 없습니다. A의 색인 준비와 컬렉션 이름을 확인해 주세요.", 503)
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
        encoding = tiktoken.get_encoding(settings.context_tokenizer)

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
            retriever=retrieval.retrieve,
            tracer=tracer,
            context_hasher=context_hasher,
        )
