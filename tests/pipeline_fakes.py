"""Shared fake model and external resources for pipeline tests."""

from dataclasses import asdict
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.messages import AIMessage

from app.service.resources import Resources
from shared.contracts import Chunk, ModelAnswer


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
                status="answered",
                answer=self.answer,
                reason="제공된 자료에 근거합니다.",
                source_ids=self.source_ids,
            ),
            "parsing_error": None,
            "raw": AIMessage(
                content="모델 출력",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            ),
        }


def make_query_resources():
    """Build fresh fake external resources around the real retrieval function."""
    chunk = Chunk("DEMO-p1-c1", "DEMO", "DEMO.pdf", 1, "예약 접수와 확정은 별도입니다.")
    vector = SimpleNamespace(tolist=lambda: [0.1, 0.2])
    embedder = SimpleNamespace(embed_query=Mock(return_value=vector))
    qdrant = SimpleNamespace(
        query_points=Mock(
            return_value=SimpleNamespace(
                points=[SimpleNamespace(payload=asdict(chunk), score=0.9)],
            )
        ),
        close=Mock(),
    )
    cleanup = ExitStack()
    cleanup.callback(qdrant.close)
    return Resources(
        embedder=embedder,
        qdrant=qdrant,
        collection="existing-test-index",
        model=FakeModel(["S1"]),
        count_tokens=len,
        tracer=None,
        cleanup=cleanup,
    )
