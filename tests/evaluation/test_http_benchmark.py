import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.evaluation.scripts.http_benchmark import post_query, trace_id_for


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if payload["question"] == "slow":
            time.sleep(0.5)
        if payload["question"] == "fail":
            status, body = 502, {"request_id": "rid-fail", "status": "error", "answer": "",
                                 "reason": "모델 호출 실패", "citations": []}
        elif payload["question"] == "broken":
            status, body = 200, "not json"
        else:
            status, body = 200, {"request_id": "rid-ok", "status": "answered", "answer": "a",
                                 "reason": "r", "citations": [], "mode": payload.get("retrieval_mode")}
        data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-ID", "rid-header")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_success_keeps_response_and_request_id(base_url):
    row = post_query(base_url, "q", "naive", timeout_s=5)
    assert row["outcome"] == "success" and row["http_status"] == 200
    assert row["request_id"] == "rid-ok" and row["x_request_id"] == "rid-header"
    assert row["response"]["mode"] == "naive" and row["latency_scope"] == "http_client"


def test_http_error_records_status_and_server_reason(base_url):
    row = post_query(base_url, "fail", None, timeout_s=5)
    assert row["outcome"] == "http_error" and row["http_status"] == 502
    assert row["error"] == "HTTP 502: 모델 호출 실패" and row["request_id"] == "rid-fail"


def test_timeout_and_unreadable_body_are_not_success(base_url):
    slow = post_query(base_url, "slow", None, timeout_s=0.1)
    assert slow["outcome"] == "timeout" and slow["http_status"] is None and slow["request_id"] is None
    broken = post_query(base_url, "broken", None, timeout_s=5)
    assert broken["outcome"] == "invalid_response" and broken["request_id"] == "rid-header"


def test_latency_covers_until_body_is_read(base_url):
    row = post_query(base_url, "slow", None, timeout_s=5)
    assert row["outcome"] == "success" and row["latency_ms"] >= 500


def test_connection_error_and_trace_id():
    row = post_query("http://127.0.0.1:9", "q", None, timeout_s=1)  # 열려 있지 않은 포트
    assert row["outcome"] in ("connection_error", "timeout") and row["request_id"] is None
    assert trace_id_for(None) is None
    assert trace_id_for("abc") == trace_id_for("abc") and len(trace_id_for("abc")) == 32
