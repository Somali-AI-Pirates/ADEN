"""HTTP 측정(클라이언트)·서버 미들웨어 로그·평가 CLI(service) 시간을 request_id·질문별로 나란히 정리한다.

    python -m experimental.Damsebi.http_latency_table --http <ID> [<ID> ...] --server-log <로그> --cli <실험 ID> --out <md>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_LINE = re.compile(r"request_id=(\w+) method=POST path=/query status_code=(\d+) latency_ms=(\d+)")


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", nargs="+", required=True)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    server = {}
    for line in Path(args.server_log).read_text(encoding="utf-8", errors="replace").splitlines():
        match = SERVER_LINE.search(line)
        if match:
            server[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    cli = {}
    for run in _jsonl(ROOT / "artifacts" / "experiments" / args.cli / "runs.jsonl"):
        cli[run["question_id"]] = run
    lines = ["| 실행 | 질문 | 결과 | HTTP | 클라이언트(ms) | 서버 미들웨어(ms) | 차이(ms) | trace 저장 |",
             "|---|---|---|---|---:|---:|---:|---|"]
    by_question = {}
    for experiment in args.http:
        for row in _jsonl(ROOT / "artifacts" / "experiments" / experiment / "http_runs.jsonl"):
            status, server_ms = server.get(row["request_id"], (None, None))
            gap = row["latency_ms"] - server_ms if server_ms is not None else None
            verification = (row.get("langfuse_verification") or {}).get("status")
            lines.append(f"| {experiment} | {row['question_id']} | {row['outcome']} | {row['http_status']} | "
                         f"{row['latency_ms']:,} | {server_ms if server_ms is not None else '-'} | "
                         f"{gap if gap is not None else '-'} | {verification} |")
            if row["outcome"] == "success":
                by_question.setdefault(row["question_id"], []).append((row["latency_ms"], server_ms))
    lines += ["", "| 질문 | HTTP 성공 | 클라이언트 중앙값(ms) | 서버 중앙값(ms) | 평가 CLI service(ms, 1회) |",
              "|---|---:|---:|---:|---:|"]
    for question_id, values in by_question.items():
        client_ms = [v[0] for v in values]
        server_ms = [v[1] for v in values if v[1] is not None]
        lines.append(f"| {question_id} | {len(values)} | {statistics.median(client_ms):,.0f} | "
                     f"{statistics.median(server_ms):,.0f} | {cli[question_id]['latency_ms']:,} |" if server_ms else
                     f"| {question_id} | {len(values)} | {statistics.median(client_ms):,.0f} | - | "
                     f"{cli[question_id]['latency_ms']:,} |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
