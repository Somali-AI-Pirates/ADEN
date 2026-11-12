from dataclasses import asdict
import hashlib
import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from app.evaluation.scripts import _common, run_benchmark, run_security
from shared.contracts import Chunk
from shared.settings import DEFAULT_CONFIG
from tests.pipeline_fakes import make_query_resources


@pytest.fixture
def configured_service(monkeypatch):
    module = importlib.import_module("app.service.bootstrap")
    monkeypatch.setattr(module, "load_dotenv", Mock())
    monkeypatch.setattr(_common, "load_dotenv", Mock())  # 저장소의 실제 .env를 읽지 않는다
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


def test_benchmark_config_reaches_runtime_context_and_metrics(tmp_path, monkeypatch, configured_service):
    config, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_benchmark", "--questions", str(questions),
                        "--adapter", "app.service.bootstrap:query_adapter", "--config", str(config),
                        "--experiment-id", "configured", "--modes", "naive",
                        "--artifacts-dir", str(tmp_path / "a"), "--reports-dir", str(tmp_path / "r")])
    with patch.dict(os.environ, {"ADEN_CONFIG": str(DEFAULT_CONFIG)}):  # --config가 우선한다
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
    assert manifest["dataset_id"] == "advanced" and manifest["benchmark"]["k"] == 3
    assert manifest["config_hash"] == "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest()
    assert len(runs) == 1 and runs[0]["status"] == "answered"
    assert runs[0]["dataset_id"] == "advanced" and len(runs[0]["final_context_chunks"]) == 3
    assert scores[0]["retrieval"]["k"] == 3


def benchmark_argv(tmp_path, questions, *extra):
    return ["run_benchmark", "--questions", str(questions),
            "--adapter", "app.service.bootstrap:query_adapter", "--experiment-id", "k-check",
            "--modes", "naive", "--artifacts-dir", str(tmp_path / "a"),
            "--reports-dir", str(tmp_path / "r"), *extra]


def security_argv(tmp_path, questions, *extra):
    return ["run_security", "--security-file", str(questions),
            "--retrieve", "app.service.bootstrap:retrieve_context",
            "--generate", "app.service.bootstrap:generate_with_context", "--retrieval-mode", "naive",
            "--experiment-id", "k-check", "--artifacts-dir", str(tmp_path / "a"),
            "--reports-dir", str(tmp_path / "r"), *extra]


@pytest.mark.parametrize("use_aden_config, expected_k", [(True, 3), (False, 8)])
def test_benchmark_without_config_uses_aden_config_then_default(tmp_path, monkeypatch, configured_service,
                                                                use_aden_config, expected_k):
    config, questions = write_inputs(tmp_path)
    chosen = config if use_aden_config else DEFAULT_CONFIG
    monkeypatch.setattr("sys.argv", benchmark_argv(tmp_path, questions))
    with patch.dict(os.environ):
        os.environ.pop("ADEN_CONFIG", None)
        if use_aden_config:
            os.environ["ADEN_CONFIG"] = str(config)
        run_benchmark.main()
    manifest = json.loads((tmp_path / "a" / "k-check" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_hash"] == "sha256:" + hashlib.sha256(chosen.read_bytes()).hexdigest()
    assert manifest["benchmark"]["k"] == expected_k
    assert configured_service[0]._config_path == chosen.resolve()


@pytest.mark.parametrize("argv, removed", [
    (benchmark_argv, ["--k", "3"]),
    (benchmark_argv, ["--config-hash", "manual-hash"]),
    (benchmark_argv, ["--dataset-id", "basic"]),
    (security_argv, ["--config-hash", "manual-hash"]),
    (security_argv, ["--dataset-id", "basic"]),
])
def test_removed_options_are_rejected_by_argparse(tmp_path, monkeypatch, capsys, configured_service,
                                                  argv, removed):
    _, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", argv(tmp_path, questions, *removed))
    script = run_benchmark if argv is benchmark_argv else run_security
    with pytest.raises(SystemExit) as exc:
        script.main()
    assert exc.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err
    configured_service[1].assert_not_called()
    assert not (tmp_path / "a" / "k-check").exists()


@pytest.mark.parametrize("problem", ["missing", "yaml", "settings", "aden_config_missing"])
@pytest.mark.parametrize("argv", [benchmark_argv, security_argv])
def test_bad_config_stops_before_adapter_and_graders(tmp_path, monkeypatch, configured_service, argv, problem):
    _, questions = write_inputs(tmp_path)
    bad = tmp_path / "bad.yaml"
    if problem == "yaml":
        bad.write_text("dataset: [unclosed\n", encoding="utf-8")
    elif problem == "settings":
        bad.write_text("dataset: only-this\n", encoding="utf-8")
    script = run_benchmark if argv is benchmark_argv else run_security
    if script is run_benchmark:
        factories = ["load_callable", "make_openai_judge", "make_ragas_faithfulness"]
    else:
        factories = ["load_callable", "make_openai_security_judge"]
    for name in factories:
        monkeypatch.setattr(script, name, Mock())
    extra = ["--judge-model", "judge"]
    if problem != "aden_config_missing":
        extra += ["--config", str(bad)]
    monkeypatch.setattr("sys.argv", argv(tmp_path, questions, *extra))
    with patch.dict(os.environ, {"ADEN_CONFIG": str(bad)}):
        with pytest.raises(SystemExit, match="평가 설정을 읽지 못했다"):
            script.main()
    for name in factories:
        getattr(script, name).assert_not_called()
    configured_service[1].assert_not_called()
    assert not (tmp_path / "a" / "k-check").exists()


def test_security_config_applies_once_to_both_callables(tmp_path, monkeypatch, configured_service):
    config, questions = write_inputs(tmp_path)
    monkeypatch.setattr("sys.argv", ["run_security", "--security-file", str(questions),
                        "--retrieve", "app.service.bootstrap:retrieve_context",
                        "--generate", "app.service.bootstrap:generate_with_context",
                        "--config", str(config), "--retrieval-mode", "naive", "--experiment-id", "configured",
                        "--artifacts-dir", str(tmp_path / "a"), "--reports-dir", str(tmp_path / "r")])
    run_security.main()
    service, loader, resources = configured_service
    loader.assert_called_once()  # 두 callable이 같은 설정·자원을 쓴다
    assert service._config_path == config.resolve()
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
