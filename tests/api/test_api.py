import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
import yaml
from fastapi.testclient import TestClient
from openai import APITimeoutError

from app.api.app import create_app
from shared.contracts import Chunk, GenerationResult, ModelAnswer, QueryResponse, RetrievedChunk
from shared.exceptions import ServiceError
from shared.settings import DEFAULT_CONFIG
from app.observability.records import compute_context_hash, normalize_run_record
from tests.pipeline_fakes import FakeModel, make_query_resources


class ApiTests(unittest.TestCase):
    def test_HTTP_query_runs_real_pipeline_and_emits_full_record(self):
        resources = make_query_resources()
        records = []
        question = "예약이 확정됐나요?"
        with patch("app.api.lifespan.load_resources", return_value=resources):
            app = create_app(run_record_sink=records.append)
            with TestClient(app) as client:
                self.assertTrue(client.get("/health").json()["query_ready"])
                response = client.post("/query", json={"question": question})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "answered")
                self.assertEqual(result["answer"], "예약 접수와 확정은 별도입니다. [S1]")
                self.assertEqual(result["request_id"], response.headers["X-Request-ID"])
                self.assertEqual(result["citations"], [{
                    "source_id": "S1", "book_id": "DEMO", "file_name": "DEMO.pdf", "page": 1,
                }])
                self.assertEqual(client.post("/query", json={"question": " "}).status_code, 422)
            self.assertIsNone(app.state.resources)
        resources.close.assert_called_once()
        resources.embedder.embed_query.assert_called_once_with(question)
        resources.qdrant.query_points.assert_called_once_with(
            resources.collection, query=[0.1, 0.2],
            limit=20, with_payload=True,
        )
        self.assertIn(question, resources.model.messages[1].content)
        self.assertIn("예약 접수와 확정은 별도입니다.", resources.model.messages[1].content)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["request_id"], result["request_id"])
        self.assertEqual(record["question"], question)
        self.assertEqual(record["retrieval_mode"], "naive")
        self.assertEqual(record["status"], "answered")
        self.assertEqual(record["citations"], result["citations"])
        self.assertEqual(record["final_context_chunks"][0]["chunk_id"], "DEMO-p1-c1")
        self.assertEqual(record["final_context_hash"], compute_context_hash(record["final_context_chunks"]))
        normalized = normalize_run_record(record)
        self.assertEqual(normalized["usage"], {"input_tokens": 100, "output_tokens": 20, "status": "complete"})
        self.assertEqual(record["attempts"][0]["input_tokens"], 100)
        self.assertEqual(record["attempts"][0]["output_tokens"], 20)
        self.assertEqual(record["latency_scope"], "http")
        self.assertGreaterEqual(record["latency_ms"], 0)

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

    def test_create_app_does_not_load_resources_before_startup(self):
        with patch("app.api.lifespan.load_resources", return_value=self.resources) as load:
            app = create_app()
            self.assertIsNone(app.state.resources)
            load.assert_not_called()
            with TestClient(app) as client:
                self.assertTrue(client.get("/health").json()["query_ready"])
                load.assert_called_once()
                self.assertIs(app.state.resources, self.resources)
            self.assertIsNone(app.state.resources)
        self.resources.close.assert_called_once()

    def test_apps_keep_resources_isolated_and_close_them_at_shutdown(self):
        first_resources = SimpleNamespace(label="first", close=Mock())
        second_resources = SimpleNamespace(label="second", close=Mock())

        def answer(_payload, resources, request_id, _settings, *, run_record):
            return QueryResponse(
                request_id=request_id, status="abstained", answer="",
                reason=resources.label, citations=[],
            )

        with (
            patch(
                "app.api.lifespan.load_resources",
                side_effect=[first_resources, second_resources],
            ) as load,
            patch("app.api.routes.answer_query", side_effect=answer),
        ):
            first_app = create_app()
            second_app = create_app()
            with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
                self.assertTrue(first_client.get("/health").json()["query_ready"])
                self.assertTrue(second_client.get("/health").json()["query_ready"])
                self.assertIs(first_app.state.resources, first_resources)
                self.assertIs(second_app.state.resources, second_resources)
                self.assertIsNot(first_app.state.resources, second_app.state.resources)
                self.assertEqual(load.call_count, 2)
                first_answer = first_client.post("/query", json={"question": "예약 문의"})
                second_answer = second_client.post("/query", json={"question": "예약 문의"})
                self.assertEqual(first_answer.json()["reason"], "first")
                self.assertEqual(second_answer.json()["reason"], "second")
            self.assertIsNone(first_app.state.resources)
            self.assertIsNone(second_app.state.resources)
        first_resources.close.assert_called_once()
        second_resources.close.assert_called_once()

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
        app = create_app()
        with (
            patch("app.service.resources.load_dotenv"),
            patch("app.service.resources.import_module", side_effect=ModuleNotFoundError(name="app.indexing.embeddings")),
            patch("app.service.resources.QdrantClient") as qdrant,
            patch("app.service.resources.OpenAI") as model,
            TestClient(app) as client,
        ):
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)
            self.assertFalse(client.get("/health").json()["query_ready"])
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("A의 검색 모듈", response.json()["reason"])
        self.assertIsNone(app.state.resources)
        qdrant.assert_not_called()
        model.assert_not_called()

    def test_preparation_failure_is_not_reported_as_ready(self):
        app = create_app()
        with (
            patch("app.api.lifespan.load_resources", side_effect=ServiceError("서버 설정 확인이 필요합니다.", 503)),
            TestClient(app) as client,
        ):
            self.assertFalse(client.get("/health").json()["query_ready"])
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)
            response = client.post("/query", json={"question": "예약 문의"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "error")
        self.assertIsNone(app.state.resources)

    def test_custom_config_loads_resources_and_uses_configured_default_mode(self):
        config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config["dataset"] = "custom-dataset"
        config["retrieval"]["default_mode"] = "hybrid"

        def answer_with_request_id(_payload, _resources, request_id, _settings, *, run_record):
            return QueryResponse(
                request_id=request_id, status="abstained", answer="",
                reason="문서가 부족합니다.", citations=[],
            )
        with TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "custom.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with (
                patch("app.api.lifespan.load_resources", return_value=self.resources) as load,
                patch("app.api.routes.answer_query", side_effect=answer_with_request_id) as answer,
            ):
                app = create_app(config_path)
                self.assertIsNone(app.state.resources)
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").json(), {
                        "status": "ok", "dataset": "custom-dataset", "query_ready": True,
                    })
                    response = client.post("/query", json={"question": "예약 문의"})
                    load.assert_called_once()
                    settings = load.call_args.args[0]
                    self.assertEqual(settings.dataset, "custom-dataset")
                    answer.assert_called_once()
                    payload, resources, request_id, used_settings = answer.call_args.args
                    self.assertEqual(payload.retrieval_mode, "hybrid")
                    self.assertIs(resources, self.resources)
                    self.assertEqual(request_id, response.headers["X-Request-ID"])
                    self.assertEqual(used_settings.dataset, "custom-dataset")
                self.assertIsNone(app.state.resources)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "abstained")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
        self.resources.close.assert_called_once()

    def test_unexpected_preparation_error_is_sanitized(self):
        app = create_app()
        with (
            patch("app.api.lifespan.load_resources", side_effect=RuntimeError("private startup detail")),
            self.assertLogs("app.api.lifespan", level="ERROR") as logs,
            TestClient(app) as client,
        ):
            self.assertFalse(client.get("/health").json()["query_ready"])
            self.assertEqual(client.get("/docs").status_code, 200)
            response = client.post("/query", json={"question": "예약 문의"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["reason"], "문서 검색 준비에 실패했습니다. 서버 설정을 확인해 주세요.")
        self.assertNotIn("private startup detail", response.text)
        self.assertNotIn("private startup detail", "\n".join(logs.output))
        self.assertIsNone(app.state.resources)

    def test_unexpected_query_error_keeps_safe_response_and_closes_resources(self):
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            patch("app.api.routes.answer_query", side_effect=RuntimeError("private query detail")) as answer,
            self.assertLogs("app.api.errors", level="ERROR") as logs,
            TestClient(create_app(), raise_server_exceptions=False) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        answer.assert_called_once()
        self.resources.close.assert_called_once()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
        self.assertNotIn("private query detail", response.text)
        self.assertNotIn("private query detail", "\n".join(logs.output))

    def test_concurrent_requests_keep_records_and_http_sink_data_separate(self):
        barrier = Barrier(2)
        lock = Lock()
        records_by_id = {}
        sinks = []

        def answer(payload, resources, request_id, settings, *, run_record):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise AssertionError("synchronous query ran on the event loop")
            with lock:
                records_by_id[request_id] = run_record
            run_record.update(request_id=request_id, question=payload.question)
            barrier.wait(timeout=5)
            return QueryResponse(
                request_id=request_id, status="abstained", answer="",
                reason="자료가 부족합니다.", citations=[],
            )

        questions = ["첫 번째 질문", "두 번째 질문"]
        with (
            patch("app.api.lifespan.load_resources", return_value=self.resources),
            patch("app.api.routes.answer_query", side_effect=answer),
            TestClient(create_app(run_record_sink=sinks.append)) as client,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            responses = list(executor.map(
                lambda question: client.post("/query", json={"question": question}),
                questions,
            ))

        self.assertEqual([response.status_code for response in responses], [200, 200])
        response_by_id = {response.headers["X-Request-ID"]: response for response in responses}
        self.assertEqual(len(response_by_id), 2)
        self.assertEqual(set(records_by_id), set(response_by_id))
        self.assertEqual(len({id(record) for record in records_by_id.values()}), 2)
        self.assertEqual(
            {request_id: record["question"] for request_id, record in records_by_id.items()},
            {response.json()["request_id"]: question for response, question in zip(responses, questions)},
        )
        self.assertEqual(len(sinks), 2)
        sink_by_id = {record["request_id"]: record for record in sinks}
        self.assertEqual(set(sink_by_id), set(response_by_id))
        for request_id, record in sink_by_id.items():
            self.assertEqual(record["latency_scope"], "http")
            self.assertGreaterEqual(record["latency_ms"], 0)
            self.assertEqual(record["question"], records_by_id[request_id]["question"])
            self.assertEqual(response_by_id[request_id].json()["request_id"], request_id)

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
