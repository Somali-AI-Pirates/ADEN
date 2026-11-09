from dataclasses import asdict
import importlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from app.evaluation.scripts import run_benchmark, run_security
from shared.contracts import Chunk
from shared.settings import DEFAULT_CONFIG
from tests.pipeline_fakes import make_query_resources


@pytest.fixture
def configured_service(monkeypatch):
    module = importlib.import_module("app.service.bootstrap")
    monkeypatch.setattr(module, "load_dotenv", Mock())
    resources = make_query_resources()
    resources.qdrant.query_points.return_value.points = [
        SimpleNamespace(payload=asdict(Chunk(f"DEMO-p{i}-c1", "DEMO", "DEMO.pdf", i,
                                           "예약 접수와 확정은 별도입니다.")), score=1 / i)
        for i in range(1, 9)
    ]

    def load_resources(settings):
        resources.collection = settings.qdrant_collection
        return resources

    loader = Mock(side_effect=load_resources)
    service = module.ServiceBootstrap(resources_loader=loader)
    monkeypatch.setattr(module, "_default", service)
    monkeypatch.setattr(module, "query_adapter", module._QueryAdapter(service))
    yield service, loader, resources
    service.close()


def write_inputs(tmp_path):
    settings = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    settings.update(dataset="advanced", qdrant_collection="configured-advanced-index",
                    generation_model="configured-test-model")
    settings["retrieval"]["k"] = 3
    config = tmp_path / "advanced.yaml"
    config.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{
        "id": "Q1", "question": "예약 접수와 확정은 같은가요?",
        "reference_answer": "별도이다.", "attack_text": "TEST-ATTACK",
        "evidence_groups": [[{"book_id": "DEMO", "page": 1}]],
    }], ensure_ascii=False), encoding="utf-8")
    return config, questions


