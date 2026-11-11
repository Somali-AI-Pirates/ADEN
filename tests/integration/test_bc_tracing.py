"""Actual stage boundaries and score transport without a Langfuse server."""
import pytest

from app.evaluation.samples import FakeLangfuseClient
from app.observability.tracing import Tracer


def request(client):
    return Tracer(client).start_request("bc-trace", question="SAMPLE", retrieval_mode="naive",
                                        dataset_id="advanced", config_hash="test-config")


def test_stages_end_after_work_and_active_records_do_not_duplicate():
    client = FakeLangfuseClient()
    trace = request(client)
    with trace.stage("generation-attempt-1", as_type="generation"):
        generation = client.observations[-1]
        assert not generation["ended"]
        with trace.stage("validation-attempt-1"):
            assert client.observations[-1]["parent"] == "generation-attempt-1"
        trace.record_attempt(attempt=1, model="fake", model_input=[], model_output="SAMPLE",
                             input_tokens=0, output_tokens=2, latency_ms=12)
    assert generation["ended"]
    assert sum(o["name"] == "generation-attempt-1" for o in client.observations) == 1
    assert generation["updates"][-1]["metadata"]["latency_ms"] == 12
    assert generation["updates"][-1]["usage_details"]["input"] == 0


def test_context_trace_contains_budget_information():
    client = FakeLangfuseClient()
    trace = request(client)
    with trace.stage("retrieval", as_type="retriever") as updates:
        updates["output"] = {"candidates": [{"chunk_id": "kept"}, {"chunk_id": "excluded"}]}
    with trace.stage("final-context"):
        trace.record_retrieval(retrieved_candidates=[], final_context_chunks=[],
                               final_context_hash="test", model_context_token_count=42,
                               budget_excluded_chunk_ids=["excluded"])
    assert [o["name"] for o in client.observations] == ["rag-query", "retrieval", "final-context"]
    output = client.observations[-1]["updates"][-1]["output"]
    assert output["model_context_token_count"] == 42
    assert output["budget_excluded_chunk_ids"] == ["excluded"]


def test_partial_trace_is_not_verified_as_complete():
    client = FakeLangfuseClient()
    tracer = Tracer(client)
    trace = request(client)
    tracer.flush()
    result = tracer.verify_trace(trace.trace_id, timeout_s=0,
                                 expected_observations=["rag-query", "final-context"])
    assert result["status"] == "partial"
    assert result["missing_observations"] == ["final-context"]


def test_tracking_update_failure_does_not_replace_business_exception():
    client = FakeLangfuseClient(fail_on="update")
    trace = request(client)
    with pytest.raises(ValueError, match="business failure"):
        with trace.stage("validation"):
            raise ValueError("business failure")
    assert trace.status == "sdk_error"
    assert client.observations[-1]["ended"]


def test_score_zero_is_sent_and_nonfinite_is_rejected():
    client = FakeLangfuseClient()
    tracer = Tracer(client)
    assert tracer.record_score("trace", "recall", 0, score_id="test-score")["status"] == "recorded_unverified"
    assert client.scores[0]["value"] == 0.0
    assert client.scores[0]["score_id"] == "test-score"
    assert tracer.record_score("trace", "recall", float("nan"))["status"] == "invalid"
    assert len(client.scores) == 1
    assert Tracer().record_score("trace", "recall", 0)["status"] == "disabled"
