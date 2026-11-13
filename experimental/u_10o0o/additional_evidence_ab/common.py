"""Paths and atomic local records for one isolated experiment."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parent
NAME = "advanced-additional-evidence-20261113"
OUTPUT = ROOT / "artifacts/u_10o0o" / NAME
RECORDS = ROOT / "experimental/u_10o0o/records" / NAME
RUNTIME = OUTPUT / "runtime"
BUNDLE = ROOT / "artifacts/advanced-table-rag"
REVISION = "92e8c038a432c334400619ff38e603be5d863c2e"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
