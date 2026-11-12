"""Replay saved A searches to isolate candidate packing from LLM variation."""

from collections import Counter
from pathlib import Path
from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json
from experimental.u_10o0o.pattern_retrieval.established_score import (
    page_key,
    score_item,
)
from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id
from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
from .score import load


def main():
    _, saved, refs, metadata = load()
    pages = {
        page_key(p): p
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    retriever = object.__new__(Retriever)
    output = []
    for rid, result in saved["A"].items():

        def expand(rows):
            return [(pages[(r["book_id"], r["page"])], r["score"]) for r in rows]

        groups = [expand(result["searches"][0]["ranked"])]
        index, sql_index = 1, 0
        for turn in result["turns"]:
            if turn.get("status") != "executed":
                continue
            groups.append(expand(result["searches"][index]["ranked"]))
            index += 1
            if turn["arguments"]["sql"] is not None:
                sql = result["sql_attempts"][sql_index]
                sql_index += 1
                if sql["status"] == "ok":
                    groups.append(
                        [
                            (pages[(p["book_id"], p["page"])], 1.0)
                            for p in sql["sources"]
                        ]
                    )
        assert index == len(result["searches"]) and sql_index == len(
            result["sql_attempts"]
        )
        selected = result["finish"]["selected_sources"]
        old_rank, old_context, old_sql = retriever.context(
            groups, selected, result["sql_attempts"]
        )
        assert (
            old_context.context_hash == result["traces"]["table_rag"]["context_sha256"]
        ), rid
        assert old_sql == result["sql_results"], rid
        new_rank, new_context, new_sql = retriever.context(
            groups, selected, result["sql_attempts"], preserve_evidence=True
        )
        actual_old = {source_id(p) for p in context_pages(old_context)}
        actual_new = {source_id(p) for p in context_pages(new_context)}
        observed = {source_id(p) for g in groups for p, _ in g}
        m = metadata[rid]
        row = {
            "id": rid,
            "suite": m["suite"],
            "legacy_reproduced": True,
            "unobserved_selected": sorted(set(selected) - observed),
            "old_not_retained": sorted((set(selected) & observed) - actual_old),
            "new_not_retained": sorted((set(selected) & observed) - actual_new),
            "changed": actual_old != actual_new,
        }
        ref = refs[m["suite"]][m["question_id"]]
        trace = {**result["traces"]["table_rag"], "arm": "replay"}
        old = score_item(m, ref, trace, {"pages": context_pages(old_context)}, pages)
        new = score_item(m, ref, trace, {"pages": context_pages(new_context)}, pages)
        row["category"] = old["category"]
        if old["scored"]:
            metric = old["complete_metric"]
            row.update(
                old_metrics=old["stages"]["final"],
                new_metrics=new["stages"]["final"],
                old_complete=old["stages"]["final"][metric],
                new_complete=new["stages"]["final"][metric],
            )
        output.append(row)
    write_json(Path(__file__).parent / "same-search-replay.json", output)
    print(
        "Reproduced",
        len(output),
        "changed",
        sum(r["changed"] for r in output),
        "selected losses",
        sum(len(r["old_not_retained"]) for r in output),
        "->",
        sum(len(r["new_not_retained"]) for r in output),
    )
    print(
        "Complete delta",
        dict(
            Counter(
                r["new_complete"] - r["old_complete"]
                for r in output
                if "old_complete" in r
            )
        ),
    )


if __name__ == "__main__":
    main()
