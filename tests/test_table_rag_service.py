import asyncio
import json
from hashlib import sha256

import pytest
from langchain_core.messages import AIMessage

from app.observability.tracing import Tracer
from app.rag.model import GeneratedAnswer, RagModelError
from app.rag.models import SearchResult, SourceRef, make_document
from app.rag.service import RagService


class _Store:
    def schema(self, table_ids):
        assert not table_ids
        return []


class _Model:
    def __init__(self, invalid_source=False):
        self.invalid_source = invalid_source
        self.closed = 0

    def planner_overhead(self, count_tokens):
        return count_tokens("plan and tool schemas")

    async def plan(self, history, *, finish_only, calls):
        assert not finish_only
        assert len(history) == 1
        observed = json.loads(history[0].content)
        source = observed["initial_evidence"][0]["passages"][0]["source_id"]
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "finish",
                    "id": "call_1",
                    "args": {
                        "status": "sufficient",
                        "selected_sources": [source],
                        "interim_summary": "not sent to the answer model",
                        "conflicts": [],
                    },
                }
            ],
        )

    async def answer(self, payload, *, context_hash, calls):
        value = json.loads(payload)
        assert set(value) == {"question", "evidence", "retrieval_status"}
        assert set(value["evidence"]) == {"pages", "sql"}
        assert (
            sha256(
                json.dumps(value["evidence"], ensure_ascii=False).encode()
            ).hexdigest()
            == context_hash
        )
        assert "not sent to the answer model" not in payload
        assert "off-topic-secret" not in payload
        source = value["evidence"]["pages"][0]["source_id"]
        return GeneratedAnswer(
            answer=value["question"],
            citations=["AS999:p9999" if self.invalid_source else source],
            status="answered",
        )

    async def aclose(self):
        self.closed += 1


def _service(model, close_search=None):
    sources = {
        question: make_document(
            f"자료 {page}",
            SourceRef(book_id="AS001", file_name="AS001.pdf", page=page),
            f"AS001:p{page:04d}:page",
        )
        for page, question in enumerate(("첫 질문", "둘째 질문"), start=1)
    }
    page_lookup = {
        document.metadata["source_id"]: document for document in sources.values()
    }
    return RagService(
        search=lambda question: SearchResult((sources[question],), (), {}),
        page_lookup=page_lookup.get,
        table_store=_Store(),
        model=model,
        tracer=Tracer(),
        manifest={"policy": "table_rag_a"},
        close_search=close_search,
    )


def test_service_graph_records_exact_evidence_and_isolates_concurrent_requests(tmp_path):
    async def run():
        model = _Model()
        service = _service(model)
        service._run_directory = tmp_path
        try:
            first, second = await asyncio.gather(
                service.ainvoke(" 첫 질문 ", request_id="1" * 32),
                service.ainvoke("둘째 질문", request_id="2" * 32),
            )
            assert first.response.request_id == "1" * 32
            assert second.response.request_id == "2" * 32
            assert first.response.citations[0].source_id == "AS001:p0001"
            assert second.response.citations[0].source_id == "AS001:p0002"
            saved = json.loads((tmp_path / ("1" * 32) / "runs.jsonl").read_text())
            saved_second = json.loads((tmp_path / ("2" * 32) / "runs.jsonl").read_text())
            assert saved["request_id"] == first.response.request_id
            assert saved_second["request_id"] == second.response.request_id
            assert saved["question"] == "첫 질문"
            assert saved["final_context_chunks"][0]["text"] == "자료 1"
            first_record = first.to_record()
            assert first_record["question"] == "첫 질문"
            assert (
                first_record["context_hash"]
                == sha256(first_record["context_text"].encode()).hexdigest()
            )
            assert first_record["model_context_hash"] == first_record["context_hash"]
            assert first_record["final_context_hash"].startswith("sha256:")
            assert first_record["candidate_source_ids"] == ["AS001:p0001"]
            assert first_record["final_context_chunks"][0]["text"] == "자료 1"
        finally:
            await service.aclose()
            await service.aclose()
            assert model.closed == 1

    asyncio.run(run())


