import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from shared.contracts import RetrievalMode

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "advanced.yaml"
CONFIG_ENV = "ADEN_CONFIG"
_CONNECTION_ENV = {
    "qdrant_url": ("QDRANT_URL",),
    "langfuse_base_url": ("LANGFUSE_BASE_URL", "LANGFUSE_HOST"),
}


def read_config_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise ValueError(f"Invalid YAML in {path.name}") from None
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return raw


def read_local_runtime_config(root: Path) -> dict[str, Any]:
    path = root / "configs/local.yaml"
    if not path.is_file():
        return {}
    raw = read_config_mapping(path)
    if raw.keys() - {"documents_dir", "table_rag", "connections"}:
        raise ValueError("configs/local.yaml supports only documents_dir, table_rag, and connections")
    return raw


def connection_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    connections = raw.get("connections", {})
    if not isinstance(connections, dict) or connections.keys() - {
        "qdrant_url", "qdrant_local", "langfuse_base_url",
    }:
        raise ValueError("Unsupported connections settings")
    for name in ("qdrant_url", "langfuse_base_url"):
        value = connections.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"connections.{name} must be a URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and (port is None or port > 0)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and not any(char.isspace() for char in value)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"connections.{name} must be a URL without credentials or query parameters")
    if type(connections.get("qdrant_local", False)) is not bool:
        raise ValueError("connections.qdrant_local must be a boolean")
    return connections


def connection_url(
    connections: Mapping[str, Any],
    name: str,
    *environment: Mapping[str, str],
) -> str | None:
    for values in environment:
        present = [values[key] for key in _CONNECTION_ENV[name] if key in values]
        if present:
            resolved = next((value for value in present if value), None)
            if resolved is not None:
                connection_settings({"connections": {name: resolved}})
            return resolved
    return connections.get(name)


def legacy_connection_url(name: str) -> str | None:
    """Resolve a legacy endpoint independently of the Advanced-only local Qdrant."""
    if any(key in os.environ for key in _CONNECTION_ENV[name]):
        return connection_url({}, name, os.environ)
    configured = configured_path()
    selected = connection_settings(read_config_mapping(configured)) if configured.exists() else {}
    workstation = connection_settings(read_local_runtime_config(PROJECT_ROOT))
    sources = (selected, workstation) if os.environ.get(CONFIG_ENV) else (workstation, selected)
    for connections in sources:
        if name == "qdrant_url" and connections.get("qdrant_local"):
            continue
        if name in connections:
            return connection_url(connections, name)
    return None


class RetrievalSettings(BaseModel):
    default_mode: RetrievalMode
    k: int = Field(gt=0)
    context_token_budget: int = Field(gt=0)
    candidate_count: int = Field(default=20, gt=0)


class EvaluationSettings(BaseModel):
    """LLM-evaluator policy, independent from answer-generation settings."""

    reasoning_effort: Literal["max"] = "max"
    fallback_reasoning_effort: Literal["xhigh"] = "xhigh"
    max_output_tokens: int = Field(default=128000, gt=0)
    timeout_seconds: float = Field(default=600, gt=0)
    max_retries: int = Field(default=2, ge=0)


class Settings(BaseModel):
    dataset: str
    documents_dir: Path
    generation_model: str
    retrieval: RetrievalSettings
    qdrant_collection: str = "advanced_snowflake_ko_pages"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_device: str = "cpu"
    model_timeout_seconds: float = Field(default=60, gt=0)
    max_output_tokens: int = Field(default=2048, gt=0)
    # 추론 모델의 추론 강도. 없으면 모델 기본값을 쓴다. 추론 토큰도 max_output_tokens 안에 포함된다.
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    generation_max_attempts: int = Field(default=2, ge=1)
    context_tokenizer: str = "o200k_base"
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)


def configured_path(default: Path = DEFAULT_CONFIG) -> Path:
    """B/C 평가 CLI가 쓰는 설정 선택. ADEN_CONFIG(저장소 루트 기준 경로)가 있으면 그 파일이다."""
    chosen = os.environ.get(CONFIG_ENV, "").strip()
    if not chosen:
        return default
    path = PROJECT_ROOT / chosen
    if not path.is_file():
        raise FileNotFoundError(f"{CONFIG_ENV}가 가리키는 설정 파일이 없습니다: {path}")
    return path


def load_settings(path: Path = DEFAULT_CONFIG) -> Settings:
    with path.open(encoding="utf-8") as config:
        settings = Settings.model_validate(yaml.safe_load(config))
    if not settings.documents_dir.is_absolute():
        settings.documents_dir = PROJECT_ROOT / settings.documents_dir
    return settings
