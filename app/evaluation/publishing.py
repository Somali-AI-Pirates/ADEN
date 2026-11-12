"""Explicit score publication, separate from generation and local evaluation."""
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from pathlib import Path

GROUPS = ("retrieval", "faithfulness", "judge", "evidence")  # 지표군 = 지표 이름의 첫 마디


def expected_observations(run):
    names = ["rag-query"]
    if run.get("retrieved_candidates") is not None:
        names.append("retrieval")
    if run.get("final_context_chunks") is not None:
        names.append("final-context")
    return names + [f"generation-attempt-{a['attempt']}" for a in run.get("attempts", [])]


def score_items(row, groups=GROUPS):
    """채점 기록 한 건에서 지표 이름 → 점수 항목(고른 지표군만). 게시와 수신 대조가 같은 이름을 쓴다."""
    k = row["retrieval"]["k"]
    items = {f"retrieval.hit@{k}": row["retrieval"]["hit_at_k"],
             f"retrieval.recall@{k}": row["retrieval"]["recall_at_k"],
             "faithfulness": row["faithfulness"],
             **{f"judge.{key}": value for key, value in row["judge"].items()},
             **{f"evidence.{key}": value for key, value in row.get("evidence_diagnostics", {}).items()}}
    selected = {}
    for name, item in items.items():
        if name.split(".")[0] in groups:
            selected[name] = item
    return selected


def conditions(manifest):
    """한 평가 세트가 공유해야 하는 채점 조건. 실험 ID와 원본 실험은 세트 안에서 달라도 된다."""
    bench = manifest.get("benchmark") or {}
    return {"dataset_id": manifest.get("dataset_id"), "config_hash": manifest["config_hash"],
            "sample": bool(manifest.get("sample")),
            **{key: bench.get(key) for key in
               ("k", "judge", "faithfulness", "judge_prompt", "judge_schema", "references_sha256")}}


def plan_publication(manifest, *, evaluation_set=None, groups=None, artifacts_dir=None,
                     request_ids=()):
    """게시할 평가 세트와 지표군을 정한다. 점수 이름은 `<세트>.<지표>`라 세트끼리 집계가 섞이지 않는다.

    같은 세트 이름을 쓴 다른 실험이 있으면 채점 조건이 같고 요청이 겹치지 않아야 한다
    (v1·v2는 조건이 달라서, 반복 회차는 요청이 겹쳐서 다른 세트가 된다).
    shortcut: artifacts_dir에 저장된 실험만 대조한다. 다른 PC의 게시와 겹치는지는 서버 점수의
    metadata(evaluation_hash)를 조회해 확인한다.
    """
    evaluation_set = evaluation_set or manifest["experiment_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", evaluation_set):
        raise ValueError(f"평가 세트 이름은 영문·숫자·'-'·'_'만 쓴다: {evaluation_set}")
    version = hashlib.sha256(json.dumps(conditions(manifest), sort_keys=True).encode()).hexdigest()
    if artifacts_dir:
        manifest_paths = Path(artifacts_dir).glob("*/manifest.json")
    else:
        manifest_paths = ()
    for path in manifest_paths:
        other = json.loads(path.read_text(encoding="utf-8"))
        used = other.get("langfuse_scores") or {}
        if (used.get("evaluation_set") != evaluation_set
                or other.get("experiment_id") == manifest["experiment_id"]):
            continue
        if used.get("evaluation_hash") != version:
            raise ValueError(f"평가 세트 '{evaluation_set}'는 실험 {other.get('experiment_id')}에서 다른 "
                             "채점 조건으로 게시됐다. 조건이 다르면 다른 세트 이름을 쓴다")
        graded = path.with_name("scores.jsonl")
        if graded.exists():
            lines = graded.read_text(encoding="utf-8").splitlines()
        else:
            lines = []
        graded_request_ids = set()
        for line in lines:
            if line.strip():
                graded_request_ids.add(json.loads(line)["request_id"])
        if graded_request_ids & set(request_ids):
            raise ValueError(f"평가 세트 '{evaluation_set}'에 실험 {other.get('experiment_id')}가 같은 요청을 "
                             "이미 채점해 게시했다. 반복 회차는 다른 세트 이름을 쓴다")
    selected_groups = []
    for group in GROUPS:
        if group in (groups or GROUPS):
            selected_groups.append(group)
    return {"status": "incomplete",  # 게시가 끝나면 실제 상태로 바뀐다. 남아 있으면 게시 중 중단된 것
            "evaluation_set": evaluation_set, "groups": selected_groups,
            "evaluation_hash": version, "sample": bool(manifest.get("sample"))}


def publish_evaluation_scores(tracer, runs, scores, manifest):
    """manifest["langfuse_scores"]의 게시 계획(없으면 실험 ID 세트·전체 지표군)대로 점수를 보낸다."""
    plan = manifest.get("langfuse_scores") or plan_publication(manifest)
    if tracer is None or not getattr(tracer, "enabled", False):
        return {**plan, "status": "disabled", "score_status_counts": {}, "flush": None}
    metadata = {**conditions(manifest), "experiment_id": manifest["experiment_id"],
                "source_experiment_id": (manifest.get("regrade") or {}).get("source_experiment_id")
                or manifest["experiment_id"],
                "evaluation_set": plan["evaluation_set"], "evaluation_hash": plan["evaluation_hash"]}
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
            # 세트·trace·지표가 같으면 같은 ID다. 다시 게시해도 서버에는 한 건만 남는다
            score_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aden:{trace_id}:{name}"))
            comment = f"{'SAMPLE; ' if manifest.get('sample') else ''}evaluation={manifest['experiment_id']}; {item.get('reason', '')}"
            try:
                result = tracer.record_score(trace_id, name, value, comment=comment, score_id=score_id,
                                             metadata=metadata)
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
