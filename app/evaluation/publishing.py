"""Explicit score publication, separate from generation and local evaluation."""
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

GROUPS = ("retrieval", "faithfulness", "judge", "evidence")  # 지표군 = 지표 이름의 첫 마디


def expected_observations(run):
    names = ["rag-query"]
    if run.get("retrieved_candidates") is not None:
        names.append("retrieval")
    if run.get("final_context_chunks") is not None:
        names.append("final-context")
    attempts = []
    for attempt in run.get("attempts", []):
        role = attempt.get("role")
        if role in {"planner", "answer"}:
            attempts.append(f"{role}-{attempt['attempt']}")
        else:
            attempts.append(f"generation-attempt-{attempt['attempt']}")
    return names + attempts


def expected_payload_observations(run):
    """Observations expected to contain the actual input and output payloads."""
    names = ["rag-query"]
    if run.get("retrieved_candidates") is not None:
        names.append("retrieval")
    if run.get("final_context_chunks") is not None:
        names.append("final-context")
    for attempt in run.get("attempts", []):
        if attempt.get("error") is None and attempt.get("model_output") is not None:
            role = attempt.get("role")
            if role in {"planner", "answer"}:
                names.append(f"{role}-{attempt['attempt']}")
            else:
                names.append(f"generation-attempt-{attempt['attempt']}")
    return names


def score_items(row, groups=GROUPS):
    """채점 기록 한 건에서 지표 이름 → 점수 항목(고른 지표군만). 게시와 수신 대조가 같은 이름을 쓴다."""
    k = row["retrieval"]["k"]
    items = {f"retrieval.hit@{k}": row["retrieval"]["hit_at_k"],
             f"retrieval.recall@{k}": row["retrieval"]["recall_at_k"],
             "faithfulness": row["faithfulness"],
             **{f"judge.{key}": value for key, value in row["judge"].items()},
             **{f"evidence.{key}": value for key, value in row.get("evidence_diagnostics", {}).items()}}
    items.update({
        f"evidence.table_rag.{key}": value
        for key, value in row.get("table_rag_metrics", {}).items()
    })
    selected = {}
    for name, item in items.items():
        if name.split(".")[0] in groups:
            selected[name] = item
    return selected


def conditions(manifest):
    """한 평가 세트가 공유해야 하는 채점 조건. 실험 ID와 원본 실험은 세트 안에서 달라도 된다."""
    bench = manifest.get("benchmark") or {}
    result = {"dataset_id": manifest.get("dataset_id"), "config_hash": manifest["config_hash"],
              "sample": bool(manifest.get("sample")),
              **{key: bench.get(key) for key in
                 ("k", "judge", "faithfulness", "judge_prompt", "judge_schema", "references_sha256")}}
    http_records = manifest.get("http_records")
    if isinstance(http_records, dict):
        result["server_config_hashes"] = sorted(set(http_records.get("server_config_hashes") or []))
    return result


