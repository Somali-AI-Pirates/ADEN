"""Keep strict metrics and append reviewed corrections on unchanged denominators."""

from collections import Counter, defaultdict
from importlib import import_module

from experimental.u_10o0o.pattern_retrieval.common import (
    file_sha,
    object_sha,
    read_json,
    write_json,
)

score = import_module(__package__ + ".score")


def main():
    _, _, saved, _ = score.load_completed()
    dest = score.OUTPUT / "evidence-review"
    run = read_json(dest / "run.json")
    assert run["status"] == "complete"
    reviews = {}
    for name, sha in run["files"].items():
        assert file_sha(dest / name) == sha
        r = read_json(dest / name)
        reviews[(r["id"], r["arm"])] = r
    manual = read_json(score.OUTPUT / "manual-review.json")
    rows = read_json(score.RECORDS / "per-question.json")
    assert len(reviews) == sum(
        not r["arms"][a]["metrics"]["final"]["all_groups"]
        for r in rows
        for a in ("A", "B")
    )
    audit = []
    verdicts = {}
    for q in rows:
        for arm in ("A", "B"):
            strict_pass = bool(q["arms"][arm]["metrics"]["final"]["all_groups"])
            if strict_pass:
                verdicts[(q["id"], arm)] = "strict_pass_not_semantically_audited"
                continue
            r = reviews[(q["id"], arm)]
            auto = (r["review"] or {}).get("verdict", "review_error")
            override = manual.get(q["id"] + "|" + arm)
            if auto == "sufficient":
                assert override is not None, (q["id"], arm)
            final = override["verdict"] if override else auto
            if final == "sufficient":
                assert override and (
                    not r["quote_validation"]
                    or override.get("quote_errors_manually_resolved")
                )
            if final == "sufficient" and auto != "sufficient":
                proof = reviews[(q["id"], override["proof_from_arm"])]
                assert proof["review"]["verdict"] == "sufficient"
                evidence = {
                    f"{p['book_id']}:p{p['page']:04d}": score.normalize(p["text"])
                    for p in saved[arm][q["id"]]["contexts"]["table_rag"]
                }
                for claim in proof["review"]["claims"]:
                    assert claim["status"] == "supported"
                    for e in claim["evidence"]:
                        assert score.normalize(e["quote"]) in evidence.get(
                            e["source_id"], ""
                        )
            if r["quote_validation"] and not override and auto != "sufficient":
                # A failed quote check cannot support an automatic recovery.
                final = "uncertain_quote_validation"
            verdicts[(q["id"], arm)] = final
            sources = sorted(
                {
                    e["source_id"]
                    for c in (r["review"] or {}).get("claims", [])
                    for e in c["evidence"]
                }
            )
            audit.append(
                {
                    "id": q["id"],
                    "arm": arm,
                    "automatic_verdict": auto,
                    "final_verdict": final,
                    "recovered": final == "sufficient",
                    "assistant_override_or_confirmation": override,
                    "automatic_reason": (r["review"] or {}).get("missing_or_ambiguity")
                    or (r["review"] or {}).get("reason"),
                    "proof_sources": sources,
                    "quote_validation_error_count": len(r["quote_validation"]),
                    "private_review_file": str(
                        (dest / (object_sha([q["id"], arm]) + ".json")).relative_to(
                            score.ROOT
                        )
                    ),
                }
            )
    groups = defaultdict(list)
    for q in rows:
        groups["all"].append(q)
        groups[q["split"]].append(q)
        groups[
            "preexisting_ambiguous"
            if q["preexisting_ambiguous"]
            else "excluding_preexisting_ambiguous"
        ].append(q)
        if q["split"] == "peer_multi_doc":
            suffix = "false" if q["false_premise"] else "normal"
            groups[f"multi_{q['required_documents']}_{suffix}"].append(q)
            groups[f"multi_{suffix}"].append(q)
            groups[f"multi_{q['required_documents']}_all"].append(q)
    summary = {}
    for name, qs in groups.items():
        summary[name] = {"count": len(qs), "arms": {}}
        for arm in ("A", "B"):
            counts = Counter(verdicts[(q["id"], arm)] for q in qs)
            strict = counts["strict_pass_not_semantically_audited"]
            recovered = counts["sufficient"]
            summary[name]["arms"][arm] = {
                "strict_complete": strict,
                "additional_recovered": recovered,
                "adjusted_complete": strict + recovered,
                "strict_percent": 100 * strict / len(qs),
                "adjusted_percent": 100 * (strict + recovered) / len(qs),
                "failure_review_categories": dict(counts),
            }
    changes = []
    for q in rows:
        strict = {
            a: bool(q["arms"][a]["metrics"]["final"]["all_groups"]) for a in ("A", "B")
        }
        adjusted = {
            a: strict[a] or verdicts[(q["id"], a)] == "sufficient" for a in ("A", "B")
        }
        if strict["A"] != strict["B"] or adjusted["A"] != adjusted["B"]:
            changes.append(
                {
                    "id": q["id"],
                    "split": q["split"],
                    "preexisting_ambiguous": q["preexisting_ambiguous"],
                    "strict": strict,
                    "adjusted": adjusted,
                    "review": {a: verdicts[(q["id"], a)] for a in ("A", "B")},
                }
            )
    write_json(score.RECORDS / "evidence-audit.json", audit)
    write_json(score.RECORDS / "reviewed-summary.json", summary)
    write_json(score.RECORDS / "differences.json", changes)
    write_json(
        score.RECORDS / "audit-manifest.json",
        {
            "review_run_sha256": file_sha(dest / "run.json"),
            "manual_review_sha256": file_sha(score.OUTPUT / "manual-review.json"),
            "script_sha256": file_sha(__file__),
            "reviewed_failed_arm_contexts": len(audit),
            "all_automatic_positive_proposals_assistant_reviewed": True,
            "strict_passes_semantically_audited": False,
            "independent_human_review": False,
            "answer_generation": False,
            "denominators_changed_by_posthoc_review": False,
        },
    )
    print(
        {
            k: {a: v["adjusted_complete"] for a, v in x["arms"].items()}
            for k, x in summary.items()
        }
    )
    print("automatic reviews", len(reviews), "manual records", len(manual))
    assert all(not x["answers"] for arm in saved.values() for x in arm.values())


if __name__ == "__main__":
    main()
