"""기존 FastAPI `/query`를 HTTP로 호출해 응답과 클라이언트 측 응답 시간을 기록한다(C 평가 클라이언트).

- 시간(`latency_scope=http_client`): 요청을 보내기 직전부터 응답 본문을 다 받을 때까지. 서버 기동,
  Judge·RAGAS 채점, trace 조회 시간은 넣지 않는다. 평가 어댑터의 `latency_ms`(`service`)와 다른 값이다.
- 결과: success(200·응답 해석) / http_error(4xx·5xx, 본문의 오류 사유 기록) / timeout / connection_error /
  invalid_response(200인데 응답을 해석하지 못함).
- 응답의 request_id(없으면 `X-Request-ID` 헤더)로 Langfuse trace ID를 계산해 서버 기록과 연결한다.
  API 응답에 없는 검색 후보·최종 문맥·모델 입력으로 RunRecord를 만들지 않는다. 필요하면 trace를 조회한다.

    python -m app.evaluation.scripts.http_benchmark --base-url http://127.0.0.1:8000 \
        --questions <질문 파일> --ids M19 --modes hybrid_rerank --experiment-id <ID> [--verify-langfuse]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.evaluation.dataset import load_questions
from app.evaluation.report import write_json, write_jsonl
from app.evaluation.scripts._common import ROOT, git_info, guard_overwrite, load_env, now_id, sha256_file


def _read(request: urllib.request.Request, timeout_s: float) -> tuple[int | None, Any, bytes, str | None, str | None]:
    """(HTTP 상태, 헤더, 본문, outcome, 오류). 4xx·5xx도 본문을 끝까지 읽는다."""
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.headers, response.read(), None, None
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(), "http_error", f"HTTP {error.code}"
    except TimeoutError as error:
        return None, None, b"", "timeout", type(error).__name__
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            return None, None, b"", "timeout", type(error.reason).__name__
        return None, None, b"", "connection_error", f"{type(error.reason).__name__}: {error.reason}"


def post_query(base_url: str, question: str, mode: str | None, timeout_s: float) -> dict[str, Any]:
    payload = {"question": question}
    if mode:
        payload["retrieval_mode"] = mode
    request = urllib.request.Request(f"{base_url.rstrip('/')}/query", method="POST",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    status, headers, body, outcome, error = _read(request, timeout_s)
    latency_ms = round((time.perf_counter() - started) * 1000)
    response = None
    if body:
        try:
            response = json.loads(body)
        except ValueError:
            response = None
    if outcome is None:
        if isinstance(response, dict) and response.get("request_id"):
            outcome = "success"
        else:
            outcome, error = "invalid_response", "응답 본문을 QueryResponse로 해석하지 못함"
    elif outcome == "http_error" and isinstance(response, dict) and response.get("reason"):
        error = f"HTTP {status}: {response['reason']}"
    request_id = response.get("request_id") if isinstance(response, dict) else None
    header_id = headers.get("X-Request-ID") if headers is not None else None
    return {"http_status": status, "outcome": outcome, "error": error, "latency_ms": latency_ms,
            "latency_scope": "http_client", "request_id": request_id or header_id,
            "x_request_id": header_id, "response_bytes": len(body),
            "response": response if isinstance(response, dict) else None}


def trace_id_for(request_id: str | None) -> str | None:
    """서버와 같은 방식(`create_trace_id(seed=request_id)`)으로 trace ID를 계산한다."""
    if not request_id:
        return None
    from langfuse import Langfuse  # 지연 import: SDK 초기화 없이 정적 메서드만 쓴다

    return Langfuse.create_trace_id(seed=request_id)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--questions", required=True, help="질문 파일(id·question만 사용)")
    p.add_argument("--ids", help="질문 ID, 쉼표 구분(기본: 전체)")
    p.add_argument("--modes", default="", help="검색 방식, 쉼표 구분(비우면 서버 기본 방식)")
    p.add_argument("--timeout", type=float, default=120.0, help="요청당 클라이언트 제한 시간(초)")
    p.add_argument("--condition", default="", help="실행 조건 설명(서버 설정·근거 공급 등). 기록에만 남긴다")
    p.add_argument("--experiment-id")
    p.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    p.add_argument("--verify-langfuse", action="store_true", help="응답 뒤 trace 저장을 조회(시간 측정과 별도)")
    p.add_argument("--langfuse-verify-timeout", type=float, default=90.0)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ids = None
    if args.ids:
        ids = []
        for requested_id in args.ids.split(","):
            ids.append(requested_id.strip())
    questions = load_questions(args.questions, ids)
    modes = []
    for mode in args.modes.split(","):
        if mode.strip():
            modes.append(mode.strip())
    experiment_id = args.experiment_id or now_id("http")
    out = Path(args.artifacts_dir) / experiment_id
    guard_overwrite(out / "http_runs.jsonl", args.overwrite)
    health = post_health(args.base_url, args.timeout)
    rows = []
    for question in questions:
        for mode in modes or [None]:
            row = {"experiment_id": experiment_id, "question_id": question["id"], "retrieval_mode": mode,
                   "condition": args.condition, **post_query(args.base_url, question["question"], mode, args.timeout)}
            row["langfuse_trace_id"] = trace_id_for(row["request_id"])
            rows.append(row)
            print(row["question_id"], mode, row["outcome"], row["http_status"], f"{row['latency_ms']} ms")
    manifest = {"experiment_id": experiment_id, "kind": "http_client", "base_url": args.base_url,
                "health": health, "client_timeout_s": args.timeout, "condition": args.condition,
                "questions_file": Path(args.questions).name, "questions_sha256": sha256_file(Path(args.questions)),
                "question_ids": [q["id"] for q in questions], "modes": modes or ["(서버 기본)"], **git_info(),
                "latency_scope": "http_client: 요청 직전~응답 본문 수신 완료. 채점·trace 조회 제외"}
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "http_runs.jsonl", rows)  # trace 조회 전에 저장한다
    write_json(out / "manifest.json", manifest)
    if args.verify_langfuse:
        load_env()
        from app.observability.tracing import Tracer

        tracer = Tracer.from_env()
        deadline = time.monotonic() + args.langfuse_verify_timeout
        for row in rows:
            row["langfuse_verification"] = tracer.verify_trace(
                row["langfuse_trace_id"], timeout_s=max(0.0, deadline - time.monotonic()))
        write_jsonl(out / "http_runs.jsonl", rows)
    summary = {}
    for row in rows:
        group = summary.setdefault(row["retrieval_mode"] or "(서버 기본)", {"requests": 0, "outcomes": {}, "ms": []})
        group["requests"] += 1
        group["outcomes"][row["outcome"]] = group["outcomes"].get(row["outcome"], 0) + 1
        if row["outcome"] == "success":
            group["ms"].append(row["latency_ms"])
    for group in summary.values():
        latencies = group.pop("ms")
        group["success_latency_ms"] = {"mean": statistics.fmean(latencies) if latencies else None,
                                       "median": statistics.median(latencies) if latencies else None,
                                       "max": max(latencies) if latencies else None}
    manifest["summary"] = summary
    write_json(out / "manifest.json", manifest)
    print(json.dumps(summary, ensure_ascii=False))
    print(f"HTTP 기록: {out}")


def post_health(base_url: str, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/health")
    started = time.perf_counter()
    status, _, body, outcome, error = _read(request, timeout_s)
    result = {"http_status": status, "outcome": outcome or "success", "error": error,
              "latency_ms": round((time.perf_counter() - started) * 1000)}
    try:
        result["body"] = json.loads(body) if body else None
    except ValueError:
        result["body"] = None
    return result


if __name__ == "__main__":
    main()
