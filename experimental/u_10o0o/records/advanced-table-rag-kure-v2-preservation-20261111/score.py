"""Score saved v2/v3 outputs, then prepare symmetric evidence audits."""

from collections import Counter
from importlib import import_module
from pathlib import Path
from hashlib import sha256

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    score_item,
    official_metrics,
    page_key,
)
from .run_benchmark import OUT, CORE, PEER

RECORDS = Path(__file__).parent
PREFIX = "experimental.u_10o0o.records."
CORE_SCORE = import_module(
    PREFIX + "advanced-table-rag-official-category-20261111.score"
)
PEER_SCORE = import_module(PREFIX + "advanced-table-rag-preservation-20261111.score")
ARMS = ("v2_A", "v2_B", "v3_A", "v3_B")


def load():
    protocol = read_json(OUT / "protocol.json")
    qs = read_json(OUT / "questions.json")
    assert file_sha(OUT / "questions.json") == protocol["questions_sha256"]
    assert (
        file_sha(OUT / "question-metadata.json") == protocol["question_metadata_sha256"]
    )
    saved = {}
    for arm in ("A", "B"):
        manifest = read_json(OUT / arm / "run.json")
        assert manifest["status"] == "complete" and manifest["count"] == 491
        assert manifest["protocol_sha256"] == file_sha(OUT / "protocol.json")
        saved["v2_" + arm] = {}
        for name, digest in manifest["files"].items():
            path = OUT / arm / name
            assert file_sha(path) == digest
            row = read_json(path)
            saved["v2_" + arm][row["id"]] = row
    # No reference content is opened until all new retrievals are saved and checked.
    cq, csaved, crefs, cmeta = CORE_SCORE.load()
    pq, psaved, prefs, pmeta = PEER_SCORE.load()
    for cohort, origin in [("core", CORE), ("peer", PEER)]:
        assert file_sha(OUT / f"{cohort}-references.json") == file_sha(
            origin / "references.json"
        )
    for arm in ("A", "B"):
        saved["v3_" + arm] = {**csaved[arm], **psaved[arm]}
    assert len(qs) == len(cq) + len(pq) == 491
    by_q = {q["id"]: q for q in cq + pq}
    for q in qs:
        assert q["question"] == by_q[q["id"]]["question"]
        for arm in ARMS:
            r = saved[arm][q["id"]]
            assert r["question_sha256"] == object_sha(q["question"])
            assert len(r["contexts"]["table_rag"]) <= 8
            assert r["traces"]["table_rag"]["context_tokens"] <= 8000
            assert len(r["traces"]["table_rag"]["ranked"]) <= 20
    meta = {
        **{k: {**v, "cohort": "core"} for k, v in cmeta.items()},
        **{k: {**v, "cohort": "peer"} for k, v in pmeta.items()},
    }
    refs = {
        q["id"]: crefs[cmeta[q["id"]]["suite"]][cmeta[q["id"]]["question_id"]]
        for q in cq
    }
    refs.update(prefs)
    return qs, saved, meta, refs


def existing_reviews(saved):
    reused = {}
    for cohort, origin in [("core", CORE), ("peer", PEER)]:
        run = read_json(origin / "evidence-review/run.json")
        automatic = {}
        for name, digest in run["files"].items():
            assert file_sha(origin / "evidence-review" / name) == digest
            row = read_json(origin / "evidence-review" / name)
            automatic[(row["id"], row["arm"])] = row
        for row in read_json(origin / "manual-review.json"):
            key = (row["id"], row["arm"])
            actual = {
                f"{p['book_id']}:p{p['page']:04d}": sha256(
                    p["text"].encode()
                ).hexdigest()
                for p in saved["v3_" + row["arm"]][row["id"]]["contexts"]["table_rag"]
            }
            if row.get("source_text_hashes"):
                assert actual == row["source_text_hashes"]
            automatic.setdefault(key, {})["prior_adjudication"] = row
        for (rid, arm), audit in automatic.items():
            reused[(rid, "v3_" + arm)] = {
                "id": rid,
                "arm": "v3_" + arm,
                "cohort": cohort,
                "audit": audit,
                "origin": str(origin.relative_to(ROOT)),
                "manual_file_sha256": file_sha(origin / "manual-review.json"),
                "run_file_sha256": file_sha(origin / "evidence-review/run.json"),
            }
    return reused


