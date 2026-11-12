from dataclasses import replace
from pathlib import Path

from app.ingestion.scripts import prepare_table_rag
from app.rag.config import (
    PROJECT_ROOT,
    RuntimeCredentials,
    TableRagSettings,
    resolve_project_path,
)


def test_build_uses_shared_paths_and_runtime_device_mapping(
    monkeypatch, tmp_path, capsys
):
    settings = TableRagSettings(
        data_directory=tmp_path / "configured-data",
        documents_dir=tmp_path / "configured-pdfs",
        device="cpu_gpu_rerank",
    )
    captured = {}

    def load_settings(*, data_directory=None):
        captured["data_directory_override"] = data_directory
        if data_directory is None:
            return settings
        return replace(settings, data_directory=resolve_project_path(data_directory))

    def build_bundle(documents, output, **kwargs):
        captured.update(documents=documents, output=output, **kwargs)
        return output

    monkeypatch.setattr(prepare_table_rag, "load_table_rag_settings", load_settings)
    monkeypatch.setattr(prepare_table_rag, "build_bundle", build_bundle)
    monkeypatch.setattr(prepare_table_rag, "check_bundle", lambda path: {"pages": 1})

    prepare_table_rag.main(
        [
            "build",
            "--work-dir",
            "tmp/staging",
            "--reuse-vectors-from",
            "artifacts/vector-cache",
        ]
    )

    assert captured["data_directory_override"] is None
    assert captured["documents"] == settings.documents_dir
    assert captured["output"] == settings.data_directory
    assert captured["device"] == "cpu"
    assert captured["work_dir"] == PROJECT_ROOT / "tmp/staging"
    assert captured["cache_bundle"] == PROJECT_ROOT / "artifacts/vector-cache"
    assert "Advanced TableRAG bundle ready:" in capsys.readouterr().out


def test_qdrant_cli_uses_shared_credentials_defaults_and_explicit_overrides(
    monkeypatch, capsys, tmp_path
):
    credentials = RuntimeCredentials(
        qdrant_url="https://unit-test.invalid",
        qdrant_api_key="unit-test-secret",
    )
    settings = TableRagSettings(
        data_directory=tmp_path / "configured-data",
        collection="advanced_table_rag_configured",
        credentials=credentials,
    )
    captured = {}

    def load_settings(*, data_directory=None):
        captured.setdefault("settings_overrides", []).append(data_directory)
        if data_directory is None:
            return settings
        return replace(settings, data_directory=resolve_project_path(data_directory))

    def upload(directory, *, collection, credentials):
        captured["upload"] = (directory, collection, credentials)
        return {"status": "ready"}

    def check(directory, *, collection, full, credentials):
        captured["check"] = (directory, collection, full, credentials)
        return {"status": "ready"}

    monkeypatch.setattr(prepare_table_rag, "load_table_rag_settings", load_settings)
    monkeypatch.setattr(prepare_table_rag, "upload_qdrant_index", upload)
    monkeypatch.setattr(prepare_table_rag, "check_qdrant_index", check)

    prepare_table_rag.main(["upload-qdrant"])
    assert captured["upload"] == (
        settings.data_directory,
        settings.collection,
        credentials,
    )

    prepare_table_rag.main(
        [
            "check-qdrant",
            "--directory",
            "relative/data",
            "--collection",
            "advanced_table_rag_override",
            "--full",
        ]
    )
    assert captured["check"] == (
        PROJECT_ROOT / "relative/data",
        "advanced_table_rag_override",
        True,
        credentials,
    )
    assert captured["settings_overrides"] == [None, Path("relative/data")]
    assert "unit-test-secret" not in capsys.readouterr().out
