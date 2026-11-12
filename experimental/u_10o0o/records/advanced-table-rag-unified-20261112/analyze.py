"""Post-run scoring and symmetric evidence audit for unified TableRAG."""

import argparse
from collections import Counter
from hashlib import sha256
from importlib import import_module
from pathlib import Path
import re
import unicodedata
import numpy as np
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.table_rag.retrieval import json_payload

RECORDS = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / RECORDS.name / "v3"
BASE = "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111"


def scorer():
    m = import_module(BASE + ".score")
    m.OUT, m.RECORDS = OUT, RECORDS
    m.telemetry = timing
    return m


def prepare():
    m = scorer()
    m.main()
    qs, saved, refs, meta = m.load()
    questions = {q["id"]: q for q in qs}
    items = read_json(OUT / "review-inputs.json")
    keys = {(r["id"], r["arm"]) for r in items}
    old = read_json(ROOT / BASE.replace(".", "/") / "adjudications.json")
    ambiguous = {r["id"] for r in old if r["verdict"] == "ambiguous_scope"}
    added = []
    for rid in sorted(ambiguous):
        for arm in ("A", "B"):
            if (rid, arm) in keys:
                continue
            row, metadata = saved[arm][rid], meta[rid]
            items.append(
                {
                    "id": rid,
                    "arm": arm,
                    "question": questions[rid]["question"],
                    "reference": refs[metadata["suite"]][metadata["question_id"]],
                    "context": row["contexts"]["table_rag"],
                    "sql": row["sql_results"],
                }
            )
            added.append([rid, arm])
    write_json(OUT / "review-inputs.json", items)
    manifest = read_json(RECORDS / "score-manifest.json")
    manifest.update(review_count=len(items), previously_ambiguous_added=added)
    write_json(RECORDS / "score-manifest.json", manifest)
    print({"review_outputs": len(items), "prior_ambiguity_additions": len(added)})


def evidence(row):
    return {
        **{
            f"{p['book_id']}:p{p['page']:04d}": p["text"]
            for p in row["contexts"]["table_rag"]
        },
        **{f"SQL{i}": json_payload(s) for i, s in enumerate(row["sql_results"])},
    }


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def drafts():
    _, saved, _, _ = scorer().load()
    run = read_json(OUT / "evidence-review/run.json")
    assert run["status"] == "complete"
    rows = []
    for filename, digest in run["files"].items():
        path = OUT / "evidence-review" / filename
        assert file_sha(path) == digest
        audit = read_json(path)
        row = saved[audit["arm"]][audit["id"]]
        result = audit["review"]
        rows.append(
            {
                "id": audit["id"],
                "arm": audit["arm"],
                "verdict": result["verdict"] if result else "uncertain",
                "reason": result["reason"]
                if result
                else "Model review incomplete; local review required",
                "claims": result["claims"] if result else [],
                "scope_ok": result["scope_ok"] if result else False,
                "source_hashes": {
                    k: sha256(v.encode()).hexdigest() for k, v in evidence(row).items()
                },
                "model_review_valid": audit["eligible_for_assistant_review"],
                "assistant_checked": False,
                "review_type": "AI evidence review plus assistant adjudication; no independent human review",
            }
        )
    if (OUT / "adjudications.json").exists():
        raise FileExistsError("Do not overwrite adjudications")
    write_json(OUT / "adjudications.json", rows)
    print(
        {
            "drafts": len(rows),
            "verdicts": dict(Counter(r["verdict"] for r in rows)),
            "model_errors": sum(not r["model_review_valid"] for r in rows),
        }
    )


def quantile(values):
    return (
        {f"p{q}": float(np.percentile(values, q)) for q in (50, 95)}
        if values
        else {"p50": 0, "p95": 0}
    )


