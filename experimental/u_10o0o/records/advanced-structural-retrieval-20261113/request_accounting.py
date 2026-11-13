"""Count new provider requests separately from reused baseline and projected workflows."""

import json
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parents[4]
out = root / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"
rec = root / "experimental/u_10o0o/records/advanced-structural-retrieval-20261113"
totals = defaultdict(
    lambda: {
        "requests_with_record": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "usage_unknown": 0,
        "failed_records": 0,
    }
)
for p in (out / "runs").glob("r*.json"):
    if p.name.endswith("started.json"):
        continue
    r = json.loads(p.read_text())
    if r["arm"] not in {"A", "R", "P"} or r.get("reused_from"):
        continue
    t = totals["planner_" + r["arm"]]
    for call in r.get("model_calls", []):
        t["requests_with_record"] += 1
        usage = call.get("usage") or {}
        if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
            t["usage_unknown"] += 1
        t["input_tokens"] += usage.get("input_tokens") or 0
        t["output_tokens"] += usage.get("output_tokens") or 0
        t["failed_records"] += call.get("status") != "complete"
for p in (out / "api").glob("*.json"):
    if p.name.endswith("started.json"):
        continue
    r = json.loads(p.read_text())
    name = p.stem
    group = (
        "selection"
        if name.startswith("select-")
        else "review"
        if name.startswith("review-")
        else "audit"
        if name.startswith("audit-")
        else "dataset_preparation"
    )
    t = totals[group]
    t["requests_with_record"] += 1
    u = r.get("usage") or {}
    t["input_tokens"] += u.get("input_tokens", 0)
    t["output_tokens"] += u.get("output_tokens", 0)
    t["usage_unknown"] += not bool(u)
    t["failed_records"] += r.get("status") != "complete"
interrupted = []
for p in (out / "api").glob("*.started.json"):
    response = p.with_name(p.name.replace(".started.json", ".json"))
    if not response.exists():
        interrupted.append(p.name)
for p in (out / "invalidated-selector-v1").glob("select-*.started.json"):
    interrupted.append("archived/" + p.name)
result = {
    "by_stage": dict(totals),
    "new_requests_with_record": sum(t["requests_with_record"] for t in totals.values()),
    "known_input_tokens": sum(t["input_tokens"] for t in totals.values()),
    "known_output_tokens": sum(t["output_tokens"] for t in totals.values()),
    "interrupted_without_response_record": len(interrupted),
    "usage_unknown_records": sum(t["usage_unknown"] for t in totals.values()),
    "reused_baseline_runs_not_new_charges": 90,
    "invalidated_V1_requests_included": True,
    "answer_generation_calls": 0,
    "usd_cost": "Not estimated: provider pricing and interrupted-request billing are not verified. Token totals are known usage, not a complete invoice.",
}
(rec / "request-accounting.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
(out / "interrupted-request-inventory.json").write_text(
    json.dumps(interrupted, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
