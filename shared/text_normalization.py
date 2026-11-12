"""Value normalization shared by record ingestion and read-only lookup."""

from unicodedata import normalize


def normalize_text(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())
