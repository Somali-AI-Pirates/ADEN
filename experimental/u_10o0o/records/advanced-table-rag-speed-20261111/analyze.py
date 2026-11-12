"""Analyze completed replay; use fresh repeated trials for latency conclusions."""

import argparse
from pathlib import Path
import numpy as np

from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
)

DEST = Path(__file__).resolve().parent


def percentiles(values):
    return {
        "n": len(values),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
    }


def stage_means(rows):
    searches = [s for r in rows for s in r["searches"]]
    keys = sorted({k for s in searches for k in s["stage_seconds"]})
    return {
        k: sum(s["stage_seconds"].get(k, 0) for s in searches) / len(searches)
        for k in keys
    }


def analyze(directory):
    record = read_json(directory / "run.json")
    assert record["status"] == "complete"
    for name, sha in record["files"].items():
        assert file_sha(directory / name) == sha
    protocol = read_json(directory / "protocol.json")
    preflight = read_json(directory / "preflight.json")
    rows = {
        a: read_json(directory / f"quality-{a}.json") for a in preflight["supported"]
    }
    arows = {r["id"]: r for r in rows["A"]}
    output = {}
    for arm, items in rows.items():
        differences = []
        ranked_identical = True
        for row in items:
            base = arows[row["id"]]
            for current, original in zip(
                row["searches"], base["searches"], strict=True
            ):
                same = current["signature_sha256"] == original["signature_sha256"]
                ranked_identical &= same
                if same:
                    differences.extend(
                        abs(x - y)
                        for x, y in zip(
                            current["scores"], original["scores"], strict=True
                        )
                    )
        output[arm] = {
            "questions": len(items),
            "searches": sum(len(r["searches"]) for r in items),
            "identical_search_signatures_to_A": ranked_identical,
            "score_max_abs_difference_to_A": max(differences, default=0),
            "score_values_changed": sum(x != 0 for x in differences),
            "score_values_compared": len(differences),
            "saved_search_identical": sum(
                s["same_saved_search"] for r in items for s in r["searches"]
            ),
            "saved_context_identical": sum(r["same_saved_context"] for r in items),
            "SQL_values_and_provenance_identical": all(
                r["sql_equal"] and r["same_saved_sql"] for r in items
            ),
        }
    timing = read_json(directory / "timing.json")
    repeated = {}
    for workers in (1, 4):
        repeated[str(workers)] = {}
        avals = [
            r["seconds"]
            for trial in timing
            if trial["workers"] == workers and trial["arm"] == "A"
            for r in trial["rows"]
        ]
        for arm in rows:
            trials = [t for t in timing if t["workers"] == workers and t["arm"] == arm]
            values = [r for trial in trials for r in trial["rows"]]
            distribution = percentiles([r["seconds"] for r in values])
            repeated[str(workers)][arm] = {
                **distribution,
                "p50_reduction_vs_A": 1
                - distribution["p50"] / float(np.percentile(avals, 50)),
                "p95_reduction_vs_A": 1
                - distribution["p95"] / float(np.percentile(avals, 95)),
                "mean_stage_seconds_per_search": stage_means(values),
                "rounds": [
                    {
                        "repeat": t["repeat"],
                        "wall_seconds": t["wall_seconds"],
                        **percentiles([r["seconds"] for r in t["rows"]]),
                    }
                    for t in trials
                ],
                "identical_final_to_A": sum(
                    r["final_signature_sha256"]
                    == arows[r["id"]]["final_signature_sha256"]
                    for r in values
                ),
                "gpu_used_mib_max": max(
                    float(s.split(",")[2]) for t in trials for s in t["gpu_samples"]
                ),
            }
    excluded_path = directory / "timing-excluded.json"
    result = {
        "full_quality_replay": output,
        "repeated_timing": repeated,
        "excluded_timing_trials": len(read_json(excluded_path))
        if excluded_path.exists()
        else 0,
        "unsupported": preflight["failures"],
        "inherited_quality": protocol["inherited"],
        "timing_scope": "API-free local replay; worker-entry to completion; excludes executor submission queue; 15 fixed questions repeated 3 times. Quality checkpoint latencies are not used: interrupted run had external GPU contention.",
        "run_sha256": file_sha(directory / "run.json"),
        "script_sha256": file_sha(Path(__file__)),
    }
    write_json(DEST / "analysis.json", result)
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    analyze(parser.parse_args().directory.resolve())
