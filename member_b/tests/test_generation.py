import unittest
from unittest.mock import Mock

from langchain_core.messages import AIMessage

from shared.contracts import Chunk, ModelAnswer, RetrievedChunk
from member_b.generation.chain import generate_answer
from member_b.generation.context import build_context


class FakeModel:
    model_name = "test-model"
    max_tokens = 2048

    def __init__(self, source_ids, answer="예약 접수와 확정은 별도입니다. [S1]"):
        self.source_ids = source_ids
        self.answer = answer
        self.messages = []

    def with_structured_output(self, schema, **options):
        assert schema is ModelAnswer
        assert options == {"method": "json_schema", "include_raw": True}
        return self

    def invoke(self, messages):
        self.messages = messages
        return {
            "parsed": ModelAnswer(
                status="answered", answer=self.answer,
                reason="제공된 자료에 근거합니다.", source_ids=self.source_ids,
            ),
            "parsing_error": None,
            "raw": AIMessage(
                content="모델 출력",
                usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            ),
        }


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
