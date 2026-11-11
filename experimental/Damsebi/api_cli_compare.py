"""API(/query) 요청의 서버 trace와 평가 CLI 실행 기록을 같은 질문·검색 방식으로 대조한다.

API 응답에는 근거·문맥·모델 입력이 없으므로 request_id로 계산한 trace ID의 Langfuse 관측에서 읽는다.
모델 출력 문장은 실행마다 다를 수 있어 비교하지 않는다(상태·인용 수만 참고로 보인다).

    python -m experimental.Damsebi.api_cli_compare --http <http_runs.jsonl> --cli <runs.jsonl> --out <md>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experimental.Damsebi.check_langfuse import as_obj, fetch

from app.observability.tracing import Tracer

REQUIRED = ("rag-query", "retrieval", "final-context", "generation-attempt-1")


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def compare(http_row: dict, run: dict, observations: list) -> dict:
    by_name = {}
    for observation in observations:
        by_name[observation.name] = observation
    root = by_name.get("rag-query")
    metadata = (root.metadata if root is not None else None) or {}
    retrieval_output = as_obj(by_name["retrieval"].output) if "retrieval" in by_name else {}
    context = as_obj(by_name["final-context"].output) if "final-context" in by_name else {}
    generation = by_name.get("generation-attempt-1")
    api_candidates = []
    for candidate in (retrieval_output or {}).get("candidates") or []:
        api_candidates.append(candidate.get("chunk_id"))
    cli_candidates = []
    for candidate in run.get("retrieved_candidates") or []:
        cli_candidates.append(candidate["chunk_id"])
    api_final = []
    for chunk in (context or {}).get("final_context_chunks") or []:
        api_final.append(chunk.get("chunk_id"))
    cli_final = []
    for chunk in run.get("final_context_chunks") or []:
        cli_final.append(chunk["chunk_id"])
    api_input = as_obj(generation.input) if generation is not None else None
    cli_input = (run.get("attempts") or [{}])[0].get("model_input")
    root_input = as_obj(root.input) if root is not None else {}
    checks = {
        "질문": (root_input or {}).get("question") == run["question"],
        "검색 방식": metadata.get("retrieval_mode") == run["retrieval_mode"],
        "설정 해시(config_hash)": metadata.get("config_hash") == run["config_hash"],
        "근거 후보 순서": api_candidates == cli_candidates and bool(cli_candidates),
        "최종 문맥 청크": api_final == cli_final and bool(cli_final),
        "final_context_hash": (context or {}).get("final_context_hash") == run["final_context_hash"],
        "model_context_hash": (context or {}).get("model_context_hash") == run.get("model_context_hash"),
        "문맥 토큰 수": (context or {}).get("model_context_token_count") == run.get("model_context_token_count"),
        "모델 입력(1차 시도)": api_input is not None and api_input == cli_input,
        "trace 관측": all(name in by_name for name in REQUIRED),
    }
    return {"question_id": http_row["question_id"], "retrieval_mode": http_row["retrieval_mode"],
            "api_request_id": http_row["request_id"], "api_trace_id": http_row["langfuse_trace_id"],
            "cli_request_id": run["request_id"], "cli_trace_id": run.get("langfuse_trace_id"),
            "api_status": (http_row.get("response") or {}).get("status"), "cli_status": run["status"],
            "api_citations": len((http_row.get("response") or {}).get("citations") or []),
            "api_trace_request_id": metadata.get("request_id"), "checks": checks,
            "observations": sorted(by_name)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--http", required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    tracer = Tracer.from_env()
    if not tracer.enabled:
        raise SystemExit("Langfuse 키가 없어 서버 trace를 조회할 수 없다")
    client = tracer._client  # check_langfuse.fetch와 같은 SDK 클라이언트(조회 전용)
    runs = {}
    for run in _jsonl(Path(args.cli)):
        runs[(run["question_id"], run["retrieval_mode"])] = run
    results = []
    for row in _jsonl(Path(args.http)):
        if row["outcome"] != "success":
            continue
        run = runs[(row["question_id"], row["retrieval_mode"])]
        results.append(compare(row, run, fetch(client, row["langfuse_trace_id"], REQUIRED)))
    names = list(results[0]["checks"]) if results else []
    lines = ["| 질문 | API request_id | 서버 trace request_id 일치 | " + " | ".join(names) + " |",
             "|---|---|---|" + "---|" * len(names)]
    for result in results:
        cells = []
        for name in names:
            cells.append("일치" if result["checks"][name] else "**불일치**")
        lines.append(f"| {result['question_id']} {result['retrieval_mode']} | `{result['api_request_id'][:12]}` | "
                     f"{result['api_trace_request_id'] == result['api_request_id']} | " + " | ".join(cells) + " |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with Path(args.out).with_suffix(".jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
