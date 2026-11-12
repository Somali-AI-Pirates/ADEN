"""Validate adjudications and aggregate only the actual retained contexts."""

from collections import Counter
from hashlib import sha256
from pathlib import Path

import numpy as np

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"


def report(m):
    _, saved, _, _ = m.scorer().load()
    scores = read_json(HERE / "per-question.json")
    items = read_json(OUT / "review-inputs.json")
    decisions = read_json(OUT / "adjudications.json")
    reviews = {(r["id"], r["arm"]): r for r in decisions}
    assert len(reviews) == len(decisions)
    assert set(reviews) == {(r["id"], r["arm"]) for r in items}
    for (rid, arm), review in reviews.items():
        assert review["source_validation_complete"] is True
        assert review["assistant_checked"] or (
            review["verification_mode"] == "model_source_checked"
            and review["model_review_valid"]
        )
        assert review["verdict"] in {"sufficient", "insufficient", "ambiguous_question"}
        sources = m.evidence(saved[arm][rid])
        assert {
            k: sha256(v.encode()).hexdigest() for k, v in sources.items()
        } == review["source_hashes"]
        for claim in review["claims"]:
            if claim["status"] == "supported":
                assert claim["evidence"]
            for proof in claim["evidence"]:
                assert proof["source_id"] in sources
                assert len(m.norm(proof["quote"])) >= 8
                assert m.norm(proof["quote"]) in m.norm(sources[proof["source_id"]])
        if review["verdict"] == "sufficient":
            assert review["scope_ok"] and review["claims"]
            assert all(c["status"] == "supported" for c in review["claims"])
    for arm, rows in scores.items():
        for row in rows:
            if not row["scored"]:
                continue
            strict = bool(row["stages"]["final"][row["complete_metric"]])
            review = reviews.get((row["id"], arm))
            assert strict or review is not None
            row.update(
                strict_complete=strict,
                reviewed_complete=review["verdict"] == "sufficient"
                if review
                else strict,
                review_verdict=review["verdict"]
                if review
                else "strict_pass_not_reviewed",
            )
    indexed = {a: {r["id"]: r for r in rows} for a, rows in scores.items()}
    first = scores["A"]
    groups = {
        "official_all": [r["id"] for r in first if r["suite"].startswith("official_")],
        "official_complex": [
            r["id"]
            for r in first
            if r["suite"].startswith("official_") and r["complex"]
        ],
        "categories_positive": [
            r["id"]
            for r in first
            if r["scored"] and not r["suite"].startswith("official_")
        ],
        **{
            f"S{i:02}": [r["id"] for r in first if r["category"] == f"S{i:02}"]
            for i in range(1, 8)
        },
        "work_rules_all": [r["id"] for r in first if r["suite"] == "work_rules"],
        **{
            name: [
                r["id"]
                for r in first
                if r["suite"] == "semantic_queries"
                and lo <= int(r["question_id"].rsplit("-", 1)[-1]) <= hi
            ]
            for name, lo, hi in [("semantic_single", 1, 20), ("semantic_multi", 21, 40)]
        },
    }
    summary = {}
    for name, ids in groups.items():
        group = {"n": len(ids)}
        for arm in ("A", "B"):
            rows = [indexed[arm][rid] for rid in ids]
            group[arm] = {
                "strict": sum(r["strict_complete"] for r in rows),
                "reviewed": sum(r["reviewed_complete"] for r in rows),
                "candidate": sum(
                    r["stages"]["available"][r["complete_metric"]] for r in rows
                ),
                "all_query_candidate_complete": sum(
                    r["all_search_union"][r["complete_metric"]] for r in rows
                ),
                "page_recall": float(
                    np.mean([r["stages"]["final"]["page_recall"] for r in rows])
                ),
                "timing": m.timing([saved[arm][rid] for rid in ids]),
            }
        group["improved"] = sum(
            indexed["B"][rid]["reviewed_complete"]
            > indexed["A"][rid]["reviewed_complete"]
            for rid in ids
        )
        group["regressed"] = sum(
            indexed["B"][rid]["reviewed_complete"]
            < indexed["A"][rid]["reviewed_complete"]
            for rid in ids
        )
        summary[name] = group
    changes = [
        {
            "id": r["id"],
            "category": r["category"],
            "A": r["reviewed_complete"],
            "B": indexed["B"][r["id"]]["reviewed_complete"],
        }
        for r in first
        if r["scored"]
        and r["reviewed_complete"] != indexed["B"][r["id"]]["reviewed_complete"]
    ]
    timing = {a: m.timing(list(rows.values())) for a, rows in saved.items()}
    controls = {
        a: {
            "count": len(rows),
            "within_page_token_limits": all(
                len(r["contexts"]["table_rag"]) <= 8
                and r["traces"]["table_rag"]["context_tokens"] <= 8000
                and len(r["traces"]["table_rag"]["ranked"]) <= 20
                for r in rows.values()
            ),
            "fast_path": sum(
                r["selection_audit"].get("fast_path", False) for r in rows.values()
            ),
            "max_api_calls": max(len(r["turns"]) for r in rows.values()),
            "max_hybrid_calls": max(
                sum(s["kind"] == "documents" for s in r["searches"])
                for r in rows.values()
            ),
            "selection_errors": sum(
                bool(r["selection_audit"].get("errors")) for r in rows.values()
            ),
            "plan_validation_fallbacks": sum(
                bool(t.get("plan_validation"))
                for r in rows.values()
                for t in r["turns"]
            ),
            "request_counts": dict(
                Counter(
                    len((r["selection_audit"].get("plan") or {}).get("requests", []))
                    for r in rows.values()
                )
            ),
        }
        for a, rows in saved.items()
    }
    assert (
        controls["B"]["max_api_calls"] <= 3 and controls["B"]["max_hybrid_calls"] <= 5
    )
    write_json(HERE / "reviewed-summary.json", summary)
    write_json(HERE / "reviewed-per-question.json", scores)
    write_json(HERE / "reviewed-differences.json", changes)
    write_json(HERE / "timing.json", timing)
    write_json(HERE / "controls.json", controls)
    write_json(
        HERE / "review-manifest.json",
        {
            "all_required_checked": True,
            "reviewed_outputs": len(reviews),
            "source_quote_hash_checks": True,
            "independent_human_review": False,
            "assistant_checked_or_exact_reuse": sum(
                r["assistant_checked"] for r in decisions
            ),
            "model_plus_source_checks": sum(
                not r["assistant_checked"] for r in decisions
            ),
            "exact_reused_outputs": len(read_json(HERE / "reused-reviews.json")),
            "scope": "All official contexts, symmetric category strict misses and prior ambiguous cases",
            "review_inputs_sha256": file_sha(OUT / "review-inputs.json"),
            "adjudications_sha256": file_sha(OUT / "adjudications.json"),
        },
    )
    print(
        {
            name: {a: summary[name][a]["reviewed"] for a in ("A", "B")}
            for name in ("official_all", "categories_positive", "semantic_multi")
        }
    )
