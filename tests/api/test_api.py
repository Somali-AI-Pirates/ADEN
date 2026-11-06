from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import yaml
from hashlib import sha256
from fastapi.testclient import TestClient
from openai import APITimeoutError

from app.api.app import create_app
from shared.contracts import Chunk, GenerationResult, ModelAnswer, QueryResponse, RetrievedChunk
from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG
from tests.generation.test_generation import FakeModel


class ApiTests(unittest.TestCase):
    def test_HTTP_record_sink_gets_full_record_and_server_latency(self):
        records = []
        self.resources.model = FakeModel(["S1"])
        self.resources.retriever.return_value = self.candidates
        self.resources.context_hasher = Mock(return_value="sha256:fake-C-hash")
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            TestClient(create_app(run_record_sink=records.append)) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["request_id"], response.json()["request_id"])
        self.assertEqual(records[0]["latency_scope"], "http")
        self.assertGreaterEqual(records[0]["latency_ms"], 0)
        self.assertEqual(records[0]["attempts"][0]["input_tokens"], 100)

    def test_record_sink_failure_does_not_replace_business_answer(self):
        self.resources.model = FakeModel(["S1"])
        self.resources.retriever.return_value = self.candidates
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            self.assertLogs("app.api.middleware", level="WARNING"),
            TestClient(create_app(run_record_sink=Mock(side_effect=RuntimeError("private sink error")))) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "answered")

    def setUp(self):
        self.resources = SimpleNamespace(model=object(), count_tokens=len, close=Mock(),
                                         retriever=Mock(), tracer=None, context_hasher=None)
        self.candidates = [RetrievedChunk(
            Chunk("DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다."),
            1, 1.0, "dense",
        )]
        self.generated = GenerationResult(
            model_answer=ModelAnswer(
                status="answered", answer="예약 접수와 확정은 별도입니다. [S1]",
                reason="문서에 근거합니다.", source_ids=["S1"],
            ),
            attempts=[], usage=None,
        )

    def test_default_app_loads_resources_and_answers_query(self):
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            patch.object(self.resources, "retriever", return_value=self.candidates) as retrieve,
            patch("app.service.query.generate_answer", return_value=self.generated),
        ):
            with TestClient(create_app()) as client:
                self.assertTrue(client.get("/health").json()["query_ready"])
                response = client.post("/query", json={"question": "예약이 확정됐나요?"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "answered")
                self.assertEqual(data["citations"][0]["page"], 1)
                self.assertEqual(data["request_id"], response.headers["X-Request-ID"])
                self.assertEqual(retrieve.call_args.args[1], "naive")
                self.assertEqual(retrieve.call_args.kwargs["top_n"], 20)
                self.assertEqual(client.post("/query", json={"question": " "}).status_code, 422)
            self.resources.close.assert_called_once()

    def test_empty_search_abstains_without_model_call(self):
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            patch.object(self.resources, "retriever", return_value=[]),
            patch("app.service.query.generate_answer") as generate,
            TestClient(create_app()) as client,
        ):
            response = client.post("/query", json={"question": "자료에 없는 질문"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "abstained")
            generate.assert_not_called()

    def test_request_id_connects_retrieval_and_final_response(self):
        trace = Mock()
        self.resources.tracer = Mock()
        self.resources.tracer.start_request.return_value = trace
        self.resources.context_hasher = Mock(return_value="sha256:test-context")
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            patch.object(self.resources, "retriever", return_value=self.candidates),
            patch("app.service.query.generate_answer", return_value=self.generated),
            TestClient(create_app()) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        request_id = response.json()["request_id"]
        self.assertEqual(self.resources.tracer.start_request.call_args.args[0], request_id)
        retrieval = trace.record_retrieval.call_args.kwargs
        self.assertEqual(retrieval["retrieved_candidates"][0]["book_id"], "DEMO")
        self.assertEqual(retrieval["final_context_chunks"][0]["source_id"], "S1")
        self.assertEqual(retrieval["final_context_hash"], "sha256:test-context")
        self.resources.context_hasher.assert_called_once_with(retrieval["final_context_chunks"])
        self.assertEqual(retrieval["model_context_hash"], sha256(retrieval["model_context_text"].encode()).hexdigest())
        self.assertEqual(trace.finish.call_args.kwargs["status"], "answered")

    def test_B_only_starts_docs_and_explains_missing_A(self):
        with (
            patch("app.service.resources.load_dotenv"),
            patch("app.service.resources.import_module", side_effect=ModuleNotFoundError(name="app.indexing.embeddings")),
            patch("app.service.resources.QdrantClient") as qdrant,
            patch("app.service.resources.OpenAI") as model,
            TestClient(create_app()) as client,
        ):
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)
            self.assertFalse(client.get("/health").json()["query_ready"])
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("A의 검색 모듈", response.json()["reason"])
        qdrant.assert_not_called()
        model.assert_not_called()

    def test_preparation_failure_is_not_reported_as_ready(self):
        with (
            patch("app.api.lifespan.load_resources", side_effect=ServiceError("서버 설정 확인이 필요합니다.", 503)),
            TestClient(create_app()) as client,
        ):
            self.assertFalse(client.get("/health").json()["query_ready"])
            response = client.post("/query", json={"question": "예약 문의"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "error")

    def test_custom_config_and_handler_skip_default_resource_loading(self):
        async def answer(payload, request_id):
            return QueryResponse(
                request_id=request_id, status="abstained", answer="",
                reason="문서가 부족합니다.", citations=[],
            )

        handler = AsyncMock(side_effect=answer)
        config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config["dataset"] = "custom-dataset"
        config["retrieval"]["default_mode"] = "hybrid"
        with TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "custom.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with patch("app.api.lifespan.load_resources") as load:
                app = create_app(config_path, query_handler=handler)
                self.assertIs(app.state.query_handler, handler)
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").json(), {
                        "status": "ok", "dataset": "custom-dataset", "query_ready": True,
                    })
                    response = client.post("/query", json={"question": "예약 문의"})
                load.assert_not_called()
                self.assertIsNone(app.state.query_handler)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "abstained")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
        handler.assert_awaited_once()
        payload, request_id = handler.await_args.args
        self.assertEqual(payload.retrieval_mode, "hybrid")
        self.assertEqual(request_id, response.headers["X-Request-ID"])

    def test_unexpected_preparation_error_is_sanitized(self):
        with (
            patch("app.api.lifespan.load_resources", side_effect=RuntimeError("private startup detail")),
            self.assertLogs("app.api.lifespan", level="ERROR") as logs,
            TestClient(create_app()) as client,
        ):
            self.assertFalse(client.get("/health").json()["query_ready"])
            self.assertEqual(client.get("/docs").status_code, 200)
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["reason"], "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요.")
        self.assertNotIn("private startup detail", response.text)
        self.assertNotIn("private startup detail", "\n".join(logs.output))

    def test_unexpected_handler_error_keeps_safe_response_and_request_id(self):
        handler = AsyncMock(side_effect=RuntimeError("private handler detail"))
        with (
            self.assertLogs("app.api.errors", level="ERROR") as logs,
            TestClient(create_app(query_handler=handler), raise_server_exceptions=False) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
        self.assertNotIn("private handler detail", response.text)
        self.assertNotIn("private handler detail", "\n".join(logs.output))

    def test_processing_errors_are_distinct_and_keep_request_id(self):
        timeout = APITimeoutError(request=httpx.Request("POST", "https://example.test"))
        invalid = self.generated.model_copy(update={
            "model_answer": self.generated.model_answer.model_copy(update={"source_ids": ["S9"]}),
        })
        cases = [
            (NotImplementedError(), None, 501),
            (RuntimeError("private internal detail"), None, 503),
            (None, timeout, 504),
            (None, invalid, 502),
        ]
        for retrieval_error, model_result, code in cases:
            with self.subTest(code=code):
                with (
                    patch("app.api.lifespan.load_resources", return_value=self.resources),
                    patch.object(
                        self.resources, "retriever",
                        side_effect=retrieval_error, return_value=self.candidates,
                    ),
                    patch(
                        "app.service.query.generate_answer",
                        side_effect=model_result if isinstance(model_result, Exception) else None,
                        return_value=model_result or self.generated,
                    ),
                    TestClient(create_app()) as client,
                ):
                    response = client.post("/query", json={"question": "예약 문의"})
                    self.assertEqual(response.status_code, code, response.text)
                    self.assertEqual(response.json()["status"], "error")
                    self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
                    self.assertNotIn("private internal detail", response.text)


if __name__ == "__main__":
    unittest.main()
