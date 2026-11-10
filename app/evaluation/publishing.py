"""Explicit score publication, separate from generation and local evaluation."""
import hashlib
import json
import math
import uuid
from collections import Counter


def expected_observations(run):
    names = ["rag-query"]
    if run.get("retrieved_candidates") is not None:
        names.append("retrieval")
    if run.get("final_context_chunks") is not None:
        names.append("final-context")
    return names + [f"generation-attempt-{a['attempt']}" for a in run.get("attempts", [])]


def publish_evaluation_scores(tracer, runs, scores, manifest):
    if tracer is None or not getattr(tracer, "enabled", False):
        return {"status": "disabled", "score_status_counts": {}, "flush": None}
    bench = manifest.get("benchmark") or {}
    identity = {"experiment_id": manifest["experiment_id"], "config_hash": manifest["config_hash"],
                "sample": bool(manifest.get("sample")),
                **{key: bench.get(key) for key in
                   ("k", "judge", "faithfulness", "judge_prompt", "references_sha256")}}
    version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    by_request = {r["request_id"]: r for r in runs}
    counts = Counter()
    for row in scores:
        trace_id = by_request[row["request_id"]].get("langfuse_trace_id")
        k = row["retrieval"]["k"]
        items = {f"retrieval.hit@{k}": row["retrieval"]["hit_at_k"],
                 f"retrieval.recall@{k}": row["retrieval"]["recall_at_k"],
                 "faithfulness": row["faithfulness"],
                 **{f"judge.{key}": value for key, value in row["judge"].items()},
                 **{f"evidence.{key}": value for key, value in row.get("evidence_diagnostics", {}).items()}}
        statuses = Counter()
        for name, item in items.items():
            value = item.get("value")
            if (item.get("status") != "scored" or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)):
                statuses["skipped"] += 1
                continue
            if not trace_id:
                statuses["no_trace_id"] += 1
                continue
            score_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aden:{version}:{trace_id}:{name}"))
            comment = f"{'SAMPLE; ' if manifest.get('sample') else ''}evaluation={manifest['experiment_id']}; {item.get('reason', '')}"
            try:
                result = tracer.record_score(trace_id, name, value, comment=comment, score_id=score_id)
                statuses[result["status"]] += 1
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
    return {"status": status, "score_status_counts": dict(counts), "flush": flush,
            "evaluation_hash": version, "sample": bool(manifest.get("sample"))}
