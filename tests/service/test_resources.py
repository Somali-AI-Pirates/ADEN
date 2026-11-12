from contextlib import ExitStack
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.service.resources import load_resources
from shared.exceptions import ServiceError
from shared.settings import load_settings


def broken_factory(settings):
    raise RuntimeError("private path detail")


def empty_factory(settings):
    return None


def not_a_provider(settings):
    return "not callable"


class ResourceTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                },
                clear=True,
            )
        )
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
        for expected in (self.settings.qdrant_collection, "ADEN_QDRANT_CLIENT", "ADEN_EVIDENCE_PROVIDER"):
            self.assertIn(expected, str(caught.exception))  # 무엇이 없고 어떻게 연결하는지 알린다
        self.client.close.assert_called_once()
        self.openai.assert_not_called()
        self.tracer_factory.from_env.return_value.shutdown.assert_called_once()

    def test_misconfigured_connection_names_the_setting_without_private_details(self):
        here = "tests.service.test_resources"
        cases = [
            ({"ADEN_QDRANT_CLIENT": f"{here}:empty_factory",
              "ADEN_EVIDENCE_PROVIDER": f"{here}:not_a_provider"}, "하나만"),
            ({"ADEN_QDRANT_CLIENT": "no_such_module:factory"}, "ModuleNotFoundError"),
            ({"ADEN_QDRANT_CLIENT": f"{here}:missing"}, "AttributeError"),
            ({"ADEN_QDRANT_CLIENT": f"{here}:broken_factory"}, "RuntimeError"),
            ({"ADEN_QDRANT_CLIENT": f"{here}:empty_factory"}, "반환하지 않았습니다"),
            ({"ADEN_EVIDENCE_PROVIDER": f"{here}:not_a_provider"}, "근거 공급 함수"),
        ]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env):
                with self.assertRaises(ServiceError) as caught:
                    load_resources(self.settings)
                message = str(caught.exception)
                self.assertEqual(caught.exception.status_code, 503)
                self.assertIn(expected, message)
                self.assertIn(next(iter(env)), message)
                self.assertNotIn("private path detail", message)
        self.factory.assert_not_called()
        self.openai.assert_not_called()

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
