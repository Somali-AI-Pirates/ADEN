from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from hashlib import sha256
from fastapi.testclient import TestClient
from openai import APITimeoutError

from member_b.api.app import create_app
from shared.contracts import Chunk, GenerationResult, ModelAnswer, RetrievedChunk
from shared.exceptions import ServiceError
from member_c.observability.records import compute_context_hash


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.resources = SimpleNamespace(model=object(), count_tokens=len, close=Mock())
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
            patch("member_b.api.app.load_resources", return_value=self.resources),
            patch("member_b.service.query.retrieve", return_value=self.candidates) as retrieve,
            patch("member_b.service.query.generate_answer", return_value=self.generated),
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
            patch("member_b.api.app.load_resources", return_value=self.resources),
            patch("member_b.service.query.retrieve", return_value=[]),
            patch("member_b.service.query.generate_answer") as generate,
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
        with (
            patch("member_b.api.app.load_resources", return_value=self.resources),
            patch("member_b.service.query.retrieve", return_value=self.candidates),
            patch("member_b.service.query.generate_answer", return_value=self.generated),
            TestClient(create_app()) as client,
        ):
            response = client.post("/query", json={"question": "예약 문의"})
        request_id = response.json()["request_id"]
        self.assertEqual(self.resources.tracer.start_request.call_args.args[0], request_id)
        retrieval = trace.record_retrieval.call_args.kwargs
        self.assertEqual(retrieval["retrieved_candidates"][0]["book_id"], "DEMO")
        self.assertEqual(retrieval["final_context_chunks"][0]["source_id"], "S1")
        self.assertEqual(retrieval["final_context_hash"], compute_context_hash(retrieval["final_context_chunks"]))
        self.assertEqual(retrieval["model_context_hash"], sha256(retrieval["model_context_text"].encode()).hexdigest())
        self.assertEqual(trace.finish.call_args.kwargs["status"], "answered")

    def test_preparation_failure_is_not_reported_as_ready(self):
        with (
            patch("member_b.api.app.load_resources", side_effect=ServiceError("서버 설정 확인이 필요합니다.", 503)),
            TestClient(create_app()) as client,
        ):
            self.assertFalse(client.get("/health").json()["query_ready"])
            response = client.post("/query", json={"question": "예약 문의"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "error")

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
                    patch("member_b.api.app.load_resources", return_value=self.resources),
                    patch(
                        "member_b.service.query.retrieve",
                        side_effect=retrieval_error, return_value=self.candidates,
                    ),
                    patch(
                        "member_b.service.query.generate_answer",
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
