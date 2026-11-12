"""Resolve TableRAG runtime settings once at the application boundary."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from shared.settings import PROJECT_ROOT

DEFAULT_DATA_DIRECTORY = PROJECT_ROOT / "artifacts/advanced-table-rag"
DEFAULT_RUN_DIRECTORY = PROJECT_ROOT / "artifacts/rag-runs"
RETRIEVAL_MODE = "table_rag"


@dataclass(frozen=True)
class RuntimeCredentials:
    """Connection secrets are never part of settings diagnostics or manifests."""

    openai_api_key: str | None = field(default=None, repr=False)
    qdrant_url: str | None = field(default=None, repr=False)
    qdrant_api_key: str | None = field(default=None, repr=False)
    langfuse_public_key: str | None = field(default=None, repr=False)
    langfuse_secret_key: str | None = field(default=None, repr=False)
    langfuse_base_url: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TableRagSettings:
    data_directory: Path = DEFAULT_DATA_DIRECTORY
    run_directory: Path = DEFAULT_RUN_DIRECTORY
    documents_dir: Path = PROJECT_ROOT / "data/advanced/documents"
    device: str = "cpu"
    vector_backend: str = "numpy"
    collection: str = "advanced_table_rag"
    trace_enabled: bool = True
    credentials: RuntimeCredentials = field(
        default_factory=RuntimeCredentials, repr=False, compare=False
    )

    @property
    def dataset(self) -> str:
        return "advanced"

    def public_manifest(self) -> dict[str, Any]:
        return {
            "data_directory": str(self.data_directory),
            "run_directory": str(self.run_directory),
            "documents_dir": str(self.documents_dir),
            "dataset": self.dataset,
            "device": self.device,
            "vector_backend": self.vector_backend,
            "collection": self.collection,
            "trace_enabled": self.trace_enabled,
        }


_ENV_KEYS = {
    "data_directory": "ADEN_TABLE_RAG_DIR",
    "run_directory": "ADEN_TABLE_RAG_RUN_DIR",
    "device": "ADEN_TABLE_RAG_DEVICE",
    "vector_backend": "ADEN_TABLE_RAG_VECTOR_BACKEND",
    "collection": "ADEN_TABLE_RAG_COLLECTION",
    "trace_enabled": "ADEN_TABLE_RAG_TRACE",
}


def resolve_project_path(value: str | Path, root: Path = PROJECT_ROOT) -> Path:
    """Resolve a runtime/CLI path independently of the current directory."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("TableRAG paths must be non-empty strings or Paths")
    path = Path(value).expanduser()
    return (root / path).resolve()


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"1", "true", "yes", "on"}:
            return True
        if value.lower() in {"0", "false", "no", "off"}:
            return False
    raise ValueError("table_rag.trace_enabled must be a boolean or 0/1")


def load_table_rag_settings(
    *,
    data_directory: str | Path | None = None,
    run_directory: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> TableRagSettings:
    """CLI > process environment > root .env > table_rag YAML > defaults.

    Legacy top-level YAML model/search settings intentionally do not configure
    TableRAG. Reading .env here never changes the process environment.
    """
    root = Path(project_root).resolve()
    dotenv_path = resolve_project_path(env_file, root) if env_file is not None else root / ".env"
    # Disable interpolation so ambient process values cannot change a supplied
    # environment snapshot or silently alter a credential.
    merged = {
        key: value
        for key, value in dotenv_values(dotenv_path, interpolate=False).items()
        if value is not None
    }
    merged.update(os.environ if env is None else env)
    selected_config = config_path or merged.get("ADEN_CONFIG")
    yaml_path = resolve_project_path(selected_config, root) if selected_config else root / "configs/advanced.yaml"
    values: dict[str, Any] = {}
    documents_dir: str | Path = "data/advanced/documents"
    if yaml_path.exists() or selected_config:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("TableRAG config must be a YAML mapping")
        if raw.get("dataset", "advanced") != "advanced":
            raise ValueError("TableRAG supports only the advanced dataset")
        documents_dir = raw.get("documents_dir", documents_dir)
        section = raw.get("table_rag", {})
        if not isinstance(section, dict):
            raise ValueError("table_rag must be a YAML mapping")
        unknown = section.keys() - _ENV_KEYS.keys()
        if unknown:
            raise ValueError(f"Unsupported table_rag settings: {', '.join(sorted(unknown))}")
        values.update(section)
    for name, key in _ENV_KEYS.items():
        if key in merged:
            # Existing API uses an empty data override to mean "not selected".
            if name == "data_directory" and not merged[key].strip():
                continue
            values[name] = merged[key]
    if data_directory is not None:
        values["data_directory"] = data_directory
    if run_directory is not None:
        values["run_directory"] = run_directory
    device = values.get("device", "cpu")
    backend = values.get("vector_backend", "numpy")
    collection = values.get("collection", "advanced_table_rag")
    if device not in ("cpu", "cuda", "cpu_gpu_rerank"):
        raise ValueError("Unsupported TableRAG device")
    if backend not in ("numpy", "qdrant"):
        raise ValueError("Unsupported TableRAG vector backend")
    if not isinstance(collection, str) or not collection.strip():
        raise ValueError("TableRAG collection must be a non-empty string")
    credentials = RuntimeCredentials(
        openai_api_key=merged.get("OPENAI_API_KEY") or None,
        qdrant_url=merged.get("QDRANT_URL") or None,
        qdrant_api_key=merged.get("QDRANT_API_KEY") or None,
        langfuse_public_key=merged.get("LANGFUSE_PUBLIC_KEY") or None,
        langfuse_secret_key=merged.get("LANGFUSE_SECRET_KEY") or None,
        langfuse_base_url=merged.get("LANGFUSE_BASE_URL") or merged.get("LANGFUSE_HOST") or None,
    )
    return TableRagSettings(
        data_directory=resolve_project_path(values.get("data_directory", "artifacts/advanced-table-rag"), root),
        run_directory=resolve_project_path(values.get("run_directory", "artifacts/rag-runs"), root),
        documents_dir=resolve_project_path(documents_dir, root),
        device=device,
        vector_backend=backend,
        collection=collection,
        trace_enabled=_boolean(values.get("trace_enabled", True)),
        credentials=credentials,
    )
