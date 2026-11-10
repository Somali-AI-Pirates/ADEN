from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from shared.contracts import RetrievalMode

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "advanced.yaml"


class RetrievalSettings(BaseModel):
    default_mode: RetrievalMode
    k: int = Field(gt=0)
    context_token_budget: int = Field(gt=0)
    candidate_count: int = Field(default=20, gt=0)


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
    generation_max_attempts: int = Field(default=2, ge=1)
    context_tokenizer: str = "o200k_base"


def load_settings(path: Path = DEFAULT_CONFIG) -> Settings:
    with path.open(encoding="utf-8") as config:
        settings = Settings.model_validate(yaml.safe_load(config))
    if not settings.documents_dir.is_absolute():
        settings.documents_dir = PROJECT_ROOT / settings.documents_dir
    return settings
