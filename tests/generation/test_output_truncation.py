"""출력 한도에서 잘린 응답(LengthFinishReasonError)의 재생성·기록·HTTP 상태."""

import unittest
from unittest.mock import Mock

from openai import LengthFinishReasonError
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion

from app.generation.chain import TRUNCATION_FEEDBACK, generate_answer
from app.generation.context import build_context
from app.service.query import answer_query
from shared.contracts import Chunk, QueryRequest, RetrievedChunk
from shared.exceptions import ServiceError
from shared.settings import load_settings, DEFAULT_CONFIG
from tests.pipeline_fakes import FakeModel, make_query_resources


def truncated():
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=2048, total_tokens=2148)
    return LengthFinishReasonError(completion=ChatCompletion.model_construct(usage=usage))


class OutputTruncationTests(unittest.TestCase):
    def setUp(self):
        chunk = Chunk("DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다.")
        self.context = build_context([RetrievedChunk(chunk, 1, 1.0, "dense")], 1, 4000, len)

    def test_truncated_first_attempt_is_regenerated_with_feedback(self):
        model = FakeModel(["S1"])
        valid = FakeModel(["S1"]).invoke([])
        calls = []

        def invoke(values, *args, **kwargs):
            calls.append(values)
            if len(calls) == 1:
                raise truncated()
            return valid

        model.invoke = Mock(side_effect=invoke)
        result = generate_answer("예약이 확정됐나요?", self.context, model, max_attempts=2)
        self.assertEqual(result.model_answer.source_ids, ["S1"])
        self.assertEqual([item["attempt"] for item in result.attempts], [1, 2])
        self.assertEqual(result.attempts[0]["error"], "LengthFinishReasonError")
        self.assertEqual(result.attempts[0]["output_tokens"], 2048)
        self.assertIsNone(result.attempts[1]["error"])
        system = [call.to_messages()[0].content for call in calls]
        self.assertNotIn(TRUNCATION_FEEDBACK, system[0])
        self.assertIn(TRUNCATION_FEEDBACK, system[1])

    def test_truncation_on_every_attempt_raises_after_recording_each(self):
        model = FakeModel(["S1"])
        model.invoke = Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(truncated()))
        attempts = []
        with self.assertRaises(LengthFinishReasonError):
            generate_answer("예약이 확정됐나요?", self.context, model, attempt_log=attempts, max_attempts=2)
        self.assertEqual([item["attempt"] for item in attempts], [1, 2])
        self.assertEqual({item["error"] for item in attempts}, {"LengthFinishReasonError"})

    def test_other_model_errors_are_still_not_retried(self):
        model = FakeModel(["S1"])
        model.invoke = Mock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            generate_answer("예약이 확정됐나요?", self.context, model, max_attempts=3)
        self.assertEqual(model.invoke.call_count, 1)

    def test_service_returns_502_and_records_truncation_error_type(self):
        resources = make_query_resources()
        resources.model.invoke = Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(truncated()))
        settings = load_settings(DEFAULT_CONFIG)
        record = {}
        with self.assertRaises(ServiceError) as caught:
            answer_query(QueryRequest(question="예약 문의"), resources, "req-trunc", settings, run_record=record)
        resources.close()
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error_type"], "LengthFinishReasonError")
        self.assertEqual(len(record["attempts"]), settings.generation_max_attempts)


if __name__ == "__main__":
    unittest.main()