def main():
    qs, saved, meta, refs = load()
    pages = {
        page_key(p): p
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    reuse = existing_reviews(saved)
    # Reuse an audit across arms only when question and complete final evidence match.
    signatures = {}
    for (rid, arm), audit in reuse.items():
        output = saved[arm][rid]
        signature = object_sha(
            [
                output["question_sha256"],
                output["contexts"]["table_rag"],
                output["sql_results"],
            ]
        )
        signatures[(rid, signature)] = audit
    for rid in meta:
        for arm in ARMS:
            if (rid, arm) in reuse:
                continue
            output = saved[arm][rid]
            signature = object_sha(
                [
                    output["question_sha256"],
                    output["contexts"]["table_rag"],
                    output["sql_results"],
                ]
            )
            prior = signatures.get((rid, signature))
            if prior:
                reuse[(rid, arm)] = {
                    **prior,
                    "arm": arm,
                    "reused_from_arm": prior["arm"],
                    "exact_evidence_signature": signature,
                }
    report = []
    pending = []
    kept = []
    required = []
    for q in qs:
        rid = q["id"]
        m = meta[rid]
        row = {
            "id": rid,
            "cohort": q["cohort"],
            "kind": m.get("kind", m.get("suite")),
            "repaired": m.get("repaired", False),
            "prior_ambiguous_normal": m.get("prior_ambiguous_normal", False),
            "arms": {},
        }
        for arm in ARMS:
            r = saved[arm][rid]
            ctx = r["contexts"]["table_rag"]
            detail = {
                "status": r["finish"]["status"],
                "seconds": r["retrieval_seconds"],
                "api_calls": len(r["turns"]),
                "api_failures": sum(
                    t["api"]["status"] != "complete" for t in r["turns"]
                ),
                "search_calls": len(r["searches"]),
                "sql_calls": len(r["sql_attempts"]),
                "selected_not_retained": r.get("selection_audit", {}).get(
                    "not_retained", []
                ),
                "pages": [{"book_id": p["book_id"], "page": p["page"]} for p in ctx],
            }
            if q["cohort"] == "core":
                item = score_item(
                    m,
                    refs[rid],
                    {**r["traces"]["table_rag"], "arm": arm},
                    {"pages": ctx},
                    pages,
                )
                row.update(category=item["category"], scored=item["scored"])
                if item["scored"]:
                    metric = item["complete_metric"]
                    detail.update(
                        strict=int(item["stages"]["final"][metric]),
                        candidate=int(item["stages"]["available"][metric]),
                        page_recall=item["stages"]["final"]["page_recall"],
                    )
            else:
                row.update(category=m["kind"], scored=m["kind"] != "clarification")
                if row["scored"]:
                    final = official_metrics(refs[rid], ctx, 8)
                    candidate = official_metrics(
                        refs[rid], r["traces"]["table_rag"]["ranked"], 20
                    )
                    detail.update(
                        strict=int(final["all_groups"]),
                        candidate=int(candidate["all_groups"]),
                        page_recall=final["page_recall"],
                    )
                else:
                    detail.update(
                        correct_clarification=int(
                            r["finish"]["status"] == "needs_clarification"
                        ),
                        overconfirmation=int(r["finish"]["status"] == "sufficient"),
                    )
            row["arms"][arm] = detail
        needs_review = row["scored"] and (
            any(not d["strict"] for d in row["arms"].values())
            or row["kind"] == "false_premise"
            or row["prior_ambiguous_normal"]
            or rid == "semantic_queries:AS-SEM-32"
        )
        if needs_review:
            for arm in ARMS:
                required.append({"id": rid, "arm": arm, "cohort": q["cohort"]})
                if (rid, arm) in reuse:
                    kept.append(reuse[(rid, arm)])
                else:
                    r = saved[arm][rid]
                    pending.append(
                        {
                            "id": rid,
                            "arm": arm,
                            "cohort": q["cohort"],
                            "question": q["question"],
                            "reference": refs[rid],
                            "context": r["contexts"]["table_rag"],
                            "sql": r["sql_results"],
                        }
                    )
        report.append(row)
    write_json(RECORDS / "per-question.json", report)
    write_json(OUT / "review-inputs.json", pending)
    write_json(OUT / "reused-reviews.json", kept)
    write_json(RECORDS / "required-reviews.json", required)
    write_json(
        RECORDS / "score-manifest.json",
        {
            "references_read_after_both_runs": True,
            "new_searches_saved": 704,
            "required_reviews": len(required),
            "reused_reviews": len(kept),
            "new_review_count": len(pending),
            "new_review_inputs_sha256": file_sha(OUT / "review-inputs.json"),
            "reused_reviews_sha256": file_sha(OUT / "reused-reviews.json"),
            "policy_sha256": file_sha(RECORDS.parents[1] / "evaluation-policy.md"),
        },
    )
    for cohort in ("core", "peer"):
        rows = [r for r in report if r["cohort"] == cohort]
        print(
            cohort, {a: sum(r["arms"][a].get("strict", 0) for r in rows) for a in ARMS}
        )
    print("New reviews", len(pending), "reused", len(kept), "total", len(required))
    print("Review cohorts", Counter((r["cohort"], r["arm"]) for r in pending))


if __name__ == "__main__":
    main()
