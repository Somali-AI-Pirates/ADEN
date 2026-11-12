"""Resolve TableRAG runtime settings once at the application boundary."""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from shared.settings import (
    PROJECT_ROOT,
    connection_settings,
    connection_url,
    read_config_mapping,
    read_local_runtime_config,
)

DEFAULT_DATA_DIRECTORY = PROJECT_ROOT / "artifacts/advanced-table-rag"
DEFAULT_RUN_DIRECTORY = PROJECT_ROOT / "artifacts/rag-runs"
RETRIEVAL_MODE = "table_rag"


def _positive_integer(name: str, value: Any, *, minimum: int) -> int:
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        parsed = int(value.strip())
    else:
        raise ValueError(f"{name} must be an integer")
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _finite_seconds(name: str, value: Any, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number of seconds")
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{name} must be a finite number of seconds")
        try:
            parsed = float(value)
        except ValueError as error:
            raise ValueError(f"{name} must be a finite number of seconds") from error
    else:
        parsed = float(value)
    if not math.isfinite(parsed) or (parsed < 0 if allow_zero else parsed <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a finite {qualifier} number of seconds")
    return parsed


@dataclass(frozen=True)
class RuntimeCredentials:
    """Connection secrets are never part of settings diagnostics or manifests."""

    openai_api_key: str | None = field(default=None, repr=False)
    qdrant_url: str | None = field(default=None, repr=False)
    qdrant_api_key: str | None = field(default=None, repr=False)
    langfuse_public_key: str | None = field(default=None, repr=False)
    langfuse_secret_key: str | None = field(default=None, repr=False)
    langfuse_base_url: str | None = field(default=None, repr=False)
    qdrant_local: bool = False


@dataclass(frozen=True)
class TableRagSettings:
    data_directory: Path = DEFAULT_DATA_DIRECTORY
    run_directory: Path = DEFAULT_RUN_DIRECTORY
    documents_dir: Path = PROJECT_ROOT / "data/advanced/documents"
    device: str = "cpu"
    vector_backend: str = "numpy"
    collection: str = "advanced_table_rag"
    trace_enabled: bool = True
    max_active_requests: int = 1
    max_waiting_requests: int = 2
    queue_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 300.0
    shutdown_grace_seconds: float = 30.0
    credentials: RuntimeCredentials = field(
        default_factory=RuntimeCredentials, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_active_requests",
            _positive_integer(
                "max_active_requests", self.max_active_requests, minimum=1
            ),
        )
        object.__setattr__(
            self,
            "max_waiting_requests",
            _positive_integer(
                "max_waiting_requests", self.max_waiting_requests, minimum=0
            ),
        )
        for name, allow_zero in (
            ("queue_timeout_seconds", False),
            ("request_timeout_seconds", False),
            ("shutdown_grace_seconds", True),
        ):
            object.__setattr__(
                self, name, _finite_seconds(name, getattr(self, name), allow_zero=allow_zero)
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
            "max_active_requests": self.max_active_requests,
            "max_waiting_requests": self.max_waiting_requests,
            "queue_timeout_seconds": self.queue_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "shutdown_grace_seconds": self.shutdown_grace_seconds,
        }


_ENV_KEYS = {
    "data_directory": "ADEN_TABLE_RAG_DIR",
    "run_directory": "ADEN_TABLE_RAG_RUN_DIR",
    "device": "ADEN_TABLE_RAG_DEVICE",
    "vector_backend": "ADEN_TABLE_RAG_VECTOR_BACKEND",
    "collection": "ADEN_TABLE_RAG_COLLECTION",
    "trace_enabled": "ADEN_TABLE_RAG_TRACE",
    "max_active_requests": "ADEN_TABLE_RAG_MAX_ACTIVE_REQUESTS",
    "max_waiting_requests": "ADEN_TABLE_RAG_MAX_WAITING_REQUESTS",
    "queue_timeout_seconds": "ADEN_TABLE_RAG_QUEUE_TIMEOUT_SECONDS",
    "request_timeout_seconds": "ADEN_TABLE_RAG_REQUEST_TIMEOUT_SECONDS",
    "shutdown_grace_seconds": "ADEN_TABLE_RAG_SHUTDOWN_GRACE_SECONDS",
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
    """CLI > process environment > root .env > local/base YAML > defaults.

    Legacy top-level YAML model/search settings intentionally do not configure
    TableRAG. Reading .env here never changes the process environment.
    """
    root = Path(project_root).resolve()
    dotenv_path = resolve_project_path(env_file, root) if env_file is not None else root / ".env"
    # Disable interpolation so ambient process values cannot change a supplied
    # environment snapshot or silently alter a credential.
    dotenv = {
        key: value
        for key, value in dotenv_values(dotenv_path, interpolate=False).items()
        if value is not None
    }
    process = os.environ if env is None else env
    merged = {**dotenv, **process}
    selected_config = config_path or merged.get("ADEN_CONFIG")
    yaml_path = resolve_project_path(selected_config, root) if selected_config else root / "configs/advanced.yaml"
    values: dict[str, Any] = {}
    documents_dir: str | Path = "data/advanced/documents"
    connections: dict[str, Any] = {}
    documents = [read_config_mapping(yaml_path)] if yaml_path.exists() or selected_config else []
    if not selected_config:
        documents.append(read_local_runtime_config(root))
    for raw in documents:
        if raw.get("dataset", "advanced") != "advanced":
            raise ValueError("TableRAG supports only the advanced dataset")
        documents_dir = raw.get("documents_dir", documents_dir)
        section = raw.get("table_rag", {})
        if not isinstance(section, dict):
            raise ValueError("table_rag must be a YAML mapping")
        if section.keys() - _ENV_KEYS.keys():
            raise ValueError("Unsupported table_rag settings")
        values.update(section)
        connections.update(connection_settings(raw))
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
    local_qdrant = connections.get("qdrant_local", False)
    credentials = RuntimeCredentials(
        openai_api_key=merged.get("OPENAI_API_KEY") or None,
        qdrant_url=connection_url(connections, "qdrant_url", process, dotenv),
        qdrant_api_key=None if local_qdrant else merged.get("QDRANT_API_KEY") or None,
        langfuse_public_key=merged.get("LANGFUSE_PUBLIC_KEY") or None,
        langfuse_secret_key=merged.get("LANGFUSE_SECRET_KEY") or None,
        langfuse_base_url=connection_url(connections, "langfuse_base_url", process, dotenv),
        qdrant_local=local_qdrant,
    )
    return TableRagSettings(
        data_directory=resolve_project_path(values.get("data_directory", "artifacts/advanced-table-rag"), root),
        run_directory=resolve_project_path(values.get("run_directory", "artifacts/rag-runs"), root),
        documents_dir=resolve_project_path(documents_dir, root),
        device=device,
        vector_backend=backend,
        collection=collection,
        trace_enabled=_boolean(values.get("trace_enabled", True)),
        max_active_requests=_positive_integer(
            "max_active_requests", values.get("max_active_requests", 1), minimum=1
        ),
        max_waiting_requests=_positive_integer(
            "max_waiting_requests", values.get("max_waiting_requests", 2), minimum=0
        ),
        queue_timeout_seconds=_finite_seconds(
            "queue_timeout_seconds", values.get("queue_timeout_seconds", 10)
        ),
        request_timeout_seconds=_finite_seconds(
            "request_timeout_seconds", values.get("request_timeout_seconds", 300)
        ),
        shutdown_grace_seconds=_finite_seconds(
            "shutdown_grace_seconds",
            values.get("shutdown_grace_seconds", 30),
            allow_zero=True,
        ),
        credentials=credentials,
    )
