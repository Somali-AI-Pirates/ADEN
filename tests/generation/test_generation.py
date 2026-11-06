import unittest
from unittest.mock import Mock

from app.generation.chain import generate_answer
from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk
from tests.pipeline_fakes import FakeModel


class GenerationTests(unittest.TestCase):
    def setUp(self):
        chunk = Chunk("DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다.")
        self.context = build_context([RetrievedChunk(chunk, 1, 1.0, "dense")], 1, 4000, len)

    def test_generates_from_saved_context_and_keeps_usage(self):
        model = FakeModel(["S1"])
        result = generate_answer("예약이 확정됐나요?", self.context, model, defense="방어 지시")
        self.assertEqual(result.model_answer.status, "answered")
        self.assertIn(self.context.text, model.messages[1].content)
        self.assertIn("방어 지시", model.messages[0].content)
        self.assertEqual(result.usage["input_tokens"], 100)
        self.assertEqual(result.attempts[0]["model_output"]["content"], "모델 출력")
        self.assertEqual(result.attempts[0]["model"], "test-model")
        self.assertEqual(result.attempts[0]["input_tokens"], 100)
        self.assertIn(self.context.text, result.attempts[0]["model_input"][1]["content"])

    def test_rejects_unknown_or_missing_answer_sources(self):
        cases = [
            (["S9"], "예약 접수와 확정은 별도입니다. [S1]"),
            ([], "예약 접수와 확정은 별도입니다."),
            (["S1"], "예약 접수와 확정은 별도입니다. [S1][S9]"),
        ]
        for source_ids, answer in cases:
            with self.subTest(source_ids=source_ids, answer=answer), self.assertRaises(ValueError):
                generate_answer("예약이 확정됐나요?", self.context, FakeModel(source_ids, answer))

    def test_rejects_blank_answer_or_reason(self):
        for field in ("answer", "reason"):
            with self.subTest(field=field):
                result = FakeModel(["S1"]).invoke([])
                setattr(result["parsed"], field, "   ")
                model = FakeModel(["S1"])
                model.invoke = Mock(return_value=result)
                with self.assertRaises(ValueError):
                    generate_answer("예약 문의", self.context, model)

    def test_adds_valid_body_marks_missing_from_source_ids(self):
        result = generate_answer("예약이 확정됐나요?", self.context, FakeModel([]))
        self.assertEqual(result.model_answer.source_ids, ["S1"])

    def test_records_actual_messages_output_and_usage(self):
        trace = Mock()
        generate_answer("예약이 확정됐나요?", self.context, FakeModel(["S1"]), trace=trace)
        record = trace.record_attempt.call_args.kwargs
        self.assertEqual(record["model"], "test-model")
        self.assertEqual(record["input_tokens"], 100)
        self.assertEqual(record["output_tokens"], 20)
        self.assertIn(self.context.text, record["model_input"][1]["content"])
        self.assertEqual(record["model_output"]["content"], "모델 출력")

    def test_invalid_output_is_regenerated_and_every_attempt_is_kept(self):
        model = FakeModel(["S1"])
        invalid = FakeModel(["S9"]).invoke([])
        valid = FakeModel(["S1"]).invoke([])
        model.invoke = Mock(side_effect=[invalid, valid])
        result = generate_answer("예약이 확정됐나요?", self.context, model, max_attempts=2)
        self.assertEqual(result.model_answer.source_ids, ["S1"])
        self.assertEqual([item["attempt"] for item in result.attempts], [1, 2])
        self.assertEqual(result.attempts[0]["error"], "ValueError")
        self.assertIsNone(result.attempts[1]["error"])

    def test_retry_limit_keeps_every_attempt_before_raising(self):
        model = FakeModel(["S9"])
        attempts = []
        with self.assertRaises(ValueError):
            generate_answer("예약이 확정됐나요?", self.context, model, attempt_log=attempts, max_attempts=3)
        self.assertEqual([item["attempt"] for item in attempts], [1, 2, 3])

    def test_failed_model_call_records_input_and_error_type(self):
        trace = Mock()
        model = FakeModel(["S1"])
        model.invoke = Mock(side_effect=RuntimeError("private internal detail"))
        with self.assertRaises(RuntimeError):
            generate_answer("예약이 확정됐나요?", self.context, model, trace=trace)
        record = trace.record_attempt.call_args.kwargs
        self.assertEqual(record["error"], "RuntimeError")
        self.assertNotIn("private internal detail", str(record))


if __name__ == "__main__":
    unittest.main()
