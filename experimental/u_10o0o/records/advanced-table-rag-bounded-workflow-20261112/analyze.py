"""Established scorer plus symmetric evidence review, after both runs are complete."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"


def analyzer():
    m = import_module(
        "experimental.u_10o0o.records.advanced-table-rag-unified-20261112.analyze"
    )
    m.OUT, m.RECORDS = OUT, HERE
    return m


def prepare():
    m = analyzer()
    m.prepare()
    qs, saved, refs, metadata = m.scorer().load()
    items = read_json(OUT / "review-inputs.json")
    keys = {(r["id"], r["arm"]) for r in items}
    for q in qs:
        meta = metadata[q["id"]]
        if not meta["suite"].startswith("official_"):
            continue
        for arm in ("A", "B"):
            if (q["id"], arm) in keys:
                continue
            row = saved[arm][q["id"]]
            items.append(
                {
                    "id": q["id"],
                    "arm": arm,
                    "question": q["question"],
                    "reference": refs[meta["suite"]][meta["question_id"]],
                    "context": row["contexts"]["table_rag"],
                    "sql": row["sql_results"],
                }
            )
    write_json(OUT / "review-inputs.json", items)
    manifest = read_json(HERE / "score-manifest.json")
    manifest.update(
        review_count=len(items),
        review_policy="All official contexts plus both arms of every category strict miss and prior ambiguity",
    )
    write_json(HERE / "score-manifest.json", manifest)
    print({"review_outputs": len(items)})


def review():
    m = import_module(
        "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111.review"
    )
    m.OUTPUT, m.RECORDS = OUT, HERE
    m.ThreadPoolExecutor = lambda **kwargs: ThreadPoolExecutor(max_workers=8)
    m.main()


def report():
    m = analyzer()
    from importlib import import_module

    import_module(
        "experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.finalize"
    ).report(m)
    _, saved, _, _ = m.scorer().load()
    flows = []
    for rid in saved["A"]:
        flows.append(
            {
                "id": rid,
                "arms": {
                    arm: {
                        "status": rows[rid]["finish"]["status"],
                        "selected_sources": rows[rid]["finish"]["selected_sources"],
                        "seconds": rows[rid]["retrieval_seconds"],
                        "calls": len(rows[rid]["turns"]),
                        "tools": [t.get("tool") for t in rows[rid]["turns"]],
                        "fast_path": rows[rid]["selection_audit"].get(
                            "fast_path", False
                        ),
                        "request_count": len(
                            (rows[rid]["selection_audit"].get("plan") or {}).get(
                                "requests", []
                            )
                        ),
                        "coverage": [
                            {k: v for k, v in c.items() if k != "missing_query"}
                            for c in rows[rid]["finish"].get("coverage", [])
                        ],
                        "search_requests": [
                            s.get("request_ids", []) for s in rows[rid]["searches"]
                        ],
                        "search_kinds": [s["kind"] for s in rows[rid]["searches"]],
                        "errors": rows[rid]["selection_audit"].get("errors", []),
                    }
                    for arm, rows in saved.items()
                },
            }
        )
    write_json(HERE / "routing-per-question.json", flows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "review", "drafts", "report"])
    command = p.parse_args().command
    if command == "drafts":
        analyzer().drafts()
    else:
        globals()[command]()
