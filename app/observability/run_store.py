from __future__ import annotations

import errno
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.observability.records import normalize_run_record

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_DIRECTORY = PROJECT_ROOT / "artifacts" / "rag-runs"
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")


class RequestRunStoreError(RuntimeError):
    """A completed RAG request could not be saved as an evaluation run."""


def _json_default(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _write_exclusive(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _atomic_json(directory: Path, name: str, value: Any) -> None:
    temporary = directory / f".{name}.tmp"
    serialized = json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    _write_exclusive(temporary, serialized)
    os.replace(temporary, directory / name)


def _write_run(directory: Path, record: Mapping[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
    temporary = directory / ".runs.jsonl.tmp"
    _write_exclusive(temporary, line)
    os.replace(temporary, directory / "runs.jsonl")


def _manifest(
    record: Mapping[str, Any],
    service_manifest: Mapping[str, Any] | None,
    origin: str,
) -> dict[str, Any]:
    manifest = {
        "experiment_id": "table-rag",
        "dataset_id": record["dataset_id"],
        "config_hash": record["config_hash"],
        "sample": False,
        "table_rag": {
            "origin": origin,
            "request_id": record["request_id"],
            "question_ids": [record["question_id"]],
            "run_status": "complete",
            "run_count": 1,
            "error_count": int(record["status"] == "error"),
        },
        "trace_id": record.get("langfuse_trace_id"),
    }
    if service_manifest:
        manifest["service"] = dict(service_manifest)
    return manifest


def _store(
    record: Mapping[str, Any],
    *,
    service_manifest: Mapping[str, Any] | None,
    origin: str,
    output_root: Path,
) -> Path:
    request_id = record.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise RequestRunStoreError("Invalid request ID for saved run")
    record = normalize_run_record(record)

    output_root = Path(output_root).resolve()
    target = output_root / request_id
    temporary: Path | None = None
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError("A run already exists for this request ID")
        temporary = Path(tempfile.mkdtemp(prefix=f".{request_id}-", dir=output_root))
        _write_run(temporary, record)
        _atomic_json(temporary, "manifest.json", _manifest(record, service_manifest, origin))
        # The directory becomes visible only after both files are complete.
        os.rename(temporary, target)
        temporary = None
        return target
    except FileExistsError as exc:
        raise RequestRunStoreError(
            f"A run already exists for request ID {request_id}"
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise RequestRunStoreError(
                f"A run already exists for request ID {request_id}"
            ) from exc
        raise RequestRunStoreError(
            f"Could not save request run {request_id}: {type(exc).__name__}"
        ) from exc
    except Exception as exc:
        raise RequestRunStoreError(
            f"Could not save request run {request_id}: {type(exc).__name__}"
        ) from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def _evaluation_record(run_record: Mapping[str, Any], origin: str) -> dict[str, Any]:
    request_id = run_record.get("request_id")
    question = run_record.get("question")
    if not isinstance(request_id, str) or not isinstance(question, str):
        raise RequestRunStoreError("Run record requires question and request ID")
    record = dict(run_record)
    record.update(
        experiment_id="table-rag",
        question_id=request_id,
        dataset_id=str(record.get("dataset_id") or "advanced"),
        retrieval_mode="table_rag",
        config_hash=str(record.get("config_hash") or "unknown"),
        origin=origin,
    )
    usage = record.get("usage")
    usage_attempts = usage.get("attempts") if isinstance(usage, Mapping) else None
    attempts = record.get("attempts")
    if not isinstance(attempts, list) or (not attempts and isinstance(usage_attempts, list)):
        record["attempts"] = list(usage_attempts or [])
    return normalize_run_record(record)


def save_request_run(
    run_record: Mapping[str, Any],
    *,
    service_manifest: Mapping[str, Any] | None = None,
    origin: str = "service",
    output_root: str | Path = DEFAULT_RUNS_DIRECTORY,
) -> Path:
    """Persist a RAG result using the run-record contract consumed by graders."""
    record = _evaluation_record(run_record, origin)
    return _store(
        record,
        service_manifest=service_manifest,
        origin=origin,
        output_root=Path(output_root),
    )


def save_request_failure(
    question: str,
    request_id: str,
    error: BaseException,
    *,
    service_manifest: Mapping[str, Any] | None = None,
    config_hash: str = "unknown",
    origin: str = "service",
    output_root: str | Path = DEFAULT_RUNS_DIRECTORY,
) -> Path:
    """Persist a failed request without exception text, secrets, or private payloads."""
    metadata = getattr(error, "rag_metadata", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    usage = metadata.get("usage")
    usage_attempts = usage.get("attempts") if isinstance(usage, Mapping) else None
    attempts = list(metadata.get("attempts") or usage_attempts or [])
    record = {
        "experiment_id": "table-rag",
        "question_id": request_id,
        "request_id": request_id,
        "dataset_id": "advanced",
        "retrieval_mode": "table_rag",
        "config_hash": config_hash,
        "question": question,
        "retrieved_candidates": metadata.get("retrieved_candidates"),
        "final_context_chunks": metadata.get("final_context_chunks"),
        "final_context_hash": metadata.get("final_context_hash"),
        "model_context_text": metadata.get("model_context_text"),
        "model_context_hash": metadata.get("model_context_hash"),
        "status": "error",
        "answer": None,
        "reason": None,
        "citations": [],
        "latency_ms": int(metadata.get("latency_ms") or 0),
        "attempts": attempts,
        "langfuse_trace_id": metadata.get("langfuse_trace_id") or metadata.get("trace_id"),
        "sql_results": list(metadata.get("sql_results") or []),
        "included_sql_results": list(metadata.get("included_sql_results") or []),
        "diagnostics": dict(metadata.get("diagnostics") or {}),
        "usage": usage,
        "error_type": type(error).__name__,
        "origin": origin,
    }
    return _store(
        record,
        service_manifest=service_manifest,
        origin=origin,
        output_root=Path(output_root),
    )
