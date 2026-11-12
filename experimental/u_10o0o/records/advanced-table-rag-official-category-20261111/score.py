"""Score unchanged established questions only after both retrieval runs finish."""

from collections import Counter
from pathlib import Path
import numpy as np
from app.evaluation.dataset import load_references
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    score_item,
    aggregates,
    page_key,
)

OUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-official-category-20261111/v1"
RECORDS = Path(__file__).parent


def load():
    protocol = read_json(OUT / "protocol.json")
    qs = read_json(OUT / "questions.json")
    assert len(qs) == 278
    saved = {}
    for arm in ("A", "B"):
        run = read_json(OUT / arm / "run.json")
        assert run["status"] == "complete" and run["count"] == len(qs)
        assert run["protocol_sha256"] == file_sha(OUT / "protocol.json")
        saved[arm] = {}
        for name, sha in run["files"].items():
            path = OUT / arm / name
            assert file_sha(path) == sha
            r = read_json(path)
            saved[arm][r["id"]] = r
        assert set(saved[arm]) == {q["id"] for q in qs}
    for name, key in [
        ("questions.json", "questions_sha256"),
        ("references.json", "references_sha256"),
        ("question-metadata.json", "question_metadata_sha256"),
    ]:
        assert file_sha(OUT / name) == protocol[key]
    # Reference contents are first read after every output is verified above.
    sources = read_json(OUT / "references.json")
    refs = {}
    for suite, source in sources.items():
        assert file_sha(ROOT / source["path"]) == source["sha256"]
        refs[suite] = load_references(ROOT / source["path"])
    meta = {m["id"]: m for m in read_json(OUT / "question-metadata.json")}
    return qs, saved, refs, meta


def telemetry(rows):
    turns = [t for r in rows for t in r["turns"]]
    api = [t["api"] for t in turns]
    return {
        "count": len(rows),
        "planner_calls": len(api),
        "search_calls": sum(len(r["searches"]) for r in rows),
        "sql_calls": sum(len(r["sql_attempts"]) for r in rows),
        "api_status": dict(Counter(a["status"] for a in api)),
        "finish_status": dict(Counter(r["finish"]["status"] for r in rows)),
        "api_failure_questions": [
            r["id"]
            for r in rows
            if any(t["api"]["status"] != "complete" for t in r["turns"])
        ],
        "retrieval_p50": float(
            np.percentile([r["retrieval_seconds"] for r in rows], 50)
        ),
        "retrieval_p95": float(
            np.percentile([r["retrieval_seconds"] for r in rows], 95)
        ),
        "selected_not_retained": sum(
            len(r["selection_audit"]["not_retained"]) for r in rows
        ),
        "usage": {
            k: sum(a.get(k, 0) for a in api)
            for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        },
    }


