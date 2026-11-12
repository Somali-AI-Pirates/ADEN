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



def test_detailed_generation_trace_verifies_visible_payloads():
    client = FakeLangfuseClient()
    requested_fields = []
    original_get_many = client._get_many

    def get_many(*, trace_id, limit=50, fields=None):
        requested_fields.append(fields)
        return original_get_many(trace_id=trace_id, limit=limit)

    client.api.observations.get_many = get_many
    tracer = Tracer(client)
    trace = tracer.start_request(
        "detail-request",
        question="실제 질문",
        retrieval_mode="table_rag",
        dataset_id="advanced",
        config_hash="config",
    )
    trace.record_attempt(
        role="planner",
        attempt=1,
        model="gpt-6-luna",
        model_input={"instructions": "규칙", "history": [{"role": "user", "content": "실제 질문"}]},
        tool_schema=[{"name": "lookup_records"}],
        model_output={"tool": "lookup_records", "arguments": {"targets": []}},
        input_tokens=10,
        output_tokens=2,
    )
    trace.record_attempt(
        role="answer",
        attempt=1,
        model="gpt-6-luna",
        model_input={"question": "실제 질문", "context": "실제 원문"},
        model_output={"answer": "확인했습니다."},
        input_tokens=20,
        output_tokens=3,
    )
    done = trace.finish(
        status="answered",
        answer="확인했습니다.",
        reason="원문을 확인했습니다.",
        citations=[],
        latency_ms=30,
    )
    tracer.flush()

    result = tracer.verify_trace(
        done["langfuse_trace_id"],
        timeout_s=0,
        expected_observations=[
            "rag-query",
            "planner-1",
            "answer-1",
        ],
        require_payload_names=[
            "rag-query",
            "planner-1",
            "answer-1",
        ],
    )

    assert result["status"] == "verified"
    assert requested_fields == ["core,basic,time,io,metadata,model,usage"]
    plan = next(
        observation
        for observation in client.observations
        if observation["name"] == "planner-1"
    )
    assert plan["input"]["request"]["history"][0]["content"] == "실제 질문"
    assert plan["input"]["tool_schema"][0]["name"] == "lookup_records"
    assert plan["output"]["arguments"] == {"targets": []}


def test_detailed_trace_verification_reports_missing_model_payload():
    client = FakeLangfuseClient()
    tracer = Tracer(client)
    trace = request(client)
    trace.record_attempt(
        role="answer",
        attempt=1,
        model="gpt-6-luna",
        model_input={"question": "q"},
        model_output=None,
    )
    trace.finish(status="answered", answer="a", reason=None, citations=[], latency_ms=1)
    tracer.flush()

    result = tracer.verify_trace(
        trace.trace_id,
        timeout_s=0,
        expected_observations=["rag-query", "answer-1"],
        require_payload_names=["rag-query", "answer-1"],
    )

    assert result["status"] == "partial"
    assert result["missing_payload"] == ["answer-1"]


def test_langfuse_score_readback_waits_briefly_for_server_ingestion():
    from datetime import datetime, timezone

    class DelayedScoreClient(FakeLangfuseClient):
        def __init__(self):
            super().__init__()
            self.score_reads = 0

        def _delayed_scores(self, *, id=None, limit=2, request_options=None):
            self.score_reads += 1
            self.request_options = request_options
            if self.score_reads < 3:
                return type("Response", (), {"data": []})()
            return self._get_scores(id=id, limit=limit, request_options=request_options)

    client = DelayedScoreClient()
    client.api.scores.get_many = client._delayed_scores
    tracer = Tracer(client)
    timestamp = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    assert tracer.record_score(
        "trace",
        "eval.faithfulness",
        0.75,
        score_id="score-1",
        timestamp=timestamp,
    )["status"] == "recorded_unverified"
    tracer.flush()

    assert tracer.verify_score(
        "score-1",
        name="eval.faithfulness",
        value=0.75,
        timestamp=timestamp,
        timeout_s=0.1,
        interval_s=0.001,
    )["status"] == "verified"
    assert client.score_reads == 3
    assert client.request_options["max_retries"] == 0
    assert client.request_options["timeout_in_seconds"] > 0
    assert tracer.verify_score(
        "score-missing",
        name="eval.faithfulness",
        value=0.75,
        timestamp=timestamp,
        timeout_s=0,
    )["status"] == "not_found"


