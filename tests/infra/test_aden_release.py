import hashlib
import importlib.util
import json
import os
import re
import stat
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "aden_release_under_test", Path(__file__).parents[2] / "infra/production/aden_release.py"
)
r = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = r
SPEC.loader.exec_module(r)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Clock:
    value = 0.0
    def monotonic(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class Docker:
    def __init__(self, web):
        self.web = web
        self.commands = []
        self.api = None
        self.healthy = True
        self.bad_images = set()

    def run(self, argv, *, timeout=60, cwd=None):
        self.commands.append(list(argv))
        if argv[:2] == ["/usr/bin/docker", "pull"] or argv[:3] == ["/usr/bin/docker", "rm", "--force"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["/usr/bin/docker", "image", "inspect"]:
            image = argv[-1]
            revision = "a" * 40 if image.endswith("1" * 64) or image.endswith("2" * 64) else "b" * 40
            data = {"Os": "linux", "Architecture": "arm64", "RepoDigests": [image],
                    "Config": {"Labels": {"org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.source": r.SOURCE}}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
        if argv[:3] == ["/usr/bin/docker", "compose", "--project-name"]:
            envfile = Path(argv[argv.index("--env-file") + 1])
            command = argv[argv.index("--env-file") + 2:]
            if command[0] == "up":
                self.api = next(x.split("=", 1)[1] for x in envfile.read_text().splitlines()
                                if x.startswith("ADEN_API_IMAGE="))
                self.healthy = self.api not in self.bad_images
            elif command[0] == "ps":
                return subprocess.CompletedProcess(argv, 0, "c" * 12 if self.api else "", "")
            elif command[0] == "rm":
                self.api = None
            else:
                raise AssertionError(command)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["/usr/bin/docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, "healthy" if self.healthy else "unhealthy", "")
        if argv[:3] == ["/usr/bin/docker", "create", "--name"]:
            assert argv[-1] == "/bin/true", "scratch export needs a container command"
            return subprocess.CompletedProcess(argv, 0, "d" * 12, "")
        if argv[:2] == ["/usr/bin/docker", "cp"]:
            dest = Path(argv[-1])
            for item in self.web.iterdir():
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, symlinks=True)
                else:
                    shutil.copy2(item, target, follow_symlinks=False)
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


class Http:
    def __init__(self, docker):
        self.docker = docker
        self.calls = []
        self.expose_ready = False
        self.fail_external = set()

    def get(self, url, headers):
        self.calls.append((url, dict(headers)))
        if "127.0.0.1" in url:
            ok = self.docker.api is not None and self.docker.healthy
            return r.HttpResult(200 if ok else 503, {}, b'{"ready":true}' if ok else b'{"ready":false}')
        fail = self.docker.api in self.fail_external
        if url == r.PUBLIC + "/":
            return r.HttpResult(503 if fail else 200, {"content-type":"text/html"}, b"<html>ADEN</html>")
        if url.endswith("/api/health/live"):
            return r.HttpResult(503 if fail else 200, {}, b'{"status":"ok"}')
        if url.endswith("/api/documents/AS102"):
            return r.HttpResult(206, {"content-type":"application/pdf", "content-range":"bytes 0-7/99"}, b"%PDF-123")
        if url.endswith("/api/health/ready"):
            return r.HttpResult(200 if self.expose_ready else 404, {}, b'{"ready":true}')
        raise AssertionError(url)


def make(tmp_path, enabled=True):
    p = r.Paths.testing(tmp_path)
    for d in (p.root, p.releases, p.state_dir, p.bundle_dir, p.documents_dir,
              p.models_dir, p.runs_dir, p.policy_file.parent):
        d.mkdir(parents=True, exist_ok=True)
    p.compose_file.write_text("services:\n  api:\n    image: pinned\n")
    p.runtime_config.write_text("table_rag:\n  device: cpu\n")
    p.runtime_env.write_text("OPENAI_API_KEY=never-log-this\n")
    p.bundle_manifest.write_text('{"schema_version":1}\n')
    (p.documents_dir / "AS102.pdf").write_bytes(b"%PDF-test")
    (p.documents_dir / "asset-manifest.json").write_text(json.dumps({
        "schema_version":1, "files":{"AS102.pdf":sha(p.documents_dir / "AS102.pdf")}}))
    (p.models_dir / "weights.bin").write_bytes(b"model")
    (p.models_dir / "asset-manifest.json").write_text(json.dumps({
        "schema_version":1, "files":{"weights.bin":sha(p.models_dir / "weights.bin")}}))
    p.policy_file.write_text(json.dumps({"schema_version":1, "deployment_enabled":enabled,
        "approved_sha256":{"compose":sha(p.compose_file), "runtime_config":sha(p.runtime_config),
        "bundle_manifest":sha(p.bundle_manifest), "documents_manifest":sha(p.documents_manifest),
        "models_manifest":sha(p.models_manifest)}}))
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>ADEN</html>")
    docker = Docker(web)
    http = Http(docker)
    clock = Clock()
    manager = r.ReleaseManager(p, runner=docker, http_get=http.get, enforce_ownership=False,
                               monotonic=clock.monotonic, sleep=clock.sleep)
    return p, manager, docker, http


def payload(run, commit, api, web):
    return r.parse_payload(json.dumps({"schema_version":1,"operation":"deploy","commit_sha":commit,
        "run_number":run,"run_attempt":1,"api_image":api,"web_image":web}).encode())


API_A = f"{r.API_REPO}@sha256:{'1'*64}"
WEB_A = f"{r.WEB_REPO}@sha256:{'2'*64}"
API_B = f"{r.API_REPO}@sha256:{'3'*64}"
WEB_B = f"{r.WEB_REPO}@sha256:{'4'*64}"


def test_stdin_contract_rejects_extras_duplicate_keys_tags_and_oversize():
    good = {"schema_version":1,"operation":"deploy","commit_sha":"a"*40,"run_number":1,"run_attempt":1,
            "api_image":API_A,"web_image":WEB_A}
    assert r.parse_payload(json.dumps(good).encode())["operation"] == "deploy"
    for raw, code in [
        (json.dumps({**good,"compose_file":"/tmp/x"}).encode(), "invalid_input_fields"),
        (b'{"schema_version":1,"schema_version":1,"operation":"status"}', "invalid_json"),
        (json.dumps({**good,"api_image":"ghcr.io/other/x:latest"}).encode(), "invalid_image_ref"),
        (json.dumps({**good,"run_number":True}).encode(), "invalid_run_sequence"),
        (b" "*(r.MAX_INPUT_BYTES+1), "input_too_large"),
    ]:
        with pytest.raises(r.ReleaseError, match=code):
            r.parse_payload(raw)


def test_content_manifest_rejects_path_escape_and_symlink(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret")
    (root / "ok").write_text("ok")
    manifest = root / "asset-manifest.json"
    manifest.write_text(json.dumps({"schema_version":1,"files":{"ok":sha(root / "ok")}}))
    r._verify_content_manifest(manifest, root, allow_links=False)
    for name, code in (("../secret","asset_manifest_invalid"),):
        manifest.write_text(json.dumps({"schema_version":1,"files":{name:sha(outside)}}))
        with pytest.raises(r.ReleaseError, match=code):
            r._verify_content_manifest(manifest, root, allow_links=True)
    (root / "escape").symlink_to(outside)
    manifest.write_text(json.dumps({"schema_version":1,"files":{"escape":sha(outside)}}))
    with pytest.raises(r.ReleaseError, match="asset_manifest_symlink"):
        r._verify_content_manifest(manifest, root, allow_links=False)
    with pytest.raises(r.ReleaseError, match="asset_path_escape"):
        r._verify_content_manifest(manifest, root, allow_links=True)


def test_deploy_then_rollback_uses_immutable_compose_and_env_files(tmp_path):
    p, manager, docker, http = make(tmp_path)
    a = manager.execute(payload(10,"a"*40,API_A,WEB_A))
    b = manager.execute(payload(11,"b"*40,API_B,WEB_B))
    result = manager.execute(r.parse_payload(b'{"schema_version":1,"operation":"rollback"}'))
    assert result["status"] == "rolled_back" and result["release_id"] == a["release_id"]
    assert (p.releases / a["release_id"] / "compose.yaml").is_file()
    compose = [cmd for cmd in docker.commands if cmd[:2] == ["/usr/bin/docker","compose"]]
    assert compose and all("--env-file" in cmd for cmd in compose)
    assert any(url.endswith("/api/documents/AS102") and h == {"Range":"bytes=0-7"} for url,h in http.calls)
    state = manager._state()
    assert state["active_release_id"] == a["release_id"]
    assert state["previous_release_id"] == b["release_id"]
    assert state["accepted_sequence"] == [11,1]


def test_stale_deploy_and_modified_pdf_fail_before_docker(tmp_path):
    p, manager, docker, _ = make(tmp_path)
    manager.execute(payload(10,"a"*40,API_A,WEB_A))
    count = len(docker.commands)
    with pytest.raises(r.ReleaseError, match="stale_release"):
        manager.execute(payload(9,"b"*40,API_B,WEB_B))
    assert len(docker.commands) == count
    p2, manager2, docker2, _ = make(tmp_path / "tampered")
    (p2.documents_dir / "AS102.pdf").write_bytes(b"tampered")
    with pytest.raises(r.ReleaseError, match="asset_fingerprint_mismatch"):
        manager2.execute(payload(1,"a"*40,API_A,WEB_A))
    assert not docker2.commands


def test_disabled_and_first_release_failure_leave_no_active_release(tmp_path):
    _, disabled, docker, _ = make(tmp_path / "disabled", enabled=False)
    with pytest.raises(r.ReleaseError, match="deployment_disabled"):
        disabled.execute(payload(1,"a"*40,API_A,WEB_A))
    assert not docker.commands
    p, manager, docker, http = make(tmp_path / "failed")
    http.expose_ready = True
    with pytest.raises(r.ReleaseError, match="readiness_publicly_exposed"):
        manager.execute(payload(1,"a"*40,API_A,WEB_A))
    assert manager._state()["active_release_id"] is None
    assert not p.current.exists() and not p.current.is_symlink()
    assert docker.api is None


def test_failed_upgrade_restores_previous_api_web_and_state(tmp_path):
    p, manager, docker, http = make(tmp_path)
    first = manager.execute(payload(10, "a" * 40, API_A, WEB_A))
    http.fail_external.add(API_B)
    with pytest.raises(r.ReleaseError, match="public_web_smoke_failed"):
        manager.execute(payload(11, "b" * 40, API_B, WEB_B))
    state = manager._state()
    assert state["active_release_id"] == first["release_id"]
    assert p.current.resolve() == p.releases / first["release_id"]
    assert docker.api == API_A
    assert not p.pending_file.exists()


def test_committed_state_finalizes_pending_and_is_never_rolled_back(tmp_path):
    p, manager, _, _ = make(tmp_path)
    first = manager.execute(payload(1, "a" * 40, API_A, WEB_A))
    p.pending_file.write_text(json.dumps({"schema_version": 1, "operation": "deploy",
        "candidate_release_id": first["release_id"], "previous_release_id": None, "created_at": "test"}))
    with pytest.raises(r.ReleaseError, match="no_previous_release"):
        manager.execute(r.parse_payload(b'{"schema_version":1,"operation":"rollback"}'))
    assert manager._state()["active_release_id"] == first["release_id"]
    assert not p.pending_file.exists()
    assert p.current.resolve() == p.releases / first["release_id"]


def test_pending_cleanup_and_audit_write_failure_do_not_undo_committed_release(tmp_path):
    p, manager, _, _ = make(tmp_path / "cleanup")
    clear = manager._clear_pending
    manager._clear_pending = lambda: (_ for _ in ()).throw(OSError("disk"))
    result = manager.execute(payload(1, "a" * 40, API_A, WEB_A))
    assert result["status"] == "deployed" and result["recovery_pending"] is True
    assert manager._state()["active_release_id"] == result["release_id"]
    manager._clear_pending = clear
    with pytest.raises(r.ReleaseError, match="no_previous_release"):
        manager.execute(r.parse_payload(b'{"schema_version":1,"operation":"rollback"}'))
    assert not p.pending_file.exists()

    _, manager2, _, _ = make(tmp_path / "history")
    manager2._record = lambda *args, **kwargs: (_ for _ in ()).throw(r.ReleaseError("history_write_failed"))
    result2 = manager2.execute(payload(1, "a" * 40, API_A, WEB_A))
    assert result2["status"] == "deployed" and result2["audit_recorded"] is False
    assert manager2._state()["active_release_id"] == result2["release_id"]


def test_unapproved_top_level_pdf_blocks_activation(tmp_path):
    p, manager, docker, _ = make(tmp_path)
    (p.documents_dir / "AS999.pdf").write_bytes(b"%PDF-unapproved")
    with pytest.raises(r.ReleaseError, match="unapproved_document_pdf"):
        manager.execute(payload(1, "a" * 40, API_A, WEB_A))
    assert not docker.commands


def test_model_symlink_is_allowed_only_inside_approved_root(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    blob = root / "blobs" / "model.bin"
    blob.parent.mkdir()
    blob.write_bytes(b"model")
    (root / "snapshot.bin").symlink_to(blob)
    manifest = root / "asset-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": {"snapshot.bin": sha(blob)}}))
    r._verify_content_manifest(manifest, root, allow_links=True)
    manifest.write_text(json.dumps({"schema_version": 1, "files": {"snapshot.bin": sha(blob)}}))
    with pytest.raises(r.ReleaseError, match="asset_manifest_symlink"):
        r._verify_content_manifest(manifest, root, allow_links=False)


def test_deploy_and_rollback_shell_contract_is_fixed(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "ssh-capture"
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$CAPTURE_ARGS"\ncat > "$CAPTURE_STDIN"\n')
    fake_ssh.chmod(0o755)
    key, known = tmp_path / "key", tmp_path / "known_hosts"
    key.write_text("dummy-key")
    known.write_text("example ssh-ed25519 dummy")
    payload_file = tmp_path / "release.json"
    release_json = json.dumps({"schema_version":1,"operation":"deploy","commit_sha":"a"*40,
        "run_number":1,"run_attempt":1,"api_image":API_A,"web_image":WEB_A})
    payload_file.write_text(release_json)
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "CAPTURE_ARGS": str(capture) + ".args",
        "CAPTURE_STDIN": str(capture) + ".stdin", "ADEN_DEPLOY_HOST":"129.225.142.54",
        "ADEN_DEPLOY_USER":"aden-deploy", "ADEN_DEPLOY_KEY_FILE":str(key),
        "ADEN_SSH_KNOWN_HOSTS":str(known)}
    deploy = Path(__file__).parents[2] / "infra/deploy.sh"
    result = subprocess.run(["sh", str(deploy), str(payload_file)], env=env, capture_output=True)
    assert result.returncode == 0
    args = Path(str(capture) + ".args").read_text().splitlines()
    assert args[-1:] == ["aden-deploy"]
    assert Path(str(capture) + ".stdin").read_text() == release_json
    rollback = Path(__file__).parents[2] / "infra/rollback.sh"
    result = subprocess.run(["sh", str(rollback)], env=env, capture_output=True)
    assert result.returncode == 0
    args = Path(str(capture) + ".args").read_text().splitlines()
    assert args[-1:] == ["aden-rollback"]
    assert Path(str(capture) + ".stdin").read_text() == json.dumps({"schema_version": 1, "operation": "rollback"}, separators=(",", ":")) + chr(10)
    env["ADEN_DEPLOY_HOST"] = "host;touch"
    result = subprocess.run(["sh", str(deploy), str(payload_file)], env=env, capture_output=True)
    assert result.returncode != 0


def test_compose_update_does_not_change_previous_release_rollback(tmp_path):
    p, manager, docker, _ = make(tmp_path)
    first = manager.execute(payload(10, "a" * 40, API_A, WEB_A))
    old_compose = (p.releases / first["release_id"] / "compose.yaml").read_bytes()
    p.compose_file.write_text("services:\n  api:\n    image: newer-approved-compose\n")
    policy = json.loads(p.policy_file.read_text())
    policy["approved_sha256"]["compose"] = sha(p.compose_file)
    p.policy_file.write_text(json.dumps(policy))
    manager.execute(payload(11, "b" * 40, API_B, WEB_B))
    manager.execute(r.parse_payload(b'{"schema_version":1,"operation":"rollback"}'))
    assert (p.releases / first["release_id"] / "compose.yaml").read_bytes() == old_compose
    up_calls = [cmd for cmd in docker.commands if cmd[:2] == ["/usr/bin/docker", "compose"] and "up" in cmd]
    assert Path(up_calls[-1][up_calls[-1].index("--file") + 1]) == p.releases / first["release_id"] / "compose.yaml"


def test_disk_floor_and_command_errors_fail_closed(tmp_path, monkeypatch):
    _, manager, docker, _ = make(tmp_path)
    monkeypatch.setattr(r.shutil, "disk_usage", lambda _: type("Usage", (), {"free": r.MIN_FREE_BYTES - 1})())
    with pytest.raises(r.ReleaseError, match="insufficient_disk"):
        manager.execute(payload(1, "a" * 40, API_A, WEB_A))
    assert not docker.commands
    monkeypatch.setattr(r.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "never-log-this"))
    with pytest.raises(r.ReleaseError, match="command_failed") as error:
        r.CommandRunner().run(["/usr/bin/docker", "pull", API_A])
    assert "never-log-this" not in str(error.value)


def test_server_gate_rejects_shell_suffix_and_client_pins_known_hosts(tmp_path):
    gate = Path(__file__).parents[2] / "infra/production/aden-deploy-gate"
    result = subprocess.run(["sh", str(gate)], env={"SSH_ORIGINAL_COMMAND": "aden-deploy; id"}, capture_output=True)
    assert result.returncode == 64
    wrapper = Path(__file__).parents[2] / "infra/production/aden-release-wrapper"
    result = subprocess.run(["sh", str(wrapper), "status"], capture_output=True)
    assert result.returncode == 64
    shell_test = Path(__file__).parents[0] / "test_aden_release.py"
    assert shell_test.is_file()
    assert "GlobalKnownHostsFile=/dev/null" in (Path(__file__).parents[2] / "infra/deploy.sh").read_text()
    assert "GlobalKnownHostsFile=/dev/null" in (Path(__file__).parents[2] / "infra/rollback.sh").read_text()


def test_file_lock_serializes_two_callers(tmp_path):
    import threading

    lock = tmp_path / "release.lock"
    started = threading.Event()
    entered = threading.Event()
    with r._Lock(lock, enforce_ownership=False):
        def acquire():
            started.set()
            with r._Lock(lock, enforce_ownership=False):
                entered.set()
        worker = threading.Thread(target=acquire)
        worker.start()
        assert started.wait(1)
        assert not entered.wait(0.05)
    assert entered.wait(1)
    worker.join()

def test_installed_permission_contract_accepts_private_state_and_public_release(tmp_path, monkeypatch):
    paths, manager, _, _ = make(tmp_path)
    paths.state_dir.chmod(0o700)
    paths.policy_file.chmod(0o600)
    paths.runtime_env.chmod(0o600)
    paths.runs_dir.chmod(0o750)

    # Keep real mode bits while simulating the fixed server accounts.
    original_lstat = Path.lstat
    def server_lstat(path):
        result = original_lstat(path)
        if not path.is_relative_to(tmp_path):
            return result
        fields = list(result)
        fields[stat.ST_UID] = 994 if path == paths.runs_dir else 0
        fields[stat.ST_GID] = 985 if path == paths.runs_dir else 0
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", server_lstat)
    manager.enforce_ownership = True
    manager._ensure_dirs()
    assert manager._status()["deployment_enabled"] is True
    result = manager._deploy(payload(1, "a" * 40, API_A, WEB_A))
    release = paths.releases / result["release_id"]
    assert stat.S_IMODE(release.stat().st_mode) == 0o755
    assert stat.S_IMODE(paths.state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((release / "release.env").stat().st_mode) == 0o600
    assert manager._status()["active_release_id"] == result["release_id"]
    paths.state_dir.chmod(0o755)
    with pytest.raises(r.ReleaseError, match="unsafe_permissions"):
        manager._ensure_dirs()


def test_failed_release_reuses_cached_previous_api_during_registry_outage(tmp_path):
    paths, manager, docker, http = make(tmp_path)
    result = manager.execute(payload(1, "a" * 40, API_A, WEB_A))
    original_run = docker.run

    def run(argv, **kwargs):
        if argv == ["/usr/bin/docker", "pull", API_A]:
            raise r.ReleaseError("registry_unavailable")
        return original_run(argv, **kwargs)

    docker.run = run
    http.fail_external.add(API_B)
    with pytest.raises(r.ReleaseError, match="public_web_smoke_failed"):
        manager.execute(payload(2, "b" * 40, API_B, WEB_B))
    assert docker.api == API_A
    assert manager._state()["active_release_id"] == result["release_id"]
    assert not paths.pending_file.exists()
    starts = [command for command in docker.commands if "--force-recreate" in command]
    assert all(command[command.index("--pull") + 1] == "never" for command in starts)


def test_release_schema_is_json_and_matches_a_digest_release():
    schema = json.loads((Path(__file__).parents[2] / "infra/release.schema.json").read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    deploy = schema["oneOf"][0]
    value = payload(1, "a" * 40, API_A, WEB_A)
    assert set(deploy["required"]) == set(value)
    for name in ("api_image", "web_image", "commit_sha"):
        assert re.fullmatch(deploy["properties"][name]["pattern"], value[name])
    value["schema_version"] = True
    with pytest.raises(r.ReleaseError, match="invalid_input"):
        r.parse_payload(json.dumps(value).encode())
