"""Reassemble the same saved v2 A searches without any new model calls."""

from collections import Counter
from .score import load, RECORDS
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json
from experimental.u_10o0o.pattern_retrieval.established_score import (
    page_key,
    score_item,
    official_metrics,
)
from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id
from experimental.u_10o0o.advanced_is_lookup_ab import context_pages


def main():
    _, saved, meta, refs = load()
    pages = {
        page_key(p): p
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    retriever = object.__new__(Retriever)
    output = []
    for rid, result in saved["v2_A"].items():

        def expand(rows):
            return [(pages[(p["book_id"], p["page"])], p["score"]) for p in rows]

        groups = [expand(result["searches"][0]["ranked"])]
        index = 1
        sql_index = 0
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
        old_rank, old_ctx, old_sql = retriever.context(
            groups, selected, result["sql_attempts"]
        )
        assert (
            old_ctx.context_hash == result["traces"]["table_rag"]["context_sha256"]
        ), rid
        assert old_sql == result["sql_results"], rid
        new_rank, new_ctx, new_sql = retriever.context(
            groups, selected, result["sql_attempts"], preserve_evidence=True
        )
        old_pages = context_pages(old_ctx)
        new_pages = context_pages(new_ctx)
        old_ids = {source_id(p) for p in old_pages}
        new_ids = {source_id(p) for p in new_pages}
        observed = {source_id(p) for g in groups for p, _ in g}
        m = meta[rid]
        row = {
            "id": rid,
            "cohort": m["cohort"],
            "legacy_reproduced": True,
            "old_not_retained": sorted((set(selected) & observed) - old_ids),
            "new_not_retained": sorted((set(selected) & observed) - new_ids),
            "changed": old_ids != new_ids,
        }
        if m["cohort"] == "core":
            scores = []
            for ctx, ranked in [(old_pages, old_rank), (new_pages, new_rank)]:
                trace = {
                    **result["traces"]["table_rag"],
                    "ranked": [_source_info(p, v) for p, v in ranked],
                }
                item = score_item(m, refs[rid], trace, {"pages": ctx}, pages)
                if item["scored"]:
                    scores.append(int(item["stages"]["final"][item["complete_metric"]]))
            if scores:
                row.update(old_complete=scores[0], new_complete=scores[1])
        elif m["kind"] != "clarification":
            row.update(
                old_complete=int(
                    official_metrics(refs[rid], old_pages, 8)["all_groups"]
                ),
                new_complete=int(
                    official_metrics(refs[rid], new_pages, 8)["all_groups"]
                ),
            )
        output.append(row)
    write_json(RECORDS / "same-search-replay.json", output)
    for cohort in ("core", "peer"):
        rs = [r for r in output if r["cohort"] == cohort]
        print(
            cohort,
            len(rs),
            "selected loss",
            sum(len(r["old_not_retained"]) for r in rs),
            "->",
            sum(len(r["new_not_retained"]) for r in rs),
            "strict delta",
            dict(
                Counter(
                    r["new_complete"] - r["old_complete"]
                    for r in rs
                    if "old_complete" in r
                )
            ),
        )


if __name__ == "__main__":
    main()
