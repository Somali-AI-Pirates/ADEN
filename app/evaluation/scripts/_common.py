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

from dotenv import load_dotenv

from app.evaluation.publishing import GROUPS, plan_publication
from shared.settings import PROJECT_ROOT

ROOT = PROJECT_ROOT


def load_env() -> None:
    """서비스 bootstrap을 거치지 않는 경로(저장 확인·재채점)용. 이미 설정된 환경변수를 우선한다."""
    load_dotenv(ROOT / ".env")


def git_info() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    status = run("status", "--porcelain")
    commit = run("rev-parse", "HEAD")
    if status is None:
        dirty = None
    else:
        dirty = bool(status)
    return {"git_commit": commit, "git_dirty": dirty}


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


def load_callable(spec: str, *, config_path: Path | None = None) -> Any:
    """'package.module:attr' 형식. B의 서비스 코드를 C 모듈이 직접 import하지 않도록 실행 시 지정한다."""
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit(f"'모듈:이름' 형식이어야 한다: {spec}")
    loaded_module = importlib.import_module(module)
    function = getattr(loaded_module, attr)
    configure = getattr(loaded_module, "configure_evaluation", None)
    if config_path is not None and callable(configure):
        configure(config_path)
    return function


def add_publish_options(parser: Any) -> None:
    parser.add_argument("--evaluation-set",
                        help="게시 점수 이름 `<세트>.<지표>`의 세트(기본: 실험 ID). v1·v2와 반복 회차는 "
                             "서로 다른 세트로 두고, 같은 세트에는 같은 채점 조건만 게시한다")
    parser.add_argument("--publish-groups", nargs="+", choices=[*GROUPS, "all"], metavar="지표군",
                        help=f"게시할 지표군: {' '.join(GROUPS)} 또는 all")


def publication_plan(args: Any, manifest: dict[str, Any], *, default_groups: Any = GROUPS,
                     request_ids: Any = ()) -> dict[str, Any] | None:
    """점수를 게시할 실행이면 유료 호출 전에 평가 세트와 지표군을 확정한다."""
    if not args.publish_langfuse_scores:
        return None
    chosen = args.publish_groups or default_groups
    try:
        if "all" in chosen:
            groups = GROUPS
        else:
            groups = chosen
        return plan_publication(manifest, evaluation_set=args.evaluation_set,
                                groups=groups,
                                artifacts_dir=args.artifacts_dir, request_ids=request_ids)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def guard_overwrite(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SystemExit(f"{path}가 이미 있다. 새 실험 ID를 쓰거나 --overwrite를 지정한다.")