def timing(rows):
    turns = [t["api"] for r in rows for t in r["turns"]]
    searches = [s for r in rows for s in r["searches"]]
    hybrid = [s for s in searches if s.get("kind", "documents") == "documents"]
    records = [s for s in searches if s.get("kind") == "records"]
    tables = [s for s in searches if s.get("kind") == "tables"]
    sql = [s for r in rows for s in r["sql_attempts"]]
    return {
        "n": len(rows),
        "end_to_end": quantile([r["retrieval_seconds"] for r in rows]),
        "api_per_question": quantile(
            [sum(t["api"]["seconds"] for t in r["turns"]) for r in rows]
        ),
        "hybrid_per_question": quantile(
            [
                sum(
                    s["seconds"]
                    for s in r["searches"]
                    if s.get("kind", "documents") == "documents"
                )
                for r in rows
            ]
        ),
        "record_lookup_per_call": quantile([s["seconds"] for s in records]),
        "table_discovery_per_call": quantile([s["seconds"] for s in tables]),
        "planner_calls": len(turns),
        "hybrid_search_calls": len(hybrid),
        "record_lookup_calls": len(records),
        "table_discovery_calls": len(tables),
        "tools": dict(
            Counter(t.get("tool", "provider_failure") for r in rows for t in r["turns"])
        ),
        "tool_statuses": dict(
            Counter(t.get("status", "unknown") for r in rows for t in r["turns"])
        ),
        "record_resolution": dict(
            Counter(t["status"] for s in records for t in s["resolution"]["targets"])
        ),
        "sql_calls": len(sql),
        "sql_ok": sum(s["status"] == "ok" for s in sql),
        "sql_kept": sum(len(r["sql_results"]) for r in rows),
        "finish": dict(Counter(r["finish"]["status"] for r in rows)),
        "api_status": dict(Counter(t["status"] for t in turns)),
        "usage": {
            k: sum(t.get(k, 0) for t in turns)
            for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        },
    }


