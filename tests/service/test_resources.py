from contextlib import ExitStack
from dataclasses import asdict
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.observability.records import compute_context_hash
from app.service.resources import load_resources
from shared.contracts import Chunk
from shared.exceptions import ServiceError
from shared.settings import load_settings
from tests.pipeline_fakes import FakeModel


class ResourceTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "QDRANT_URL": " https://example.test ",
                    "QDRANT_API_KEY": " test-qdrant-key ",
                },
                clear=True,
            )
        )
        stack.enter_context(patch("app.api.app.load_dotenv"))
        self.embedder = SimpleNamespace(
            dim=2,
            embed_query=Mock(return_value=SimpleNamespace(tolist=lambda: [0.1, 0.2])),
        )
        stack.enter_context(
            patch("app.service.resources.Embedder", return_value=self.embedder)
        )
        self.tracer_factory = stack.enter_context(patch("app.service.resources.Tracer"))
        self.factory = stack.enter_context(patch("app.service.resources.QdrantClient"))
        self.client = self.factory.return_value
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value.config.params.vectors.size = 2
        self.openai = stack.enter_context(patch("app.service.resources.OpenAI"))
        self.model_factory = stack.enter_context(
            patch("app.service.resources.ChatOpenAI")
        )
        stack.enter_context(
            patch(
                "app.service.resources.tiktoken.get_encoding",
                return_value=SimpleNamespace(encode=lambda text: text),
            )
        )
        self.settings = load_settings()

    def test_startup_runs_real_pipeline_and_closes_shared_clients(self):
        chunk = Chunk(
            "DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다."
        )
        self.client.query_points.return_value = SimpleNamespace(
            points=[SimpleNamespace(payload=asdict(chunk), score=0.9)],
        )
        self.model_factory.return_value = FakeModel(["S1"])
        with TestClient(create_app()) as client:
            self.assertTrue(client.get("/health").json()["query_ready"])
            for _ in range(2):
                response = client.post("/query", json={"question": "예약 문의"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "answered")
                self.assertEqual(
                    response.json()["citations"],
                    [
                        {
                            "source_id": "S1",
                            "book_id": "DEMO",
                            "file_name": "DEMO.pdf",
                            "page": 1,
                        }
                    ],
                )
            self.client.close.assert_not_called()
            self.openai.return_value.close.assert_not_called()
        trace = self.tracer_factory.from_env.return_value.start_request.return_value
        record = trace.record_retrieval.call_args.kwargs
        self.assertEqual(
            record["final_context_hash"],
            compute_context_hash(record["final_context_chunks"]),
        )
        self.factory.assert_called_once_with(
            url="https://example.test",
            api_key="test-qdrant-key",
            timeout=20,
            check_compatibility=False,
        )
        self.openai.assert_called_once()
        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_missing_credentials_stop_before_clients_are_created(self):
        del os.environ["OPENAI_API_KEY"]
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertIn("OPENAI_API_KEY", str(caught.exception))
        self.factory.assert_not_called()
        self.openai.assert_not_called()
        self.tracer_factory.from_env.assert_not_called()

    def test_missing_index_is_503_and_closes_created_clients(self):
        self.client.collection_exists.return_value = False
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.client.close.assert_called_once()
        self.openai.assert_not_called()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_failed_index_lookup_closes_created_clients(self):
        self.client.collection_exists.side_effect = RuntimeError("lookup failed")
        with self.assertRaises(RuntimeError):
            load_resources(self.settings)
        self.client.close.assert_called_once()
        self.openai.assert_not_called()

    def test_dimension_mismatch_is_rejected_and_closes_client(self):
        self.client.get_collection.return_value.config.params.vectors.size = 3
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("차원", str(caught.exception))
        self.client.close.assert_called_once()
        self.openai.assert_not_called()

    def test_generation_setup_failure_closes_all_created_clients(self):
        self.model_factory.side_effect = RuntimeError("model setup failed")
        with self.assertRaises(RuntimeError):
            load_resources(self.settings)
        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_assembly_failure_closes_all_created_clients(self):
        with patch(
            "app.service.resources.Resources",
            side_effect=RuntimeError("assembly failed"),
        ):
            with self.assertRaises(RuntimeError):
                load_resources(self.settings)
        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_shutdown_clears_owned_search_state_and_closes_clients_once(self):
        resources = load_resources(self.settings)
        resources.retrieval.keyword_index = Mock()
        resources.retrieval.reranker = Mock()
        resources.close()
        resources.close()
        self.assertIsNone(resources.retrieval.keyword_index)
        self.assertIsNone(resources.retrieval.reranker)
        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
