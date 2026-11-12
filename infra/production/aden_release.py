#!/usr/bin/env python3
"""Fixed-command root-side ADEN release controller; input is bounded JSON on stdin."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 4096
MAX_WEB_BYTES = 256 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
POST_PULL_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
HEALTH_TIMEOUT = 600
POLL_INTERVAL = 2
HTTP_TIMEOUT = 5
SOURCE = "https://github.com/Somali-AI-Pirates/ADEN"
PUBLIC = "https://aden.29232923.xyz"
API_REPO = "ghcr.io/somali-ai-pirates/aden-api"
WEB_REPO = "ghcr.io/somali-ai-pirates/aden-web"
PDF_BOOK = "AS102"
SERVICE = "api"
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_ID = re.compile(r"^[0-9]{1,10}-[0-9]{1,10}-[0-9a-f]{40}$")
HASH_KEYS = {"compose", "runtime_config", "bundle_manifest", "documents_manifest", "models_manifest"}

class ReleaseError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

@dataclass(frozen=True)
class Paths:
    root: Path
    releases: Path
    current: Path
    state_dir: Path
    state_file: Path
    pending_file: Path
    history_file: Path
    lock_file: Path
    policy_file: Path
    compose_file: Path
    runtime_config: Path
    runtime_env: Path
    bundle_dir: Path
    bundle_manifest: Path
    documents_dir: Path
    documents_manifest: Path
    models_dir: Path
    models_manifest: Path
    runs_dir: Path

    @classmethod
    def production(cls) -> "Paths":
        root, state = Path("/srv/aden"), Path("/var/lib/aden/deploy")
        bundle = Path("/srv/aden/shared/bundle")
        docs = Path("/srv/aden/shared/documents")
        models = Path("/srv/aden/shared/models")
        return cls(root, root / "releases", root / "current", state, state / "state.json",
                   state / "pending.json", state / "history.jsonl", Path("/run/lock/aden-release.lock"),
                   Path("/etc/aden/deploy-policy.json"), Path("/etc/aden/compose.production.yaml"),
                   Path("/etc/aden/runtime.yaml"), Path("/etc/aden/runtime.env"), bundle,
                   bundle / "corpus.json", docs, docs / "asset-manifest.json", models,
                   models / "asset-manifest.json", Path("/srv/aden/shared/runs"))

    @classmethod
    def testing(cls, root: Path) -> "Paths":
        root = root.resolve()
        state, etc = root / "var/lib/aden/deploy", root / "etc/aden"
        aden = root / "srv/aden"
        bundle, docs, models = aden / "shared/bundle", aden / "shared/documents", aden / "shared/models"
        return cls(aden, aden / "releases", aden / "current", state, state / "state.json",
                   state / "pending.json", state / "history.jsonl", state / "lock", etc / "deploy-policy.json",
                   etc / "compose.production.yaml", etc / "runtime.yaml", etc / "runtime.env", bundle,
                   bundle / "corpus.json", docs, docs / "asset-manifest.json", models,
                   models / "asset-manifest.json", aden / "shared/runs")

@dataclass(frozen=True)
class Policy:
    enabled: bool
    hashes: Mapping[str, str]

@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes

class CommandRunner:
    def run(self, argv: list[str], *, timeout: int = 60, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        if not argv or any(not isinstance(x, str) or "\x00" in x for x in argv):
            raise ReleaseError("invalid_internal_command")
        try:
            result = subprocess.run(argv, cwd=cwd, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError("command_failed") from exc
        if result.returncode:
            raise ReleaseError("command_failed")
        return result

class ReleaseManager:
    def __init__(self, paths: Paths, *, runner: Any | None = None,
                 http_get: Callable[[str, Mapping[str, str]], HttpResult] | None = None,
                 enforce_ownership: bool = True, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.p = paths
        self.runner = runner or CommandRunner()
        self.http_get = http_get or _http_get
        self.enforce_ownership, self.monotonic, self.sleep = enforce_ownership, monotonic, sleep

    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_dirs()
        with _Lock(self.p.lock_file, self.enforce_ownership):
            if payload["operation"] == "status":
                return self._status()
            self._recover_pending()
            if payload["operation"] == "deploy":
                return self._deploy(payload)
            if payload["operation"] == "rollback":
                return self._rollback()
            raise ReleaseError("invalid_operation")

    def _read_policy(self) -> Policy:
        value = self._json_file(self.p.policy_file, "policy_invalid", secret=True)
        if not isinstance(value, dict) or set(value) != {"schema_version", "deployment_enabled", "approved_sha256"}:
            raise ReleaseError("policy_invalid")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1 or type(value["deployment_enabled"]) is not bool:
            raise ReleaseError("policy_invalid")
        hashes = value["approved_sha256"]
        if not isinstance(hashes, dict) or set(hashes) != HASH_KEYS:
            raise ReleaseError("policy_invalid")
        if any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes.values()):
            raise ReleaseError("policy_unconfigured")
        return Policy(value["deployment_enabled"], hashes)

    def _status(self) -> dict[str, Any]:
        state = self._state()
        return {"service": "aden", "deployment_enabled": self._deployment_enabled(),
                "active_release_id": state["active_release_id"], "previous_release_id": state["previous_release_id"],
                "accepted_sequence": state["accepted_sequence"], "recovery_pending": self.p.pending_file.exists()}

    def _deployment_enabled(self) -> bool:
        try:
            return self._read_policy().enabled
        except ReleaseError:
            return False

    def _verify_host(self, policy: Policy, *, check_config: bool, expected: Mapping[str, str] | None = None, verify_contents: bool = True) -> dict[str, str]:
        for path in (self.p.root, self.p.releases, self.p.bundle_dir, self.p.documents_dir, self.p.models_dir):
            self._secure_dir(path)
        self._secure_dir(self.p.runs_dir, uid=994, gid=985, app_writable=True)
        self._secure_file(self.p.compose_file)
        self._secure_file(self.p.runtime_config if check_config else self.p.compose_file)
        self._secure_file(self.p.runtime_env, secret=True)
        self._secure_file(self.p.bundle_manifest)
        self._secure_file(self.p.documents_manifest)
        self._secure_file(self.p.models_manifest)
        result = {"compose": _hash_file(self.p.compose_file),
                  "bundle_manifest": _hash_file(self.p.bundle_manifest),
                  "documents_manifest": _hash_file(self.p.documents_manifest),
                  "models_manifest": _hash_file(self.p.models_manifest)}
        if check_config:
            self._secure_file(self.p.runtime_config)
            result["runtime_config"] = _hash_file(self.p.runtime_config)
            compare = policy.hashes
        else:
            if expected is None:
                raise ReleaseError("internal_validation_error")
            result["runtime_config"] = expected["runtime_config"]
            compare = {k: v for k, v in expected.items() if k != "runtime_config"}
        for key, digest in compare.items():
            if result[key] != digest:
                raise ReleaseError("asset_fingerprint_mismatch")
        if verify_contents:
            _verify_content_manifest(self.p.documents_manifest, self.p.documents_dir, allow_links=False)
            _verify_content_manifest(self.p.models_manifest, self.p.models_dir, allow_links=True)
        if expected is not None and any(result[k] != v for k, v in expected.items()):
            raise ReleaseError("asset_changed_during_deploy")
        return result

    def _secure_dir(
        self, path: Path, *, uid: int = 0, gid: int | None = None,
        app_writable: bool = False, private: bool = False,
    ) -> None:
        try:
            info = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ReleaseError("required_path_missing") from exc
        if not stat.S_ISDIR(info.st_mode) or resolved != path:
            raise ReleaseError("unsafe_path")
        required_mode = 0o700 if app_writable or private else 0o005
        forbidden_mode = 0o077 if private else 0o022
        if self.enforce_ownership and (
            info.st_uid != uid
            or (gid is not None and info.st_gid != gid)
            or info.st_mode & forbidden_mode
            or info.st_mode & required_mode != required_mode
        ):
            raise ReleaseError("unsafe_permissions")

    def _secure_file(self, path: Path, secret: bool = False) -> None:
        try:
            info = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ReleaseError("required_path_missing") from exc
        if not stat.S_ISREG(info.st_mode) or resolved != path:
            raise ReleaseError("unsafe_path")
        if self.enforce_ownership and (info.st_uid != 0 or info.st_mode & 0o022 or
                                       (secret and info.st_mode & 0o077) or
                                       (not secret and info.st_mode & 0o004 == 0)):
            raise ReleaseError("unsafe_permissions")

    def _ensure_dirs(self) -> None:
        if self.enforce_ownership:
            self._secure_dir(self.p.root)
            self._secure_dir(self.p.releases)
            self._secure_dir(self.p.state_dir, private=True)
        else:
            self.p.root.mkdir(parents=True, exist_ok=True)
            self.p.releases.mkdir(parents=True, exist_ok=True)
            self.p.state_dir.mkdir(parents=True, exist_ok=True)

    def _deploy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        policy = self._read_policy()
        if not policy.enabled:
            self._record("deploy", "rejected", "deployment_disabled", payload)
            raise ReleaseError("deployment_disabled")
        sequence = (payload["run_number"], payload["run_attempt"])
        state = self._state()
        accepted = state["accepted_sequence"]
        if accepted is not None and sequence <= tuple(accepted):
            self._record("deploy", "rejected", "stale_release", payload)
            raise ReleaseError("stale_release")
        self._validate_active(state)
        fingerprints = self._verify_host(policy, check_config=True)
        if shutil.disk_usage(self.p.root).free < MIN_FREE_BYTES:
            raise ReleaseError("insufficient_disk")
        commit = payload["commit_sha"]
        release_id = f"{sequence[0]}-{sequence[1]}-{commit}"
        final_dir = self.p.releases / release_id
        if final_dir.exists() or final_dir.is_symlink():
            raise ReleaseError("release_already_exists")
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.p.releases))
        candidate_ready = pending_written = False
        try:
            self._pull_verify(payload["api_image"], commit, API_REPO)
            self._pull_verify(payload["web_image"], commit, WEB_REPO)
            if shutil.disk_usage(self.p.root).free < POST_PULL_MIN_FREE_BYTES:
                raise ReleaseError("insufficient_disk_after_pull")
            web = staging / "web"
            web.mkdir(mode=0o755)
            web.chmod(0o755)
            self._extract_web(payload["web_image"], web)
            config = self.p.runtime_config.read_bytes()
            compose_bytes = self.p.compose_file.read_bytes()
            if hashlib.sha256(config).hexdigest() != fingerprints["runtime_config"]:
                raise ReleaseError("runtime_config_changed")
            if hashlib.sha256(compose_bytes).hexdigest() != fingerprints["compose"]:
                raise ReleaseError("compose_changed")
            _write_file(staging / "runtime.yaml", config, 0o644)
            _write_file(staging / "compose.yaml", compose_bytes, 0o644)
            env_text = (f"ADEN_API_IMAGE={payload['api_image']}\n"
                        f"ADEN_RELEASE_CONFIG={final_dir}/runtime.yaml\n"
                        f"ADEN_RUNTIME_ENV={self.p.runtime_env}\n")
            _write_file(staging / "release.env", env_text.encode("ascii"), 0o600)
            release = {"schema_version": 1, "release_id": release_id, "commit_sha": commit,
                       "run_number": sequence[0], "run_attempt": sequence[1],
                       "api_image": payload["api_image"], "web_image": payload["web_image"],
                       "runtime_config_sha256": fingerprints["runtime_config"],
                       "asset_sha256": {k: v for k, v in fingerprints.items() if k != "runtime_config"},
                       "created_at": _now()}
            _atomic_json(staging / "release.json", release, 0o644)
            staging.chmod(0o755)
            _fsync_tree(staging)
            os.rename(staging, final_dir)
            _fsync_dir(self.p.releases)
            candidate_ready = True
            self._write_pending({"schema_version": 1, "operation": "deploy",
                                 "candidate_release_id": release_id,
                                 "previous_release_id": state["active_release_id"], "created_at": _now()})
            pending_written = True
            self._start_api(release_id)
            self._wait_ready(release_id)
            self._verify_host(policy, check_config=False, expected=fingerprints, verify_contents=False)
            self._switch_current(release_id)
            self._public_smoke()
            self._write_state({"schema_version": 1, "accepted_sequence": list(sequence),
                               "active_release_id": release_id,
                               "previous_release_id": state["active_release_id"]})
            state_committed = True
            try:
                self._clear_pending()
            except OSError:
                pass
            audit_recorded = self._record_safely("deploy", "succeeded", "ok", payload, release_id)
            return {"status": "deployed", "release_id": release_id,
                    "recovery_pending": self.p.pending_file.exists(), "audit_recorded": audit_recorded}
        except Exception as error:
            if locals().get("state_committed", False):
                return {"status": "deployed", "release_id": release_id,
                        "recovery_pending": self.p.pending_file.exists(), "audit_recorded": False}
            if pending_written:
                if self._restore(state["active_release_id"], release_id):
                    self._clear_pending()
                    self._record("deploy", "failed_rolled_back", _error(error), payload, release_id)
                else:
                    self._record("deploy", "rollback_failed", "rollback_failed", payload, release_id)
                    raise ReleaseError("rollback_failed") from error
            else:
                self._record("deploy", "failed", _error(error), payload, release_id)
            raise error
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if candidate_ready and not pending_written and final_dir.exists():
                if self._state()["active_release_id"] != release_id:
                    shutil.rmtree(final_dir, ignore_errors=True)

    def _rollback(self) -> dict[str, Any]:
        state = self._state()
        active_id, target_id = state["active_release_id"], state["previous_release_id"]
        if not active_id or not target_id:
            raise ReleaseError("no_previous_release")
        self._validate_active(state)
        self._verify_release_assets(self._read_release(target_id))
        self._pull_verify_release(target_id)
        self._write_pending({"schema_version": 1, "operation": "rollback",
                             "candidate_release_id": target_id,
                             "previous_release_id": active_id, "created_at": _now()})
        try:
            self._start_api(target_id)
            self._wait_ready(target_id)
            self._switch_current(target_id)
            self._public_smoke()
            self._write_state({"schema_version": 1, "accepted_sequence": state["accepted_sequence"],
                               "active_release_id": target_id, "previous_release_id": active_id})
            state_committed = True
            try:
                self._clear_pending()
            except OSError:
                pass
            audit_recorded = self._record_safely("rollback", "succeeded", "ok", release_id=target_id)
            return {"status": "rolled_back", "release_id": target_id,
                    "recovery_pending": self.p.pending_file.exists(), "audit_recorded": audit_recorded}
        except Exception as error:
            if locals().get("state_committed", False):
                return {"status": "rolled_back", "release_id": target_id,
                        "recovery_pending": self.p.pending_file.exists(), "audit_recorded": False}
            if self._restore(active_id, target_id):
                self._clear_pending()
                self._record("rollback", "failed_rolled_back", _error(error), release_id=target_id)
            else:
                self._record("rollback", "rollback_failed", "rollback_failed", release_id=target_id)
                raise ReleaseError("rollback_failed") from error
            raise error

    def _recover_pending(self) -> None:
        if not self.p.pending_file.exists():
            return
        pending = self._json_file(self.p.pending_file, "pending_state_invalid", secret=True)
        if not isinstance(pending, dict) or set(pending) != {
            "schema_version", "operation", "candidate_release_id", "previous_release_id", "created_at"
        } or type(pending["schema_version"]) is not int or pending["schema_version"] != 1 or pending["operation"] not in {"deploy", "rollback"}:
            raise ReleaseError("pending_state_invalid")
        candidate, previous = pending["candidate_release_id"], pending["previous_release_id"]
        state = self._state()
        if state["active_release_id"] == candidate and state["previous_release_id"] == previous:
            self._clear_pending()
            self._record_safely("recovery", "succeeded", "finalized_committed_release", release_id=candidate)
            return
        if (candidate is not None and (not isinstance(candidate, str) or not RELEASE_ID.fullmatch(candidate))) or (
            previous is not None and (not isinstance(previous, str) or not RELEASE_ID.fullmatch(previous))
        ):
            raise ReleaseError("pending_state_invalid")
        if not self._restore(previous, candidate):
            self._record("recovery", "rollback_failed", "rollback_failed", release_id=candidate)
            raise ReleaseError("recovery_required")
        self._clear_pending()
        self._record("recovery", "succeeded", "restored_previous", release_id=previous)

    def _restore(self, previous_id: str | None, candidate_id: str | None) -> bool:
        try:
            if previous_id is None:
                if candidate_id:
                    env = self.p.releases / candidate_id / "release.env"
                    if env.is_file() and not env.is_symlink():
                        self._compose(candidate_id, ["rm", "--stop", "--force", SERVICE])
                    self._remove_current_if(candidate_id)
                return not self.p.current.exists() and not self.p.current.is_symlink()
            release = self._read_release(previous_id)
            self._verify_release_assets(release)
            self._pull_verify_release(previous_id)
            self._start_api(previous_id)
            self._wait_ready(previous_id)
            self._switch_current(previous_id)
            self._public_smoke()
            return True
        except Exception:
            return False

    def _validate_active(self, state: Mapping[str, Any]) -> None:
        active = state["active_release_id"]
        if active is None:
            if self.p.current.exists() or self.p.current.is_symlink():
                raise ReleaseError("active_pointer_mismatch")
            return
        if not isinstance(active, str) or not RELEASE_ID.fullmatch(active):
            raise ReleaseError("state_invalid")
        expected = self.p.releases / active
        if not self.p.current.is_symlink() or os.readlink(self.p.current) != str(expected):
            raise ReleaseError("active_pointer_mismatch")
        self._read_release(active)

    def _state(self) -> dict[str, Any]:
        default = {"schema_version": 1, "accepted_sequence": None,
                   "active_release_id": None, "previous_release_id": None}
        if not self.p.state_file.exists():
            return default
        value = self._json_file(self.p.state_file, "state_invalid", secret=True)
        if not isinstance(value, dict) or set(value) != set(default) or value["schema_version"] != 1:
            raise ReleaseError("state_invalid")
        seq = value["accepted_sequence"]
        if seq is not None and (not isinstance(seq, list) or len(seq) != 2 or
                                any(type(x) is not int or x < 1 for x in seq)):
            raise ReleaseError("state_invalid")
        for key in ("active_release_id", "previous_release_id"):
            item = value[key]
            if item is not None and (not isinstance(item, str) or not RELEASE_ID.fullmatch(item)):
                raise ReleaseError("state_invalid")
        return value

    def _read_release(self, release_id: str) -> dict[str, Any]:
        if not RELEASE_ID.fullmatch(release_id):
            raise ReleaseError("release_invalid")
        value = self._json_file(self.p.releases / release_id / "release.json", "release_invalid")
        required = {"schema_version", "release_id", "commit_sha", "run_number", "run_attempt",
                    "api_image", "web_image", "runtime_config_sha256", "asset_sha256", "created_at"}
        if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ReleaseError("release_invalid")
        if value["release_id"] != release_id or not isinstance(value["commit_sha"], str) or not SHA.fullmatch(value["commit_sha"]):
            raise ReleaseError("release_invalid")
        if type(value["run_number"]) is not int or type(value["run_attempt"]) is not int or min(value["run_number"], value["run_attempt"]) < 1:
            raise ReleaseError("release_invalid")
        _image_ref(API_REPO, value["api_image"])
        _image_ref(WEB_REPO, value["web_image"])
        if not isinstance(value["runtime_config_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["runtime_config_sha256"]):
            raise ReleaseError("release_invalid")
        hashes = value["asset_sha256"]
        asset_keys = HASH_KEYS - {"runtime_config"}
        if not isinstance(hashes, dict) or set(hashes) != asset_keys or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()
        ):
            raise ReleaseError("release_invalid")
        return value

    def _verify_release_assets(self, release: Mapping[str, Any]) -> None:
        asset_hashes = release["asset_sha256"]
        expected = {**asset_hashes, "runtime_config": release["runtime_config_sha256"]}
        for path in (self.p.root, self.p.releases, self.p.bundle_dir, self.p.documents_dir, self.p.models_dir):
            self._secure_dir(path)
        self._secure_dir(self.p.runs_dir, uid=994, gid=985, app_writable=True)
        self._secure_file(self.p.runtime_env, secret=True)
        release_dir = self.p.releases / release["release_id"]
        config = release_dir / "runtime.yaml"
        compose_snapshot = release_dir / "compose.yaml"
        self._secure_file(config)
        self._secure_file(compose_snapshot)
        for path in (self.p.bundle_manifest, self.p.documents_manifest, self.p.models_manifest):
            self._secure_file(path)
        actual = {"compose": _hash_file(compose_snapshot),
                  "bundle_manifest": _hash_file(self.p.bundle_manifest),
                  "documents_manifest": _hash_file(self.p.documents_manifest),
                  "models_manifest": _hash_file(self.p.models_manifest),
                  "runtime_config": _hash_file(config)}
        if any(actual[key] != digest for key, digest in expected.items()):
            raise ReleaseError("rollback_asset_mismatch")
        _verify_content_manifest(self.p.documents_manifest, self.p.documents_dir, allow_links=False)
        _verify_content_manifest(self.p.models_manifest, self.p.models_dir, allow_links=True)
        self._validate_release_files(release["release_id"])

    def _pull_verify(
        self, image: str, commit: str, repository: str, *, prefer_local: bool = False
    ) -> None:
        pull = ["/usr/bin/docker", "pull", image]
        inspect = ["/usr/bin/docker", "image", "inspect", "--format", "{{json .}}", image]
        if not prefer_local:
            self.runner.run(pull, timeout=600)
        try:
            result = self.runner.run(inspect)
        except ReleaseError:
            if not prefer_local:
                raise
            self.runner.run(pull, timeout=600)
            result = self.runner.run(inspect)
        try:
            image_info = json.loads(result.stdout)
            labels = image_info["Config"]["Labels"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ReleaseError("image_metadata_invalid") from exc
        if (_image_ref(repository, image) != image or image_info.get("Os") != "linux" or image_info.get("Architecture") != "arm64" or
            not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != commit or
            labels.get("org.opencontainers.image.source", "").rstrip("/") != SOURCE or
            image not in image_info.get("RepoDigests", [])):
            raise ReleaseError("image_identity_mismatch")

    def _pull_verify_release(self, release_id: str) -> None:
        release = self._read_release(release_id)
        self._pull_verify(release["api_image"], release["commit_sha"], API_REPO, prefer_local=True)

    def _extract_web(self, image: str, destination: Path) -> None:
        name = f"aden-web-extract-{uuid.uuid4().hex[:12]}"
        container_id = None
        try:
            # The scratch export is copied from an unstarted container.
            output = self.runner.run(["/usr/bin/docker", "create", "--name", name, image, "/bin/true"]).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{12,64}", output):
                raise ReleaseError("web_extract_failed")
            container_id = output
            self.runner.run(["/usr/bin/docker", "cp", f"{container_id}:/web/.", str(destination)], timeout=120)
            self._validate_web_tree(destination)
        finally:
            if container_id:
                try:
                    self.runner.run(["/usr/bin/docker", "rm", "--force", container_id])
                except ReleaseError:
                    pass

    def _validate_web_tree(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o005 != 0o005:
            raise ReleaseError("unsafe_web_asset")
        total = count = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            base = Path(current)
            for name in dirs:
                item = base / name
                if item.is_symlink() or not item.is_dir() or item.stat().st_mode & 0o005 != 0o005:
                    raise ReleaseError("unsafe_web_asset")
            for name in files:
                item = base / name
                info = item.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o004:
                    raise ReleaseError("unsafe_web_asset")
                total += info.st_size
                count += 1
                if total > MAX_WEB_BYTES or count > 10000:
                    raise ReleaseError("web_asset_limit")
        index = root / "index.html"
        if index.is_symlink() or not index.is_file() or index.stat().st_size > 2 * 1024 * 1024:
            raise ReleaseError("web_index_missing")
        if b"<html" not in index.read_bytes().lower():
            raise ReleaseError("web_index_invalid")

    def _start_api(self, release_id: str) -> None:
        self._validate_release_files(release_id)
        self._compose(release_id, ["up", "--detach", "--pull", "never", "--force-recreate", SERVICE])

    def _compose(self, release_id: str, args: list[str]):
        release_dir = self.p.releases / release_id
        compose_file, env_file = release_dir / "compose.yaml", release_dir / "release.env"
        if compose_file.is_symlink() or env_file.is_symlink() or not compose_file.is_file() or not env_file.is_file():
            raise ReleaseError("release_invalid")
        argv = ["/usr/bin/docker", "compose", "--project-name", "aden", "--file",
                str(compose_file), "--env-file", str(env_file), *args]
        return self.runner.run(argv, timeout=600, cwd=self.p.root)

    def _wait_ready(self, release_id: str) -> None:
        deadline = self.monotonic() + HEALTH_TIMEOUT
        while self.monotonic() < deadline:
            try:
                result = self._compose(release_id, ["ps", "--quiet", SERVICE])
                container_id = result.stdout.strip()
                if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                    raise ReleaseError("not_ready")
                health = self.runner.run(["/usr/bin/docker", "inspect", "--format",
                    "{{.State.Health.Status}}", container_id], timeout=10).stdout.strip()
                ready = self.http_get("http://127.0.0.1:18010/health/ready", {})
                body = _parse_object(ready.body)
                if health == "healthy" and ready.status == 200 and body.get("ready") is True:
                    return
            except (ReleaseError, ValueError, TypeError):
                pass
            self.sleep(POLL_INTERVAL)
        raise ReleaseError("readiness_timeout")

    def _public_smoke(self) -> None:
        web = self.http_get(PUBLIC + "/", {})
        if web.status != 200 or "text/html" not in _header(web.headers, "content-type").lower() or b"<html" not in web.body[:1024].lower():
            raise ReleaseError("public_web_smoke_failed")
        live = self.http_get(PUBLIC + "/api/health/live", {})
        if live.status != 200 or _parse_object(live.body).get("status") != "ok":
            raise ReleaseError("public_api_smoke_failed")
        pdf = self.http_get(PUBLIC + f"/api/documents/{PDF_BOOK}", {"Range": "bytes=0-7"})
        content_range = _header(pdf.headers, "content-range")
        if (pdf.status != 206 or not _header(pdf.headers, "content-type").lower().startswith("application/pdf") or
            not re.fullmatch(r"bytes 0-7/[1-9][0-9]*", content_range) or not pdf.body.startswith(b"%PDF-")):
            raise ReleaseError("public_pdf_range_smoke_failed")
        worker = self.http_get(PUBLIC + "/pdf-assets/pdf.worker.min.mjs", {"Range": "bytes=0-0"})
        worker_mime = _header(worker.headers, "content-type").split(";", 1)[0].strip().lower()
        if worker.status not in (200, 206) or worker_mime != "text/javascript":
            raise ReleaseError("public_pdf_worker_smoke_failed")
        ready = self.http_get(PUBLIC + "/api/health/ready", {})
        if 200 <= ready.status < 300:
            raise ReleaseError("readiness_publicly_exposed")

    def _switch_current(self, release_id: str) -> None:
        if not RELEASE_ID.fullmatch(release_id):
            raise ReleaseError("release_invalid")
        release_dir = self.p.releases / release_id
        if release_dir.is_symlink() or not release_dir.is_dir():
            raise ReleaseError("release_invalid")
        self._validate_release_files(release_id)
        if self.p.current.exists() and not self.p.current.is_symlink():
            raise ReleaseError("unsafe_current_path")
        temporary = self.p.root / f".current-{uuid.uuid4().hex}"
        try:
            os.symlink(str(release_dir), temporary)
            os.replace(temporary, self.p.current)
            _fsync_dir(self.p.root)
        finally:
            if temporary.is_symlink():
                temporary.unlink()

    def _remove_current_if(self, release_id: str) -> None:
        if self.p.current.exists() and not self.p.current.is_symlink():
            raise ReleaseError("unsafe_current_path")
        expected = str(self.p.releases / release_id)
        if self.p.current.is_symlink() and os.readlink(self.p.current) == expected:
            self.p.current.unlink()
            _fsync_dir(self.p.root)

    def _validate_release_files(self, release_id: str) -> None:
        if not RELEASE_ID.fullmatch(release_id):
            raise ReleaseError("release_invalid")
        root = self.p.releases / release_id
        if root.is_symlink() or not root.is_dir():
            raise ReleaseError("release_invalid")
        if self.enforce_ownership:
            self._secure_dir(root)
        release = self._read_release(release_id)
        config, compose, env = root / "runtime.yaml", root / "compose.yaml", root / "release.env"
        if any(path.is_symlink() or not path.is_file() for path in (config, compose, env)):
            raise ReleaseError("release_invalid")
        if self.enforce_ownership:
            self._secure_file(config)
            self._secure_file(compose)
            self._secure_file(env, secret=True)
        if (config.stat().st_size > 1024 * 1024 or _hash_file(config) != release["runtime_config_sha256"]
                or _hash_file(compose) != release["asset_sha256"]["compose"]):
            raise ReleaseError("release_invalid")
        env_expected = (f"ADEN_API_IMAGE={release['api_image']}" + chr(10) +
                        f"ADEN_RELEASE_CONFIG={root}/runtime.yaml" + chr(10) +
                        f"ADEN_RUNTIME_ENV={self.p.runtime_env}" + chr(10)).encode("ascii")
        if env.read_bytes() != env_expected:
            raise ReleaseError("release_invalid")
        self._validate_web_tree(root / "web")

    def _json_file(self, path: Path, code: str, *, secret: bool = False):
        limit = 8 * 1024 * 1024
        try:
            if self.enforce_ownership:
                self._secure_file(path, secret=secret)
            with path.open("rb") as source:
                raw = source.read(limit + 1)
            if len(raw) > limit:
                raise ReleaseError(code)
            return json.loads(raw, object_pairs_hook=_unique_pairs)
        except (OSError, ValueError, TypeError) as exc:
            raise ReleaseError(code) from exc

    def _write_state(self, value: Mapping[str, Any]) -> None:
        _atomic_json(self.p.state_file, value, 0o600)

    def _write_pending(self, value: Mapping[str, Any]) -> None:
        if self.p.pending_file.exists() or self.p.pending_file.is_symlink():
            raise ReleaseError("recovery_required")
        _atomic_json(self.p.pending_file, value, 0o600)

    def _clear_pending(self) -> None:
        try:
            self.p.pending_file.unlink()
            _fsync_dir(self.p.state_dir)
        except FileNotFoundError:
            pass

    def _record_safely(self, operation: str, result: str, reason: str,
                       payload: Mapping[str, Any] | None = None, release_id: str | None = None) -> bool:
        try:
            self._record(operation, result, reason, payload, release_id)
            return True
        except ReleaseError:
            return False

    def _record(self, operation: str, result: str, reason: str,
                payload: Mapping[str, Any] | None = None, release_id: str | None = None) -> None:
        event: dict[str, Any] = {"at": _now(), "operation": operation, "result": result, "reason": reason}
        if payload:
            event.update({k: payload[k] for k in ("commit_sha", "run_number", "run_attempt") if k in payload})
        if release_id:
            event["release_id"] = release_id
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.p.history_file, flags, 0o600)
            with os.fdopen(fd, "ab") as stream:
                stream.write((json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_dir(self.p.state_dir)
        except OSError as exc:
            raise ReleaseError("history_write_failed") from exc

class _Lock:
    def __init__(self, path: Path, enforce_ownership: bool):
        self.path, self.enforce_ownership, self.fd = path, enforce_ownership, None
    def __enter__(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (self.enforce_ownership and (info.st_uid != 0 or info.st_mode & 0o077)):
            os.close(fd)
            raise ReleaseError("unsafe_lock_permissions")
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.fd = fd
        return self
    def __exit__(self, *_):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def parse_payload(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_INPUT_BYTES:
        raise ReleaseError("input_too_large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseError("invalid_json") from exc
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ReleaseError("invalid_input")
    operation = value.get("operation")
    if operation == "deploy":
        expected = {"schema_version", "operation", "commit_sha", "run_number", "run_attempt", "api_image", "web_image"}
        if set(value) != expected:
            raise ReleaseError("invalid_input_fields")
        if not isinstance(value["commit_sha"], str) or not SHA.fullmatch(value["commit_sha"]):
            raise ReleaseError("invalid_commit_sha")
        for key in ("run_number", "run_attempt"):
            if type(value[key]) is not int or not 1 <= value[key] <= 2_147_483_647:
                raise ReleaseError("invalid_run_sequence")
        value["api_image"] = _image_ref(API_REPO, value["api_image"])
        value["web_image"] = _image_ref(WEB_REPO, value["web_image"])
    elif operation in {"rollback", "status"}:
        if set(value) != {"schema_version", "operation"}:
            raise ReleaseError("invalid_input_fields")
    else:
        raise ReleaseError("invalid_operation")
    return value


def _image_ref(repository: str, value: Any) -> str:
    prefix = repository + "@"
    if not isinstance(value, str) or not value.startswith(prefix) or not DIGEST.fullmatch(value[len(prefix):]):
        raise ReleaseError("invalid_image_ref")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _http_get(url: str, headers: Mapping[str, str]) -> HttpResult:
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT) as response:
            body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ReleaseError("http_response_too_large")
            return HttpResult(response.status, dict(response.headers.items()), body)
    except urllib.error.HTTPError as exc:
        body = exc.read(2 * 1024 * 1024 + 1)
        return HttpResult(exc.code, dict(exc.headers.items()), body[:2 * 1024 * 1024])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReleaseError("external_smoke_failed") from exc


def _header(headers: Mapping[str, str], name: str) -> str:
    return next((str(v) for k, v in headers.items() if k.lower() == name.lower()), "")


def _parse_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ReleaseError("invalid_health_response") from exc
    if not isinstance(value, dict):
        raise ReleaseError("invalid_health_response")
    return value


def _hash_file(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path:
            raise ReleaseError("unsafe_path")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ReleaseError("required_path_missing") from exc


def _write_file(path: Path, content: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise ReleaseError("unsafe_write_target")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode, follow_symlinks=False)


def _atomic_json(path: Path, value: Mapping[str, Any], mode: int) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ReleaseError("unsafe_write_target")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    content = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_file(temp, content, mode)
    os.replace(temp, path)
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for current, _dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in files:
            file = base / name
            if file.is_symlink() or not file.is_file():
                raise ReleaseError("unsafe_release_file")
            fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_dir(base)
    _fsync_dir(root.parent)


def _verify_content_manifest(manifest_path: Path, root: Path, *, allow_links: bool) -> None:
    """Hash each listed file; model symlinks may resolve only inside the approved root."""
    try:
        with manifest_path.open("rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ReleaseError("asset_manifest_too_large")
        manifest = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (OSError, ValueError, TypeError) as exc:
        raise ReleaseError("asset_manifest_invalid") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "files"} or manifest["schema_version"] != 1:
        raise ReleaseError("asset_manifest_invalid")
    files = manifest["files"]
    if not isinstance(files, dict) or not files or len(files) > 50000:
        raise ReleaseError("asset_manifest_invalid")
    root_real = root.resolve(strict=True)
    if root.name == "documents":
        manifest_pdfs = {name for name in files if "/" not in name and name.lower().endswith(".pdf")}
        actual_pdfs = {path.name for path in root.glob("*.pdf")}
        if actual_pdfs != manifest_pdfs:
            raise ReleaseError("unapproved_document_pdf")
    for relative, expected in files.items():
        if (not isinstance(relative, str) or not relative or chr(0) in relative or "\\" in relative
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ReleaseError("asset_manifest_invalid")
        raw_parts = relative.split("/")
        relpath = PurePosixPath(relative)
        if relpath.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
            raise ReleaseError("asset_manifest_invalid")
        candidate = root.joinpath(*relpath.parts)
        current = root
        for part in relpath.parts[:-1]:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ReleaseError("asset_manifest_invalid")
        if candidate.is_symlink() and not allow_links:
            raise ReleaseError("asset_manifest_symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ReleaseError("asset_file_missing") from exc
        if not resolved.is_relative_to(root_real) or not resolved.is_file():
            raise ReleaseError("asset_path_escape")
        if _hash_file(resolved) != expected:
            raise ReleaseError("asset_fingerprint_mismatch")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(error: BaseException) -> str:
    return error.code if isinstance(error, ReleaseError) else "unexpected_failure"


def _read_stdin() -> bytes:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ReleaseError("input_too_large")
    return raw


def main() -> int:
    if len(sys.argv) != 1:
        print(json.dumps({"status": "rejected", "code": "arguments_not_supported"}))
        return 64
    try:
        payload = parse_payload(_read_stdin())
        response = ReleaseManager(Paths.production()).execute(payload)
    except ReleaseError as exc:
        print(json.dumps({"status": "failed", "code": exc.code}, sort_keys=True))
        return 1
    except Exception:
        # Raw exception text may include paths or command output; never expose it.
        print(json.dumps({"status": "failed", "code": "unexpected_failure"}, sort_keys=True))
        return 1
    print(json.dumps(response, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
