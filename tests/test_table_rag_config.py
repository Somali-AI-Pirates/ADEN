"""Runtime settings precedence, without model, cloud, or filesystem mutation."""

from dataclasses import FrozenInstanceError
import json
import os

import pytest

from app.rag.config import load_table_rag_settings


def test_cli_environment_dotenv_yaml_and_defaults(tmp_path):
    config = tmp_path / "custom.yaml"
    config.write_text(
        "table_rag:\n"
        "  data_directory: yaml-data\n"
        "  run_directory: yaml-runs\n"
        "  device: cuda\n"
        "  vector_backend: qdrant\n"
        "  collection: yaml-collection\n"
        "  trace_enabled: false\n"
        "embedding_model: ignored-legacy-model\n"
    )
    (tmp_path / ".env").write_text(
        "ADEN_CONFIG=custom.yaml\n"
        "ADEN_TABLE_RAG_DIR=dotenv-data\n"
        "ADEN_TABLE_RAG_DEVICE=cpu_gpu_rerank\n"
        "ADEN_TABLE_RAG_COLLECTION=dotenv-collection\n"
    )
    original = dict(os.environ)
    settings = load_table_rag_settings(
        project_root=tmp_path,
        env={"ADEN_TABLE_RAG_DIR": "process-data", "ADEN_TABLE_RAG_COLLECTION": "process-collection"},
        data_directory="cli-data",
    )
    assert settings.data_directory == tmp_path / "cli-data"
    assert settings.run_directory == tmp_path / "yaml-runs"
    assert settings.device == "cpu_gpu_rerank"
    assert settings.vector_backend == "qdrant"
    assert settings.collection == "process-collection"
    assert settings.trace_enabled is False
    assert dict(os.environ) == original
    with pytest.raises(FrozenInstanceError):
        settings.device = "cuda"


def test_unspecified_cli_path_does_not_override_environment(tmp_path):
    settings = load_table_rag_settings(
        project_root=tmp_path, env={"ADEN_TABLE_RAG_DIR": "process-data"}
    )
    assert settings.data_directory == tmp_path / "process-data"
    assert settings.run_directory == tmp_path / "artifacts/rag-runs"
    assert settings.vector_backend == "numpy"
    assert settings.device == "cpu"
    assert settings.trace_enabled is True


def test_legacy_yaml_does_not_override_fixed_table_policy(tmp_path):
    config = tmp_path / "basic.yaml"
    config.write_text(
        "embedding_device: cuda\n"
        "embedding_model: legacy-embedding\n"
        "reasoning_effort: low\n"
        "qdrant_collection: basic\n"
        "retrieval: {k: 2, candidate_count: 5}\n"
    )
    settings = load_table_rag_settings(config_path=config, project_root=tmp_path, env={})
    assert settings.device == "cpu"
    assert settings.collection == "advanced_table_rag"
    assert "embedding_model" not in settings.public_manifest()


def test_private_connection_values_never_enter_manifest_or_repr(tmp_path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=dotenv-secret\n"
        "QDRANT_URL=https://private-host\n"
        "QDRANT_API_KEY=qdrant-secret\n"
        "LANGFUSE_SECRET_KEY=trace-secret\n"
        "LANGFUSE_PUBLIC_KEY=trace-public\n"
        "LANGFUSE_HOST=https://trace-host\n"
    )
    settings = load_table_rag_settings(
        project_root=tmp_path, env={"OPENAI_API_KEY": "process-secret"}
    )
    assert settings.credentials.openai_api_key == "process-secret"
    assert settings.credentials.langfuse_base_url == "https://trace-host"
    public = json.dumps(settings.public_manifest()) + repr(settings) + repr(settings.credentials)
    for value in ("secret", "private-host", "trace-host", "trace-public"):
        assert value not in public


def test_empty_process_secret_does_not_reenable_dotenv_secret(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-secret\n")
    settings = load_table_rag_settings(
        project_root=tmp_path, env={"OPENAI_API_KEY": ""}
    )
    assert settings.credentials.openai_api_key is None


@pytest.mark.parametrize(
    "environment",
    [
        {"ADEN_TABLE_RAG_DEVICE": "unavailable"},
        {"ADEN_TABLE_RAG_VECTOR_BACKEND": "typo"},
        {"ADEN_TABLE_RAG_TRACE": "maybe"},
        {"ADEN_TABLE_RAG_COLLECTION": ""},
    ],
)
def test_invalid_runtime_settings_fail_before_loading_resources(tmp_path, environment):
    with pytest.raises(ValueError):
        load_table_rag_settings(project_root=tmp_path, env=environment)


def test_explicit_missing_yaml_is_not_ignored(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_table_rag_settings(project_root=tmp_path, env={"ADEN_CONFIG": "missing.yaml"})


@pytest.mark.parametrize("content", ["[]", "table_rag: []", "table_rag: {candidate_count: 50}"])
def test_malformed_or_policy_tuning_yaml_is_rejected(tmp_path, content):
    (tmp_path / "config.yaml").write_text(content)
    with pytest.raises(ValueError):
        load_table_rag_settings(project_root=tmp_path, config_path="config.yaml", env={})

def test_empty_data_override_preserves_legacy_unset_semantics(tmp_path):
    settings = load_table_rag_settings(
        project_root=tmp_path, env={"ADEN_TABLE_RAG_DIR": ""}
    )
    assert settings.data_directory == tmp_path / "artifacts/advanced-table-rag"


def test_runtime_only_yaml_supports_api_document_serving(tmp_path):
    config = tmp_path / "runtime.yaml"
    config.write_text(
        "dataset: advanced\n"
        "documents_dir: source-pdfs\n"
        "table_rag: {data_directory: prepared, device: cpu}\n"
    )
    settings = load_table_rag_settings(
        config_path=config, project_root=tmp_path, env={}
    )
    assert settings.documents_dir == tmp_path / "source-pdfs"
    assert settings.dataset == "advanced"
    assert settings.data_directory == tmp_path / "prepared"


def test_basic_configuration_is_not_silently_used_as_advanced(tmp_path):
    (tmp_path / "basic.yaml").write_text("dataset: basic\n")
    with pytest.raises(ValueError, match="only the advanced"):
        load_table_rag_settings(
            config_path="basic.yaml", project_root=tmp_path, env={}
        )


def test_cli_paths_are_resolved_from_project_root_not_current_directory(tmp_path, monkeypatch):
    from app.rag.config import resolve_project_path

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert resolve_project_path("runs", tmp_path) == tmp_path / "runs"
    absolute = tmp_path / "absolute"
    assert resolve_project_path(absolute, tmp_path) == absolute
