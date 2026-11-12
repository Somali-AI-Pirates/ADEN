"""Stable locations and policy identity shared by the server and CLI."""

from shared.settings import PROJECT_ROOT

DEFAULT_DATA_DIRECTORY = PROJECT_ROOT / "artifacts/advanced-table-rag"
DEFAULT_RUN_DIRECTORY = PROJECT_ROOT / "artifacts/rag-runs"
RETRIEVAL_MODE = "table_rag"