def test_answer_citations_must_be_from_final_context():
    async def run():
        service = _service(_Model(invalid_source=True))
        try:
            with pytest.raises(
                RagModelError, match="outside the final context"
            ) as failure:
                await service.ainvoke("첫 질문", request_id="invalid-citation")
            assert failure.value.rag_metadata["final_source_ids"] == ["AS001:p0001"]
            assert (
                failure.value.rag_metadata["diagnostics"]["termination_reason"]
                == "finished"
            )
        finally:
            await service.aclose()

    asyncio.run(run())


def test_answer_cannot_upgrade_an_insufficient_retrieval():
    class OverconfidentModel(_Model):
        async def plan(self, history, *, finish_only, calls):
            message = await super().plan(history, finish_only=finish_only, calls=calls)
            message.tool_calls[0]["args"]["status"] = "insufficient"
            return message

    async def run():
        service = _service(OverconfidentModel())
        try:
            with pytest.raises(RagModelError, match="contradicts"):
                await service.ainvoke("첫 질문", request_id="status-check")
        finally:
            await service.aclose()

    asyncio.run(run())


def test_service_closes_search_client_even_if_model_close_fails():
    closed = []

    class FailingModel(_Model):
        async def aclose(self):
            raise RuntimeError("model close failed")

    async def run():
        service = _service(FailingModel(), close_search=lambda: closed.append(True))
        with pytest.raises(RuntimeError, match="model close failed"):
            await service.aclose()
        await service.aclose()
        assert closed == [True]

    asyncio.run(run())


