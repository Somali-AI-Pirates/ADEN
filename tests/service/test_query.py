from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from app.indexing.keyword_store import KeywordIndex
from app.observability.records import normalize_run_record
from app.observability.tracing import Tracer
from app.service.query import answer_query, prompt_hash, record_query
from shared.contracts import Chunk
from shared.settings import load_settings
from tests.pipeline_fakes import make_query_resources


def test_service_uses_default_mode_in_record_and_trace():
    settings = load_settings()
    settings.retrieval.default_mode = "hybrid"
    resources = make_query_resources()
    resources.tracer = Mock()
    point = resources.qdrant.query_points.return_value.points[0]
    resources.retrieval.keyword_index = KeywordIndex([Chunk(**point.payload)])
    try:
        with patch("app.service.query.prompt_hash", wraps=prompt_hash) as hash_prompt:
            record = record_query("예약 문의", resources, "service-default", settings)
    finally:
        resources.close()

    hash_prompt.assert_called_once()
    assert record["retrieval_mode"] == "hybrid"
    assert resources.tracer.start_request.call_args.kwargs["retrieval_mode"] == "hybrid"
    assert record["status"] == "answered"
    assert normalize_run_record(record)["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "status": "complete",
    }
    assert record["latency_scope"] == "service"


def test_disabled_trace_skips_http_record_preparation():
    resources = make_query_resources()
    resources.tracer = Tracer()
    try:
        with (
            patch("app.service.query.prompt_hash") as hash_prompt,
            patch("app.service.query.candidate_records") as serialize_candidates,
            patch.object(AIMessage, "model_dump") as serialize_model_output,
        ):
            response = answer_query("예약 문의", resources, "request", load_settings())
    finally:
        resources.close()

    assert response.status == "answered"
    assert [citation.source_id for citation in response.citations] == ["S1"]
    hash_prompt.assert_not_called()
    serialize_candidates.assert_not_called()
    serialize_model_output.assert_not_called()


def test_context_failure_keeps_search_candidates_with_tracing_disabled():
    resources = make_query_resources()
    resources.tracer = Tracer()
    resources.count_tokens = Mock(side_effect=ValueError("private context failure"))
    try:
        record = record_query("예약 문의", resources, "request", load_settings())
    finally:
        resources.close()

    assert record["status"] == "error"
    assert len(record["retrieved_candidates"]) == 1
    assert record["final_context_chunks"] is None
    assert record["attempts"] == []
    assert record["langfuse_status"] == "disabled"
    assert "private context failure" not in record["reason"]
