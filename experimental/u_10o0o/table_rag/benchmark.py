"""Paired resumable run. References are inaccessible until all outputs exist."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from collections import Counter

from app.generation.context import build_context
from experimental.u_10o0o.advanced_is_lookup_ab import BASELINE, context_pages
from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
    check_runtime,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    validate_saved,
    page_key,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.table_rag.agent import (
    TableAgent,
    generate_answer,
    PRESERVATION_INSTRUCTIONS,
    ANSWER_INSTRUCTIONS,
    TOOLS,
    EFFORT,
    MODEL_ID,
    MAX_STEPS,
)
from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id, json_payload

ARMS = ("baseline", "table_rag")


def runtime():
    from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
        loaded_runtime,
    )

    stamps = {row["path"]: row for row in loaded_runtime()}
    for p in Path(__file__).parent.glob("*.py"):
        if not p.name.startswith("test_"):
            path = str(p.relative_to(ROOT))
            stamps[path] = {"path": path, "sha256": file_sha(p)}
    return [stamps[k] for k in sorted(stamps)]


def freeze(directory, device):
    if (directory / "reuse-proof.json").exists():
        raise ValueError(
            "Legacy inherited retrievals cannot seed the B policy; use a fresh output"
        )
    manifest, _, _ = validate_saved(BASELINE)
    question_path = BASELINE / "questions.json"
    protocol = {
        "baseline": str(BASELINE),
        "baseline_manifest_sha256": file_sha(BASELINE / "manifest.json"),
        "questions_sha256": file_sha(question_path),
        "store_sha256": file_sha(directory / "store.json"),
        "vectors_sha256": file_sha(directory / "vectors.json"),
        "model": MODEL_ID,
        "reasoning": EFFORT,
        "max_steps": MAX_STEPS,
        "execution_mode": device,
        "query_device": "cpu" if device == "cpu_gpu_rerank" else device,
        "reranker_device": "cuda" if device == "cpu_gpu_rerank" else "cpu",
        "reranker_dtype": "float32",
        "gpu_fraction": 0.12
        if device == "cpu_gpu_rerank"
        else 0.22
        if device == "shared_cuda"
        else None,
        "threads": 2,
        "reranker_batch_size": 4,
        "search_lock_scope": "split" if device == "cpu_gpu_rerank" else "coarse",
        "max_candidates": 20,
        "max_pages": 8,
        "context_tokens": 8000,
        "workers": 4,
        "local_retrieval": {
            "sparse_top": 50,
            "heading_bonus": 0.25,
            "dense_top": 50,
            "rrf": [0.5, 0.5, 60],
            "rerank": "full top20 once per subquery",
            "merge": "selected observed sources first, then round robin top20",
            "sql_preview_rows": 40,
            "sql_timeout": 2,
            "planner_timeout": 30,
        },
        "evidence_policy": "preserve",
        "prompt_sha256": object_sha(
            [PRESERVATION_INSTRUCTIONS, ANSWER_INSTRUCTIONS, TOOLS]
        ),
        "runtime_files": runtime(),
        "references_opened": False,
    }
    path = directory / "protocol.json"
    if path.exists():
        if read_json(path) != protocol:
            raise ValueError(
                "Frozen protocol changed; start a new run, do not tune an existing evaluation"
            )
    else:
        write_json(path, protocol)
        write_json(directory / "questions.json", read_json(question_path))
    return manifest, protocol


def answer_pages(pages):
    return [{**p, "source_id": source_id(p)} for p in pages]


def cached_answer(directory, client, q, arm, pages, sql=()):
    cache = Path(directory) / "answer-cache"
    cache.mkdir(exist_ok=True)
    signature = object_sha(
        {
            "question": q["question"],
            "pages": pages,
            "sql": list(sql),
            "model": MODEL_ID,
            "reasoning": EFFORT,
            "prompt": ANSWER_INSTRUCTIONS,
            "max_output_tokens": 2200,
            "timeout": 30,
        }
    )
    path = cache / (object_sha(q["id"]) + "-" + arm + ".json")
    if path.exists():
        saved = read_json(path)
        if saved["signature"] != signature:
            raise ValueError("Answer cache input differs")
        return saved["result"]
    marker = path.with_suffix(".started.json")
    if marker.exists():
        raise ValueError("Unfinished answer call; refusing automatic paid retry")
    write_json(
        marker, {"id": q["id"], "signature": signature, "references_opened": False}
    )
    result = generate_answer(client, q["question"], pages, sql)
    write_json(
        path,
        {
            "id": q["id"],
            "signature": signature,
            "result": result,
            "references_opened": False,
        },
    )
    return result


def baseline_answers(directory):
    _, _, contexts = validate_saved(BASELINE)
    questions = read_json(BASELINE / "questions.json")
    client = StructuredLunaInterpreter().client

    def one(q):
        return cached_answer(
            directory, client, q, "baseline", answer_pages(contexts[q["id"]]["pages"])
        )

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            for i, _ in enumerate(executor.map(one, questions), 1):
                if i % 20 == 0:
                    print(f"Baseline answers saved {i}/280", flush=True)
    finally:
        client.close()


def run(directory, device="cpu", limit=None):
    directory = Path(directory)
    manifest, protocol = freeze(directory, device)
    _, baseline_traces, baseline_contexts = validate_saved(BASELINE)
    questions = read_json(directory / "questions.json")
    result_dir = directory / "results"
    result_dir.mkdir(exist_ok=True)
    pending = [
        q
        for q in questions
        if not (result_dir / (object_sha(q["id"]) + ".json")).exists()
    ]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        finalize(directory)
        return
    retriever = Retriever(directory, device=device)
    client = StructuredLunaInterpreter().client
    agent = TableAgent(retriever, client, preserve_evidence=True)

    def one(q):
        name = object_sha(q["id"])
        path = result_dir / (name + ".json")
        started_path = result_dir / (name + ".started.json")
        if started_path.exists():
            raise ValueError(
                "Unfinished request needs explicit recovery; refusing duplicate paid calls"
            )
        write_json(
            started_path, {"id": q["id"], "question_sha256": object_sha(q["question"])}
        )
        started = perf_counter()
        trace = baseline_traces[q["id"]]
        ranked = [
            (retriever.page_map[page_key(p)], p["score"]) for p in trace["ranked"]
        ]
        baseline = build_context(_context_candidates(ranked), 8, 8000, _count_tokens)
        if (
            baseline.context_hash != trace["context_sha256"]
            or context_pages(baseline) != baseline_contexts[q["id"]]["pages"]
        ):
            raise ValueError("Saved baseline context did not reproduce")
        result = agent.run(
            q["question"],
            checkpoint=lambda data: write_json(
                result_dir / (name + ".progress.json"), data
            ),
        )
        pages = context_pages(result["context"])
        if len(result["ranked"]) > 20 or len(pages) > 8:
            raise ValueError("Page budget exceeded")
        evidence_tokens = result["context"].token_count + _count_tokens(
            json_payload(result["sql_results"])
        )
        if evidence_tokens > 8000:
            raise ValueError("Evidence token budget exceeded")
        trace_new = {
            **trace,
            "arm": "table_rag",
            "pool": [_source_info(p, s) for p, s in result["searches"][0]["ranked"]],
            "after_hg_pool": [
                _source_info(p, s) for p, s in result["searches"][0]["ranked"]
            ],
            "ranked": [_source_info(p, s) for p, s in result["ranked"]],
            "context_sha256": result["context"].context_hash,
            "context_tokens": evidence_tokens,
            "final_pages": [{k: p[k] for k in ("book_id", "page")} for p in pages],
            "routes": {"planner": result["finish"]["status"]},
        }
        # Persist retrieval before any generation; the independent answer call
        # gets only final budgeted evidence, not the planner's larger history.
        saved = {
            "id": q["id"],
            "question_sha256": object_sha(q["question"]),
            "traces": {
                "baseline": {**trace, "arm": "baseline"},
                "table_rag": trace_new,
            },
            "contexts": {
                "baseline": baseline_contexts[q["id"]]["pages"],
                "table_rag": pages,
            },
            "sql_results": result["sql_results"],
            "sql_attempts": result["sql_attempts"],
            "finish": result["finish"],
            "selection_audit": result["selection_audit"],
            "turns": result["turns"],
            "searches": [
                {**s, "ranked": [_source_info(p, score) for p, score in s["ranked"]]}
                for s in result["searches"]
            ],
            "retrieval_seconds": result["seconds"],
            "answers": {},
        }
        write_json(result_dir / (name + ".retrieved.json"), saved)
        # Alternate order, retaining the same answer prompt/model for both arms.
        order = ARMS if int(name[0], 16) % 2 else tuple(reversed(ARMS))
        for arm in order:
            answer = cached_answer(
                directory,
                client,
                q,
                arm,
                answer_pages(saved["contexts"][arm]),
                saved["sql_results"] if arm == "table_rag" else (),
            )
            saved["answers"][arm] = answer
            write_json(result_dir / (name + ".answers-progress.json"), saved["answers"])
        saved["seconds"] = perf_counter() - started
        write_json(path, saved)
        return q["id"], len(result["turns"]), len(result["searches"])

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            for i, (qid, calls, searches) in enumerate(executor.map(one, pending), 1):
                print(
                    f"Saved {i}/{len(pending)} id={qid} planner_calls={calls} searches={searches}",
                    flush=True,
                )
    finally:
        client.close()
    check_runtime(protocol["runtime_files"])
    finalize(directory)


def finalize(directory):
    questions = read_json(directory / "questions.json")
    files = [directory / "results" / (object_sha(q["id"]) + ".json") for q in questions]
    saved = [p for p in files if p.exists()]
    complete = len(saved) == len(questions)
    write_json(
        directory / "run.json",
        {
            "status": "complete" if complete else "partial",
            "count": len(saved),
            "expected": len(questions),
            "references_opened": False,
            "protocol_sha256": file_sha(directory / "protocol.json"),
            "files": {str(p.relative_to(directory)): file_sha(p) for p in saved},
        },
    )
    print(
        f"Run {'complete' if complete else 'partial'}: {len(saved)}/{len(questions)}",
        flush=True,
    )


def saved_results(directory):
    run = read_json(directory / "run.json")
    if (
        run["status"] != "complete"
        or run["count"] != 280
        or run["references_opened"] is not False
    ):
        raise ValueError("All 280 retrieval/answer pairs must be saved before scoring")
    if file_sha(directory / "protocol.json") != run["protocol_sha256"]:
        raise ValueError("Protocol changed")
    check_runtime(read_json(directory / "protocol.json")["runtime_files"])
    for name, sha in run["files"].items():
        if file_sha(directory / name) != sha:
            raise ValueError("Saved output changed")
    return {r["id"]: r for r in (read_json(directory / n) for n in run["files"])}


def score(directory):
    from app.evaluation.dataset import load_references
    from experimental.u_10o0o.pattern_retrieval.established_score import (
        aggregates,
        differences,
        score_item,
    )
    from experimental.u_10o0o.prepare import CACHE_PATH

    directory = Path(directory)
    saved = saved_results(directory)
    manifest, _, _ = validate_saved(BASELINE)
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    results = {
        arm: [
            score_item(
                m,
                refs[m["suite"]][m["question_id"]],
                saved[m["id"]]["traces"][arm],
                {"pages": saved[m["id"]]["contexts"][arm]},
                pages,
            )
            for m in manifest["items"]
        ]
        for arm in ARMS
    }
    write_json(
        directory / "summary.json", {a: aggregates(r) for a, r in results.items()}
    )
    write_json(directory / "per-question.json", results)
    write_json(
        directory / "differences.json",
        differences({"baseline": results["baseline"], "A": results["table_rag"]}),
    )
    write_json(
        directory / "score-manifest.json",
        {
            "run_sha256": file_sha(directory / "run.json"),
            "references_read_after_all_outputs": True,
            "references": [
                {"path": r["references"], "sha256": file_sha(ROOT / r["references"])}
                for r in manifest["question_sources"]
            ],
            "intent_accuracy": "not claimed: no independently labelled decomposition targets",
        },
    )
    for a, rows in results.items():
        summary = aggregates(rows)
        print(
            a,
            {
                k: summary[k]["stages"]["final"]["complete"]
                for k in ("official_all", "categories_positive")
            },
            flush=True,
        )
    return results


def telemetry(directory):
    import numpy as np

    records = list(saved_results(Path(directory)).values())

    def stats(values):
        return (
            {
                "n": len(values),
                "p50": float(np.percentile(values, 50)),
                "p95": float(np.percentile(values, 95)),
            }
            if values
            else {}
        )

    apis = [t["api"] for r in records for t in r["turns"]]
    output = {
        "planner_calls": len(apis),
        "planner_status": dict(Counter(a["status"] for a in apis)),
        "tool_calls": dict(
            Counter(t.get("tool", "none") for r in records for t in r["turns"])
        ),
        "finish_status": dict(Counter(r["finish"]["status"] for r in records)),
        "search_count_histogram": dict(Counter(len(r["searches"]) for r in records)),
        "sql_empty_results": sum(
            s.get("status") == "ok" and not s.get("rows")
            for r in records
            for s in r["sql_attempts"]
        ),
        "sql_truncated_previews": sum(
            bool(s.get("preview_truncated")) for r in records for s in r["sql_attempts"]
        ),
        "sql_queries": sum(len(r["sql_attempts"]) for r in records),
        "sql_status": dict(
            Counter(s["status"] for r in records for s in r["sql_attempts"])
        ),
        "sql_in_final": sum(bool(r["sql_results"]) for r in records),
        "search_calls": sum(len(r["searches"]) for r in records),
        "reranker_work": {
            key: sum(
                s.get("work", {}).get(key, 0) for r in records for s in r["searches"]
            )
            for key in ("rerank_pairs", "raw_pair_tokens", "truncated_pairs")
        },
        "planner_api_seconds": stats([a["seconds"] for a in apis]),
        "retrieval_wall_seconds": stats([r["retrieval_seconds"] for r in records]),
        "search_seconds_including_lock_queue": stats(
            [s["seconds"] for r in records for s in r["searches"]]
        ),
        "api_usage": {
            key: sum(a.get(key, 0) or 0 for a in apis)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "reasoning_tokens",
                "cached_input_tokens",
            )
        },
        "answer_status": {
            arm: dict(
                Counter(r["answers"][arm]["telemetry"]["status"] for r in records)
            )
            for arm in ARMS
        },
        "answer_api_usage": {
            arm: {
                key: sum(
                    r["answers"][arm]["telemetry"].get(key, 0) or 0 for r in records
                )
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "cached_input_tokens",
                )
            }
            for arm in ARMS
        },
        "answer_seconds": {
            arm: stats([r["answers"][arm]["telemetry"]["seconds"] for r in records])
            for arm in ARMS
        },
        "invalid_citation_answers": {
            arm: sum(bool(r["answers"][arm]["invalid_citations"]) for r in records)
            for arm in ARMS
        },
    }
    output["api_usage"]["total_tokens"] = (
        output["api_usage"]["input_tokens"] + output["api_usage"]["output_tokens"]
    )
    for usage in output["answer_api_usage"].values():
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    write_json(Path(directory) / "telemetry.json", output)
    return output
