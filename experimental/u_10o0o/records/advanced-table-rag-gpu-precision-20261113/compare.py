"""Compare saved reranking runs and score reference evidence after replay."""

import argparse
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

import numpy as np
from benchmark import ARM_ORDER, BUNDLE, FROZEN

from app.evaluation.dataset import load_questions, load_references
from app.rag.corpus import load_corpus
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.pattern_retrieval.established_retrieve import question_sources
from experimental.u_10o0o.pattern_retrieval.established_score import official_metrics

BASE = "float32"
TRIAL = "bfloat16"


def read(path):
    return json.loads(path.read_bytes())


def stats(values):
    p50, p95 = np.percentile(values, [50, 95])
    return {"p50": float(p50), "p95": float(p95), "mean": float(np.mean(values))}


def collect(run, expected_ids):
    if tuple(session["dtype"] for session in run["sessions"]) != ARM_ORDER:
        raise ValueError("Missing or unexpected precision sessions")
    samples = {dtype: defaultdict(list) for dtype in (BASE, TRIAL)}
    for session in run["sessions"]:
        if len(session["samples"]) != 2 * len(expected_ids):
            raise ValueError("Incomplete session")
        for sample in session["samples"]:
            samples[session["dtype"]][sample["id"]].append(sample)
    for arm in samples.values():
        if set(arm) != expected_ids or any(len(rows) != 6 for rows in arm.values()):
            raise ValueError("Expected six measurements per question and precision")
    return samples


def score(suite, reference, pages):
    if suite == "negative_control":
        return {"status": "negative_control"}
    if suite.startswith("official_"):
        metrics = official_metrics(reference, pages)
        if metrics is None:
            return {"status": "no_page_reference"}
        return {
            "status": "scored",
            "complete": bool(metrics["all_groups"]),
            "matched": metrics["matched_groups"],
            "required": metrics["required_groups"],
            "page_recall": metrics["page_recall"],
        }
    metrics = fact_coverage(reference, pages)
    if not metrics["required_facts"]:
        raise ValueError("Expected fact requirements")
    return {
        "status": "scored",
        "complete": bool(metrics["all_facts"]),
        "matched": metrics["required_facts_hit"],
        "required": metrics["required_facts"],
    }


def compare(run_path, output):
    run = read(run_path)
    for name, digest in run["fingerprints"].items():
        if sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Replay fingerprint no longer matches {name}")
    frozen = {row["id"]: row for row in read(FROZEN)["rows"]}
    samples = collect(run, set(frozen))
    for question_id, row in frozen.items():
        units = {candidate["unit_id"] for candidate in row["candidates"]}
        for dtype in samples:
            arm = samples[dtype][question_id]
            for sample in arm:
                if len(sample["order"]) != 20 or set(sample["order"]) != units:
                    raise ValueError(f"Candidate set differs for {question_id}")
                if len(sample["final_pages"]) > 8 or sample["context_tokens"] > 8000:
                    raise ValueError(f"Context budget exceeded for {question_id}")
                for field in ("order", "final_pages", "context_sha256", "work"):
                    if sample[field] != arm[0][field]:
                        raise ValueError(f"Unstable repeat {field} for {question_id}")
            if arm[0]["work"] != samples[BASE][question_id][0]["work"]:
                raise ValueError(f"Different input tokens for {question_id}")
    paths = question_sources()
    needed = {question_id.split(":", 1)[0] for question_id in frozen}
    ref_digests = {}
    refs = {}
    for suite in needed:
        question_file, reference_file = paths[suite]
        questions = {
            row["id"]: row["question"] for row in load_questions(question_file)
        }
        for question_id, saved in frozen.items():
            row_suite, original_id = question_id.split(":", 1)
            if row_suite == suite and saved["question"] != questions[original_id]:
                raise ValueError(f"Question changed: {question_id}")
        refs[suite] = load_references(reference_file)
        ref_digests[str(reference_file.relative_to(Path.cwd()))] = sha256(
            reference_file.read_bytes()
        ).hexdigest()

    corpus = load_corpus(BUNDLE)
    try:
        comparisons = []
        for question_id in frozen:
            suite, original_id = question_id.split(":", 1)
            reference = refs[suite][original_id]
            first = samples[BASE][question_id][0]
            second = samples[TRIAL][question_id][0]
            before, after = first["final_pages"], second["final_pages"]
            a_scores = dict(zip(first["order"], first["scores"], strict=True))
            b_scores = dict(zip(second["order"], second["scores"], strict=True))
            evidence = {}
            for dtype, ids in ((BASE, before), (TRIAL, after)):
                pages = [
                    {
                        "book_id": corpus.pages_by_source[source].metadata["book_id"],
                        "page": corpus.pages_by_source[source].metadata["page"],
                        "text": corpus.pages_by_source[source].page_content,
                    }
                    for source in ids
                ]
                evidence[dtype] = score(suite, reference, pages)
            comparisons.append(
                {
                    "id": question_id,
                    "rank_order_changed": first["order"] != second["order"],
                    "moved_units": sum(
                        a != b
                        for a, b in zip(first["order"], second["order"], strict=True)
                    ),
                    "final_order_changed": before != after,
                    "final_set_changed": set(before) != set(after),
                    "context_changed": first["context_sha256"]
                    != second["context_sha256"],
                    "added_pages": sorted(set(after) - set(before)),
                    "removed_pages": sorted(set(before) - set(after)),
                    "final_pages": {BASE: before, TRIAL: after},
                    "max_score_delta": max(
                        abs(a_scores[key] - b_scores[key]) for key in a_scores
                    ),
                    "median_ms": {
                        dtype: float(
                            np.median([s["ms"] for s in samples[dtype][question_id]])
                        )
                        for dtype in (BASE, TRIAL)
                    },
                    "evidence": evidence,
                }
            )
    finally:
        corpus.close()

    timings = {}
    for dtype, rows in samples.items():
        sessions = [session for session in run["sessions"] if session["dtype"] == dtype]
        observations = [sample["ms"] for repeats in rows.values() for sample in repeats]
        timings[dtype] = {
            "samples": len(observations),
            "ms": stats(observations),
            "session_ms": [
                stats([sample["ms"] for sample in s["samples"]]) for s in sessions
            ],
            "peak_allocated_mib": max(s["peak_allocated_mib"] for s in sessions),
            "peak_reserved_mib": max(s["peak_reserved_mib"] for s in sessions),
        }
    summary = {
        "timing": timings,
        "changed": {
            key: sum(row[key] for row in comparisons)
            for key in (
                "rank_order_changed",
                "final_order_changed",
                "final_set_changed",
                "context_changed",
            )
        },
        "work_per_pass": {
            key: sum(rows[0]["work"][key] for rows in samples[BASE].values())
            for key in ("pairs", "raw_pair_tokens", "truncated_pairs")
        },
        "reference_sha256": ref_digests,
        "replay_sha256": sha256(run_path.read_bytes()).hexdigest(),
        "per_question": comparisons,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps({k: v for k, v in summary.items() if k != "per_question"}, indent=2)
    )
    for row in comparisons:
        print(
            row["id"],
            row["moved_units"],
            row["added_pages"],
            row["removed_pages"],
            row["evidence"],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    compare(args.replay, args.output)
