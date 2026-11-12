"""HTTP 평가 기록(`http_benchmark`)을 서버 trace와 이어 채점할 수 있는 실행 기록(runs.jsonl)으로 만든다.

API 응답에는 검색 후보·최종 문맥·모델 입력이 없다. 같은 request_id의 서버 trace에서 읽어 RunRecord를 만든다. TableRAG는 planner·answer 호출과 최종 문맥 JSON에 포함된 SQL 결과도 복원한다.
- 답변을 새로 생성하지 않는다. 답변·상태·reason·인용·질문·검색 방식·request_id가 HTTP 응답과 trace에서
  같을 때만 연결한다(오류 응답은 API 오류 처리기가 문구를 정하므로 상태만 대조한다).
- request_id가 없는 요청(timeout 등)과 trace를 읽지 못했거나 응답과 다른 요청은 runs.jsonl에 넣지 않고
  manifest의 `http_records.unlinked`에 사유를 남긴다. 원본 HTTP 폴더는 수정하지 않는다.
- 결과 폴더를 `regrade --source`로 채점한다. scores.jsonl과 게시 점수도 같은 request_id·trace로 이어진다.

    python -m app.evaluation.scripts.http_records --source <HTTP 실험 ID> --questions <질문 파일> \
        --config configs/advanced.yaml
    python -m app.evaluation.scripts.regrade --source <HTTP 실험 ID>-records --references <정답 파일> \
        --judge-model <모델> --ragas-model <모델>
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.evaluation.dataset import load_questions
from app.evaluation.report import write_json, write_jsonl
from app.evaluation.scripts._common import ROOT, base_manifest, guard_overwrite, load_env, sha256_file
from app.observability.records import SUPPORTED_RETRIEVAL_MODES, normalize_run_record
from app.observability.tracing import ROOT_NAME, Tracer
from shared.settings import load_settings


def _obj(value: Any) -> Any:
    """Langfuse API는 관측 입출력을 JSON 문자열로 돌려준다."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _attempts(by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    named_attempts = []
    pattern = re.compile(r"^(?:(planner|answer)|generation-attempt)-(\d+)$")
    for name, observation in by_name.items():
        match = pattern.fullmatch(name)
        if match:
            named_role, number = match.groups()
            named_attempts.append((int(number), named_role, observation))

    attempts = []
    for attempt_number, (trace_attempt, named_role, observation) in enumerate(
        sorted(named_attempts, key=lambda item: item[0]), start=1
    ):
        metadata = observation.get("metadata") or {}
        usage = observation.get("usageDetails") or {}
        level = getattr(observation.get("level"), "value", observation.get("level"))
        role = metadata.get("role") or named_role
        raw_input = _obj(observation.get("input"))
        if isinstance(raw_input, dict) and "request" in raw_input:
            model_input = raw_input.get("request")
            tool_schema = raw_input.get("tool_schema")
        else:
            model_input = raw_input
            tool_schema = None
        tokens = {}
        usage_known = metadata.get("usage_known")
        for side in ("input", "output"):  # 서버가 사용량을 모른다고 기록한 값은 null로 둔다
            if isinstance(usage_known, bool):
                known = usage_known
            elif isinstance(usage_known, Mapping):
                known = metadata.get(f"usage_known.{side}", usage_known.get(side))
            else:
                known = metadata.get(f"usage_known.{side}")
            tokens[side] = usage.get(side) if known is not False else None
        error = None
        if level == "ERROR":
            error = observation.get("statusMessage") or "error"
        attempt = {
            "attempt": attempt_number, "model": observation.get("model"),
            "model_input": model_input, "model_output": _obj(observation.get("output")),
            "input_tokens": tokens["input"], "output_tokens": tokens["output"], "error": error,
            "role": role,
            "prompt_variant": metadata.get("prompt_variant"),
            "model_parameters": observation.get("modelParameters") or None,
            "latency_ms": metadata.get("latency_ms"),
            "calendar_reference": metadata.get("calendar_reference") or "",
        }
        if tool_schema is not None:
            attempt["tool_schema"] = tool_schema
        if trace_attempt != attempt_number:
            attempt["trace_attempt"] = trace_attempt
        attempts.append(attempt)
    return attempts