def plan_publication(
    manifest,
    *,
    evaluation_set=None,
    groups=None,
    artifacts_dir=None,
    request_ids=(),
    skip_same_experiment=True,
    exclude_manifest=None,
):
    """Validate a score namespace against locally saved publication plans.

    Local manifests and scores are checked for matching conditions and duplicate request IDs.
    Other workspaces or remote Langfuse scores are outside this preflight; use a unique
    evaluation_set across machines unless the remote metadata has been checked separately.
    """
    evaluation_set = evaluation_set or manifest["experiment_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", evaluation_set):
        raise ValueError(f"평가 세트 이름은 영문·숫자·'-'·'_'만 쓴다: {evaluation_set}")
    version = hashlib.sha256(
        json.dumps(conditions(manifest), sort_keys=True).encode()
    ).hexdigest()
    excluded = Path(exclude_manifest).resolve() if exclude_manifest else None
    if artifacts_dir:
        manifest_paths = Path(artifacts_dir).rglob("manifest.json")
    else:
        manifest_paths = ()
    for path in manifest_paths:
        if excluded is not None and path.resolve() == excluded:
            continue
        other = json.loads(path.read_text(encoding="utf-8"))
        used = other.get("langfuse_scores") or {}
        if used.get("evaluation_set") != evaluation_set:
            continue
        if skip_same_experiment and other.get("experiment_id") == manifest["experiment_id"]:
            continue
        if used.get("evaluation_hash") != version:
            raise ValueError(
                f"평가 세트 '{evaluation_set}'는 {path.parent}에서 다른 채점 조건으로 "
                "게시 또는 예약됐다. 조건이 다르면 다른 세트 이름을 쓴다"
            )
        graded = path.with_name("scores.jsonl")
        if graded.exists():
            lines = graded.read_text(encoding="utf-8").splitlines()
        else:
            lines = []
        graded_request_ids = {
            json.loads(line)["request_id"] for line in lines if line.strip()
        }
        if graded_request_ids & set(request_ids):
            raise ValueError(
                f"평가 세트 '{evaluation_set}'에 같은 요청이 이미 채점됐다. "
                "반복 회차는 다른 세트 이름을 쓴다"
            )
    selected_groups = [group for group in GROUPS if group in (groups or GROUPS)]
    return {
        "status": "incomplete",
        "evaluation_set": evaluation_set,
        "groups": selected_groups,
        "evaluation_hash": version,
        "sample": bool(manifest.get("sample")),
    }


def publish_evaluation_scores(tracer, runs, scores, manifest):
    """manifest["langfuse_scores"]의 게시 계획(없으면 실험 ID 세트·전체 지표군)대로 점수를 보낸다."""
    plan = manifest.get("langfuse_scores") or plan_publication(manifest)
    if tracer is None or not getattr(tracer, "enabled", False):
        return {**plan, "status": "disabled", "score_status_counts": {}, "flush": None}
    metadata = {**conditions(manifest), "experiment_id": manifest["experiment_id"],
                "source_experiment_id": (manifest.get("regrade") or {}).get("source_experiment_id")
                or manifest["experiment_id"],
                "evaluation_set": plan["evaluation_set"], "evaluation_hash": plan["evaluation_hash"]}
    timestamp = manifest.get("langfuse_score_timestamp")
    score_options = {"timestamp": datetime.fromisoformat(timestamp)} if timestamp else {}
    by_request = {r["request_id"]: r for r in runs}
    counts = Counter()
    for row in scores:
        trace_id = by_request[row["request_id"]].get("langfuse_trace_id")
        statuses = Counter()
        for metric, item in score_items(row, plan["groups"]).items():
            value = item.get("value")
            if (item.get("status") != "scored" or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)):
                statuses["skipped"] += 1
                continue
            if not trace_id:
                statuses["no_trace_id"] += 1
                continue
            name = f"{plan['evaluation_set']}.{metric}"
            # TableRAG 재게시는 저장된 ID, 지표 이름, timestamp를 함께 사용한다
            score_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aden:{trace_id}:{name}"))
            comment = f"{'SAMPLE; ' if manifest.get('sample') else ''}evaluation={manifest['experiment_id']}; {item.get('reason', '')}"
            if timestamp:
                comment = f"evaluation={manifest['experiment_id']}; grade={manifest['grade_id']}"
            try:
                result = tracer.record_score(trace_id, name, value, comment=comment, score_id=score_id,
                                             metadata=metadata, **score_options)
                statuses[result["status"]] += 1
                if result["status"] == "recorded_unverified":  # 수신 조회에 쓸 ID
                    row.setdefault("langfuse_score_ids", {})[name] = score_id
            except Exception as exc:
                statuses["sdk_error"] += 1
                row.setdefault("langfuse_score_errors", []).append(type(exc).__name__)
        row["langfuse_score_status_counts"] = dict(statuses)
        counts.update(statuses)
    try:
        flush = tracer.flush()
    except Exception as exc:
        flush = {"status": "failed", "error": type(exc).__name__}
    failed = counts["sdk_error"] or counts["invalid"] or flush.get("status") == "failed"
    status = "partial" if failed else "recorded_unverified" if counts["recorded_unverified"] else "no_scores"
    return {**plan, "status": status, "score_status_counts": dict(counts), "flush": flush}
