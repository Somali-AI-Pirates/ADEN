import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import MagicMock, Mock, patch

import httpx
import yaml
from fastapi.testclient import TestClient
from openai import APITimeoutError
from pydantic import ValidationError

from app.api.main import app
from app.indexing.keyword_store import KeywordIndex
from app.observability.records import compute_context_hash
from shared.contracts import Chunk
from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG
from tests.pipeline_fakes import FakeModel, make_query_resources


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.resources = make_query_resources()
        self.environment_root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("app.api.lifespan.PROJECT_ROOT", self.environment_root))
        legacy_config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        legacy_config["rag_backend"] = "legacy"
        config_path = self.environment_root / "legacy.yaml"
        config_path.write_text(yaml.safe_dump(legacy_config), encoding="utf-8")
        self.enterContext(patch("app.api.lifespan.DEFAULT_CONFIG", config_path))

    def test_startup_loads_root_environment_and_preserves_existing_values(self):
        (self.environment_root / ".env").write_text(
            "ADEN_TEST_LOADED=from-file\nADEN_TEST_EXISTING=from-file\n",
            encoding="utf-8",
        )
        with (
            patch.dict(os.environ, {"ADEN_TEST_EXISTING": "from-process"}, clear=True),
            self.client(),
        ):
            self.assertEqual(os.environ.get("ADEN_TEST_LOADED"), "from-file")
            self.assertEqual(os.environ["ADEN_TEST_EXISTING"], "from-process")

    @contextmanager
    def client(self, **options):
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            TestClient(app, **options) as client,
        ):
            yield client

    def test_http_query_runs_real_pipeline_and_returns_citations(self):
        question = "예약이 확정됐나요?"
        with self.client() as client:
            self.assertTrue(client.get("/health").json()["query_ready"])
            response = client.post("/query", json={"question": question})
        result = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "예약 접수와 확정은 별도입니다. [S1]")
        self.assertEqual(result["request_id"], response.headers["X-Request-ID"])
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
        self.resources.embedder.embed_query.assert_called_once_with(question)
        self.resources.qdrant.query_points.assert_called_once_with(
            self.resources.collection,
            query=[0.1, 0.2],
            limit=20,
            with_payload=True,
        )
        self.assertIn(question, self.resources.model.messages[1].content)
        self.resources.qdrant.close.assert_called_once()

    def test_http_logs_cover_success_validation_unready_and_unexpected_errors(self):
        with self.assertLogs("app.api.middleware", level="INFO") as logs:
            with self.client(raise_server_exceptions=False) as client:
                success = client.post("/query", json={"question": "예약 문의"})
                invalid = client.post("/query", json={"question": " "})
                with patch(
                    "app.api.routes.answer_query",
                    side_effect=RuntimeError("private failure"),
                ):
                    failed = client.post("/query", json={"question": "예약 문의"})
            with (
                patch(
                    "app.api.lifespan.load_resources",
                    side_effect=ServiceError("준비 중", 503),
                ),
                TestClient(app) as client,
            ):
                unready = client.post("/query", json={"question": "예약 문의"})
        responses = [success, invalid, failed, unready]
        self.assertEqual([r.status_code for r in responses], [200, 422, 500, 503])
        self.assertEqual(len(logs.records), len(responses))
        by_id = {record.request_id: record for record in logs.records}
        for response in responses:
            request_id = response.headers["X-Request-ID"]
            self.assertEqual(response.json()["request_id"], request_id)
            record = by_id[request_id]
            self.assertEqual(record.status_code, response.status_code)
            self.assertEqual(record.path, "/query")
            self.assertEqual(record.latency_scope, "http")
            self.assertGreaterEqual(record.latency_ms, 0)

    def test_http_log_does_not_include_question_query_or_headers(self):
        secret = "private-diagnostic-value"
        with self.assertLogs("app.api.middleware", level="INFO") as logs:
            with self.client() as client:
                response = client.post(
                    "/query?token=" + secret,
                    json={"question": secret},
                    headers={"Authorization": "Bearer " + secret},
                )
                unknown = client.get("/" + secret)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(unknown.status_code, 404)
        self.assertNotIn(secret, "\n".join(logs.output))
        self.assertEqual(logs.records[-1].path, "<unmatched>")

    def test_each_start_prepares_and_closes_its_own_resources(self):
        second = make_query_resources()
        second.model = FakeModel(["S1"], answer="재시작 후 답변입니다. [S1]")
        with patch(
            "app.api.lifespan.load_resources", side_effect=[self.resources, second]
        ):
            with TestClient(app) as client:
                first_response = client.post("/query", json={"question": "예약 문의"})
            self.assertEqual(first_response.status_code, 200)
            self.assertEqual(
                first_response.json()["answer"], self.resources.model.answer
            )
            self.assertIsNone(app.state.resources)
            self.resources.qdrant.close.assert_called_once()

            with TestClient(app) as client:
                second_response = client.post("/query", json={"question": "예약 문의"})
            self.assertEqual(second_response.status_code, 200)
            self.assertEqual(second_response.json()["answer"], second.model.answer)
            self.assertIsNone(app.state.resources)
            second.qdrant.close.assert_called_once()

    def test_invalid_yaml_stops_startup_before_loading_resources(self):
        invalid_config = self.environment_root / "invalid.yaml"
        invalid_config.write_text("dataset: basic\n", encoding="utf-8")

        with (
            patch("app.api.lifespan.DEFAULT_CONFIG", invalid_config),
            patch("app.api.lifespan.load_resources") as load,
            self.assertRaises(ValidationError),
            TestClient(app),
        ):
            pass

        load.assert_not_called()

    def test_empty_search_abstains_without_calling_model(self):
        self.resources.qdrant.query_points.return_value.points = []
        self.resources.model.invoke = Mock()
        with self.client() as client:
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "abstained")
        self.resources.model.invoke.assert_not_called()

    def test_request_id_connects_trace_retrieval_and_response(self):
        self.resources.tracer = MagicMock()
        trace = self.resources.tracer.start_request.return_value
        with self.client() as client:
            response = client.post("/query", json={"question": "예약 문의"})
        request_id = response.json()["request_id"]
        self.assertEqual(
            self.resources.tracer.start_request.call_args.args[0], request_id
        )
        retrieval = trace.record_retrieval.call_args.kwargs
        self.assertEqual(
            retrieval["final_context_hash"],
            compute_context_hash(retrieval["final_context_chunks"]),
        )
        self.assertEqual(trace.finish.call_args.kwargs["status"], "answered")

    def test_preparation_failure_keeps_docs_available_and_query_unready(self):
        with (
            patch(
                "app.api.lifespan.load_resources",
                side_effect=ServiceError("서버 설정 확인", 503),
            ),
            TestClient(app) as client,
        ):
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertFalse(health.json()["query_ready"])
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["request_id"], response.headers["X-Request-ID"]
        )

    def test_configured_default_and_explicit_mode_reach_the_real_pipeline(self):
        config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config["rag_backend"] = "legacy"
        config["dataset"] = "custom-dataset"
        config["retrieval"]["default_mode"] = "hybrid"
        point = self.resources.qdrant.query_points.return_value.points[0]
        self.resources.retrieval.keyword_index = KeywordIndex([Chunk(**point.payload)])
        self.resources.tracer = MagicMock()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with (
                patch("app.api.lifespan.DEFAULT_CONFIG", path),
                self.client() as client,
            ):
                for payload, expected in [
                    ({"question": "예약 문의"}, "hybrid"),
                    ({"question": "예약 문의", "retrieval_mode": "naive"}, "naive"),
                ]:
                    response = client.post("/query", json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    call = self.resources.tracer.start_request.call_args
                    self.assertEqual(call.kwargs["retrieval_mode"], expected)
                    self.assertEqual(call.kwargs["dataset_id"], "custom-dataset")

    def test_preparation_and_query_errors_do_not_expose_private_details(self):
        with (
            patch(
                "app.api.lifespan.load_resources",
                side_effect=RuntimeError("private startup"),
            ),
            self.assertLogs("app.api.lifespan", level="ERROR") as logs,
            TestClient(app) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private startup", response.text + "\n".join(logs.output))
        with (
            self.client(raise_server_exceptions=False) as client,
            patch(
                "app.api.routes.answer_query", side_effect=RuntimeError("private query")
            ),
            self.assertLogs("app.api.errors", level="ERROR") as logs,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private query", response.text + "\n".join(logs.output))
        self.assertEqual(
            response.json()["request_id"], response.headers["X-Request-ID"]
        )

    def test_concurrent_requests_run_off_the_event_loop_and_keep_ids_separate(self):
        barrier = Barrier(2)
        points = self.resources.qdrant.query_points.return_value

        def query_points(*args, **kwargs):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            barrier.wait(timeout=5)
            return points

        self.resources.qdrant.query_points.side_effect = query_points
        with self.assertLogs("app.api.middleware", level="INFO") as logs:
            with self.client() as client, ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(
                    pool.map(
                        lambda question: client.post(
                            "/query", json={"question": question}
                        ),
                        ["첫 번째 질문", "두 번째 질문"],
                    )
                )
        self.assertEqual([response.status_code for response in responses], [200, 200])
        ids = {response.headers["X-Request-ID"] for response in responses}
        self.assertEqual(len(ids), 2)
        self.assertEqual({record.request_id for record in logs.records}, ids)
        for response in responses:
            self.assertEqual(
                response.json()["request_id"], response.headers["X-Request-ID"]
            )

    def test_processing_errors_keep_the_public_status_and_request_id(self):
        cases = [
            ("retrieval", NotImplementedError(), 501),
            ("retrieval", RuntimeError("private retrieval"), 503),
            (
                "model",
                APITimeoutError(request=httpx.Request("POST", "https://example.test")),
                504,
            ),
            ("citation", None, 502),
        ]
        for kind, error, code in cases:
            with self.subTest(code=code):
                self.resources = make_query_resources()
                if kind == "retrieval":
                    self.resources.qdrant.query_points.side_effect = error
                elif kind == "model":
                    self.resources.model.invoke = Mock(side_effect=error)
                else:
                    self.resources.model.source_ids = ["S9"]
                with self.client() as client:
                    response = client.post("/query", json={"question": "예약 문의"})
                self.assertEqual(response.status_code, code, response.text)
                self.assertEqual(response.json()["status"], "error")
                self.assertEqual(
                    response.json()["request_id"], response.headers["X-Request-ID"]
                )
                self.assertNotIn("private retrieval", response.text)
                self.resources.qdrant.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
