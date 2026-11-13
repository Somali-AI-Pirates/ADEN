"""Durable, input-bound checkpoints for saved benchmark runs and grading."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shared.settings import PROJECT_ROOT


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fingerprint_files(paths: Sequence[Path]) -> dict[str, str]:
    return {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths))
    }


def assert_inputs_unchanged(fingerprints: Mapping[str, str]) -> None:
    for name, expected in fingerprints.items():
        path = Path(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen evaluation input changed: {name}. Start a new run.")


def runtime_files() -> list[Path]:
    paths = []
    for name in ("app/rag", "app/service", "app/generation", "app/retrieval", "app/observability", "shared"):
        directory = PROJECT_ROOT / name
        paths.extend(p for p in directory.rglob("*") if p.is_file() and p.suffix in {".py", ".txt"})
    paths.extend(PROJECT_ROOT / name for name in ("app/ingestion/layout.py", "pyproject.toml", "uv.lock"))
    return paths


def evaluator_files() -> list[Path]:
    root = PROJECT_ROOT / "app/evaluation"
    return [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in {".py", ".txt", ".json"}
    ]


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_run_prefix(rows: Sequence[Mapping[str, Any]], questions: Sequence[Mapping[str, str]],
                        *, experiment_id: str, config_hash: str) -> None:
    if len(rows) > len(questions):
        raise ValueError("Saved run contains more questions than the frozen selection")
    request_ids = set()
    for row, question in zip(rows, questions):
        if (row.get("question_id") != question["id"] or row.get("question") != question["question"]
                or row.get("experiment_id") != experiment_id or row.get("config_hash") != config_hash):
            raise ValueError("Saved run does not match the frozen question prefix")
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in request_ids:
            raise ValueError("Saved run has missing or duplicate request IDs")
        request_ids.add(request_id)


def checkpoint_path(directory: Path, request_id: str) -> Path:
    # The request ID can come from imported HTTP logs. Never use it as a path.
    return directory / "checkpoints" / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")


def load_grade_checkpoint(directory: Path, record: Mapping[str, Any], conditions_hash: str) -> dict | None:
    path = checkpoint_path(directory, record["request_id"])
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if (saved.get("record_sha256") != digest_json(record)
            or saved.get("conditions_sha256") != conditions_hash
            or saved["score"].get("request_id") != record["request_id"]):
        raise ValueError("Grade checkpoint does not match the saved response or grading conditions")
    return saved["score"]


def save_grade_checkpoint(directory: Path, record: Mapping[str, Any],
                          conditions_hash: str, score: Mapping[str, Any]) -> None:
    path = checkpoint_path(directory, record["request_id"])
    if path.exists():
        raise FileExistsError("A completed grade checkpoint must not be overwritten")
    atomic_json(path, {"record_sha256": digest_json(record),
                      "conditions_sha256": conditions_hash, "score": score})


@contextmanager
def evaluation_lock(directory: Path, identifier: str):
    """Prevent two local writers from spending against the same run/grade."""
    lock_dir = directory / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / (hashlib.sha256(identifier.encode()).hexdigest() + ".lock")
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            if error.errno not in {11, 13}:
                raise
            raise ValueError("This evaluation run is already being written by another process") from error
        try:
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
