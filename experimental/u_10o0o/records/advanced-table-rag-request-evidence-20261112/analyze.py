"""Reuse the established symmetric scorer/reviewer; all 116 outputs are reviewed."""

import argparse
from importlib import import_module
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v1"


def analyzer():
    m = import_module(
        "experimental.u_10o0o.records.advanced-table-rag-unified-20261112.analyze"
    )
    m.OUT, m.RECORDS = OUT, HERE
    scorer = import_module("experimental.u_10o0o.records." + HERE.name + ".score")
    scorer.telemetry = m.timing
    m.scorer = lambda: scorer
    return m


def report(m):
    m.report()
    summary = read_json(HERE / "reviewed-summary.json")
    write_json(
        HERE / "reviewed-summary.json", {k: v for k, v in summary.items() if v["n"]}
    )
    manifest = read_json(HERE / "review-manifest.json")
    manifest["scope"] = (
        "All 58 official questions in both arms; 116 actual returned contexts"
    )
    write_json(HERE / "review-manifest.json", manifest)
    _, saved, _, _ = m.scorer().load()
    scores = {
        a: {r["id"]: r for r in rows}
        for a, rows in read_json(HERE / "reviewed-per-question.json").items()
    }
    flows = []
    for rid in saved["A"]:
        row = {"id": rid, "arms": {}}
        for a in ("A", "B"):
            r, score = saved[a][rid], scores[a][rid]
            row["arms"][a] = {
                "strict_complete": score["strict_complete"],
                "reviewed_complete": score["reviewed_complete"],
                "stages": score["stages"],
                "all_search_union": score["all_search_union"],
                "final_sources": [
                    f"{p['book_id']}:p{p['page']:04d}"
                    for p in r["contexts"]["table_rag"]
                ],
                "selected_sources": r["finish"]["selected_sources"],
                "tools": [t.get("tool", t.get("status")) for t in r["turns"]],
                "selection_audit": r["selection_audit"],
                "requirement_count": len(r["finish"].get("requirements", [])),
                "finish": r["finish"]["status"],
                "seconds": r["retrieval_seconds"],
            }
        flows.append(row)
    write_json(HERE / "routing-per-question.json", flows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["score", "drafts", "report"])
    cmd = p.parse_args().command
    m = analyzer()
    if cmd == "score":
        m.scorer().main()
    elif cmd == "report":
        report(m)
    else:
        m.drafts()
