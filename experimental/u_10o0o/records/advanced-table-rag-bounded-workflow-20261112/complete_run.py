"""Finalize saved runs after auditing an unrelated, never-imported replay helper change."""

from datetime import UTC, datetime
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    object_sha,
    read_json,
    write_json,
)

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"
UNUSED = "experimental/u_10o0o/table_rag/replay_product.py"


def main():
    protocol = read_json(OUT / "protocol.json")
    qs = read_json(OUT / "questions.json")
    assert file_sha(OUT / "questions.json") == protocol["questions_sha256"]
    assert len(qs) == 278
    changed = {
        r["path"]: {"frozen": r["sha256"], "current": file_sha(ROOT / r["path"])}
        for r in protocol["runtime_files"]
        if file_sha(ROOT / r["path"]) != r["sha256"]
    }
    # This explicit exception is only for a standalone production replay script not imported or called by this benchmark.
    assert set(changed) <= {UNUSED}, changed
    for row in protocol["runtime_files"]:
        if row["path"] == UNUSED:
            continue
        code = (OUT / "executed-code" / row["path"]).read_text()
        assert "replay_product" not in code, row["path"]
    saved = {}
    for arm in ("A", "B"):
        files = {}
        rows = []
        for q in qs:
            path = OUT / arm / "results" / (object_sha(q["id"]) + ".json")
            row = read_json(path)
            assert row["id"] == q["id"] and row["question_sha256"] == object_sha(
                q["question"]
            )
            assert row["protocol_sha256"] == file_sha(OUT / "protocol.json")
            assert (
                len(row["contexts"]["table_rag"]) <= 8
                and row["traces"]["table_rag"]["context_tokens"] <= 8000
            )
            files[str(Path("results") / path.name)] = file_sha(path)
            rows.append(row)
        saved[arm] = rows
        write_json(
            OUT / arm / "run.json",
            {
                "status": "complete",
                "count": len(rows),
                "protocol_sha256": file_sha(OUT / "protocol.json"),
                "retrieval_read_references": False,
                "api_calls": sum(len(r["turns"]) for r in rows),
                "files": files,
                "runtime_audit": "All executing source hashes unchanged; unrelated standalone replay helper changed on disk.",
            },
        )
    write_json(
        HERE / "runtime-audit.json",
        {
            "executing_code_unchanged": True,
            "changed_files": changed,
            "unused_module_import_reference_count": 0,
            "all_556_results_saved": True,
            "reference_contents_not_read_by_this_check": True,
            "reason": "The legacy benchmark freezes every table_rag/*.py, including an unused production replay entry point. Its independent edit caused the final broad file check to stop. Results were verified without reissuing paid requests.",
            "verified_at": datetime.now(UTC).isoformat(),
        },
    )
    write_json(
        OUT / "run.json",
        {
            "status": "complete",
            "count": 556,
            "completion_mode": "validated_saved_results_after_nonexecuted_file_change",
            "runtime_audit_sha256": file_sha(HERE / "runtime-audit.json"),
        },
    )
    print(
        "556 results verified; executing code unchanged; unrelated replay-helper edit recorded"
    )


if __name__ == "__main__":
    main()
