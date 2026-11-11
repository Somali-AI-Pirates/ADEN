"""Score saved A/baseline results using the unchanged established references."""

import argparse
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_full_metrics import fact_coverage

from .common import (
    ROOT,
    check_runtime,
    file_sha,
    object_sha,
    read_json,
    read_jsonl,
    write_json,
)


def page_key(row):
    return row.get("book_id") or Path(row["file_name"]).stem, int(row["page"])


def validate_saved(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest.get("status") != "complete" or manifest.get("trace_count") != 280:
        raise ValueError("All 280 retrievals must be saved before references are read.")
    if manifest.get("references_opened") is not False:
        raise ValueError("Retrieval/reference separation was not recorded.")
    for name in ("questions", "traces", "contexts"):
        extension = "json" if name == "questions" else "jsonl"
        if file_sha(directory / f"{name}.{extension}") != manifest[f"{name}_sha256"]:
            raise ValueError(f"Saved {name} fingerprint mismatch.")
    check_runtime(manifest["runtime_files"])
    for row in manifest["question_sources"] + manifest["inputs"]:
        if file_sha(ROOT / row["path"]) != row["sha256"]:
            raise ValueError("Input changed after retrieval.")
    traces = read_jsonl(directory / "traces.jsonl")
    contexts = read_jsonl(directory / "contexts.jsonl")
    ids = [r["id"] for r in manifest["items"]]
    questions = read_json(directory / "questions.json")
    if len(ids) != 280 or len(set(ids)) != 280:
        raise ValueError("Expected 280 unique input IDs.")
    if any([r["id"] for r in rows] != ids for rows in (traces, contexts, questions)):
        raise ValueError("Saved result identities/order do not match input.")
    for trace, context in zip(traces, contexts, strict=True):
        if trace["arm"] != manifest["arm"] or context["arm"] != manifest["arm"]:
            raise ValueError("Saved arm mismatch.")
        if (
            len(trace["pool"]) > 20
            or len(context["pages"]) > 8
            or trace["context_tokens"] > 8000
        ):
            raise ValueError("Retrieval budget exceeded.")
        keys = [page_key(row) for row in context["pages"]]
        if keys != [page_key(row) for row in trace["final_pages"]] or len(keys) != len(
            set(keys)
        ):
            raise ValueError("Actual context and final source list differ.")
    return (
        manifest,
        dict(zip(ids, traces, strict=True)),
        dict(zip(ids, contexts, strict=True)),
    )


def validate_pair(a_dir, baseline_dir):
    a = validate_saved(a_dir)
    baseline = validate_saved(baseline_dir)
    if a[0]["arm"] != "A" or baseline[0]["arm"] != "baseline":
        raise ValueError("Expected A and baseline runs.")
    for key in ("items", "questions_sha256", "question_sources", "suite_counts"):
        if a[0][key] != baseline[0][key]:
            raise ValueError("The two runs did not use identical questions.")
    return {"baseline": baseline, "A": a}


def page_metrics(expected, pages, cutoff=8):
    keys = list(dict.fromkeys(page_key(row) for row in pages))
    if not expected:
        return None
    relevant = [int(key in expected) for key in keys]
    hits = len(set(keys) & expected)
    dcg = sum(value / math.log2(rank + 2) for rank, value in enumerate(relevant))
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(expected), cutoff)))
    return {
        "required_pages": len(expected),
        "matched_pages": hits,
        "page_recall": hits / len(expected),
        "all_pages": int(hits == len(expected)),
        "hit": int(hits > 0),
        "mrr": next((1 / (i + 1) for i, value in enumerate(relevant) if value), 0.0),
        "ndcg": dcg / ideal if ideal else 0.0,
        "document_recall": len({k[0] for k in keys} & {k[0] for k in expected})
        / len({k[0] for k in expected}),
    }


def official_metrics(reference, pages, cutoff=8):
    groups = [
        {page_key(e) for e in group}
        for group in reference.get("evidence_groups", [])
        if group
    ]
    expected = set().union(*groups) if groups else set()
    result = page_metrics(expected, pages, cutoff)
    if result is not None:
        found = {page_key(row) for row in pages}
        matched = sum(bool(group & found) for group in groups)
        result.update(
            required_groups=len(groups),
            matched_groups=matched,
            group_recall=matched / len(groups),
            all_groups=int(matched == len(groups)),
        )
    return result


