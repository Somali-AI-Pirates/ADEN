"""Map dataset action labels to retrieval runtime statuses, without scoring answers."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[4]
out = root / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"
rec = root / "experimental/u_10o0o/records/advanced-structural-retrieval-20261113"


def read(p):
    return json.loads(p.read_text())


def write(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n")


labels = {
    "answer": "sufficient",
    "clarify": "needs_clarification",
    "abstain": "insufficient",
    "needs_clarification": "needs_clarification",
    "insufficient": "insufficient",
}
references = read(out / "reference-inputs.json")
accepted = {}
for split, spec in references.items():
    for row in read(root / spec["path"]):
        accepted[split + ":" + row["id"]] = [
            labels[x]
            for x in row.get("accepted_action_labels", [row["expected_action"]])
        ]
rubric = read(out / "rubric.json")
for qid, row in rubric.items():
    if qid.startswith("STRUCT-HOLD"):
        accepted[qid] = [labels[row["expected_action"]]]
rows = read(rec / "per-run.json")
summary = read(rec / "summary.json")
if not (out / "pre-canonical-status-per-run.json").exists():
    write(out / "pre-canonical-status-per-run.json", rows)
    write(out / "pre-canonical-status-summary.json", summary)
for row in rows:
    run = read(out / "runs" / (row["key"] + ".json"))
    searches = run.get("diagnostics", {}).get("searches", [])
    planner_usage = [c.get("usage") or {} for c in run.get("model_calls", [])]
    selector_usage = (run.get("selector_call") or {}).get("usage") or {}
    for field in ["input_tokens", "output_tokens"]:
        row[field] = sum(u.get(field) or 0 for u in planner_usage) + (
            selector_usage.get(field) or 0
        )
    row["graph_source_groups"] = len(searches)
    row["hybrid_search_calls"] = sum("rrf_candidates" in s for s in searches)
    row["table_catalog_calls"] = sum(s.get("kind") == "tables" for s in searches)
    row["record_result_groups"] = sum(s.get("kind") == "records" for s in searches)
    row["sql_calls"] = run.get("diagnostics", {}).get("sql_calls", 0)
    row["search_calls"] = row["hybrid_search_calls"] + row["table_catalog_calls"]
    row["baseline_termination_reason"] = run.get("termination_reason")
    row["reranker_truncated_pairs"] = sum(
        s.get("reranker", {}).get("truncated_pairs", 0) for s in searches
    )
    row["accepted_runtime_statuses"] = accepted[row["question_id"]]
    row["status_correct"] = row["status"] in row["accepted_runtime_statuses"]
    row["evidence_sufficient_but_not_sufficient_status"] = (
        row["semantic_complete"] is True and row["status"] != "sufficient"
    )
    row["false_insufficient_for_answerable_evidence"] = (
        row["semantic_complete"] is True
        and "sufficient" in row["accepted_runtime_statuses"]
        and row["status"] not in row["accepted_runtime_statuses"]
    )
    row["unsafe_sufficient_status"] = row["status"] == "sufficient" and (
        row["semantic_complete"] is False
        or "sufficient" not in row["accepted_runtime_statuses"]
    )
for group in summary:
    items = [
        x for x in rows if x["arm"] == group["arm"] and x["split"] == group["split"]
    ]
    group["status_correct"] = sum(x["status_correct"] for x in items)
    for field in [
        "search_calls",
        "hybrid_search_calls",
        "table_catalog_calls",
        "record_result_groups",
        "sql_calls",
        "reranker_truncated_pairs",
        "input_tokens",
        "output_tokens",
        "false_insufficient_for_answerable_evidence",
        "unsafe_sufficient_status",
    ]:
        group[field] = sum(x[field] for x in items)
write(rec / "per-run.json", rows)
write(rec / "summary.json", summary)
write(
    rec / "status-mapping.json",
    {
        "mapping": labels,
        "accepted_labels_source": "Frozen dataset reference inputs; official cases may allow both answer and abstain.",
        "no_evidence_queries": "Only questions without evidence requirements are excluded from semantic scoring. Clarify/abstain questions with required evidence remain scored for evidence availability.",
        "semantic_scores_changed": False,
        "retrieval_outputs_changed": False,
    },
)
print("Dataset action labels aligned; semantic scores unchanged")
