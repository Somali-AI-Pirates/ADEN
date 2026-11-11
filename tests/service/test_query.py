from unittest.mock import MagicMock

from app.indexing.keyword_store import KeywordIndex
from app.observability.records import normalize_run_record
from app.service.query import answer_query
from shared.contracts import Chunk, QueryRequest
from shared.settings import load_settings
from tests.pipeline_fakes import make_query_resources


def test_service_resolves_default_once_without_mutating_the_callers_request():
    settings = load_settings()
    settings.retrieval.default_mode = "hybrid"
    resources = make_query_resources()
    resources.tracer = MagicMock()
    point = resources.qdrant.query_points.return_value.points[0]
    resources.retrieval.keyword_index = KeywordIndex([Chunk(**point.payload)])
    request = QueryRequest(question="예약 문의")
    record = {}
    try:
        response = answer_query(
            request, resources, "service-default", settings, run_record=record
        )
    finally:
        resources.close()

    assert request.retrieval_mode is None
    assert record["retrieval_mode"] == "hybrid"
    assert resources.tracer.start_request.call_args.kwargs["retrieval_mode"] == "hybrid"
    assert response.status == "answered"
    assert normalize_run_record(record)["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "status": "complete",
    }
    assert record["latency_scope"] == "service"
