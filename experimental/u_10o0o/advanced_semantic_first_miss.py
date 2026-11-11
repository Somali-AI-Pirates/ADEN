"""Audit first missing evidence stages from saved request-wise retrieval."""

import argparse
import json
from collections import Counter
from pathlib import Path

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.context_evidence import _source_key
from experimental.u_10o0o.pattern_retrieval.common import (
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)

ARCHIVE = Path("artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01")
REFERENCES = Path(
    "experimental/u_10o0o/evaluation/advanced_scenarios/semantic_queries/references.json"
)
PAGES = Path("artifacts/u_10o0o/advanced_restored_pages.json")


def unique(sources):
    return list(dict.fromkeys(tuple(source) for source in sources))


def nominated(query):
    if not query["head40"]:
        # The original question is searched at depth20 in the frozen runner.
        head = query["head20"][:3]
    else:
        head = query["head40"][:3]
    return unique([*head, *query["rrf20"]])[:20]


def stages_for(work, trace):
    if work.get("verified_tasks", 0) or any(trace.get("requested_role_sources", [])):
        raise ValueError(
            "This frozen IS question audit supports search-only request steps."
        )
    queries = work["query_stats"]
    return {
        "retriever_union": unique(p for q in queries for p in q["union"]),
        "rrf20_union": unique(p for q in queries for p in q["rrf20"]),
        "request_nomination_union": unique(p for q in queries for p in nominated(q)),
        "merged20": unique(trace["pool"]),
        "expanded20": unique(trace["expanded"]),
        "final8": unique(trace["packed"]),
    }


def first_miss(coverage, plan_status):
    # RRF20 is one nomination branch; head40 can recover its omitted pages.
    path = (
        "retriever_union",
        "request_nomination_union",
        "merged20",
        "expanded20",
        "final8",
    )
    if all(coverage[stage] for stage in path):
        return "complete"
    if plan_status != "supported":
        return "plan_fallback"
    for stage in (
        "retriever_union",
        "request_nomination_union",
        "merged20",
        "expanded20",
        "final8",
    ):
        if not coverage[stage]:
            return stage
    return "complete"


def audit(output):
    traces = {(r["id"], r["arm"]): r for r in read_jsonl(ARCHIVE / "traces.jsonl")}
    work = {r["id"]: r for r in read_jsonl(ARCHIVE / "workload.jsonl")}
    old_scores = {
        r["id"]: r
        for r in read_jsonl(ARCHIVE / "scores.jsonl")
        if r["arm"] == "requests40" and r["id"].startswith("AS-SEM-")
    }
    if len(old_scores) != 40:
        raise ValueError("Expected 40 archived semantic results.")
    refs = load_references(REFERENCES)
    pages = {_source_key(p): p for p in read_json(PAGES)}
    contexts = {
        (r["id"], r["arm"]): r["pages"]
        for r in read_jsonl(ARCHIVE / "contexts.jsonl")
        if r["id"] in old_scores and r["arm"] == "requests40"
    }
    records = []
    for qid, old in old_scores.items():
        w, trace, reference = work[qid], traces[qid, "requests40"], refs[qid]
        stages = stages_for(w, trace)
        texts = {stage: [pages[key] for key in ids] for stage, ids in stages.items()}
        texts["final8"] = contexts[qid, "requests40"]
        whole = {
            stage: fact_coverage(reference, value) for stage, value in texts.items()
        }
        if (
            whole["merged20"]["all_facts"] != old["candidate_complete"]
            or whole["final8"]["all_facts"] != old["final_complete"]
        ):
            raise ValueError(f"Archived score does not reproduce: {qid}")
        requirements = []
        for i, requirement in enumerate(reference["evidence_requirements"], 1):
            covered = {
                stage: fact_coverage({"evidence_requirements": [requirement]}, value)[
                    "all_facts"
                ]
                for stage, value in texts.items()
            }
            requirements.append(
                {
                    "requirement": i,
                    "covered": covered,
                    "first_miss": first_miss(covered, w["plan_status"]),
                }
            )
        coverage = {stage: result["all_facts"] for stage, result in whole.items()}
        records.append(
            {
                "id": qid,
                "variant": reference["variant"],
                "plan_status": w["plan_status"],
                "executed_tasks": w["tasks"],
                "queries": len(w["query_stats"]),
                "stages": whole,
                "requirements": requirements,
                "candidate_failure": not bool(coverage["merged20"]),
                "first_miss": first_miss(coverage, w["plan_status"]),
            }
        )
    failed = [r for r in records if r["candidate_failure"]]
    if len(failed) != 19:
        raise ValueError("Expected the frozen 19 candidate failures.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "questions": 40,
        "candidate_failures": 19,
        "failure_stages": dict(Counter(r["first_miss"] for r in failed)),
        "stage_complete": {
            stage: sum(r["stages"][stage]["all_facts"] for r in records)
            for stage in records[0]["stages"]
        },
        "plan_states": dict(Counter(r["plan_status"] for r in records)),
        "external_api_calls": 0,
        "new_searches": 0,
        "inputs": [
            {"path": str(p), "sha256": file_sha(p)}
            for p in (
                ARCHIVE / "traces.jsonl",
                ARCHIVE / "workload.jsonl",
                ARCHIVE / "contexts.jsonl",
                ARCHIVE / "scores.jsonl",
                REFERENCES,
                PAGES,
                Path(__file__),
            )
        ],
    }
    write_json(
        output / "stage-diagnosis.json", {"summary": summary, "records": records}
    )
    print(json.dumps(summary["failure_stages"], ensure_ascii=False))
    print(summary["stage_complete"])


