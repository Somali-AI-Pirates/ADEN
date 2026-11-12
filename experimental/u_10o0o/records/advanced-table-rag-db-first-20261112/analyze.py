"""Reuse symmetric evidence scoring; add DB-first routing and paired latency."""

import argparse
from importlib import import_module
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v1"
PARENT = "experimental.u_10o0o.records.advanced-table-rag-unified-20261112.analyze"


def analyzer():
    module = import_module(PARENT)
    module.OUT, module.RECORDS = OUT, HERE
    original = module.timing

    def timing(rows):
        result = original(rows)
        docs = [
            [s for s in r["searches"] if s.get("kind", "documents") == "documents"]
            for r in rows
        ]
        db_start = [r["searches"][0].get("kind") == "records" for r in rows]
        result.update(
            initial_db_questions=sum(db_start),
            zero_hybrid_questions=sum(not s for s in docs),
            db_then_hybrid_questions=sum(
                db and bool(s) for db, s in zip(db_start, docs, strict=True)
            ),
        )
        return result

    module.timing = timing
    return module


def routing(module):
    _, saved, _, _ = module.scorer().load()
    scores = read_json(HERE / "reviewed-per-question.json")
    indexed = {a: {r["id"]: r for r in rows} for a, rows in scores.items()}
    eligible = {
        rid
        for rid, r in saved["B"].items()
        if r["searches"][0].get("kind") == "records"
    }
    assert all(
        s.get("kind") != "records"
        for r in saved["A"].values()
        for s in r["searches"][:1]
    )
    assert all(
        s["hg_route"] == "native_hg_disabled"
        for rows in saved.values()
        for r in rows.values()
        for s in r["searches"]
        if s.get("kind") == "documents"
    )
    summary, per_question = {}, []
    for name, ids in {
        "unique_code_db_start": sorted(eligible),
        "same_search_start": sorted(set(saved["A"]) - eligible),
        "zero_hybrid_in_B": sorted(
            rid
            for rid, r in saved["B"].items()
            if not any(s.get("kind") == "documents" for s in r["searches"])
        ),
        "all": sorted(saved["A"]),
    }.items():
        group = {"n": len(ids)}
        for a in ("A", "B"):
            items = [indexed[a][rid] for rid in ids if indexed[a][rid]["scored"]]
            group[a] = {
                "positive_n": len(items),
                "strict": sum(r["strict_complete"] for r in items),
                "reviewed": sum(r["reviewed_complete"] for r in items),
                "sufficient_but_incomplete": sum(
                    saved[a][r["id"]]["finish"]["status"] == "sufficient"
                    and not r["reviewed_complete"]
                    for r in items
                ),
                "timing": module.timing([saved[a][rid] for rid in ids]),
            }
        summary[name] = group
    for rid in saved["A"]:
        row = {"id": rid, "db_start_eligible": rid in eligible, "arms": {}}
        for a in ("A", "B"):
            r, score = saved[a][rid], indexed[a][rid]
            row["arms"][a] = {
                "initial_kind": r["searches"][0].get("kind", "documents"),
                "hybrid_calls": sum(
                    s.get("kind") == "documents" for s in r["searches"]
                ),
                "record_calls": sum(s.get("kind") == "records" for s in r["searches"]),
                "api_calls": len(r["turns"]),
                "seconds": r["retrieval_seconds"],
                "finish": r["finish"]["status"],
                "scored": score["scored"],
                "strict": score.get("strict_complete"),
                "reviewed": score.get("reviewed_complete"),
                "final_sources": [
                    f"{p['book_id']}:p{p['page']:04d}"
                    for p in r["contexts"]["table_rag"]
                ],
                "selected_sources": r["finish"]["selected_sources"],
                "tools": [t.get("tool", t.get("status")) for t in r["turns"]],
            }
        per_question.append(row)
    controls = read_json(HERE / "controls.json")
    controls.update(
        equal_prompt_and_tools=read_json(OUT / "protocol.json")["prompts"]["A"]
        == read_json(OUT / "protocol.json")["prompts"]["B"],
        unchanged_start_questions=278 - len(eligible),
        unchanged_start_candidates_equal=sum(
            saved["A"][rid]["searches"][0]["unit_candidates"]
            == saved["B"][rid]["searches"][0]["unit_candidates"]
            for rid in saved["A"]
            if rid not in eligible
        ),
    )
    write_json(HERE / "controls.json", controls)
    write_json(HERE / "routing-summary.json", summary)
    write_json(HERE / "routing-per-question.json", per_question)
    print(
        {
            "unique_code_db_start": len(eligible),
            "zero_hybrid": summary["all"]["B"]["timing"]["zero_hybrid_questions"],
        }
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "drafts", "report"])
    cmd = p.parse_args().command
    m = analyzer()
    getattr(m, cmd)()
    if cmd == "report":
        routing(m)
