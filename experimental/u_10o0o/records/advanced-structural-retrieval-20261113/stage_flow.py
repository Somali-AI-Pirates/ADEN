"""Source-level search flow for changed questions and the three original failures."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REC = Path(__file__).resolve().parent
OUT = ROOT / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"


def read(path):
    return json.loads(path.read_text())


def ranks(items, source):
    return [i + 1 for i, x in enumerate(items) if x.get("source_id") == source]


def main():
    rows = read(REC / "per-run.json")
    changes = read(REC / "paired-changes.json")
    focus = {
        "official_additional:AS-ADD02",
        "official_additional:AS-ADD06",
        "official_additional:AS-ADD09",
    }
    focus.update(x["question_id"] for x in changes if x["A"] != x["changed"])
    rubrics = read(OUT / "rubric.json")
    fixes = read(OUT / "adjudications.json")
    targets = {}
    for qid in focus:
        targets[qid] = {
            f"{x['book_id']}:p{x['page']:04d}"
            for group in rubrics[qid]["evidence_groups"]
            for x in group
        }
    for row in rows:
        qid = row["question_id"]
        if qid not in focus or not row["review_key"]:
            continue
        k = row["review_key"]
        review = (
            fixes[k]["review"]
            if k in fixes
            else read(OUT / "api" / f"review-{k}.json")["parsed"]
        )
        for claim in review["claims"]:
            targets[qid].update(
                x["source_id"]
                for x in claim["evidence"]
                if x["source_id"].startswith("AS")
            )
    result = []
    for row in rows:
        qid = row["question_id"]
        if qid not in focus:
            continue
        run = read(OUT / "runs" / (row["key"] + ".json"))
        d = run["diagnostics"]
        selector_observed = set()
        if row["arm"] in {"S", "V"}:
            prefix = "select-literal-v2-" if row["arm"] == "V" else "select-"
            selector_file = OUT / "api" / (prefix + row["key"] + ".json")
            if selector_file.exists():
                selector_observed = {
                    v["source_id"]
                    for v in read(selector_file)["payload"]["observed_evidence"]
                }
        searches = [s for s in d.get("searches", []) if "rrf_candidates" in s]
        details = []
        for source in sorted(targets[qid]):
            details.append(
                {
                    "source_id": source,
                    "in_final_candidates": source in run["candidate_source_ids"],
                    "in_final_context": source in run["final_source_ids"],
                    "selector_observed": source in selector_observed,
                    "planner_selected": source in d.get("planner_selected_sources", []),
                    "graph_observed": any(
                        source == p.get("source_id")
                        for obs in d.get("observations", [])
                        for p in obs.get("passages", [])
                    ),
                    "searches": [
                        {
                            "search": n + 1,
                            "bm25": ranks(s.get("bm25_top", []), source),
                            "dense": ranks(s.get("dense_top", []), source),
                            "rrf_pool": ranks(s.get("rrf_candidates", []), source),
                            "kure": ranks(
                                s.get("reranker", {}).get("ranked_units", []), source
                            ),
                            "returned": (
                                s.get("ordered_source_ids", []).index(source) + 1
                                if source in s.get("ordered_source_ids", [])
                                else None
                            ),
                        }
                        for n, s in enumerate(searches)
                    ],
                }
            )
        result.append(
            {
                "key": row["key"],
                "question_id": qid,
                "arm": row["arm"],
                "repeat": row["repeat"],
                "semantic_complete": row["semantic_complete"],
                "semantic_candidate_complete": row["semantic_candidate_complete"],
                "initial_route": d.get("initial_route"),
                "status": run["status"],
                "termination": run["termination_reason"],
                "query_sha256": [
                    hashlib.sha256(q.encode()).hexdigest()
                    for q in run.get("search_queries", [])
                ],
                "tools": [
                    t.get("tool")
                    for t in d.get("turns", [])
                    if t.get("status") == "executed"
                ],
                "final_sources": run["final_source_ids"],
                "source_flows": details,
            }
        )
    (REC / "stage-flow.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print("Source flow", len(result), "runs", len(focus), "questions")


if __name__ == "__main__":
    main()
