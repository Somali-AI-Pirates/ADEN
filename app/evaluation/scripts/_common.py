"""평가 스크립트 공통: 경로·재현 정보·주입 함수 로드. 비밀값은 manifest에 넣지 않는다."""

from __future__ import annotations

import hashlib
import importlib
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from shared.settings import PROJECT_ROOT

ROOT = PROJECT_ROOT


def git_info() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    status = run("status", "--porcelain")
    return {"git_commit": run("rev-parse", "HEAD"),
            "git_dirty": None if status is None else bool(status)}


def versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in ("langfuse", "ragas", "openai"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def base_manifest(*, experiment_id: str, dataset_id: str, config_hash: str,
                  sample: bool) -> dict[str, Any]:
    return {"experiment_id": experiment_id, "dataset_id": dataset_id,
            "config_hash": config_hash, "sample": sample, **git_info(),
            "versions": versions()}


def now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def load_callable(spec: str) -> Any:
    """'package.module:attr' 형식. B의 서비스 코드를 C 모듈이 직접 import하지 않도록 실행 시 지정한다."""
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit(f"'모듈:이름' 형식이어야 한다: {spec}")
    return getattr(importlib.import_module(module), attr)


def guard_overwrite(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SystemExit(f"{path}가 이미 있다. 새 실험 ID를 쓰거나 --overwrite를 지정한다.")
