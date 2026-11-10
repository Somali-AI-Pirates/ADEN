from copy import deepcopy

from app.evaluation.metrics import score
from app.evaluation.publishing import publish_evaluation_scores
from app.evaluation.samples import FakeLangfuseClient
from app.observability.tracing import Tracer


def inputs():
    manifest = {"experiment_id": "sample-bc", "config_hash": "cfg", "sample": True}
    runs = [{"request_id": "r", "langfuse_trace_id": "trace"}]
    scores = [{"request_id": "r", "retrieval": {"k": 8,
              "hit_at_k": score(0., "scored", "miss"),
              "recall_at_k": score(None, "not_applicable", "none")},
              "faithfulness": score(None, "unavailable", "no evaluator"),
              "judge": {}, "evidence_diagnostics": {}}]
    return runs, scores, manifest


def test_zero_publishes_but_missing_scores_are_skipped_and_ids_are_versioned():
    client = FakeLangfuseClient()
    tracer = Tracer(client)
    runs, scores, manifest = inputs()
    result = publish_evaluation_scores(tracer, runs, scores, manifest)
    assert result["score_status_counts"] == {"recorded_unverified": 1, "skipped": 2}
    assert client.scores[0]["value"] == 0
    assert "SAMPLE" in client.scores[0]["comment"]
    publish_evaluation_scores(tracer, runs, scores, manifest)
    assert client.scores[0]["score_id"] == client.scores[1]["score_id"]
    publish_evaluation_scores(tracer, runs, scores, manifest | {"experiment_id": "sample-regrade"})
    assert client.scores[0]["score_id"] != client.scores[2]["score_id"]


def test_publication_failure_does_not_destroy_local_scores():
    runs, scores, manifest = inputs()
    original = deepcopy(scores[0]["retrieval"])
    result = publish_evaluation_scores(Tracer(FakeLangfuseClient(fail_on="score")), runs, scores, manifest)
    assert result["status"] == "partial"
    assert scores[0]["retrieval"] == original


def test_missing_trace_does_not_claim_a_score_was_sent():
    runs, scores, manifest = inputs()
    runs[0]["langfuse_trace_id"] = None
    client = FakeLangfuseClient()
    result = publish_evaluation_scores(Tracer(client), runs, scores, manifest)
    assert result["status"] == "no_scores"
    assert client.scores == []