def main():
    qs, saved, refs, metadata = load()
    pages = {
        page_key(p): p
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    scores = {"A": [], "B": []}
    changes, flows, reviews = [], [], []
    for q in qs:
        meta = metadata[q["id"]]
        ref = refs[meta["suite"]][meta["question_id"]]
        pair = {}
        for arm in ("A", "B"):
            r = saved[arm][q["id"]]
            assert r["question_sha256"] == object_sha(q["question"])
            trace = {**r["traces"]["table_rag"], "arm": arm}
            assert (
                len(trace["ranked"]) <= 20
                and len(r["contexts"]["table_rag"]) <= 8
                and trace["context_tokens"] <= 8000
            )
            item = score_item(
                meta, ref, trace, {"pages": r["contexts"]["table_rag"]}, pages
            )
            union = {page_key(p): p for s in r["searches"] for p in s["ranked"]}
            union_trace = {
                **trace,
                "pool": list(union.values()),
                "ranked": list(union.values()),
            }
            if item["scored"]:
                item["all_search_union"] = score_item(
                    meta, ref, union_trace, {"pages": r["contexts"]["table_rag"]}, pages
                )["stages"]["available"]
            item.update(
                finish_status=r["finish"]["status"],
                seconds=r["retrieval_seconds"],
                api_calls=len(r["turns"]),
                api_failures=sum(t["api"]["status"] != "complete" for t in r["turns"]),
            )
            scores[arm].append(item)
            pair[arm] = item
        if pair["A"]["scored"]:
            metric = pair["A"]["complete_metric"]
            av = pair["A"]["stages"]["final"][metric]
            bv = pair["B"]["stages"]["final"][metric]
            changes.append(
                {
                    "id": q["id"],
                    "category": pair["A"]["category"],
                    "A": av,
                    "B": bv,
                    "status": "improved"
                    if bv > av
                    else "regressed"
                    if av > bv
                    else "unchanged",
                }
            )
            # Symmetric review: audit both outputs for every question with a strict miss.
            if not av or not bv:
                for arm in ("A", "B"):
                    r = saved[arm][q["id"]]
                    reviews.append(
                        {
                            "id": q["id"],
                            "arm": arm,
                            "question": q["question"],
                            "reference": ref,
                            "context": r["contexts"]["table_rag"],
                            "sql": r["sql_results"],
                        }
                    )
            if av != bv:
                flows.append(
                    {
                        "id": q["id"],
                        "category": pair["A"]["category"],
                        "arms": {
                            a: {
                                "stages": pair[a]["stages"],
                                "all_search_union": pair[a]["all_search_union"],
                                "api_failures": pair[a]["api_failures"],
                                "status": saved[a][q["id"]]["finish"]["status"],
                                "searches": [
                                    [
                                        {"book_id": p["book_id"], "page": p["page"]}
                                        for p in s["ranked"]
                                    ]
                                    for s in saved[a][q["id"]]["searches"]
                                ],
                                "final": [
                                    {k: p[k] for k in ("book_id", "page")}
                                    for p in saved[a][q["id"]]["contexts"]["table_rag"]
                                ],
                                "selection_audit": {
                                    k: v
                                    for k, v in saved[a][q["id"]][
                                        "selection_audit"
                                    ].items()
                                    if k != "plan"
                                },
                            }
                            for a in ("A", "B")
                        },
                    }
                )
    summary = {a: aggregates(rows) for a, rows in scores.items()}
    usage = {
        a: {
            "all": telemetry(list(rs.values())),
            "official": telemetry(
                [r for r in rs.values() if r["split"].startswith("official_")]
            ),
            "categories": telemetry(
                [r for r in rs.values() if not r["split"].startswith("official_")]
            ),
        }
        for a, rs in saved.items()
    }
    write_json(RECORDS / "per-question.json", scores)
    write_json(RECORDS / "summary.json", summary)
    write_json(RECORDS / "differences.json", changes)
    write_json(RECORDS / "difference-flows.json", flows)
    write_json(RECORDS / "usage.json", usage)
    write_json(OUT / "review-inputs.json", reviews)
    write_json(
        RECORDS / "score-manifest.json",
        {
            "references_read_after_both_runs": True,
            "reference_manifest_sha256": file_sha(OUT / "references.json"),
            "question_sha256": file_sha(OUT / "questions.json"),
            "review_count": len(reviews),
            "review_policy": "Both outputs for each strict miss question; not all strict passes semantically audited",
            "count": 278,
        },
    )
    for group in [
        "official_all",
        "categories_positive",
        *[f"S{i:02}" for i in range(1, 8)],
        "work_rules_all",
        "semantic_all",
    ]:
        print(
            group,
            [
                (
                    a,
                    summary[a][group]["scored"],
                    summary[a][group]["stages"]["final"]["complete"],
                )
                for a in ("A", "B")
            ],
        )
    print(
        "Differences",
        dict(Counter(r["status"] for r in changes)),
        "reviews",
        len(reviews),
    )


if __name__ == "__main__":
    main()
