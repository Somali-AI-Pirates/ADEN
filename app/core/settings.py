from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.contracts import RetrievalMode

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "basic.yaml"


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
    qdrant_collection: str = "basic_multilingual-e5-small_p800"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_device: str = "cpu"
    model_timeout_seconds: float = Field(default=60, gt=0)
    max_output_tokens: int = Field(default=2048, gt=0)


def load_settings(path: Path = DEFAULT_CONFIG) -> Settings:
    with path.open(encoding="utf-8") as config:
        return Settings.model_validate(yaml.safe_load(config))
