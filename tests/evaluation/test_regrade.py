import json

import pytest

from app.evaluation.graders import PROMPTS_DIR
from app.evaluation.samples import SAMPLE_EVAL
from app.evaluation.scripts import regrade, run_benchmark
from app.evaluation.scripts._common import sha256_file


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def source(tmp_path, monkeypatch):
    """샘플 벤치마크 한 번으로 원본 실험 기록을 만든다."""
    monkeypatch.setattr("sys.argv", ["run_benchmark", "--sample", "--experiment-id", "sample-src",
                                     "--artifacts-dir", str(tmp_path / "a"),
                                     "--reports-dir", str(tmp_path / "r")])
    run_benchmark.main()
    return tmp_path / "a" / "sample-src"


def run_regrade(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr("sys.argv", ["regrade", "--source", "sample-src",
                                     "--references", str(SAMPLE_EVAL),
                                     "--artifacts-dir", str(tmp_path / "a"),
                                     "--reports-dir", str(tmp_path / "r"), *extra])
    regrade.main()


def test_regrade_scores_saved_answers_with_new_prompt_without_touching_source(
        tmp_path, monkeypatch, source):
    runs_before = (source / "runs.jsonl").read_bytes()
    src_scores = {s["request_id"]: s for s in jsonl(source / "scores.jsonl")}
    prompt = tmp_path / "judge_v2.txt"
    prompt.write_text(PROMPTS_DIR.joinpath("judge.txt").read_text(encoding="utf-8")
                      + "\n추가 기준 줄\n", encoding="utf-8")

    run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-re", "--judge-prompt", str(prompt))

    assert (source / "runs.jsonl").read_bytes() == runs_before  # 원본 보존
    new_dir = tmp_path / "a" / "sample-re"
    assert not (new_dir / "runs.jsonl").exists()  # 답변을 새로 만들거나 복제하지 않음
    scores = jsonl(new_dir / "scores.jsonl")
    assert {s["request_id"] for s in scores} == set(src_scores)
    for s in scores:  # Faithfulness는 원본 그대로, 새 평가 호출 없음
        assert s["faithfulness"] == src_scores[s["request_id"]]["faithfulness"]
        assert s["grading_usage"]["faithfulness"] is None
        assert s["retrieval"] == src_scores[s["request_id"]]["retrieval"]

    manifest = json.loads((tmp_path / "r" / "sample-re" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["regrade"]["source_experiment_id"] == "sample-src"
    assert manifest["regrade"]["faithfulness"] == "reused"
    assert manifest["benchmark"]["judge_prompt"] == sha256_file(prompt)
    assert manifest["regrade"]["source_judge_prompt"] == sha256_file(PROMPTS_DIR / "judge.txt")
    assert manifest["benchmark"]["k"] == 8 and manifest["sample"] is True
    readme = (tmp_path / "r" / "sample-re" / "README.md").read_text(encoding="utf-8")
    assert "재채점" in readme and "가상 샘플 결과" in readme


@pytest.mark.parametrize("extra, message", [
    (["--experiment-id", "sample-src"], "같은 실험 ID"),
    (["--judge-model", "some-model"], "모의 Judge"),
    (["--experiment-id", "real-id"], "sample-"),
])
def test_regrade_rejects_unsafe_options(tmp_path, monkeypatch, source, extra, message):
    with pytest.raises(SystemExit, match=message):
        run_regrade(monkeypatch, tmp_path, *extra)


def test_regrade_requires_judge_model_for_real_records(tmp_path, monkeypatch, source):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    (source / "manifest.json").write_text(json.dumps({**manifest, "sample": False}), encoding="utf-8")
    with pytest.raises(SystemExit, match="--judge-model"):
        run_regrade(monkeypatch, tmp_path, "--experiment-id", "basic-re")


def test_score_client_initialization_failure_still_saves_report(tmp_path, monkeypatch, source):
    def fail():
        raise RuntimeError("private initialization detail")
    monkeypatch.setattr("app.evaluation.samples.FakeLangfuseClient", fail)
    run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-score-failure", "--publish-langfuse-scores")
    output = tmp_path / "a" / "sample-score-failure"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["langfuse_scores"]["status"] == "failed"
    assert manifest["langfuse_scores"]["error"] == "RuntimeError"
    assert (output / "scores.jsonl").is_file()
    assert (tmp_path / "r" / "sample-score-failure" / "README.md").is_file()


def test_sample_score_publication_never_loads_real_tracer(tmp_path, monkeypatch, source):
    def fail():
        pytest.fail("real Langfuse initialization in sample")
    monkeypatch.setattr("app.observability.tracing.Tracer.from_env", fail)
    run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-posted", "--publish-langfuse-scores")
    manifest = json.loads((tmp_path / "a" / "sample-posted" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["langfuse_scores"]["sample"] is True
    assert manifest["langfuse_scores"]["score_status_counts"]["recorded_unverified"] > 0
    # Judge만 바꾼 재채점은 Judge만 게시하고, 이름은 평가 세트(기본: 실험 ID)로 시작한다
    assert manifest["langfuse_scores"]["groups"] == ["judge"]
    names = [name for row in jsonl(tmp_path / "a" / "sample-posted" / "scores.jsonl")
             for name in row.get("langfuse_score_ids", {})]
    assert names and all(name.startswith("sample-posted.judge.") for name in names)


def test_scores_are_saved_before_publication_and_survive_interruption(tmp_path, monkeypatch, source):
    output = tmp_path / "a" / "sample-interrupted"
    runs_before = (source / "runs.jsonl").read_bytes()

    def interrupted(tracer, runs, scores, manifest):
        saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert saved["langfuse_scores"]["status"] == "incomplete"  # 게시 전에 이미 저장됐다
        assert len(jsonl(output / "scores.jsonl")) == len(scores)
        raise KeyboardInterrupt

    monkeypatch.setattr(regrade, "publish_evaluation_scores", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-interrupted",
                    "--publish-langfuse-scores", "--evaluation-set", "sample-set-r1")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["langfuse_scores"]["status"] == "incomplete"
    assert manifest["langfuse_scores"]["evaluation_set"] == "sample-set-r1"
    assert all(row["judge"] for row in jsonl(output / "scores.jsonl"))
    assert (source / "runs.jsonl").read_bytes() == runs_before


def test_publication_failing_midway_keeps_scores_and_sent_ids(tmp_path, monkeypatch, source):
    from app.evaluation.samples import FakeLangfuseClient
    sent = []

    def create_score(self, **kwargs):
        if len(sent) >= 2:
            raise ConnectionError("mock score failure")
        sent.append(kwargs["name"])

    monkeypatch.setattr(FakeLangfuseClient, "create_score", create_score)
    run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-midway", "--publish-langfuse-scores")
    output = tmp_path / "a" / "sample-midway"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["langfuse_scores"]["status"] == "partial"
    assert manifest["langfuse_scores"]["score_status_counts"]["recorded_unverified"] == 2
    rows = jsonl(output / "scores.jsonl")
    assert all(row["judge"] for row in rows)
    assert sorted(name for row in rows for name in row.get("langfuse_score_ids", {})) == sorted(sent)


def test_regrade_refuses_a_set_used_with_other_conditions_before_grading(tmp_path, monkeypatch, source):
    run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-v1", "--publish-langfuse-scores",
                "--evaluation-set", "sample-set")
    prompt = tmp_path / "judge_v2.txt"
    prompt.write_text("다른 기준\n", encoding="utf-8")
    monkeypatch.setattr(regrade, "grade_all", lambda *a, **k: pytest.fail("세트 충돌인데 채점을 시작했다"))
    with pytest.raises(SystemExit, match="다른 채점 조건"):
        run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-v2", "--publish-langfuse-scores",
                    "--evaluation-set", "sample-set", "--judge-prompt", str(prompt))
    with pytest.raises(SystemExit, match="반복 회차"):  # 같은 조건의 반복 채점도 세트를 나눈다
        run_regrade(monkeypatch, tmp_path, "--experiment-id", "sample-v1-r2", "--publish-langfuse-scores",
                    "--evaluation-set", "sample-set")
    assert not (tmp_path / "a" / "sample-v2").exists() and not (tmp_path / "a" / "sample-v1-r2").exists()
