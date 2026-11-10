"""B/C run the real service with supplied evidence and no A/model network."""
from contextlib import ExitStack
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from app.api.main import create_app
from app.evaluation.samples import FakeLangfuseClient
from app.generation.context import build_context
from app.observability.tracing import Tracer
from app.observability.records import normalize_run_record
from app.service.adapters import make_query_adapter, make_security_adapters
from app.service.resources import load_generation_resources, load_resources
from shared.contracts import Chunk, RetrievedChunk
from shared.settings import load_settings
from tests.pipeline_fakes import FakeModel


def evidence():
    candidates = [RetrievedChunk(Chunk(f"AS00{i}-p1", f"AS00{i}", f"AS00{i}.pdf", 1,
                                     f"SAMPLE evidence {i}"), i, 1.0/i, "fixed") for i in (1, 2)]
    context = build_context(list(reversed(candidates)), 2, 8000, len)
    return candidates, context


@pytest.fixture
def generation_setup():
    with ExitStack() as stack:
        stack.enter_context(patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True))
        stack.enter_context(patch("app.service.resources.OpenAI"))
        model = FakeModel(["S1"])
        stack.enter_context(patch("app.service.resources.ChatOpenAI", return_value=model))
        stack.enter_context(patch("app.service.resources.tiktoken.get_encoding",
                                  return_value=SimpleNamespace(encode=list)))
        yield stack, model


def test_generation_only_api_preserves_supplied_context_without_qdrant(generation_setup):
    stack, _ = generation_setup
    embedder = stack.enter_context(patch("app.service.resources.Embedder"))
    qdrant = stack.enter_context(patch("app.service.resources.QdrantClient"))
    provider = Mock(return_value=evidence())
    settings = load_settings()
    resources = load_generation_resources(settings, evidence_provider=provider)
    stack.callback(resources.close)
    stack.enter_context(patch("app.service.query.retrieve", side_effect=AssertionError("A called")))
    stack.enter_context(patch("app.service.query.build_context", side_effect=AssertionError("repacked")))
    stack.enter_context(patch("app.api.lifespan.load_dotenv"))
    with TestClient(create_app(resources_loader=lambda settings: resources)) as client:
        assert client.get("/health").json()["dataset"] == "advanced"
        response = client.post("/query", json={"question": "SAMPLE", "retrieval_mode": "hybrid"})
    assert response.status_code == 200
    assert response.json()["citations"][0]["book_id"] == "AS002"
    provider.assert_called_once_with("SAMPLE", "hybrid")
    embedder.assert_not_called()
    qdrant.assert_not_called()


@pytest.mark.parametrize("status", ["answered", "needs_clarification", "abstained"])
def test_supplied_evidence_records_real_generation_and_status(generation_setup, status):
    stack, model = generation_setup
    original = model.invoke
    def invoke(value, config=None, **kwargs):
        result = original(value, config, **kwargs)
        result["parsed"].status = status
        return result
    model.invoke = invoke
    resources = load_generation_resources(load_settings(), evidence_provider=lambda q, m: evidence())
    stack.callback(resources.close)
    record = make_query_adapter(resources, load_settings())("SAMPLE", "naive", "prepared-1")
    normalized = normalize_run_record(record)
    assert normalized["status"] == status
    assert normalized["provided_context"] is True
    assert normalized["final_context_chunks"][0]["book_id"] == "AS002"
    assert normalized["usage"]["input_tokens"] == 100
    assert normalized["attempts"][0]["latency_ms"] >= 0


def test_security_uses_supplied_evidence_once(generation_setup):
    stack, _ = generation_setup
    provider = Mock(return_value=evidence())
    resources = load_generation_resources(load_settings(), evidence_provider=provider)
    stack.callback(resources.close)
    retrieve, generate = make_security_adapters(resources, load_settings(), defense="SAMPLE defense")
    saved = retrieve("SAMPLE", "naive")
    base = generate("SAMPLE", saved["final_context_chunks"], "base")
    defense = generate("SAMPLE", saved["final_context_chunks"], "defense")
    assert base["status"] == defense["status"] == "answered"
    assert base["model_context_hash"] == defense["model_context_hash"]
    assert base["model_input"][1] == defense["model_input"][1]
    provider.assert_called_once()


def test_supplied_context_cannot_exceed_budget(generation_setup):
    stack, _ = generation_setup
    settings = load_settings()
    settings.retrieval.context_token_budget = 1
    resources = load_generation_resources(settings, evidence_provider=lambda q, m: evidence())
    stack.callback(resources.close)
    record = make_query_adapter(resources, settings)("SAMPLE", "naive", "over-budget")
    assert record["status"] == "error"
    assert record["error_type"] == "ServiceError"
    assert "한도" in record["reason"]
    assert record["attempts"] == []


def test_candidates_only_provider_uses_common_context_policy(generation_setup):
    stack, _ = generation_setup
    candidates, _ = evidence()
    resources = load_generation_resources(load_settings(), evidence_provider=lambda q, m: (candidates, None))
    stack.callback(resources.close)
    client = FakeLangfuseClient()
    resources.tracer = Tracer(client)
    record = make_query_adapter(resources, load_settings())("SAMPLE", "naive", "candidates-only")
    assert record["status"] == "answered"
    assert record["provided_context"] is True
    assert [c["book_id"] for c in record["final_context_chunks"]] == ["AS001", "AS002"]
    retrieval = next(o for o in client.observations if o["name"] == "retrieval")
    assert retrieval["updates"][-1]["metadata"]["candidate_count"] == 2
    assert retrieval["input"] == {"question": "SAMPLE", "retrieval_mode": "naive"}


def test_preloaded_memory_qdrant_needs_no_cloud_credentials(generation_setup):
    stack, _ = generation_setup
    settings = load_settings()
    client = QdrantClient(location=":memory:")
    stack.callback(client.close)
    client.create_collection(settings.qdrant_collection,
                             vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    chunk = Chunk("AS001-p1", "AS001", "AS001.pdf", 1, "SAMPLE record")
    client.upsert(settings.qdrant_collection,
                  points=[models.PointStruct(id=1, vector=[1.0, 0.0], payload=asdict(chunk))])
    embedder = SimpleNamespace(dim=2, embed_query=lambda question: np.array([1.0, 0.0]))
    stack.enter_context(patch("app.service.resources.Embedder", return_value=embedder))
    cloud_factory = stack.enter_context(patch("app.service.resources.QdrantClient"))
    resources = load_resources(settings, qdrant_client=client)
    stack.callback(resources.close)
    record = make_query_adapter(resources, settings)("SAMPLE", "naive", "memory-1")
    assert record["status"] == "answered"
    assert record["citations"][0]["book_id"] == "AS001"
    assert resources.qdrant is client
    cloud_factory.assert_not_called()


def test_generation_only_requires_a_provider_before_opening_clients(generation_setup):
    stack, _ = generation_setup
    factory = stack.enter_context(patch("app.service.resources.OpenAI"))
    with pytest.raises(ValueError, match="evidence_provider"):
        load_generation_resources(load_settings(), evidence_provider=None)
    factory.assert_not_called()