def _included_sql_results(context: dict[str, Any]) -> list[dict[str, Any]]:
    """실제 모델 입력에 포함된 SQL 결과를 최종 문맥 JSON에서 복원한다."""
    direct = context.get("included_sql_results")
    if isinstance(direct, list):
        return direct

    payload = _obj(context.get("model_context_text"))
    if isinstance(payload, dict):
        sql = payload.get("sql")
        if isinstance(sql, list) and all(isinstance(item, dict) for item in sql):
            return sql
    return []


def run_record(row: dict[str, Any], question: str, observations: list[Any], *,
               experiment_id: str) -> dict[str, Any]:
    """HTTP 기록 한 줄과 그 요청의 trace 관측으로 RunRecord를 만든다. 서로 맞지 않으면 ValueError."""
    by_name = {}
    for observation in observations:
        by_name[observation.name] = observation.dict()
    root = by_name.get(ROOT_NAME)
    if root is None:
        raise ValueError(f"trace에 {ROOT_NAME} 관측 없음")
    metadata = root.get("metadata") or {}
    query = _obj(root.get("input")) or {}
    final = _obj(root.get("output")) or {}
    response = row.get("response") or {}
    checks = [("request_id", metadata.get("request_id"), row["request_id"]),
              ("질문", query.get("question"), question),
              ("상태", final.get("status"), response.get("status"))]
    if row.get("retrieval_mode"):
        checks.append(("검색 방식", metadata.get("retrieval_mode"), row["retrieval_mode"]))
    if final.get("status") != "error":
        for key in ("answer", "reason", "citations"):
            checks.append((key, final.get(key), response.get(key)))
    for label, server, http in checks:
        if server != http:
            raise ValueError(f"{label}가 HTTP 응답과 서버 trace에서 다름")
    retrieval = by_name.get("retrieval") or {}
    context_observation = by_name.get("final-context") or {}
    context = _obj(context_observation.get("output")) or {}
    chunks = context.get("final_context_chunks")
    record = {
        "experiment_id": experiment_id, "question_id": row["question_id"], "request_id": row["request_id"],
        "dataset_id": metadata.get("dataset_id"), "config_hash": metadata.get("config_hash"),
        "prompt_hash": metadata.get("prompt_hash"), "question": question,
        "retrieval_mode": metadata.get("retrieval_mode"),
        "retrieved_candidates": (_obj(retrieval.get("output")) or {}).get("candidates"),
        "provided_context": (retrieval.get("metadata") or {}).get("provided_context"),
        "final_context_chunks": chunks, "final_context_hash": context.get("final_context_hash"),
        "model_context_text": context.get("model_context_text"),
        "model_context_hash": context.get("model_context_hash"),
        "model_context_token_count": context.get("model_context_token_count"),
        "final_context_chunk_count": len(chunks) if chunks is not None else None,
        "included_sql_results": _included_sql_results(context),
        "budget_excluded_chunk_ids": context.get("budget_excluded_chunk_ids"),
        "k": (context_observation.get("metadata") or {}).get("k"),
        "status": final["status"], "answer": final.get("answer"), "reason": final.get("reason"),
        "citations": final.get("citations") or [],
        "latency_ms": metadata.get("latency_ms"), "latency_scope": "service",
        "attempts": _attempts(by_name), "langfuse_trace_id": row["langfuse_trace_id"],
        "error_type": metadata.get("error_type"),
        "http_experiment_id": row["experiment_id"], "http_status": row["http_status"],
        "http_outcome": row["outcome"], "http_latency_ms": row["latency_ms"], "http_latency_scope": "http_client",
    }
    return normalize_run_record(record)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--source", required=True, help="http_benchmark 실험 ID(--artifacts-dir 아래 폴더)")
    p.add_argument("--questions", required=True, help="HTTP 요청에 쓴 질문 파일(해시를 원본 기록과 대조)")
    p.add_argument("--config", required=True,
                   help="서버가 쓴 설정 파일(ADEN_CONFIG). 채점 k와 manifest의 설정 해시에 쓴다")
    p.add_argument("--experiment-id", help="기본: <원본 ID>-records")
    p.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    p.add_argument("--wait", type=float, default=60.0, help="trace 관측 수집 지연을 기다릴 전체 시간(초)")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    src = Path(args.artifacts_dir) / args.source
    http_manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    if http_manifest.get("kind") != "http_client":
        raise SystemExit(f"{args.source}: http_benchmark 기록이 아니다")
    if sha256_file(Path(args.questions)) != http_manifest.get("questions_sha256"):
        raise SystemExit("--questions 파일이 HTTP 요청 때의 질문 파일과 다르다(해시 불일치)")
    experiment_id = args.experiment_id or f"{args.source}-records"
    if experiment_id == args.source:
        raise SystemExit("원본 HTTP 실험 ID에는 저장하지 않는다")
    out = Path(args.artifacts_dir) / experiment_id
    guard_overwrite(out / "runs.jsonl", args.overwrite)
    config_path = (ROOT / args.config).resolve()
    settings = load_settings(config_path)
    k = settings.retrieval.k
    questions = {}
    for question in load_questions(args.questions):
        questions[question["id"]] = question["question"].strip()  # 서비스 요청 모델과 같은 정규화
    rows = []
    for line in (src / "http_runs.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    load_env()
    tracer = Tracer.from_env()
    if not tracer.enabled:
        raise SystemExit("Langfuse 키가 없어 서버 trace를 조회할 수 없다")
    records, unlinked = [], []
    deadline = time.monotonic() + args.wait
    for row in rows:
        where = {"question_id": row["question_id"], "retrieval_mode": row["retrieval_mode"],
                 "request_id": row.get("request_id"), "outcome": row["outcome"]}
        if not row.get("request_id") or not row.get("langfuse_trace_id"):
            unlinked.append({**where, "reason": "request_id 없음(서버 요청과 연결 불가)"})
            continue
        while True:  # 수집 지연이면 다시 조회한다. 응답과 trace가 다르면 기다려도 같으므로 한도까지만
            try:
                records.append(run_record(row, questions[row["question_id"]],
                                          tracer.observations(row["langfuse_trace_id"]),
                                          experiment_id=experiment_id))
                break
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                if time.monotonic() >= deadline:
                    unlinked.append({**where, "reason": reason})
                    break
                time.sleep(5)
    tracer.shutdown()

    for record in records:
        if record["k"] is not None and record["k"] != k:
            raise SystemExit(f"서버 trace의 k {record['k']}가 --config의 retrieval.k {k}와 다르다")
    datasets = sorted({record["dataset_id"] for record in records})
    server_configs = sorted({record["config_hash"] for record in records})
    if len(datasets) > 1 or len(server_configs) > 1:
        raise SystemExit("한 HTTP 실험에 서로 다른 서버 설정의 요청이 섞였다. 조건별로 실험을 나눈다")
    modes = []
    for mode in SUPPORTED_RETRIEVAL_MODES:
        if any(record["retrieval_mode"] == mode for record in records):
            modes.append(mode)
    manifest = base_manifest(experiment_id=experiment_id,
                             dataset_id=datasets[0] if datasets else settings.dataset,
                             config_hash=sha256_file(config_path), sample=False)
    manifest["benchmark"] = {
        "k": k, "modes": modes, "question_ids": http_manifest["question_ids"],
        "selection_reason": f"HTTP 요청 {args.source}의 응답과 서버 기록",
        "questions_file": http_manifest.get("questions_file"), "judge": None, "faithfulness": None,
        "judge_prompt": None, "judge_schema": None,
        "langfuse_verification": dict(Counter(str((row.get("langfuse_verification") or {}).get("status"))
                                              for row in rows)),
    }
    manifest["http_records"] = {
        "source_experiment_id": args.source, "source_git_commit": http_manifest.get("git_commit"),
        "source_git_dirty": http_manifest.get("git_dirty"), "base_url": http_manifest.get("base_url"),
        "condition": http_manifest.get("condition"), "requests": len(rows), "linked": len(records),
        "unlinked": unlinked, "server_config_hashes": server_configs,
        "server_prompt_hashes": sorted({str(record.get("prompt_hash")) for record in records}),
        "note": "답변·상태·인용은 HTTP 응답, 후보·최종 문맥·생성 시도는 같은 request_id의 서버 trace. 새로 생성하지 않음",
    }
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "runs.jsonl", records)
    write_json(out / "manifest.json", manifest)
    print(f"연결 {len(records)}/{len(rows)}건, 미연결 {len(unlinked)}건 → {out}")
    for item in unlinked:
        print("  미연결:", json.dumps(item, ensure_ascii=False))
    if not records:
        raise SystemExit("연결된 기록이 없다. trace 저장(--verify-langfuse)과 미연결 사유를 확인한다")


if __name__ == "__main__":
    main()
