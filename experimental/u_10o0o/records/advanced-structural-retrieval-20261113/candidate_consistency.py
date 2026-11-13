"""A validated sufficient subset proves coverage of its identical candidate set."""

import json
import hashlib
from pathlib import Path
from collections import defaultdict

root = Path(__file__).resolve().parents[4]
out = root / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"
rec = root / "experimental/u_10o0o/records/advanced-structural-retrieval-20261113"


def read(p):
    return json.loads(p.read_text())


def write(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n")


def identity(run):
    pages = sorted(
        (
            {"source_id": p["source_id"], "text": p["text"]}
            for p in run["retrieved_candidates"]
        ),
        key=lambda p: p["source_id"],
    )
    return hashlib.sha256(
        json.dumps(
            {
                "question_id": run["question_id"],
                "pages": pages,
                "sql": run.get("included_sql_results", []),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


rows = read(rec / "per-run.json")
groups = defaultdict(list)
overrides = []
for row in rows:
    if row["semantic_complete"] is None:
        continue
    run = read(out / "runs" / (row["key"] + ".json"))
    if run.get("status") == "execution_error":
        continue
    groups[identity(run)].append(row)
for group in groups.values():
    proof = next(
        (
            r
            for r in group
            if r["semantic_complete"] is True and not r["review_pending"]
        ),
        None,
    )
    if proof is None:
        continue
    for row in group:
        row["candidate_subset_proof_run"] = proof["key"]
        if row["semantic_candidate_complete"] is not True:
            row["raw_semantic_candidate_complete"] = row["semantic_candidate_complete"]
            row["semantic_candidate_complete"] = True
            overrides.append(
                {
                    "run": row["key"],
                    "sufficient_subset_run": proof["key"],
                    "reason": "Same question, same complete candidate texts and SQL; validated sufficient final evidence is a subset.",
                }
            )
summary = read(rec / "summary.json")
for item in summary:
    subset = [
        x
        for x in rows
        if x["arm"] == item["arm"]
        and x["split"] == item["split"]
        and x["semantic_complete"] is not None
    ]
    item["candidate_complete"] = sum(
        x["semantic_candidate_complete"] is True for x in subset
    )
write(rec / "per-run.json", rows)
write(rec / "summary.json", summary)
write(
    rec / "candidate-consistency.json",
    {
        "rule": "A sufficient subset establishes candidate coverage for every identical candidate set. No search or final judgments are changed.",
        "overrides": overrides,
        "declared_before_quality_scoring": True,
    },
)
print("Candidate logical consistency overrides", len(overrides))
