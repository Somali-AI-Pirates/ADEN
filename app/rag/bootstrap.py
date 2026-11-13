"""Build and own shared TableRAG resources before accepting requests."""

import logging
import sqlite3
from contextlib import AsyncExitStack
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from app.observability.tracing import Tracer
from app.rag.config import RETRIEVAL_MODE, TableRagSettings, load_table_rag_settings
from app.rag.corpus import load_corpus
from app.rag.lifecycle import (
    close_async_resource,
    close_sync_resource,
    run_sync_acquiring,
)
from app.rag.model import load_model
from app.rag.readiness import build_readiness_probe
from app.rag.service import RagService
from shared.table_rag_policy import (
    ANSWER_OUTPUT_TOKENS,
    MODEL,
    PLAN_OUTPUT_TOKENS,
    REASONING_EFFORT,
    TIMEOUT_SECONDS,
    CANDIDATE_LIMIT,
    CONTEXT_WINDOW,
    MAX_CONTEXT_PAGES,
    MAX_CONTEXT_TOKENS,
)

logger = logging.getLogger(__name__)


def _runtime_fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    modules = sorted((root / "app/rag").glob("*.py"))
    modules.extend(sorted((root / "app/observability").glob("*.py")))
    modules.extend(
        root / name
        for name in (
            "app/generation/context.py",
            "app/generation/citations.py",
            "app/ingestion/layout.py",
            "shared/bm25_tokens.py",
            "shared/contracts.py",
            "shared/table_rag_policy.py",
            "shared/text_normalization.py",
        )
    )
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in modules
    }


class _CloseOnce:
    """Disarm a provisional owner when a higher-level resource takes ownership."""

    def __init__(self, resource) -> None:
        self._resource = resource

    def disarm(self) -> None:
        self._resource = None

    def close(self) -> None:
        resource, self._resource = self._resource, None
        if resource is not None:
            resource.close()


def _close_corpus(corpus) -> None:
    close = getattr(corpus, "close", None)
    if close is not None:
        close()
        return
    vectors = getattr(corpus, "vectors", None)
    memory_map = getattr(vectors, "_mmap", None)
    if memory_map is not None and not getattr(memory_map, "closed", False):
        memory_map.close()


async def load_service(
    directory: str | Path | None = None,
    *,
    settings: TableRagSettings | None = None,
) -> RagService:
    """Asynchronously load the verified DB-first TableRAG bundle and own its resources."""
    from app.rag.records import RecordIndex
    from app.rag.retrieval import load_retriever

    if settings is not None and directory is not None:
        raise ValueError("Pass either directory or settings, not both")
    settings = settings or load_table_rag_settings(data_directory=directory)
    credentials = settings.credentials
    if not credentials.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for TableRAG")
    directory = settings.data_directory.resolve()
    started = perf_counter()

    async with AsyncExitStack() as resources:

        def register_corpus(corpus):
            resources.push_async_callback(close_sync_resource, _close_corpus, corpus)

        corpus = await run_sync_acquiring(
            load_corpus, directory, on_acquired=register_corpus
        )
        dense_index = None
        dense_owner = None
        if settings.vector_backend == "qdrant":
            from app.rag.qdrant_index import open_qdrant_index

            def register_dense(index):
                nonlocal dense_owner
                dense_owner = _CloseOnce(index)
                resources.push_async_callback(close_sync_resource, dense_owner.close)

            dense_index = await run_sync_acquiring(
                open_qdrant_index,
                directory,
                corpus,
                collection=settings.collection,
                credentials=credentials,
                on_acquired=register_dense,
            )
        elif settings.vector_backend != "numpy":
            raise ValueError("ADEN_TABLE_RAG_VECTOR_BACKEND must be numpy or qdrant")

        def register_retriever(retriever):
            if dense_owner is not None:
                dense_owner.disarm()
            resources.push_async_callback(close_sync_resource, retriever.close)

        retriever = await run_sync_acquiring(
            load_retriever,
            corpus,
            device=settings.device,
            dense_index=dense_index,
            on_acquired=register_retriever,
        )

        def register_record_index(record_index):
            resources.push_async_callback(close_sync_resource, record_index.close)

        record_index = await run_sync_acquiring(
            RecordIndex, directory, on_acquired=register_record_index
        )
        model = await load_model(api_key=credentials.openai_api_key)
        resources.push_async_callback(close_async_resource, model.aclose)

        tracer = Tracer()
        if settings.trace_enabled:
            try:
                tracer = Tracer.from_credentials(
                    public_key=credentials.langfuse_public_key,
                    secret_key=credentials.langfuse_secret_key,
                    base_url=credentials.langfuse_base_url,
                )
            except Exception as error:
                logger.warning(
                    "TableRAG tracing unavailable (%s)", type(error).__name__
                )
        resources.push_async_callback(close_sync_resource, tracer.shutdown)

        readiness_probe = build_readiness_probe(directory, corpus, settings)
        resources.push_async_callback(close_async_resource, readiness_probe.aclose)

        corpus_manifest = (directory / "corpus.json").read_bytes()
        manifest = {
            "policy": RETRIEVAL_MODE,
            "db_first": True,
            "runtime_settings": settings.public_manifest(),
            "module_sha256": _runtime_fingerprints(),
            "runtime_versions": {
                name: version(name)
                for name in (
                    "langchain-core",
                    "langchain-openai",
                    "langgraph",
                    "sentence-transformers",
                    "torch",
                    "numpy",
                    "qdrant-client",
                    "tiktoken",
                    "pydantic",
                    "openai",
                    "anyio",
                )
            },
            "sqlite_version": sqlite3.sqlite_version,
            "snapshot_sha256": sha256(corpus_manifest).hexdigest(),
            "embedding": corpus.embedding,
            "retrieval": retriever.manifest,
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "context_window": CONTEXT_WINDOW,
            "plan_output_tokens": PLAN_OUTPUT_TOKENS,
            "answer_output_tokens": ANSWER_OUTPUT_TOKENS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_retries": 0,
            "prompt_sha256": model.prompt_hashes(),
            "candidate_pages": CANDIDATE_LIMIT,
            "final_pages": MAX_CONTEXT_PAGES,
            "evidence_tokens": MAX_CONTEXT_TOKENS,
        }
        logger.info("DB-first TableRAG ready in %.2f seconds", perf_counter() - started)
        service = RagService(
            search=retriever.search,
            page_lookup=corpus.pages_by_source.get,
            table_store=corpus.table_store,
            model=model,
            tracer=tracer,
            manifest=manifest,
            record_index=record_index,
            run_directory=settings.run_directory,
            resource_stack=resources,
            settings=settings,
            readiness_probe=readiness_probe,
        )
        service._resource_stack = resources.pop_all()
        return service
