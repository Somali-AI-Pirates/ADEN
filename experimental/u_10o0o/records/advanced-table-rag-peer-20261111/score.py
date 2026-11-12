"""Score saved colleague retrievals only after both A/B runs complete."""

import re
import unicodedata
from collections import Counter, defaultdict
from statistics import mean

import numpy as np

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_search import PEER_AMBIGUOUS
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    object_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import official_metrics

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-peer-20261111/v2"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-peer-20261111"


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def load_completed():
    protocol = read_json(OUTPUT / "protocol.json")
    questions = read_json(OUTPUT / "questions.json")
    assert object_sha(questions) == protocol["questions_sha256"]
    saved = {}
    for arm in ("A", "B"):
        run = read_json(OUTPUT / arm / "run.json")
        assert run["status"] == "complete" and run["count"] == 304
        assert run["protocol_sha256"] == file_sha(OUTPUT / "protocol.json")
        rows = {}
        for path, sha in run["files"].items():
            p = OUTPUT / arm / path
            assert file_sha(p) == sha
            r = read_json(p)
            rows[r["id"]] = r
        assert set(rows) == {q["id"] for q in questions}
        saved[arm] = rows
    # This gate precedes parsing any reference labels.
    refs = {}
    for source in protocol["sources"]:
        p = ROOT / source["references"]
        assert file_sha(p) == source["references_sha256"]
        refs[source["split"]] = load_references(p)
    return protocol, questions, saved, refs


def score():
    protocol, questions, saved, refs = load_completed()
    known_ambiguous = set(read_json(PEER_AMBIGUOUS))
    rows = []
    private = []
    for q in questions:
        ref = refs[q["split"]][q["question_id"]]
        ev = ref.get("evidence_groups", [])
        docs = {e["book_id"] for g in ev for e in g}
        doc_count = len(docs)
        prefix = q["question_id"].split("-")[0]
        fp = prefix.startswith("SYF") or prefix == "FPA"
        item = {
            "id": q["id"],
            "question_id": q["question_id"],
            "split": q["split"],
            "required_documents": doc_count,
            "preexisting_ambiguous": q["split"] == "peer_two_doc"
            and q["question_id"] in known_ambiguous,
            "false_premise": fp,
            "question_sha256": object_sha(q["question"]),
            "arms": {},
        }
        for arm in ("A", "B"):
            r = saved[arm][q["id"]]
            assert r["question_sha256"] == item["question_sha256"]
            ctx = r["contexts"]["table_rag"]
            trace = r["traces"]["table_rag"]
            assert len(ctx) <= 8 and trace["context_tokens"] <= 8000
            metrics = {
                "initial": official_metrics(ref, trace["pool"], 20),
                "candidate": official_metrics(ref, trace["ranked"], 20),
                "final": official_metrics(ref, ctx, 8),
            }
            # Same quoted text on another page is a review lead, not an automatic pass.
            quote_groups = []
            for group in ev:
                hits = []
                for e in group:
                    quote = normalize(e.get("quote", ""))
                    if quote:
                        hits.extend(
                            {"book_id": p["book_id"], "page": p["page"]}
                            for p in ctx
                            if quote in normalize(p["text"])
                        )
                quote_groups.append(hits)
            item["arms"][arm] = {
                "metrics": metrics,
                "pages": [{"book_id": p["book_id"], "page": p["page"]} for p in ctx],
                "quote_group_hits": sum(bool(h) for h in quote_groups),
                "required_groups": len(ev),
                "latency": r["retrieval_seconds"],
                "api_calls": len(r["turns"]),
                "search_calls": len(r["searches"]),
                "sql_attempts": len(r["sql_attempts"]),
                "sql_kept": len(r["sql_results"]),
                "api_failures": sum(
                    t["api"]["status"] != "complete" for t in r["turns"]
                ),
                "finish_status": r["finish"]["status"],
            }
            if metrics["final"] and not metrics["final"]["all_groups"]:
                private.append(
                    {
                        "id": q["id"],
                        "arm": arm,
                        "question": q["question"],
                        "reference": ref,
                        "context": ctx,
                        "sql": r["sql_results"],
                        "quote_matches": quote_groups,
                    }
                )
        rows.append(item)
    grouped = defaultdict(list)
    for r in rows:
        names = [
            "all",
            r["split"],
            "false_premise" if r["false_premise"] else "normal",
            f"documents_{r['required_documents']}",
        ]
        if r["split"] == "peer_multi_doc":
            names.append(
                f"multi_{r['required_documents']}_"
                + ("false" if r["false_premise"] else "normal")
            )
        names.append(
            "preexisting_ambiguous"
            if r["preexisting_ambiguous"]
            else "excluding_preexisting_ambiguous"
        )
        for name in names:
            grouped[name].append(r)
    summary = {}
    for name, members in sorted(grouped.items()):
        summary[name] = {"count": len(members), "arms": {}}
        for arm in ("A", "B"):
            valid = [
                m["arms"][arm] for m in members if m["arms"][arm]["metrics"]["final"]
            ]
            a = {}
            for stage in ("initial", "candidate", "final"):
                a[stage] = (
                    {
                        "count": len(valid),
                        "complete": sum(
                            v["metrics"][stage]["all_groups"] for v in valid
                        ),
                        **{
                            key: mean(v["metrics"][stage][key] for v in valid)
                            for key in (
                                "page_recall",
                                "document_recall",
                                "group_recall",
                                "mrr",
                                "ndcg",
                            )
                        },
                    }
                    if valid
                    else {}
                )
            a["api_calls"] = sum(m["arms"][arm]["api_calls"] for m in members)
            a["search_calls"] = sum(m["arms"][arm]["search_calls"] for m in members)
            a["api_failures"] = sum(m["arms"][arm]["api_failures"] for m in members)
            a["latency_p50"] = float(
                np.percentile([m["arms"][arm]["latency"] for m in members], 50)
            )
            a["latency_p95"] = float(
                np.percentile([m["arms"][arm]["latency"] for m in members], 95)
            )
            summary[name]["arms"][arm] = a
    write_json(OUTPUT / "review-inputs.json", private)
    write_json(RECORDS / "per-question.json", rows)
    write_json(RECORDS / "strict-summary.json", summary)
    write_json(
        RECORDS / "score-manifest.json",
        {
            "references_read_after_both_runs": True,
            "runs": {a: file_sha(OUTPUT / a / "run.json") for a in ("A", "B")},
            "sources": protocol["sources"],
            "script_sha256": file_sha(__file__),
            "review_candidates": len(private),
            "preexisting_ambiguous_file": str(PEER_AMBIGUOUS.relative_to(ROOT)),
            "preexisting_ambiguous_sha256": file_sha(PEER_AMBIGUOUS),
        },
    )
    print(
        "Strict counts",
        {
            name: {
                a: summary[name]["arms"][a]["final"].get("complete") for a in ("A", "B")
            }
            for name in ("all", "peer_multi_doc", "documents_3", "documents_4")
            if name in summary
        },
        "failed arm-contexts",
        len(private),
        flush=True,
    )
    print("document_distribution", dict(Counter(r["required_documents"] for r in rows)))


if __name__ == "__main__":
    score()
