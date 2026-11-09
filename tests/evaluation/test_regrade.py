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