def rank_audit(rank_dir, output):
    from experimental.u_10o0o.context_evidence import _normalize

    rows = read_jsonl(Path(rank_dir) / "ranks.jsonl")
    replay_manifest = read_json(Path(rank_dir) / "manifest.json")
    if file_sha(Path(rank_dir) / "ranks.jsonl") != replay_manifest.get(
        "results_sha256"
    ):
        raise ValueError("Rank replay result fingerprint mismatch.")
    if replay_manifest.get("outcome") != "pass" or any(
        row["status"] != "archive_match" for row in rows
    ):
        raise ValueError(
            "Restore the archived retriever configuration before rank diagnosis."
        )
    grouped = {}
    for row in rows:
        grouped.setdefault(row["id"], []).append(row)
    stage_data = read_json(output / "stage-diagnosis.json")
    refs = load_references(REFERENCES)
    pages = {_source_key(p): p for p in read_json(PAGES)}
    archived = {r["id"]: r for r in read_jsonl(ARCHIVE / "workload.jsonl")}
    records = []
    for item in stage_data["records"]:
        if not item["candidate_failure"]:
            continue
        qid = item["id"]
        query_rows = grouped[qid]
        per_kind = {}
        for kind in ("fielded_top80", "dense_top1000"):
            ranks = {}
            for row in query_rows:
                for hit in row[kind]:
                    key = (hit["book_id"], hit["page"])
                    ranks[key] = min(ranks.get(key, 10**9), hit["rank"])
            per_kind[kind] = ranks

        def required_depth(requirement, ranks):
            depths = []
            for bundle in [
                requirement["primary"],
                *requirement.get("alternatives", []),
            ]:
                if not bundle:
                    continue
                values = []
                for cite in bundle:
                    key = _source_key(cite)
                    quote = _normalize(cite["quote"])
                    if (
                        not quote
                        or key not in ranks
                        or quote not in _normalize(pages[key]["text"])
                    ):
                        break
                    values.append(ranks[key])
                if len(values) == len(bundle):
                    depths.append(max(values))
            return min(depths) if depths else None

        reqs = []
        for number, requirement in enumerate(refs[qid]["evidence_requirements"], 1):
            reqs.append(
                {
                    "requirement": number,
                    "bm25_required_depth_within80": required_depth(
                        requirement, per_kind["fielded_top80"]
                    ),
                    "dense_required_depth_within1000": required_depth(
                        requirement, per_kind["dense_top1000"]
                    ),
                    "archived_stages": item["requirements"][number - 1]["covered"],
                }
            )
        replay_union = [
            pages[_source_key(hit)] for row in query_rows for hit in row["rrf_union40"]
        ]
        old_union = [
            pages[tuple(key)]
            for row in archived[qid]["query_stats"]
            for key in row["union"]
        ]
        replay_facts = fact_coverage(refs[qid], replay_union)
        old_facts = fact_coverage(refs[qid], old_union)
        records.append(
            {
                "id": qid,
                "first_miss": item["first_miss"],
                "all_query_orders_match": all(
                    r["status"] == "archive_match" for r in query_rows
                ),
                "replayed_union_fact_coverage_equal": replay_facts == old_facts,
                "requirements": reqs,
            }
        )
    write_json(
        output / "retriever-diagnosis.json",
        {
            "records": records,
            "rank_replay_manifest_sha256": file_sha(Path(rank_dir) / "manifest.json"),
            "scope": "Best required rank across original and executed requests; ranks beyond recorded80/1000 are unknown, not absent from corpus.",
            "candidate_failure_queries": len(records),
            "all_archived_fact_coverages_reproduced": all(
                r["replayed_union_fact_coverage_equal"] for r in records
            ),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rank-dir", type=Path)
    args = parser.parse_args()
    audit(args.output_dir)
    if args.rank_dir:
        rank_audit(args.rank_dir, args.output_dir)


if __name__ == "__main__":
    main()
