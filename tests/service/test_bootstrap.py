import importlib
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from shared.exceptions import ServiceError
from shared.settings import load_settings
from app.service.bootstrap import DEFENSE_PROMPT, ServiceBootstrap, _QueryAdapter
from tests.pipeline_fakes import make_query_resources


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.resources = make_query_resources()
        self.resources.tracer = MagicMock(name="fake-tracer")
        self.resources_loader = Mock(return_value=self.resources)
        self.bootstrap = ServiceBootstrap(
            settings_loader=load_settings, resources_loader=self.resources_loader
        )
        self.addCleanup(self.bootstrap.close)

    def test_module_import_does_not_load_resources(self):
        sys.modules.pop("app.service.bootstrap", None)
        with patch("app.service.resources.load_resources") as loader:
            module = importlib.import_module("app.service.bootstrap")
        loader.assert_not_called()
        self.assertIsNone(module._default._resources)

    def test_query_adapter_prepares_once_and_exposes_tracer(self):
        adapter = _QueryAdapter(self.bootstrap)
        self.resources_loader.assert_not_called()
        self.assertIs(adapter.tracer, self.resources.tracer)
        first = adapter("SI 마감은 언제인가요?", "naive", "request-1")
        second = adapter("SI 마감은 언제인가요?", "naive", "request-2")
        self.resources_loader.assert_called_once()
        self.assertEqual(first["request_id"], "request-1")
        self.assertEqual(first["status"], "answered")
        self.assertEqual(first["latency_scope"], "in_process_adapter")
        self.assertEqual(second["request_id"], "request-2")

    def test_security_adapters_share_context_and_use_defense_prompt(self):
        retrieve_fn, generate_fn = self.bootstrap.security_adapters()
        self.assertIs(self.bootstrap.security_adapters()[0], retrieve_fn)
        context = retrieve_fn("SI 마감은 언제인가요?", "naive")
        base = generate_fn("SI 마감은 언제인가요?", context["final_context_chunks"], "base")
        defense = generate_fn("SI 마감은 언제인가요?", context["final_context_chunks"], "defense")
        self.assertEqual(base["status"], "answered")
        self.assertIsNone(base["defense_hash"])
        self.assertIsNotNone(defense["defense_hash"])
        self.assertIn(
            DEFENSE_PROMPT.read_text(encoding="utf-8").strip(),
            defense["model_input"][0]["content"],
        )
        self.assertEqual(self.resources.qdrant.query_points.call_count, 1)

    def test_close_releases_resources_once(self):
        self.bootstrap.query_adapter()
        self.bootstrap.close()
        self.bootstrap.close()
        self.resources.qdrant.close.assert_called_once()

    def test_preparation_error_is_raised_before_any_question(self):
        failing = ServiceBootstrap(
            settings_loader=load_settings,
            resources_loader=Mock(side_effect=ServiceError("설정 확인", 503)),
        )
        with self.assertRaises(ServiceError):
            _QueryAdapter(failing).tracer


if __name__ == "__main__":
    unittest.main()
