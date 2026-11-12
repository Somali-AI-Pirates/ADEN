"""Trace the observed losses without changing retrieval or evaluation labels."""

from collections import Counter
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    object_sha,
    read_json,
    write_json,
)

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"


def main():
    scores = read_json(HERE / "reviewed-per-question.json")
    indexed = {a: {r["id"]: r for r in rows} for a, rows in scores.items()}
    groups = {}
    rows = []
    for row in scores["B"]:
        rid = row["id"]
        saved = read_json(OUT / "B/results" / (object_sha(rid) + ".json"))
        docs = [s for s in saved["searches"] if s["kind"] == "documents"]
        lookups = [s for s in saved["searches"] if s["kind"] == "records"]
        warnings = [w for t in saved["turns"] for w in t.get("plan_validation", [])]
        duplicate_calls = len(docs) - len({s["query"] for s in docs})
        metric = row["complete_metric"]
        regressed = (
            row["scored"]
            and indexed["A"][rid]["reviewed_complete"]
            and not row["reviewed_complete"]
        )
        stage = (
            "not_regressed"
            if not regressed
            else "all_retrieval_union_missing_designated_evidence"
            if not row["all_search_union"][metric]
            else "merged_20_missing_designated_evidence"
            if not row["stages"]["available"][metric]
            else "final_8_missing_designated_evidence"
            if not row["stages"]["final"][metric]
            else "semantic_scope_despite_designated_hit"
        )
        item = {
            "id": rid,
            "category": row["category"],
            "reviewed_regression": bool(regressed),
            "designated_loss_stage": stage,
            "plan_fallbacks": len(warnings),
            "record_calls": len(lookups),
            "document_calls": len(docs),
            "repeated_document_queries": duplicate_calls,
            "lookup_statuses": [
                t["status"] for s in lookups for t in s["resolution"]["targets"]
            ],
            "returned_record_roles": sorted(
                {
                    src["role"]
                    for s in lookups
                    for t in s["resolution"]["targets"]
                    for e in t["entities"]
                    for src in e["sources"]
                }
            ),
            "request_count": len(
                (saved["selection_audit"].get("plan") or {}).get("requests", [])
            ),
        }
        rows.append(item)
    for category in sorted({r["category"] for r in rows}):
        selected = [r for r in rows if r["category"] == category]
        groups[category] = {
            "n": len(selected),
            "regressions": sum(r["reviewed_regression"] for r in selected),
            "no_record_lookup": sum(r["record_calls"] == 0 for r in selected),
            "plan_fallback_questions": sum(r["plan_fallbacks"] > 0 for r in selected),
            "repeated_document_queries": sum(
                r["repeated_document_queries"] for r in selected
            ),
            "loss_stages": dict(
                Counter(
                    r["designated_loss_stage"]
                    for r in selected
                    if r["reviewed_regression"]
                )
            ),
        }
    write_json(
        HERE / "diagnosis.json",
        {
            "groups": groups,
            "per_question": rows,
            "limits": "Loss stages use registered evidence even when final semantic review accepts alternatives. They locate reference loss, not every possible alternative source.",
            "code_findings": [
                "Planning precedes any retrieved evidence. The bounded repair can search or query SQL but cannot perform a later record lookup using newly discovered identifiers.",
                "An unverified field in a target discards that target set and restores the original question query. Correct indirect clues can be lost together.",
                "Record role selection does not automatically retain source pages proving the identifying date/shipper/cargo predicates.",
                "All non-fast-path questions require both planning and selection. The restricted zero-model grammar matched none of the 278 evaluation questions.",
            ],
        },
    )
    print({k: groups[k] for k in ["S02", "S04", "S06"]})
    print(
        "all_regression_stages",
        dict(
            Counter(
                r["designated_loss_stage"] for r in rows if r["reviewed_regression"]
            )
        ),
    )
    print("repeated_queries", sum(r["repeated_document_queries"] for r in rows))


if __name__ == "__main__":
    main()
