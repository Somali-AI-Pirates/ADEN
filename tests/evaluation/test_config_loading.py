import json
import os
from unittest.mock import Mock, patch

import dotenv
import pytest
import yaml

from app.evaluation.scripts import _common, regrade, run_benchmark
from app.evaluation.scripts._common import ROOT, load_config
from app.evaluation.samples import SAMPLE_EVAL
from shared.settings import DEFAULT_CONFIG


def write_config(path, *, dataset="custom", k=3):
    settings = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    settings["dataset"] = dataset
    settings["retrieval"]["k"] = k
    path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def no_dotenv(monkeypatch):
    """저장소 루트의 실제 .env를 읽지 않는다."""
    monkeypatch.setattr(_common, "load_dotenv", Mock())


def test_config_option_beats_aden_config(tmp_path, no_dotenv):
    chosen = write_config(tmp_path / "chosen.yaml", dataset="chosen")
    other = write_config(tmp_path / "other.yaml", dataset="other")
    with patch.dict(os.environ, {"ADEN_CONFIG": str(other)}):
        path, settings = load_config(str(chosen))
    assert path == chosen.resolve() and settings.dataset == "chosen"


def test_dotenv_selects_config_but_process_environment_wins(tmp_path, monkeypatch):
    from_env_file = write_config(tmp_path / "env-file.yaml", dataset="from-dotenv")
    from_process = write_config(tmp_path / "process.yaml", dataset="from-process")
    env_file = tmp_path / ".env"
    env_file.write_text(f"ADEN_CONFIG={from_env_file}\n", encoding="utf-8")
    monkeypatch.setattr(_common, "load_dotenv", lambda path: dotenv.load_dotenv(env_file))
    with patch.dict(os.environ):
        os.environ.pop("ADEN_CONFIG", None)
        assert load_config(None)[1].dataset == "from-dotenv"
    with patch.dict(os.environ, {"ADEN_CONFIG": str(from_process)}):
        assert load_config(None)[1].dataset == "from-process"
        assert os.environ["ADEN_CONFIG"] == str(from_process)


def test_default_is_advanced_and_relative_paths_are_repo_root_based(tmp_path, monkeypatch, no_dotenv):
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ):
        os.environ.pop("ADEN_CONFIG", None)
        path, settings = load_config(None)
        assert path == DEFAULT_CONFIG.resolve() and settings.dataset == "advanced"
        assert load_config("configs/basic.yaml")[0] == (ROOT / "configs" / "basic.yaml").resolve()


@pytest.mark.parametrize("content", [None, "dataset: [unclosed\n", "dataset: only-this\n"])
def test_bad_config_stops_without_falling_back(tmp_path, no_dotenv, content):
    bad = tmp_path / "bad.yaml"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    with patch.dict(os.environ):
        os.environ.pop("ADEN_CONFIG", None)
        with pytest.raises(SystemExit, match="평가 설정을 읽지 못했다"):
            load_config(str(bad))
    with patch.dict(os.environ, {"ADEN_CONFIG": str(bad)}):
        with pytest.raises(SystemExit, match="평가 설정을 읽지 못했다"):
            load_config(None)


def test_standalone_langfuse_check_ignores_service_config(tmp_path, monkeypatch, capsys, no_dotenv):
    monkeypatch.setattr("sys.argv", ["run_benchmark", "--verify-langfuse", "trace-1"])
    with patch.dict(os.environ, {"ADEN_CONFIG": str(tmp_path / "missing.yaml")}):
        for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            os.environ.pop(key, None)
        run_benchmark.main()
    assert '"status": "disabled"' in capsys.readouterr().out


def test_regrade_keeps_source_conditions_when_current_config_differs(tmp_path, monkeypatch, no_dotenv):
    monkeypatch.setattr("sys.argv", ["run_benchmark", "--sample", "--experiment-id", "sample-src",
                                     "--artifacts-dir", str(tmp_path / "a"), "--reports-dir", str(tmp_path / "r")])
    current = write_config(tmp_path / "current.yaml", dataset="changed", k=3)
    with patch.dict(os.environ, {"ADEN_CONFIG": str(current)}):
        run_benchmark.main()
        monkeypatch.setattr("sys.argv", ["regrade", "--source", "sample-src", "--references", str(SAMPLE_EVAL),
                                         "--experiment-id", "sample-re", "--artifacts-dir", str(tmp_path / "a"),
                                         "--reports-dir", str(tmp_path / "r")])
        regrade.main()
    source = json.loads((tmp_path / "a" / "sample-src" / "manifest.json").read_text(encoding="utf-8"))
    graded = json.loads((tmp_path / "a" / "sample-re" / "manifest.json").read_text(encoding="utf-8"))
    assert source["benchmark"]["k"] == graded["benchmark"]["k"] == run_benchmark.SAMPLE_K
    assert (graded["dataset_id"], graded["config_hash"]) == (source["dataset_id"], source["config_hash"])