def test_cloud_client_is_closed_when_model_loading_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.rag import qdrant_index, retrieval, service

    closed = []
    corpus = SimpleNamespace()
    dense = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.invalid")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-test-key")
    monkeypatch.setenv("ADEN_TABLE_RAG_VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("ADEN_TABLE_RAG_COLLECTION", "advanced_table_rag_test")
    monkeypatch.setattr(service, "load_corpus", lambda directory: corpus)
    settings = service.load_table_rag_settings(data_directory=tmp_path)

    def open_index(directory, loaded, *, collection, credentials):
        assert directory == tmp_path
        assert loaded is corpus
        assert collection == "advanced_table_rag_test"
        assert credentials.qdrant_url == "https://qdrant.example.invalid"
        assert credentials.qdrant_api_key == "qdrant-test-key"
        return dense

    def fail_to_load(loaded, *, device, dense_index):
        assert dense_index is dense
        raise ValueError("local model missing")

    monkeypatch.setattr(qdrant_index, "open_qdrant_index", open_index)
    monkeypatch.setattr(retrieval, "load_retriever", fail_to_load)
    with pytest.raises(ValueError, match="local model missing"):
        asyncio.run(service.load_service(settings=settings))
    assert closed == [True]


def test_invalid_vector_backend_does_not_select_a_different_backend(monkeypatch, tmp_path):
    from app.rag import service

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ADEN_TABLE_RAG_VECTOR_BACKEND", "typo")
    monkeypatch.setattr(service, "load_corpus", lambda directory: object())
    with pytest.raises(ValueError, match="Unsupported TableRAG vector backend"):
        asyncio.run(service.load_service(tmp_path))


def test_explicit_missing_langfuse_keys_disable_tracing_even_when_env_is_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "ambient-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "ambient-secret-key")

    tracer = Tracer.from_credentials(
        public_key=None,
        secret_key=None,
        base_url=None,
    )

    assert tracer.enabled is False


def test_tracer_uses_explicit_langfuse_credentials(monkeypatch):
    import langfuse

    captured = {}
    client = object()

    def create_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(langfuse, "Langfuse", create_client)
    tracer = Tracer.from_credentials(
        public_key="explicit-public-key",
        secret_key="explicit-secret-key",
        base_url="https://langfuse.example.invalid",
    )

    assert tracer.enabled is True
    assert tracer._client is client
    assert captured == {
        "public_key": "explicit-public-key",
        "secret_key": "explicit-secret-key",
        "base_url": "https://langfuse.example.invalid",
    }


def _patch_async_service_startup(monkeypatch, tmp_path, *, failure=None, fail_close=False):
    from types import SimpleNamespace

    from app.rag import qdrant_index, records, retrieval, service
    from app.rag.config import RuntimeCredentials, TableRagSettings

    (tmp_path / "corpus.json").write_text("{}", encoding="utf-8")
    events = []

    class FakeMap:
        closed = False

        def close(self):
            if not self.closed:
                self.closed = True
                events.append("corpus")

    class DenseIndex:
        def close(self):
            events.append("dense")

    dense = DenseIndex()
    corpus = SimpleNamespace(
        vectors=SimpleNamespace(_mmap=FakeMap()),
        embedding={"model_id": "test"},
        pages_by_source={},
        table_store=_Store(),
    )

    class Retriever:
        manifest = {"retrieval": {"dense": "qdrant"}}

        def search(self, question):
            raise AssertionError("search is not part of initialization")

        def close(self):
            events.append("retriever")
            dense.close()

    retriever = Retriever()

    class RecordIndex:
        def __init__(self, directory):
            if failure == "records":
                raise RuntimeError("records failed")

        def close(self):
            events.append("records")

    class Model:
        def planner_overhead(self, count_tokens):
            return 1

        def prompt_hashes(self):
            return {"plan": "a", "answer": "b"}

        async def aclose(self):
            events.append("model")
            if fail_close:
                raise RuntimeError("model close failed")

    model = Model()

    class FakeTracer:
        def __init__(self):
            pass

        @classmethod
        def from_credentials(cls, **kwargs):
            return cls()

        def shutdown(self):
            events.append("tracer")

    monkeypatch.setattr(service, "load_corpus", lambda directory: corpus)

    def open_index(directory, loaded, *, collection, credentials):
        assert loaded is corpus
        assert collection == "advanced_table_rag"
        assert credentials.qdrant_api_key == "qdrant-test"
        if failure == "qdrant":
            raise RuntimeError("qdrant failed")
        return dense

    monkeypatch.setattr(qdrant_index, "open_qdrant_index", open_index)

    def load_retriever(loaded, *, device, dense_index):
        assert loaded is corpus
        assert device == "cpu"
        assert dense_index is dense
        if failure == "retriever":
            raise RuntimeError("retriever failed")
        return retriever

    monkeypatch.setattr(retrieval, "load_retriever", load_retriever)
    monkeypatch.setattr(records, "RecordIndex", RecordIndex)

    async def load_model(*, api_key):
        assert api_key == "table-rag-test"
        if failure == "model":
            raise RuntimeError("model failed")
        return model

    monkeypatch.setattr(service, "load_model", load_model)
    monkeypatch.setattr(service, "Tracer", FakeTracer)
    settings = TableRagSettings(
        data_directory=tmp_path,
        vector_backend="qdrant",
        credentials=RuntimeCredentials(
            openai_api_key="table-rag-test",
            qdrant_url="https://qdrant.example.invalid",
            qdrant_api_key="qdrant-test",
        ),
    )
    return service, settings, events, dense, retriever, model, corpus


def test_async_service_transfers_one_resource_stack_and_closes_once(
    monkeypatch, tmp_path
):
    service_module, settings, events, dense, retriever, model, corpus = (
        _patch_async_service_startup(monkeypatch, tmp_path)
    )

    async def run():
        service = await service_module.load_service(settings=settings)
        await service.aclose()
        await service.aclose()

    asyncio.run(run())

    assert events == ["tracer", "model", "records", "retriever", "dense", "corpus"]
    assert corpus.vectors._mmap.closed is True


@pytest.mark.parametrize(
    ("failure", "expected_events"),
    [
        ("qdrant", ["corpus"]),
        ("retriever", ["dense", "corpus"]),
        ("records", ["retriever", "dense", "corpus"]),
        ("model", ["records", "retriever", "dense", "corpus"]),
    ],
)
def test_async_service_initialization_failure_closes_acquired_resources(
    monkeypatch, tmp_path, failure, expected_events
):
    service_module, settings, events, *_ = _patch_async_service_startup(
        monkeypatch, tmp_path, failure=failure
    )

    with pytest.raises(RuntimeError, match=failure):
        asyncio.run(service_module.load_service(settings=settings))

    assert events == expected_events


def test_async_service_constructor_failure_keeps_local_stack_for_cleanup(
    monkeypatch, tmp_path
):
    service_module, settings, events, *_ = _patch_async_service_startup(
        monkeypatch, tmp_path
    )

    def fail_to_construct(**kwargs):
        raise RuntimeError("graph construction failed")

    monkeypatch.setattr(service_module, "RagService", fail_to_construct)
    with pytest.raises(RuntimeError, match="graph construction failed"):
        asyncio.run(service_module.load_service(settings=settings))

    assert events == ["tracer", "model", "records", "retriever", "dense", "corpus"]


def test_async_service_close_runs_remaining_callbacks_after_one_close_error(
    monkeypatch, tmp_path
):
    service_module, settings, events, *_ = _patch_async_service_startup(
        monkeypatch, tmp_path, fail_close=True
    )
    service = asyncio.run(service_module.load_service(settings=settings))

    async def close():
        with pytest.raises(RuntimeError, match="model close failed"):
            await service.aclose()
        await service.aclose()

    asyncio.run(close())
    assert events == ["tracer", "model", "records", "retriever", "dense", "corpus"]


def test_async_service_repeated_cancellation_joins_acquisition_then_cleans_resource():
    from threading import Event
    from contextlib import AsyncExitStack

    from app.rag.service import _close_sync_resource, _run_sync_acquiring

    started = Event()
    release = Event()
    closed = []

    class Resource:
        def close(self):
            closed.append("closed")

    def create_resource():
        started.set()
        release.wait()
        return Resource()

    async def initialize():
        async with AsyncExitStack() as resources:
            await _run_sync_acquiring(
                create_resource,
                on_acquired=lambda resource: resources.push_async_callback(
                    _close_sync_resource, resource.close
                ),
            )

    async def run():
        owner = asyncio.create_task(initialize())
        await asyncio.to_thread(started.wait)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner

    asyncio.run(run())
    assert closed == ["closed"]


def test_explicit_tracer_defaults_do_not_read_later_environment(monkeypatch):
    import langfuse

    captured = {}
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://unrelated.example.invalid")
    monkeypatch.setattr(
        langfuse, "Langfuse",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    Tracer.from_credentials(public_key="snapshot-public", secret_key="snapshot-secret")
    assert captured["base_url"] == "https://cloud.langfuse.com"


def test_cancelled_request_joins_sync_search_before_resources_close():
    from threading import Event

    async def run():
        started, release, finished = Event(), Event(), Event()
        closed = []
        page = make_document(
            "자료", SourceRef(book_id="AS001", file_name="AS001.pdf", page=1),
            "AS001:p0001:page",
        )

        def search(question):
            started.set()
            release.wait()
            finished.set()
            return SearchResult((page,), (), {})

        def close_search():
            assert finished.is_set()
            closed.append("search")

        service = RagService(
            search=search, page_lookup=lambda source: page,
            table_store=_Store(), model=_Model(), tracer=Tracer(),
            manifest={"policy": "test"}, close_search=close_search,
        )
        task = asyncio.create_task(service.ainvoke("질문", request_id="cancel-test"))
        await asyncio.to_thread(started.wait)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await service.aclose()
        assert closed == ["search"]

    asyncio.run(run())