def report():
    _, saved, _, _ = scorer().load()
    scores = read_json(RECORDS / "per-question.json")
    items = read_json(OUT / "review-inputs.json")
    decisions = read_json(OUT / "adjudications.json")
    reviews = {(r["id"], r["arm"]): r for r in decisions}
    assert len(reviews) == len(decisions)
    assert set(reviews) == {(r["id"], r["arm"]) for r in items}
    for (rid, arm), review in reviews.items():
        assert review["assistant_checked"] is True, (rid, arm, "unfinished")
        assert review["verdict"] in {"sufficient", "insufficient", "ambiguous_question"}
        sources = evidence(saved[arm][rid])
        assert {
            k: sha256(v.encode()).hexdigest() for k, v in sources.items()
        } == review["source_hashes"]
        for claim in review["claims"]:
            if claim["status"] == "supported":
                assert claim["evidence"], (rid, arm, "missing proof")
            for proof in claim["evidence"]:
                assert proof["source_id"] in sources
                assert len(norm(proof["quote"])) >= 8
                assert norm(proof["quote"]) in norm(sources[proof["source_id"]]), (
                    rid,
                    arm,
                    "invalid quote",
                )
        if review["verdict"] == "sufficient":
            assert review["scope_ok"] and review["claims"]
            assert all(c["status"] == "supported" for c in review["claims"])
    indexed = {a: {r["id"]: r for r in rows} for a, rows in scores.items()}
    for arm, rows in scores.items():
        for row in rows:
            if not row["scored"]:
                continue
            strict = bool(row["stages"]["final"][row["complete_metric"]])
            review = reviews.get((row["id"], arm))
            assert strict or review is not None
            row.update(
                strict_complete=strict,
                reviewed_complete=(review["verdict"] == "sufficient")
                if review
                else strict,
                review_verdict=review["verdict"]
                if review
                else "strict_pass_not_reviewed",
            )
    first = scores["A"]
    groups = {
        "official_all": [r["id"] for r in first if r["suite"].startswith("official_")],
        "categories_positive": [
            r["id"]
            for r in first
            if r["scored"] and not r["suite"].startswith("official_")
        ],
        **{
            f"S{i:02}": [r["id"] for r in first if r["category"] == f"S{i:02}"]
            for i in range(1, 8)
        },
        "work_rules_all": [r["id"] for r in first if r["suite"] == "work_rules"],
        **{
            name: [
                r["id"]
                for r in first
                if r["suite"] == "semantic_queries"
                and lo <= int(r["question_id"].rsplit("-", 1)[-1]) <= hi
            ]
            for name, lo, hi in [("semantic_single", 1, 20), ("semantic_multi", 21, 40)]
        },
    }
    summary, changes = {}, []
    for name, ids in groups.items():
        group = {"n": len(ids)}
        for arm in ("A", "B"):
            rows = [indexed[arm][rid] for rid in ids]
            group[arm] = {
                "strict": sum(r["strict_complete"] for r in rows),
                "reviewed": sum(r["reviewed_complete"] for r in rows),
                "candidate": sum(
                    r["stages"]["available"][r["complete_metric"]] for r in rows
                ),
                "all_query_candidate_complete": sum(
                    r["all_search_union"][r["complete_metric"]] for r in rows
                ),
                "page_recall": float(
                    np.mean([r["stages"]["final"]["page_recall"] for r in rows])
                ),
                "timing": timing([saved[arm][rid] for rid in ids]),
            }
        group["improved"] = sum(
            indexed["B"][rid]["reviewed_complete"]
            > indexed["A"][rid]["reviewed_complete"]
            for rid in ids
        )
        group["regressed"] = sum(
            indexed["B"][rid]["reviewed_complete"]
            < indexed["A"][rid]["reviewed_complete"]
            for rid in ids
        )
        summary[name] = group
    for row in first:
        if not row["scored"]:
            continue
        rid = row["id"]
        left, right = indexed["A"][rid], indexed["B"][rid]
        if left["reviewed_complete"] != right["reviewed_complete"]:
            changes.append(
                {
                    "id": rid,
                    "category": row["category"],
                    "current": left["reviewed_complete"],
                    "unified": right["reviewed_complete"],
                    "reason_current": reviews.get((rid, "A"), {}).get(
                        "public_reason", "지정 근거 충족 또는 로컬 검수 사유 참조"
                    ),
                    "reason_unified": reviews.get((rid, "B"), {}).get(
                        "public_reason", "지정 근거 충족 또는 로컬 검수 사유 참조"
                    ),
                }
            )
    controls = {
        "n": len(saved["A"]),
        "initial_raw_candidates_equal": sum(
            saved["A"][rid]["searches"][0]["unit_candidates"]
            == saved["B"][rid]["searches"][0]["unit_candidates"]
            for rid in saved["A"]
        ),
        "unified_document_searches_native_hg_disabled": all(
            s["hg_route"] == "native_hg_disabled"
            for r in saved["B"].values()
            for s in r["searches"]
            if s.get("kind") == "documents"
        ),
        "same_context_hash": sum(
            saved["A"][rid]["traces"]["table_rag"]["context_sha256"]
            == saved["B"][rid]["traces"]["table_rag"]["context_sha256"]
            for rid in saved["A"]
        ),
    }
    outputs = {
        "reviewed-per-question.json": scores,
        "reviewed-summary.json": summary,
        "reviewed-differences.json": changes,
        "controls.json": controls,
        "timing.json": {
            arm: timing(list(rows.values())) for arm, rows in saved.items()
        },
        "negative-control.json": {
            arm: timing([r for r in rows.values() if r["split"] == "negative_control"])
            for arm, rows in saved.items()
        },
        "review-manifest.json": {
            "reviewed_outputs": len(decisions),
            "all_required_checked": True,
            "adjudications_sha256": file_sha(OUT / "adjudications.json"),
            "review_inputs_sha256": file_sha(OUT / "review-inputs.json"),
            "independent_human_review": False,
            "source_quote_hash_checks": True,
            "scope": "Every strict failure and comparison output, plus prior ambiguity; remaining strict passes inherited",
        },
    }
    for filename, value in outputs.items():
        write_json(RECORDS / filename, value)
    print(
        {
            k: {
                "n": s["n"],
                "current": s["A"]["reviewed"],
                "unified": s["B"]["reviewed"],
            }
            for k, s in summary.items()
        }
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "drafts", "report"])
    globals()[p.parse_args().command]()