def category(reference, suite):
    if suite.startswith("official_"):
        return suite
    if suite == "scenarios":
        return f"S{int(reference['scenario']):02}"
    return f"{suite}/{reference['variant']}"


def score_item(meta, reference, trace, context, source_pages):
    initial = [source_pages[page_key(row)] for row in trace["pool"]]
    after_lookup = [
        source_pages[page_key(row)] for row in trace.get("after_hg_pool", trace["pool"])
    ]
    available = [source_pages[page_key(row)] for row in trace["ranked"]]
    stages = {
        "initial": initial,
        "after_hg": after_lookup,
        "available": available,
        "final": context["pages"],
    }
    official = meta["suite"].startswith("official_")
    row = {
        **meta,
        "category": category(reference, meta["suite"]),
        "arm": trace["arm"],
        "returned_pages": len(context["pages"]),
        "context_tokens": trace["context_tokens"],
        "lookup_added_final_pages": len(
            {page_key(p) for p in context["pages"]} - {page_key(p) for p in initial}
        ),
        "routes": trace.get("routes", {}),
        "stages": {},
    }
    if official:
        row["complex"] = (
            sum(bool(group) for group in reference.get("evidence_groups", [])) > 1
        )
        row["multi_page"] = (
            len(
                {
                    page_key(e)
                    for group in reference.get("evidence_groups", [])
                    for e in group
                }
            )
            > 1
        )
        for stage, pages in stages.items():
            row["stages"][stage] = official_metrics(
                reference, pages, 8 if stage == "final" else 20
            )
        row["complete_metric"] = "all_groups"
    elif meta["suite"] != "negative_control":
        expected = {
            page_key(e)
            for req in reference["evidence_requirements"]
            for e in req["primary"]
        }
        for stage, pages in stages.items():
            row["stages"][stage] = {
                **page_metrics(expected, pages, 8 if stage == "final" else 20),
                **fact_coverage(reference, pages),
            }
        row["complete_metric"] = "all_facts"
    else:
        row["complete_metric"] = None
    row["scored"] = bool(row["stages"].get("final"))
    if row["scored"]:
        key = row["complete_metric"]
        row["failure_stage"] = (
            "success"
            if row["stages"]["final"][key]
            else "selection_or_budget"
            if row["stages"]["available"][key]
            else "initial_retrieval_or_lookup"
        )
    else:
        row["failure_stage"] = "not_scored"
    return row


def source_maps(manifests):
    a_dir = ROOT / manifests["A"][0]["index_dir"]
    with sqlite3.connect(f"file:{a_dir / 'corpus.sqlite3'}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        a = {
            page_key(dict(row)): dict(row)
            for row in conn.execute("SELECT book_id,page,text FROM pages")
        }
    cache = next(
        item["path"]
        for item in manifests["baseline"][0]["inputs"]
        if item["path"].endswith("advanced_restored_pages.json")
    )
    baseline = {page_key(row): row for row in read_json(ROOT / cache)}
    return {"A": a, "baseline": baseline}


def summarize(rows):
    scored = [row for row in rows if row["scored"]]
    result = {
        "total": len(rows),
        "scored": len(scored),
        "excluded": len(rows) - len(scored),
        "mean_returned_pages": fmean(r["returned_pages"] for r in rows),
        "mean_context_tokens": fmean(r["context_tokens"] for r in rows),
        "stages": {},
    }
    if not scored:
        return result
    for stage in ("initial", "after_hg", "available", "final"):
        metrics = [row["stages"][stage] for row in scored]
        complete = sum(row["stages"][stage][row["complete_metric"]] for row in scored)
        result["stages"][stage] = {
            "complete": complete,
            "complete_rate": complete / len(scored),
            **{
                key: fmean(m[key] for m in metrics)
                for key in ("page_recall", "document_recall", "hit", "mrr", "ndcg")
            },
            "matched_pages": sum(m["matched_pages"] for m in metrics),
            "required_pages": sum(m["required_pages"] for m in metrics),
            "strict_all_pages": sum(m["all_pages"] for m in metrics),
        }
        if "required_facts" in metrics[0]:
            result["stages"][stage].update(
                required_facts=sum(m["required_facts"] for m in metrics),
                matched_facts=sum(m["required_facts_hit"] for m in metrics),
                fact_recall=fmean(m["fact_recall"] for m in metrics),
            )
    return result


