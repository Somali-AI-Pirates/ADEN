import json
from copy import deepcopy

import pytest

from app.evaluation.metrics import score
from app.evaluation.publishing import plan_publication, publish_evaluation_scores
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
    # 이름이 평가 세트(기본: 실험 ID)로 시작해 같은 trace의 다른 채점과 집계가 섞이지 않는다
    assert client.scores[0]["name"] == "sample-bc.retrieval.hit@8"
    assert scores[0]["langfuse_score_ids"] == {"sample-bc.retrieval.hit@8": client.scores[0]["score_id"]}
    metadata = client.scores[0]["metadata"]
    assert metadata["experiment_id"] == metadata["source_experiment_id"] == "sample-bc"
    assert metadata["evaluation_set"] == result["evaluation_set"] == "sample-bc"
    assert metadata["evaluation_hash"] == result["evaluation_hash"]
    publish_evaluation_scores(tracer, runs, scores, manifest)  # 같은 결과 재게시: 같은 ID
    assert client.scores[0]["score_id"] == client.scores[1]["score_id"]
    publish_evaluation_scores(tracer, runs, scores, manifest | {"experiment_id": "sample-regrade"})
    assert client.scores[2]["name"] == "sample-regrade.retrieval.hit@8"
    assert client.scores[0]["score_id"] != client.scores[2]["score_id"]


def test_only_chosen_groups_are_published_under_the_set_name():
    client = FakeLangfuseClient()
    runs, scores, manifest = inputs()
    scores[0]["judge"] = {"fact": score(1., "scored", "ok")}
    manifest["regrade"] = {"source_experiment_id": "sample-src"}
    manifest["langfuse_scores"] = plan_publication(manifest, evaluation_set="dev-v2-r1", groups=["judge"])
    result = publish_evaluation_scores(Tracer(client), runs, scores, manifest)
    # 바뀌지 않은 검색 지표와 옮겨 온 Faithfulness는 보내지도, 건너뛴 것으로 세지도 않는다
    assert [s["name"] for s in client.scores] == ["dev-v2-r1.judge.fact"]
    assert result["groups"] == ["judge"] and result["score_status_counts"] == {"recorded_unverified": 1}
    assert client.scores[0]["metadata"]["source_experiment_id"] == "sample-src"


def save_experiment(root, manifest, request_ids):
    folder = root / manifest["experiment_id"]
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "scores.jsonl").write_text(
        "".join(json.dumps({"request_id": r}) + "\n" for r in request_ids), encoding="utf-8")


def test_a_set_accepts_only_the_same_conditions_and_new_requests(tmp_path):
    _, _, manifest = inputs()
    manifest["benchmark"] = {"judge": "judge-model", "judge_prompt": "sha256:v1"}
    used = plan_publication(manifest, evaluation_set="dev-v1", artifacts_dir=tmp_path)
    assert used["status"] == "incomplete"  # 게시가 끝나기 전 상태
    save_experiment(tmp_path, manifest | {"langfuse_scores": used}, ["r1"])

    other = manifest | {"experiment_id": "sample-other"}
    # 같은 조건·다른 요청은 같은 세트에 넣을 수 있고, 같은 실험의 재게시도 막지 않는다
    assert plan_publication(other, evaluation_set="dev-v1", artifacts_dir=tmp_path,
                            request_ids=["r2"])["evaluation_hash"] == used["evaluation_hash"]
    plan_publication(manifest, evaluation_set="dev-v1", artifacts_dir=tmp_path, request_ids=["r1"])
    with pytest.raises(ValueError, match="반복 회차"):
        plan_publication(other, evaluation_set="dev-v1", artifacts_dir=tmp_path, request_ids=["r1"])
    v2 = other | {"benchmark": {"judge": "judge-model", "judge_prompt": "sha256:v2"}}
    with pytest.raises(ValueError, match="다른 채점 조건"):
        plan_publication(v2, evaluation_set="dev-v1", artifacts_dir=tmp_path, request_ids=["r2"])
    assert plan_publication(v2, evaluation_set="dev-v2", artifacts_dir=tmp_path)["evaluation_set"] == "dev-v2"
    with pytest.raises(ValueError, match="세트 이름"):
        plan_publication(manifest, evaluation_set="dev.v1")


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
