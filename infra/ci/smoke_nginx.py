"""Run the production Nginx routing contract against a local fake API."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_CONFIG = ROOT / "infra/production/aden.conf"
LIMITS_CONFIG = ROOT / "infra/production/aden-limits.conf"


class FakeApi(BaseHTTPRequestHandler):
    requests_seen: list[str] = []

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.requests_seen.append(self.path)
        if self.path == "/health/live":
            self._send(200, b'{"status":"ok"}', {"Content-Type": "application/json"})
            return
        if self.path == "/documents/AS001":
            if self.headers.get("Range") != "bytes=1-3":
                self._send(200, b"abcdef", {"Content-Type": "application/pdf"})
                return
            self._send(
                206,
                b"bcd",
                {
                    "Content-Type": "application/pdf",
                    "Content-Range": "bytes 1-3/6",
                    "Accept-Ranges": "bytes",
                },
            )
            return
        self._send(404, b"not found", {"Content-Type": "text/plain"})

    def do_POST(self) -> None:
        self.requests_seen.append(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/query":
            if body.get("question") == "too-many":
                self._send(
                    429,
                    b'{"status":"error"}',
                    {"Content-Type": "application/json", "Retry-After": "10"},
                )
                return
            if body.get("question") == "upstream-timeout":
                self._send(
                    504,
                    b'{"status":"error"}',
                    {"Content-Type": "application/json", "Retry-After": "19"},
                )
                return
            self._send(200, b'{"status":"answered"}', {"Content-Type": "application/json"})
            return
        if self.path == "/query/stream":
            self._send(
                200,
                b"data: {\"type\":\"start\"}\n\n",
                {
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "X-Request-ID": "ci-smoke-request",
                },
            )
            return
        self._send(404, b"not found", {"Content-Type": "text/plain"})


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


def _test_config(directory: Path, *, listen_port: int, upstream_port: int) -> Path:
    source = PRODUCTION_CONFIG.read_text(encoding="utf-8")
    servers = list(re.finditer(r"(?m)^server\s*\{", source))
    if len(servers) != 2:
        raise AssertionError("Expected the HTTP redirect and HTTPS application servers")
    app_server = source[servers[1].start() :]
    app_server = app_server.replace(
        "listen 443 ssl http2;", f"listen 127.0.0.1:{listen_port};"
    ).replace("    listen [::]:443 ssl http2;\n", "")
    app_server = re.sub(r"(?m)^\s*ssl_certificate(?:_key)?\s+[^;]+;\s*$", "", app_server)
    app_server = re.sub(r"(?m)^\s*ssl_protocols\s+[^;]+;\s*$", "", app_server)
    app_server = app_server.replace(
        "/srv/aden/current/web", str(directory / "web")
    )
    app_server = app_server.replace(
        "/var/log/nginx/aden-access.log", str(directory / "access.log")
    ).replace("/var/log/nginx/aden-error.log", str(directory / "error.log"))
    app_server = app_server.replace("127.0.0.1:18010", f"127.0.0.1:{upstream_port}")

    config = directory / "nginx.conf"
    config.write_text(
        "\n".join(
            [
                "worker_processes 1;",
                f"pid {directory / 'nginx.pid'};",
                f"error_log {directory / 'error.log'};",
                "events { worker_connections 64; }",
                "http {",
                f"  include {LIMITS_CONFIG};",
                "  include /etc/nginx/mime.types;",
                "  default_type application/octet-stream;",
                app_server,
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (directory / "web").mkdir()
    (directory / "web/index.html").write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
    return config


def _request(port: int, method: str, path: str, *, body: bytes | None = None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = (response.status, dict(response.getheaders()), response.read())
    connection.close()
    return result


def main() -> None:
    binary = shutil.which("nginx")
    if binary is None:
        raise RuntimeError("Nginx must be installed for the routing smoke test")

    FakeApi.requests_seen = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    listen_port = _free_port()

    try:
        with tempfile.TemporaryDirectory(prefix="aden-nginx-ci-") as temporary:
            directory = Path(temporary)
            config = _test_config(
                directory,
                listen_port=listen_port,
                upstream_port=upstream.server_port,
            )
            prefix = f"{directory}/"
            subprocess.run(
                [binary, "-p", prefix, "-c", str(config), "-t"],
                check=True,
                capture_output=True,
                text=True,
            )
            nginx = subprocess.Popen(
                [binary, "-p", prefix, "-c", str(config), "-g", "daemon off;"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(50):
                    if nginx.poll() is not None:
                        raise RuntimeError("Nginx stopped before accepting requests")
                    try:
                        status, _headers, _body = _request(
                            listen_port, "GET", "/api/health/live"
                        )
                        if status == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Nginx did not start")

                live = _request(listen_port, "GET", "/api/health/live")
                assert live[0] == 200 and b'"status":"ok"' in live[2]

                before_ready = len(FakeApi.requests_seen)
                ready = _request(listen_port, "GET", "/api/health/ready")
                assert ready[0] == 404
                assert len(FakeApi.requests_seen) == before_ready

                nested_ready = _request(listen_port, "GET", "/api/health/ready/detail")
                assert nested_ready[0] == 404

                request_body = json.dumps({"question": "too-many"}).encode()
                limited = _request(
                    listen_port,
                    "POST",
                    "/api/query",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                )
                assert limited[0] == 429
                assert limited[1].get("Retry-After") == "10"

                timed_out = _request(
                    listen_port,
                    "POST",
                    "/api/query",
                    body=json.dumps({"question": "upstream-timeout"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                assert timed_out[0] == 504
                assert timed_out[1].get("Retry-After") == "19"

                streamed = _request(listen_port, "POST", "/api/query/stream", body=b"{}")
                assert streamed[0] == 200
                assert streamed[1].get("Content-Type", "").startswith("text/event-stream")
                assert streamed[1].get("X-Request-ID") == "ci-smoke-request"
                assert b'"type":"start"' in streamed[2]

                ranged = _request(
                    listen_port,
                    "GET",
                    "/api/documents/AS001",
                    headers={"Range": "bytes=1-3"},
                )
                assert ranged[0] == 206
                assert ranged[1].get("Content-Range") == "bytes 1-3/6"
                assert ranged[2] == b"bcd"

                upstream.shutdown()
                upstream.server_close()
                unavailable = _request(listen_port, "GET", "/api/health/live")
                assert unavailable[0] == 503
                assert unavailable[1].get("Retry-After") == "3600"
                assert json.loads(unavailable[2])["status"] == "unavailable"

                print("Nginx API status, SSE, readiness, and PDF Range contracts passed.")
            finally:
                nginx.terminate()
                try:
                    nginx.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    nginx.kill()
                    nginx.wait(timeout=5)
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)


if __name__ == "__main__":
    main()