def aggregates(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["category"]].append(row)
        if row["suite"].startswith("official_"):
            groups["official_all"].append(row)
            if row.get("complex"):
                groups["official_complex_groups"].append(row)
            if row.get("multi_page"):
                groups["official_multi_page"].append(row)
        else:
            groups["categories_all"].append(row)
            if row["suite"] != "negative_control":
                groups["categories_positive"].append(row)
            if row["suite"] == "semantic_queries":
                groups["semantic_all"].append(row)
            if row["suite"] == "work_rules":
                groups["work_rules_all"].append(row)
    return {group: summarize(group_rows) for group, group_rows in groups.items()}


def differences(by_arm):
    result = []
    for before, after in zip(by_arm["baseline"], by_arm["A"], strict=True):
        if before["id"] != after["id"]:
            raise ValueError("Score identity mismatch.")
        row = {
            k: before[k] for k in ("id", "suite", "question_id", "category", "scored")
        }
        row["status"] = "not_scored"
        if before["scored"]:
            metric = before["complete_metric"]
            b, a = before["stages"]["final"], after["stages"]["final"]
            delta = a[metric] - b[metric]
            row.update(
                status="improved"
                if delta > 0
                else "regressed"
                if delta < 0
                else "unchanged",
                baseline_complete=b[metric],
                A_complete=a[metric],
                baseline_initial=before["stages"]["initial"][metric],
                A_initial=after["stages"]["initial"][metric],
                page_recall_delta=a["page_recall"] - b["page_recall"],
                baseline_failure_stage=before["failure_stage"],
                A_failure_stage=after["failure_stage"],
            )
        result.append(row)
    return result


def score(a_dir, baseline_dir, audit_path, output):
    # This complete-result gate precedes every reference read.
    saved = validate_pair(a_dir, baseline_dir)
    audit = read_json(audit_path)
    expected = {s["references"] for s in saved["A"][0]["question_sources"]}
    fingerprints = audit["reference_file_fingerprints"]
    if expected != {item["path"] for item in fingerprints}:
        raise ValueError("Reference audit does not cover exactly these suites.")
    for row in fingerprints:
        if file_sha(ROOT / row["path"]) != row["sha256"]:
            raise ValueError("Audited references changed.")
    references = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in saved["A"][0]["question_sources"]
    }
    maps = source_maps(saved)
    by_arm = {}
    for arm, (manifest, traces, contexts) in saved.items():
        by_arm[arm] = [
            score_item(
                meta,
                references[meta["suite"]][meta["question_id"]],
                traces[meta["id"]],
                contexts[meta["id"]],
                maps[arm],
            )
            for meta in manifest["items"]
        ]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = {arm: aggregates(rows) for arm, rows in by_arm.items()}
    diff = differences(by_arm)
    write_json(output / "per-question.json", by_arm)
    write_json(output / "differences.json", diff)
    write_json(output / "summary.json", summary)
    write_json(
        output / "manifest.json",
        {
            "status": "complete",
            "references_read_after_both_retrievals": True,
            "retrievals": {
                arm: {
                    "directory": str(Path(d).resolve().relative_to(ROOT)),
                    "manifest_sha256": file_sha(Path(d) / "manifest.json"),
                    "native_config": saved[arm][0]["native_config"],
                    "inputs": saved[arm][0]["inputs"],
                    "code_sha256": saved[arm][0]["code_sha256"],
                }
                for arm, d in (("A", a_dir), ("baseline", baseline_dir))
            },
            "references": fingerprints,
            "audit_sha256": file_sha(audit_path),
            "score_code_sha256": file_sha(Path(__file__)),
            "scoring_helpers": [
                {"path": str(p.relative_to(ROOT)), "sha256": file_sha(p)}
                for p in (
                    ROOT / "experimental/u_10o0o/advanced_full_metrics.py",
                    ROOT / "experimental/u_10o0o/context_evidence.py",
                )
            ],
            "results_sha256": object_sha(
                {"summary": summary, "differences": diff, "per_question": by_arm}
            ),
        },
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    score(args.a_dir, args.baseline_dir, args.audit, args.output_dir)
    print("Saved common scores for official 60 + category 220.")


if __name__ == "__main__":
    main()
