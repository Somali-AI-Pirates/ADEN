"""Record requirement-level gains/losses without exposing private quotes."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[4]
out = root / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"
rec = root / "experimental/u_10o0o/records/advanced-structural-retrieval-20261113"


def read(p):
    return json.loads(p.read_text())


def write(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n")


assert read(rec / "evaluation-protocol.json")["pending"] == 0
jobs = read(out / "review-jobs.json")
fixes = (
    read(out / "adjudications.json") if (out / "adjudications.json").exists() else {}
)


def review(key):
    if key not in jobs["run_to_review"]:
        return None
    k = jobs["run_to_review"][key]
    return (
        fixes[k]["review"]
        if k in fixes
        else read(out / "api" / ("review-" + k + ".json"))["parsed"]
    )


comparisons = read(rec / "paired-changes.json")
private = []
for row in comparisons:
    n = row["repeat"]
    qid = row["question_id"]
    arm = row["arm"]
    a = review(f"r{n}-A-{qid}")
    b = review(f"r{n}-{arm}-{qid}")
    if not a or not b:
        continue
    sa = {c["requirement_id"] for c in a["claims"] if c["status"] == "supported"}
    sb = {c["requirement_id"] for c in b["claims"] if c["status"] == "supported"}
    row.update(
        gained_requirement_ids=sorted(sb - sa),
        lost_requirement_ids=sorted(sa - sb),
        A_scope_ok=a["scope_ok"],
        changed_scope_ok=b["scope_ok"],
    )
    if sa != sb or a["scope_ok"] != b["scope_ok"]:
        private.append(
            {
                "question_id": qid,
                "repeat": n,
                "arm": arm,
                "A_review": a,
                "changed_review": b,
            }
        )
write(rec / "paired-changes.json", comparisons)
write(out / "claim-change-details.json", private)
print(
    "Requirement-level comparisons", len(comparisons), "changed contexts", len(private)
)

rows = read(rec / "per-run.json")
for row in rows:
    parsed = review(row["key"])
    if parsed is None:
        row["evidence_verdict"] = "state_only"
        row["scope_ok"] = None
    else:
        row["scope_ok"] = parsed["scope_ok"]
        row["ambiguous_requirements"] = [
            c["requirement_id"] for c in parsed["claims"] if c["status"] == "ambiguous"
        ]
        row["missing_requirements"] = [
            c["requirement_id"] for c in parsed["claims"] if c["status"] == "missing"
        ]
        row["evidence_verdict"] = (
            "sufficient"
            if row["semantic_complete"]
            else "ambiguous_scope_or_evidence"
            if not parsed["scope_ok"] or row["ambiguous_requirements"]
            else "missing_evidence"
        )
write(rec / "per-run.json", rows)