@pytest.mark.parametrize("extra, expected_k", [([], 3), (["--k", "3"], 3)])
def test_benchmark_config_reaches_runtime_context_and_metrics(tmp_path, monkeypatch,
                                                            configured_service, extra, expected_k):
    config, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_benchmark", "--questions", str(questions),
                        "--adapter", "app.service.bootstrap:query_adapter", "--config", str(config),
                        "--experiment-id", "configured", "--modes", "naive",
                        "--artifacts-dir", str(tmp_path / "a"), "--reports-dir", str(tmp_path / "r"),
                        *extra])
    run_benchmark.main()
    service, loader, resources = configured_service
    loader.assert_called_once()
    actual = loader.call_args.args[0]
    assert actual.dataset == "advanced" and actual.generation_model == "configured-test-model"
    assert service._settings.retrieval.k == 3
    resources.qdrant.query_points.assert_called_once()
    assert resources.qdrant.query_points.call_args.args[0] == "configured-advanced-index"
    output = tmp_path / "a" / "configured"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    runs = [json.loads(line) for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    scores = [json.loads(line) for line in (output / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest["dataset_id"] == "advanced" and manifest["benchmark"]["k"] == expected_k
    assert len(runs) == 1 and runs[0]["status"] == "answered"
    assert runs[0]["dataset_id"] == "advanced" and len(runs[0]["final_context_chunks"]) == 3
    assert scores[0]["retrieval"]["k"] == expected_k


def benchmark_argv(tmp_path, questions, *extra):
    return ["run_benchmark", "--questions", str(questions),
            "--adapter", "app.service.bootstrap:query_adapter", "--experiment-id", "k-check",
            "--modes", "naive", "--artifacts-dir", str(tmp_path / "a"),
            "--reports-dir", str(tmp_path / "r"), *extra]


@pytest.mark.parametrize("extra, message", [
    (["--config", "{config}", "--k", "5"], "retrieval.k 3"),
    (["--config-hash", "manual-hash"], "--config 없이"),
    (["--config-hash", "manual-hash", "--k", "0"], "양수"),
])
def test_benchmark_rejects_k_not_tied_to_service_config(tmp_path, monkeypatch, configured_service,
                                                        extra, message):
    config, questions = write_inputs(tmp_path)
    extra = [str(config) if item == "{config}" else item for item in extra]
    monkeypatch.setattr("sys.argv", benchmark_argv(tmp_path, questions, *extra))
    with pytest.raises(SystemExit, match=message):
        run_benchmark.main()
    _, loader, _ = configured_service
    loader.assert_not_called()
    assert not (tmp_path / "a" / "k-check").exists()


def test_benchmark_without_config_uses_explicit_k(tmp_path, monkeypatch, configured_service):
    _, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", benchmark_argv(tmp_path, questions,
                                                   "--config-hash", "manual-hash", "--k", "8"))
    run_benchmark.main()
    output = tmp_path / "a" / "k-check"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    scores = [json.loads(line) for line in (output / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest["config_hash"] == "manual-hash" and manifest["benchmark"]["k"] == 8
    assert scores[0]["retrieval"]["k"] == 8



@pytest.mark.parametrize("script, args", [
    (run_benchmark, ["--questions", "{questions}",
                     "--adapter", "app.service.bootstrap:query_adapter", "--modes", "naive"]),
    (run_security, ["--security-file", "{questions}",
                    "--retrieve", "app.service.bootstrap:retrieve_context",
                    "--generate", "app.service.bootstrap:generate_with_context",
                    "--retrieval-mode", "naive"]),
])
def test_config_and_config_hash_are_mutually_exclusive(tmp_path, monkeypatch, capsys,
                                                       configured_service, script, args):
    config, questions = write_inputs(tmp_path)
    args = [str(questions) if item == "{questions}" else item for item in args]
    monkeypatch.setattr("sys.argv", ["cli", *args, "--config", str(config),
                                     "--config-hash", "manual-hash", "--experiment-id", "both",
                                     "--artifacts-dir", str(tmp_path / "a"),
                                     "--reports-dir", str(tmp_path / "r")])
    with pytest.raises(SystemExit) as exc:
        script.main()
    assert exc.value.code == 2 and "not allowed with" in capsys.readouterr().err
    configured_service[1].assert_not_called()  # 서비스 자원·유료 호출 전에 멈춘다
    assert not (tmp_path / "a" / "both").exists()


def test_security_config_applies_once_to_both_callables(tmp_path, monkeypatch, configured_service):
    config, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_security", "--security-file", str(questions),
                        "--retrieve", "app.service.bootstrap:retrieve_context",
                        "--generate", "app.service.bootstrap:generate_with_context",
                        "--config", str(config), "--retrieval-mode", "naive", "--experiment-id", "configured",
                        "--artifacts-dir", str(tmp_path / "a"), "--reports-dir", str(tmp_path / "r")])
    run_security.main()
    _, loader, resources = configured_service
    loader.assert_called_once()
    assert loader.call_args.args[0].dataset == "advanced"
    resources.qdrant.query_points.assert_called_once()
    assert resources.qdrant.query_points.call_args.args[0] == "configured-advanced-index"
    output = tmp_path / "a" / "configured"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "advanced"
    records = [json.loads(line) for line in (output / "security_runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and all(row["status"] == "answered" for row in records)
    assert all(len(row["final_context_chunks"]) == 3 for row in records)


def test_active_resources_reject_different_config_without_losing_existing_resources(tmp_path, configured_service):
    config, _ = write_inputs(tmp_path)
    service, loader, resources = configured_service
    service.configure(config)
    assert service.resources() is resources
    service.configure(config)
    with pytest.raises(ValueError, match="이미 준비된"):
        service.configure(tmp_path / "other.yaml")
    changed = yaml.safe_load(config.read_text(encoding="utf-8"))
    changed["retrieval"]["k"] = 4
    config.write_text(yaml.safe_dump(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="이미 준비된"):
        service.configure(config)
    assert service.resources() is resources
    loader.assert_called_once()
