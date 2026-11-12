"""Diagnose frozen peer retrievals without rerunning search or changing labels."""

from collections import Counter
from importlib import import_module

from experimental.u_10o0o.pattern_retrieval.common import write_json
from experimental.u_10o0o.pattern_retrieval.established_score import official_metrics

score = import_module(__package__ + ".score")


def main():
    _, questions, saved, refs = score.load_completed()
    output = []
    for q in questions:
        ref = refs[q["split"]][q["question_id"]]
        row = {"id": q["id"], "arms": {}}
        for arm in ("A", "B"):
            r = saved[arm][q["id"]]
            by_page = {}
            for search in r["searches"]:
                for p in search["ranked"]:
                    by_page.setdefault((p["book_id"], p["page"]), p)
            union = list(by_page.values())
            trace = r["traces"]["table_rag"]
            final = r["contexts"]["table_rag"]
            stages = {
                "initial20": official_metrics(ref, trace["pool"], 20),
                "all_search_union": official_metrics(ref, union, len(union)),
                "merged20": official_metrics(ref, trace["ranked"], 20),
                "final8": official_metrics(ref, final, 8),
            }
            stage = (
                "strict_pass"
                if stages["final8"]["all_groups"]
                else "final_selection_or_budget"
                if stages["merged20"]["all_groups"]
                else "merge_to20"
                if stages["all_search_union"]["all_groups"]
                else "retrieval_or_lookup"
            )
            row["arms"][arm] = {
                "stages": stages,
                "strict_failure_stage": stage,
                "union_pages": len(union),
                "context_tokens": trace["context_tokens"],
                "final_pages": len(final),
                "original_query_truncated": r["searches"][0]
                .get("query_input", {})
                .get("truncated"),
                "query_truncations": (
                    sum(s["query_input"]["truncated"] for s in r["searches"])
                    if all("query_input" in s for s in r["searches"])
                    else None
                ),
                "rerank_pairs": sum(
                    s.get("work", {}).get("rerank_pairs", 0) for s in r["searches"]
                ),
                "rerank_truncated_pairs": sum(
                    s.get("work", {}).get("truncated_pairs", 0) for s in r["searches"]
                ),
                "api_input_tokens": sum(
                    t["api"].get("input_tokens", 0) for t in r["turns"]
                ),
                "api_output_tokens": sum(
                    t["api"].get("output_tokens", 0) for t in r["turns"]
                ),
                "api_cached_tokens": sum(
                    t["api"].get("cached_input_tokens", 0) for t in r["turns"]
                ),
                "api_seconds": sum(t["api"]["seconds"] for t in r["turns"]),
                "search_seconds": sum(s["seconds"] for s in r["searches"]),
            }
        output.append(row)
    write_json(score.RECORDS / "stage-diagnosis.json", output)
    for arm in ("A", "B"):
        print(
            arm, dict(Counter(r["arms"][arm]["strict_failure_stage"] for r in output))
        )


if __name__ == "__main__":
    main()
