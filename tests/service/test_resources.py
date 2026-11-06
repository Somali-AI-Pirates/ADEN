from contextlib import ExitStack
from dataclasses import asdict
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.retrieval.service import retrieve
from shared.contracts import Chunk
from shared.exceptions import ServiceError
from shared.settings import load_settings
from app.service.resources import load_resources
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
        stack.enter_context(patch("app.service.resources.load_dotenv"))
        self.embedder = SimpleNamespace(dim=2)
        self.retriever = Mock()
        self.tracer_factory = Mock()
        self.hasher = Mock(return_value="sha256:test-context")
        self.modules = {
            "app.indexing.embeddings": SimpleNamespace(
                Embedder=Mock(return_value=self.embedder)
            ),
            "app.retrieval.service": SimpleNamespace(retrieve=self.retriever),
            "app.observability.tracing": SimpleNamespace(Tracer=self.tracer_factory),
            "app.observability.records": SimpleNamespace(
                compute_context_hash=self.hasher
            ),
        }

        def load_module(name):
            if name not in self.modules:
                raise ModuleNotFoundError(name=name)
            return self.modules[name]

        self.imports = stack.enter_context(
            patch("app.service.resources.import_module", side_effect=load_module)
        )
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

    def test_startup_answers_questions_and_closes_shared_clients(self):
        chunk = Chunk(
            "DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다."
        )
        self.modules["app.retrieval.service"].retrieve = retrieve
        self.embedder.embed_query = Mock(
            return_value=SimpleNamespace(tolist=lambda: [0.1, 0.2])
        )
        self.client.query_points.return_value = SimpleNamespace(
            points=[
                SimpleNamespace(payload=asdict(chunk), score=0.9),
            ]
        )
        self.model_factory.return_value = FakeModel(["S1"])
        records = []

        with TestClient(create_app(run_record_sink=records.append)) as client:
            self.assertTrue(client.get("/health").json()["query_ready"])
            for _ in range(2):
                response = client.post("/query", json={"question": "예약 문의"})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "answered")
                self.assertEqual(
                    result["citations"],
                    [
                        {
                            "source_id": "S1",
                            "book_id": "DEMO",
                            "file_name": "DEMO.pdf",
                            "page": 1,
                        }
                    ],
                )
                self.assertEqual(result["request_id"], response.headers["X-Request-ID"])
            self.client.close.assert_not_called()
            self.openai.return_value.close.assert_not_called()

        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(
                record["final_context_hash"] == "sha256:test-context"
                for record in records
            )
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

    def test_C_is_optional_without_copying_a_tracer(self):
        self.modules.pop("app.observability.tracing")
        self.modules.pop("app.observability.records")
        resources = load_resources(self.settings)
        self.assertIsNone(resources.tracer)
        self.assertIsNone(resources.context_hasher)
        self.tracer_factory.from_env.assert_not_called()
        resources.close()

    def test_missing_A_has_clear_503_before_clients_are_created(self):
        self.modules.pop("app.indexing.embeddings")
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("A의 검색 모듈", str(caught.exception))
        self.factory.assert_not_called()
        self.openai.assert_not_called()

    def test_A_dependency_failure_is_not_hidden_as_missing_A(self):
        self.imports.side_effect = ModuleNotFoundError(name="numpy")
        with self.assertRaises(ModuleNotFoundError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.name, "numpy")
        self.factory.assert_not_called()

    def test_C_dependency_failure_is_not_hidden_as_missing_C(self):
        load_module = self.imports.side_effect

        def fail_on_tracing_dependency(name):
            if name == "app.observability.tracing":
                raise ModuleNotFoundError(name="langfuse")
            return load_module(name)

        self.imports.side_effect = fail_on_tracing_dependency
        with self.assertRaises(ModuleNotFoundError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.name, "langfuse")
        self.factory.assert_not_called()

    def test_missing_credentials_stop_before_network_clients(self):
        del os.environ["OPENAI_API_KEY"]
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertIn("OPENAI_API_KEY", str(caught.exception))
        self.factory.assert_not_called()

    def test_missing_index_is_503_and_closes_client(self):
        self.client.collection_exists.return_value = False
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("검색 색인이 없습니다", str(caught.exception))
        self.client.close.assert_called_once()
        self.openai.assert_not_called()

    def test_failed_index_lookup_closes_client(self):
        self.client.collection_exists.side_effect = RuntimeError("lookup failed")
        with self.assertRaises(RuntimeError):
            load_resources(self.settings)
        self.client.close.assert_called_once()
        self.openai.assert_not_called()

    def test_dimension_validation_is_preserved(self):
        self.client.get_collection.return_value.config.params.vectors.size = 3
        with self.assertRaises(ServiceError) as caught:
            load_resources(self.settings)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("차원", str(caught.exception))
        self.client.close.assert_called_once()
        self.openai.assert_not_called()

    def test_incomplete_search_module_closes_all_created_clients(self):
        del self.modules["app.retrieval.service"].retrieve

        with self.assertRaises(AttributeError):
            load_resources(self.settings)

        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_generation_setup_failure_closes_created_clients(self):
        self.model_factory.side_effect = RuntimeError("model setup failed")

        with self.assertRaises(RuntimeError):
            load_resources(self.settings)

        self.client.close.assert_called_once()
        self.openai.return_value.close.assert_called_once()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
