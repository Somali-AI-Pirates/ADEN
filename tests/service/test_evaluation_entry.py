"""평가 CLI가 실제 서비스 진입점을 같은 설정 파일로 불러 쓰는지 가짜 외부 자원으로 확인한다."""

import csv
import json
import sys

import pytest

from app.evaluation.scripts import run_benchmark
from app.evaluation.security import check_same_context, run_security_comparison
from app.service import evaluation_entry
from shared.settings import CONFIG_ENV, DEFAULT_CONFIG
from tests.pipeline_fakes import make_query_resources

SAMPLE_EVAL = "app/evaluation/samples/sample_eval.json"


@pytest.fixture
def experiment_config(tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_text(
        DEFAULT_CONFIG.read_text(encoding="utf-8").replace("  k: 5", "  k: 3"),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def loaded(monkeypatch):
    """load_resources 대신 가짜 자원을 돌려주고, 받은 설정과 만든 자원을 남긴다."""
    calls = []

    def fake_load(settings):
        resources = make_query_resources()
        calls.append((settings, resources))
        return resources

    monkeypatch.setattr(evaluation_entry, "load_resources", fake_load)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    yield calls
    evaluation_entry.query_adapter.close()


def test_query_and_security_share_one_preparation_from_config(
    monkeypatch, experiment_config, loaded
):
    monkeypatch.setenv(CONFIG_ENV, str(experiment_config))
    run = evaluation_entry.EvaluationRun()

    record = run("예약 문의", "naive", "entry-1")
    retrieve_fn, generate_fn = run.security_adapters()
    security = run_security_comparison(
        [{"id": "security-1", "question": "예약 문의"}], retrieve_fn, generate_fn,
        experiment_id="entry-security", retrieval_mode="naive",
    )

    assert record["status"] == "answered"
    assert record["latency_scope"] == "in_process_adapter"
    assert [r["status"] for r in security] == ["answered", "answered"]
    assert check_same_context(security)["security-1"]["context_identical"] is True
    assert len(loaded) == 1
    settings, resources = loaded[0]
    assert settings.retrieval.k == 3
    assert run.tracer is resources.tracer

    run.close()
    resources.qdrant.close.assert_called_once()
    run.close()
    resources.qdrant.close.assert_called_once()


def test_run_benchmark_passes_its_config_to_the_service_entry(
    monkeypatch, tmp_path, experiment_config, loaded
):
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark", "--questions", SAMPLE_EVAL, "--ids", "SAMPLE-Q1", "--modes", "naive",
        "--adapter", "app.service.evaluation_entry:query_adapter",
        "--config", str(experiment_config), "--experiment-id", "entry-cli",
        "--artifacts-dir", str(tmp_path / "experiments"),
        "--reports-dir", str(tmp_path / "reports"),
        "--langfuse-verify-timeout", "0",
    ])

    run_benchmark.main()

    assert len(loaded) == 1
    assert loaded[0][0].retrieval.k == 3
    runs = [json.loads(line) for line in
            (tmp_path / "experiments" / "entry-cli" / "runs.jsonl").read_text(
                encoding="utf-8").splitlines()]
    assert [(r["question_id"], r["retrieval_mode"], r["status"]) for r in runs] == [
        ("SAMPLE-Q1", "naive", "answered")]
    with (tmp_path / "reports" / "entry-cli" / "comparison.csv").open(
            encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["retrieved_books"] == "DEMO"
    assert rows[0]["judge_fact_status"] == "unavailable"
