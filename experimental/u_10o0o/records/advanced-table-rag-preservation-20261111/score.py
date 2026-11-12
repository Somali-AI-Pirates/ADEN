"""Evaluate frozen retrievals; keep clarification separate from page recall."""

from collections import Counter
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.pattern_retrieval.established_score import official_metrics

OUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-preservation-20261111/v1"
RECORDS = Path(__file__).parent


def load():
    protocol = read_json(OUT / "protocol.json")
    rows = {}
    for arm in ("A", "B"):
        run = read_json(OUT / arm / "run.json")
        assert run["status"] == "complete" and run["count"] == 213
        assert run["protocol_sha256"] == file_sha(OUT / "protocol.json")
        rows[arm] = {}
        for name, sha in run["files"].items():
            p = OUT / arm / name
            assert file_sha(p) == sha
            row = read_json(p)
            rows[arm][row["id"]] = row
    qs = read_json(OUT / "questions.json")
    assert file_sha(OUT / "questions.json") == protocol["questions_sha256"]
    assert file_sha(OUT / "references.json") == protocol["references_sha256"]
    assert (
        file_sha(OUT / "question-metadata.json") == protocol["question_metadata_sha256"]
    )
    return (
        qs,
        rows,
        read_json(OUT / "references.json"),
        {m["id"]: m for m in read_json(OUT / "question-metadata.json")},
    )


def main():
    questions, saved, refs, meta = load()
    report = []
    reviews = []
    for q in questions:
        m = meta[q["id"]]
        row = {
            "id": q["id"],
            "kind": m["kind"],
            "repaired": m["repaired"],
            "prior_ambiguous_normal": m.get("prior_ambiguous_normal", False),
            "arms": {},
        }
        for arm in ("A", "B"):
            r = saved[arm][q["id"]]
            assert r["question_sha256"] == object_sha(q["question"])
            trace = r["traces"]["table_rag"]
            ctx = r["contexts"]["table_rag"]
            assert len(ctx) <= 8 and trace["context_tokens"] <= 8000
            detail = {
                "status": r["finish"]["status"],
                "api_calls": len(r["turns"]),
                "api_failures": sum(
                    t.get("api", {}).get("status") != "complete" for t in r["turns"]
                ),
                "search_calls": len(r["searches"]),
                "seconds": r["retrieval_seconds"],
                "selected_not_retained": r["selection_audit"]["not_retained"],
                "unobserved_selection": r["selection_audit"]["unobserved"],
                "context_tokens": trace["context_tokens"],
                "pages": [{"book_id": p["book_id"], "page": p["page"]} for p in ctx],
                "api_seconds": sum(
                    t.get("api", {}).get("seconds", 0) for t in r["turns"]
                ),
            }
            if m["kind"] == "clarification":
                detail["correct_clarification"] = int(
                    r["finish"]["status"] == "needs_clarification"
                )
                detail["overconfirmation"] = int(r["finish"]["status"] == "sufficient")
            else:
                ref = refs[q["id"]]
                union = {
                    (p["book_id"], p["page"]): p
                    for search in r["searches"]
                    for p in search["ranked"]
                }
                detail["metrics"] = {
                    "all_search_union": official_metrics(
                        ref, list(union.values()), len(union)
                    ),
                    "candidate": official_metrics(ref, trace["ranked"], 20),
                    "final": official_metrics(ref, ctx, 8),
                }
                # Audit every false-premise output, including strict passes.
                # Normal strict misses receive the same alternative-evidence check.
                if (
                    m["kind"] == "false_premise"
                    or not detail["metrics"]["final"]["all_groups"]
                ):
                    reviews.append(
                        {
                            "id": q["id"],
                            "arm": arm,
                            "question": q["question"],
                            "reference": ref,
                            "context": ctx,
                            "sql": r["sql_results"],
                        }
                    )
            row["arms"][arm] = detail
        report.append(row)
    write_json(RECORDS / "per-question.json", report)
    write_json(OUT / "review-inputs.json", reviews)
    write_json(
        RECORDS / "score-manifest.json",
        {
            "references_read_after_both_runs": True,
            "preparation_used_references": True,
            "questions_sha256": file_sha(OUT / "questions.json"),
            "references_sha256": file_sha(OUT / "references.json"),
            "review_count": len(reviews),
            "review_false_premise_includes_strict_passes": True,
        },
    )
    for kind in ("normal", "false_premise", "clarification"):
        group = [r for r in report if r["kind"] == kind]
        print(
            kind,
            len(group),
            {
                a: dict(Counter(r["arms"][a]["status"] for r in group))
                for a in ("A", "B")
            },
        )
    print(
        "Saved outputs before review:",
        len(report),
        "reviews:",
        len(reviews),
        flush=True,
    )


if __name__ == "__main__":
    main()
