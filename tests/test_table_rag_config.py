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


def test_request_limit_settings_follow_the_shared_precedence(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "table_rag:\n"
        "  max_active_requests: 4\n"
        "  max_waiting_requests: 4\n"
        "  queue_timeout_seconds: 20\n"
        "  request_timeout_seconds: 500\n"
        "  shutdown_grace_seconds: 40\n"
    )
    (tmp_path / ".env").write_text(
        "ADEN_CONFIG=config.yaml\n"
        "ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS=3\n"
        "ADEN_TABLE_RAG_MAX_WAITING_REQUESTS=3\n"
        "ADEN_TABLE_RAG_QUEUE_TIMEOUT_SECONDS=18\n"
        "ADEN_TABLE_RAG_REQUEST_TIMEOUT_SECONDS=450\n"
        "ADEN_TABLE_RAG_SHUTDOWN_GRACE_SECONDS=35\n"
    )
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    yaml_settings = load_table_rag_settings(
        config_path="config.yaml",
        project_root=tmp_path,
        env={},
        env_file=empty_env_file,
    )
    assert (
        yaml_settings.max_active_requests,
        yaml_settings.max_waiting_requests,
        yaml_settings.queue_timeout_seconds,
        yaml_settings.request_timeout_seconds,
        yaml_settings.shutdown_grace_seconds,
    ) == (4, 4, 20.0, 500.0, 40.0)
    dotenv_settings = load_table_rag_settings(project_root=tmp_path, env={})
    assert (
        dotenv_settings.max_active_requests,
        dotenv_settings.max_waiting_requests,
        dotenv_settings.queue_timeout_seconds,
        dotenv_settings.request_timeout_seconds,
        dotenv_settings.shutdown_grace_seconds,
    ) == (3, 3, 18.0, 450.0, 35.0)
    settings = load_table_rag_settings(
        project_root=tmp_path,
        env={
            "ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS": "2",
            "ADEN_TABLE_RAG_MAX_WAITING_REQUESTS": "2",
            "ADEN_TABLE_RAG_QUEUE_TIMEOUT_SECONDS": "15",
            "ADEN_TABLE_RAG_REQUEST_TIMEOUT_SECONDS": "350",
            "ADEN_TABLE_RAG_SHUTDOWN_GRACE_SECONDS": "25",
        },
    )
    assert (
        settings.max_active_requests,
        settings.max_waiting_requests,
        settings.queue_timeout_seconds,
        settings.request_timeout_seconds,
        settings.shutdown_grace_seconds,
    ) == (2, 2, 15.0, 350.0, 25.0)
    manifest = settings.public_manifest()
    assert manifest["max_active_requests"] == 2
    assert manifest["max_waiting_requests"] == 2
    assert manifest["queue_timeout_seconds"] == 15.0
    assert manifest["request_timeout_seconds"] == 350.0
    assert manifest["shutdown_grace_seconds"] == 25.0


def test_request_limit_defaults_are_shared_across_entrypoints(tmp_path):
    settings = load_table_rag_settings(project_root=tmp_path, env={})
    assert (
        settings.max_active_requests,
        settings.max_waiting_requests,
        settings.queue_timeout_seconds,
        settings.request_timeout_seconds,
        settings.shutdown_grace_seconds,
    ) == (1, 2, 10.0, 300.0, 30.0)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS": "true"}, "max_active_requests"),
        ({"ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS": "1.5"}, "max_active_requests"),
        ({"ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS": "0"}, "max_active_requests"),
        ({"ADEN_TABLE_RAG_MAX_WAITING_REQUESTS": "-1"}, "max_waiting_requests"),
        ({"ADEN_TABLE_RAG_QUEUE_TIMEOUT_SECONDS": "nan"}, "queue_timeout_seconds"),
        ({"ADEN_TABLE_RAG_QUEUE_TIMEOUT_SECONDS": "0"}, "queue_timeout_seconds"),
        ({"ADEN_TABLE_RAG_REQUEST_TIMEOUT_SECONDS": "inf"}, "request_timeout_seconds"),
        ({"ADEN_TABLE_RAG_REQUEST_TIMEOUT_SECONDS": "-0.1"}, "request_timeout_seconds"),
        ({"ADEN_TABLE_RAG_SHUTDOWN_GRACE_SECONDS": "-1"}, "shutdown_grace_seconds"),
    ],
)
def test_invalid_request_limit_environment_values_fail(tmp_path, environment, expected):
    with pytest.raises(ValueError, match=expected):
        load_table_rag_settings(project_root=tmp_path, env=environment)


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"max_active_requests": True}, "max_active_requests"),
        ({"max_active_requests": 0}, "max_active_requests"),
        ({"max_waiting_requests": 1.5}, "max_waiting_requests"),
        ({"queue_timeout_seconds": float("nan")}, "queue_timeout_seconds"),
        ({"queue_timeout_seconds": 0}, "queue_timeout_seconds"),
        ({"request_timeout_seconds": float("inf")}, "request_timeout_seconds"),
        ({"request_timeout_seconds": -1}, "request_timeout_seconds"),
        ({"shutdown_grace_seconds": -0.1}, "shutdown_grace_seconds"),
    ],
)
def test_direct_settings_construction_validates_request_limits(kwargs, field_name):
    from app.rag.config import TableRagSettings

    with pytest.raises(ValueError, match=field_name):
        TableRagSettings(**kwargs)
