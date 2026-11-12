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


def _service(model):
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
        service = _service(FailingModel())
        service._close_search = lambda: closed.append(True)
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
    monkeypatch.setenv("ADEN_TABLE_RAG_VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("ADEN_TABLE_RAG_COLLECTION", "advanced_table_rag_test")
    monkeypatch.setattr(service, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "load_corpus", lambda directory: corpus)

    def open_index(directory, loaded, *, collection):
        assert directory == tmp_path
        assert loaded is corpus
        assert collection == "advanced_table_rag_test"
        return dense

    def fail_to_load(loaded, *, device, dense_index):
        assert dense_index is dense
        raise ValueError("local model missing")

    monkeypatch.setattr(qdrant_index, "open_qdrant_index", open_index)
    monkeypatch.setattr(retrieval, "load_retriever", fail_to_load)
    with pytest.raises(ValueError, match="local model missing"):
        service.load_service(tmp_path)
    assert closed == [True]


def test_invalid_vector_backend_does_not_select_a_different_backend(monkeypatch, tmp_path):
    from app.rag import service

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ADEN_TABLE_RAG_VECTOR_BACKEND", "typo")
    monkeypatch.setattr(service, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "load_corpus", lambda directory: object())
    with pytest.raises(ValueError, match="ADEN_TABLE_RAG_VECTOR_BACKEND"):
        service.load_service(tmp_path)
