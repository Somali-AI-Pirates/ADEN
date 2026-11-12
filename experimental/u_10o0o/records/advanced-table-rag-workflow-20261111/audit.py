"""Post-run audit; does not read evaluation references or change runtime outputs."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.evidence import flat
from experimental.u_10o0o.table_rag.retrieval import json_payload, source_id
from experimental.u_10o0o.table_rag.workflow_experiment import saved_results

DEST = Path(__file__).resolve().parent


def runtime_order(evidence):
    """Restore insertion order from frozen Ledger.payload; persisted JSON sorts keys."""

    def ordered(row, names):
        assert set(row) <= set(names)
        return {name: row[name] for name in names if name in row}

    return {
        "pages": [
            ordered(p, ("book_id", "page", "source_id", "text", "truncated"))
            for p in evidence["pages"]
        ],
        "requirements": [
            ordered(
                r,
                (
                    "id",
                    "subject",
                    "request",
                    "question_quote",
                    "status",
                    "budget_lost_evidence",
                    "detail",
                ),
            )
            for r in evidence["requirements"]
        ],
        "intermediate_answers": [
            ordered(c, ("id", "requirement_id", "statement", "supports", "step"))
            for c in evidence["intermediate_answers"]
        ],
        "supporting_evidence": {
            key: ordered(
                p,
                (
                    "id",
                    "kind",
                    "text",
                    "source_ids",
                    "query_id",
                    "row",
                    "column",
                    "value",
                    "preview_truncated",
                    "quote",
                ),
            )
            for key, p in evidence["supporting_evidence"].items()
        },
        "sql_operations": [
            ordered(
                q,
                ("id", "sql", "status", "tables", "source_scope", "preview_truncated"),
            )
            for q in evidence["sql_operations"]
        ],
        "coverage_is_model_judgment": evidence["coverage_is_model_judgment"],
    }


def audit(directory):
    directory = Path(directory).resolve()
    rows = saved_results(directory)
    sources = {source_id(p): p for p in read_json(CACHE_PATH)}
    counters = Counter()
    usage = defaultdict(Counter)
    source_counts = []
    api_counts = []
    max_tokens = 0
    max_raw_pages = 0
    for row in rows.values():
        evidence = row["evidence"]
        tokens = _count_tokens(json_payload(runtime_order(evidence)))
        assert tokens == row["trace"]["context_tokens"] and tokens <= 8000
        assert len(evidence["pages"]) <= 8 and len(row["trace"]["ranked"]) <= 20
        max_tokens = max(max_tokens, tokens)
        max_raw_pages = max(max_raw_pages, len(evidence["pages"]))
        known = {p["source_id"] for p in evidence["pages"]}
        for proof in evidence["supporting_evidence"].values():
            known.update(proof["source_ids"])
            if proof["kind"] == "document":
                assert all(
                    flat(proof["quote"]) in flat(sources[s]["text"])
                    for s in proof["source_ids"]
                )
            else:
                step = row["steps"][int(proof["query_id"].removeprefix("sql")) - 1]
                result = step["sql"]
                assert result["status"] == "ok"
                actual = result["rows"][proof["row"]][proof["column"]]
                text = (
                    actual
                    if isinstance(actual, str)
                    else json.dumps(actual, ensure_ascii=False)
                )
                assert flat(proof["quote"]) == flat(text)
                assert set(proof["source_ids"]) == {
                    f"{p['book_id']}:p{p['page']:04d}" for p in result["sources"]
                }
            counters[proof["kind"] + "_proofs"] += 1
        source_counts.append(len(known))
        complete = bool(evidence["requirements"]) and all(
            r["status"] == "supported" for r in evidence["requirements"]
        )
        assert complete == row["complete"]
        assert row["finish"]["status"] != "sufficient" or complete
        response = row["answers"]["workflow"]
        assert (
            not response["answer"]
            or response["answer"]["status"] != "answered"
            or complete
        )
        counters["answer_coverage_guard"] += response["coverage_guard_applied"]
        counters["invalid_answer_citations"] += len(response["invalid_citations"])
        for c in row["calls"]:
            usage[c["stage"]].update(
                {
                    k: c.get(k, 0)
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "cached_input_tokens",
                    )
                }
            )
            usage[c["stage"]]["calls"] += 1
        for arm, answer in row["answers"].items():
            call = answer["usage"]
            usage["answer_" + arm].update(
                {
                    k: call.get(k, 0)
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "cached_input_tokens",
                    )
                }
            )
            usage["answer_" + arm]["calls"] += 1
        api_counts.append(len(row["calls"]) + 1)
    for p in (directory / "grades").glob("*.json"):
        if p.name.count(".") != 1:
            continue
        call = read_json(p)["usage"]
        usage["judge"].update(
            {
                k: call.get(k, 0)
                for k in (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "cached_input_tokens",
                )
            }
        )
        usage["judge"]["calls"] += 1
    for values in usage.values():
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    result = {
        "questions": len(rows),
        "budget_passed": True,
        "token_audit": "Restored runtime insertion order from frozen Ledger.payload; disk JSON sorts keys and changes token boundaries",
        "source_proof_audit_passed": True,
        "max_evidence_json_tokens": max_tokens,
        "max_raw_pages": max_raw_pages,
        "max_distinct_source_references": max(source_counts),
        "questions_with_more_than_eight_source_references": sum(
            n > 8 for n in source_counts
        ),
        "proofs_and_guards": dict(counters),
        "usage_by_stage": dict(usage),
        "service_api_calls_per_question": sum(api_counts) / len(api_counts),
        "service_calls_exclude_baseline_generation_and_judge": True,
        "token_accounting": "reasoning is included in output; cached tokens in input; pilot/aborted/in-flight unreturned usage excluded",
        "retrieval_outputs_modified": False,
        "run_sha256": file_sha(directory / "run.json"),
        "script_sha256": file_sha(Path(__file__)),
    }
    write_json(DEST / "audit.json", result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("usage_by_stage",)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    audit(parser.parse_args().directory)