def test_trace_score_readback_batches_and_paginates_one_trace():
    from datetime import datetime, timezone

    from types import SimpleNamespace

    class BatchScoreClient(FakeLangfuseClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        def _batch_scores(self, *, trace_id=None, limit=50, cursor=None, request_options=None):
            self.calls.append({"trace_id": trace_id, "limit": limit, "cursor": cursor,
                               "request_options": request_options})
            rows = {
                None: [SimpleNamespace(id="score-1", name="eval.fact", value=1.0,
                                       timestamp=timestamp)],
                "next": [SimpleNamespace(id="score-2", name="eval.evidence", value=0.0,
                                          timestamp=timestamp)],
            }[cursor]
            return SimpleNamespace(data=rows, meta=SimpleNamespace(cursor="next" if cursor is None else None))

    timestamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
    client = BatchScoreClient()
    client.api.scores.get_many = client._batch_scores
    tracer = Tracer(client)

    results = tracer.verify_scores(
        "trace-1",
        [
            {"score_id": "score-1", "name": "eval.fact", "value": 1.0, "timestamp": timestamp},
            {"score_id": "score-2", "name": "eval.evidence", "value": 0.0, "timestamp": timestamp},
        ],
        timeout_s=1.0,
        interval_s=0,
    )

    assert [result["status"] for result in results.values()] == ["verified", "verified"]
    assert len(client.calls) == 2
    assert [call["trace_id"] for call in client.calls] == ["trace-1", "trace-1"]
    assert [call["cursor"] for call in client.calls] == [None, "next"]
    assert all(call["limit"] == 100 for call in client.calls)
    assert all(call["request_options"]["max_retries"] == 0 for call in client.calls)


def test_trace_score_readback_does_not_duplicate_prior_poll_results():
    from datetime import datetime, timezone

    from types import SimpleNamespace

    class DelayedBatchClient(FakeLangfuseClient):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def _delayed_scores(self, *, trace_id=None, limit=50, cursor=None, request_options=None):
            self.reads += 1
            score_a = SimpleNamespace(id="score-a", name="eval.fact", value=1.0,
                                      timestamp=timestamp)
            score_b = SimpleNamespace(id="score-b", name="eval.evidence", value=0.0,
                                      timestamp=timestamp)
            rows = [score_a] if self.reads == 1 else [score_a, score_b]
            return SimpleNamespace(data=rows, meta=SimpleNamespace(cursor=None))

    timestamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
    client = DelayedBatchClient()
    client.api.scores.get_many = client._delayed_scores
    tracer = Tracer(client)

    results = tracer.verify_scores(
        "trace-delayed",
        [
            {"score_id": "score-a", "name": "eval.fact", "value": 1.0, "timestamp": timestamp},
            {"score_id": "score-b", "name": "eval.evidence", "value": 0.0, "timestamp": timestamp},
        ],
        timeout_s=1.0,
        interval_s=0.001,
    )

    assert [result["status"] for result in results.values()] == ["verified", "verified"]
    assert client.reads == 2


def test_score_readback_error_records_type_and_status_without_body():
    from datetime import datetime, timezone

    class RateLimited(Exception):
        status_code = 429
        body = {"private": "do not persist"}

    client = FakeLangfuseClient()
    client.api.scores.get_many = lambda **_kwargs: (_ for _ in ()).throw(RateLimited())
    tracer = Tracer(client)
    timestamp = datetime(2026, 11, 12, tzinfo=timezone.utc)

    result = tracer.verify_scores(
        "trace-1",
        [{"score_id": "score-1", "name": "eval.fact", "value": 1.0, "timestamp": timestamp}],
        timeout_s=0,
    )["score-1"]

    assert result == {"status": "failed", "error": "RateLimited", "status_code": 429}
    assert "body" not in result


def test_score_readback_timeout_caps_sdk_request_and_never_fetches_after_deadline(
    monkeypatch,
):
    from datetime import datetime, timezone

    import app.observability.tracing as tracing

    clock = [0.0]

    class SlowScoreClient(FakeLangfuseClient):
        def __init__(self):
            super().__init__()
            self.reads = 0
            self.options = []

        def _slow_scores(self, *, id=None, limit=2, request_options=None):
            self.reads += 1
            self.options.append(request_options)
            clock[0] = 6.0
            return type("Response", (), {"data": []})()

    client = SlowScoreClient()
    client.api.scores.get_many = client._slow_scores
    monkeypatch.setattr(tracing.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(tracing.time, "sleep", lambda _duration: None)
    tracer = Tracer(client)

    result = tracer.verify_score(
        "score-slow",
        name="eval.faithfulness",
        value=0.75,
        timestamp=datetime(2026, 11, 12, tzinfo=timezone.utc),
        timeout_s=5.0,
        interval_s=0.5,
    )

    assert result["status"] == "not_found"
    assert client.reads == 1
    assert client.options[0]["timeout_in_seconds"] == 5.0
    assert client.options[0]["max_retries"] == 0


def test_score_readback_timeout_zero_performs_one_bounded_probe(monkeypatch):
    from datetime import datetime, timezone

    import app.observability.tracing as tracing

    clock = [0.0]

    class ProbeClient(FakeLangfuseClient):
        def __init__(self):
            super().__init__()
            self.reads = 0
            self.options = None

        def _probe(self, *, id=None, limit=2, request_options=None):
            self.reads += 1
            self.options = request_options
            return type("Response", (), {"data": []})()

    client = ProbeClient()
    client.api.scores.get_many = client._probe
    monkeypatch.setattr(tracing.time, "monotonic", lambda: clock[0])
    tracer = Tracer(client)

    result = tracer.verify_score(
        "score-probe",
        name="eval.faithfulness",
        value=0.75,
        timestamp=datetime(2026, 11, 12, tzinfo=timezone.utc),
        timeout_s=0,
    )

    assert result["status"] == "not_found"
    assert client.reads == 1
    assert client.options["timeout_in_seconds"] == 0.001
    assert client.options["max_retries"] == 0
