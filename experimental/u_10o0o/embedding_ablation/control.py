"""Confirm the Snowflake arm reproduces the adopted baseline's saved retrievals."""

import argparse

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)

from .__main__ import DEFAULT_RUN

PREVIOUS = ROOT / "artifacts/u_10o0o/advanced-normalized-baseline-20261111/default-v1"


def compare(run=DEFAULT_RUN):
    before = read_jsonl(PREVIOUS / "traces.jsonl")
    after = read_jsonl(run / "snowflake/retrieval/traces.jsonl")
    before_questions = read_json(PREVIOUS / "questions.json")
    after_questions = read_json(run / "questions.json")
    if before_questions != after_questions:
        raise ValueError("Snowflake control did not use the adopted questions")
    counts = {
        "questions": 0,
        "initial_pools_identical": 0,
        "post_lookup_order_identical": 0,
        "final_contexts_identical": 0,
    }
    mismatch = []
    for a, b in zip(before, after, strict=True):
        if a["id"] != b["id"]:
            raise ValueError("Control question identities differ")
        counts["questions"] += 1
        same = {
            "initial_pools_identical": [p["source_id"] for p in a["pool"]]
            == [p["source_id"] for p in b["pool"]],
            "post_lookup_order_identical": [p["source_id"] for p in a["ranked"]]
            == [p["source_id"] for p in b["ranked"]],
            "final_contexts_identical": a["context_sha256"] == b["context_sha256"],
        }
        for key, value in same.items():
            counts[key] += int(value)
        if not all(same.values()):
            mismatch.append({"id": a["id"], **same})
    report = {
        **counts,
        "differences": mismatch,
        "previous_manifest_sha256": file_sha(PREVIOUS / "manifest.json"),
        "new_manifest_sha256": file_sha(run / "snowflake/retrieval/manifest.json"),
    }
    write_json(run / "control-verification.json", report)
    print(counts, flush=True)
    if mismatch or counts["questions"] != 280:
        raise ValueError(
            "Investigate control differences before interpreting model replacements"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=type(DEFAULT_RUN), default=DEFAULT_RUN)
    compare(parser.parse_args().run)
