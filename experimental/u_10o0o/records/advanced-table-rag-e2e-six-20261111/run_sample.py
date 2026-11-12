"""Six preselected v5 requests through the updated default Retriever and live agent."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copyfile
from time import perf_counter

from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    object_sha,
    file_sha,
    check_runtime,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.table_rag.agent import (
    TableAgent,
    generate_answer,
    MODEL_ID,
    EFFORT,
    INSTRUCTIONS,
    ANSWER_INSTRUCTIONS,
    TOOLS,
)
from experimental.u_10o0o.table_rag.benchmark import (
    runtime,
    saved_results,
    answer_pages,
)
from experimental.u_10o0o.table_rag.retrieval import Retriever, json_payload
from experimental.u_10o0o.table_rag.speed import (
    V5,
    gpu_state,
    search_signature,
    wait_gpu_available,
)

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-e2e-six-20261111/v1"


def run():
    selection = read_json(OUTPUT / "selection.json")
    if (OUTPUT / "protocol.json").exists():
        raise FileExistsError(
            "This six-question paid run was already started; no automatic retry"
        )
    saved = saved_results(V5)
    if selection["source_run_sha256"] != file_sha(V5 / "run.json"):
        raise ValueError("Source v5 changed")
    if len(selection["items"]) != 6 or len({q["id"] for q in selection["items"]}) != 6:
        raise ValueError("Expected six distinct frozen questions")
    for q in selection["items"]:
        if (
            object_sha(q["question"]) != q["question_sha256"]
            or q["question_sha256"] != saved[q["id"]]["question_sha256"]
        ):
            raise ValueError("Selected question text differs from saved v5")
    inputs = runtime()
    inputs.append(
        {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": file_sha(Path(__file__)),
        }
    )
    for item in inputs:
        target = OUTPUT / "executed-code" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / item["path"], target)
    protocol = {
        "model": MODEL_ID,
        "effort": EFFORT,
        "workers": 4,
        "selection_sha256": file_sha(OUTPUT / "selection.json"),
        "source_run_sha256": selection["source_run_sha256"],
        "runtime_files": inputs,
        "api_retries": 0,
        "max_api_calls": 42,
        "prompt_sha256": object_sha([INSTRUCTIONS, ANSWER_INSTRUCTIONS, TOOLS]),
        "search_lock_scope": "split",
        "reranker_batch_size": 4,
        "query_device": "cpu",
        "query_dtype": "bfloat16",
        "reranker_device": "cuda",
        "reranker_dtype": "float32",
        "gpu_fraction": 0.12,
        "candidate_pages": 20,
        "final_pages": 8,
        "evidence_tokens": 8000,
        "reference_labels_read": False,
        "answer_cache_used": False,
        "cold_start_included": False,
        "executor_submission_queue_included": False,
        "gpu_before_loading": gpu_state(),
    }
    write_json(OUTPUT / "protocol.json", protocol)
    wait_gpu_available()
    retriever = Retriever(V5, device="cpu_gpu_rerank")
    # Real default path, not the profiling subclass. Also warms up local models.
    checks = []
    for q in selection["items"]:
        current = retriever.search(q["question"])
        checks.append(
            {
                "id": q["id"],
                "initial_search_identical_to_v5": search_signature(current)
                == search_signature(saved[q["id"]]["searches"][0], stored=True),
            }
        )
    write_json(OUTPUT / "preflight.json", checks)
    if not all(r["initial_search_identical_to_v5"] for r in checks):
        raise ValueError("Updated default initial search differs before paid calls")
    wait_gpu_available()
    client = StructuredLunaInterpreter().client
    agent = TableAgent(retriever, client)
    results = OUTPUT / "results"
    results.mkdir(exist_ok=False)

    def one(q):
        name = object_sha(q["id"])
        marker = results / (name + ".started.json")
        write_json(marker, {"id": q["id"], "question_sha256": q["question_sha256"]})
        before = gpu_state()
        started = perf_counter()
        result = agent.run(
            q["question"],
            checkpoint=lambda data: write_json(
                results / (name + ".progress.json"), data
            ),
        )
        pages = context_pages(result["context"])
        tokens = result["context"].token_count + _count_tokens(
            json_payload(result["sql_results"])
        )
        if tokens > 8000:
            raise ValueError("Evidence budget exceeded")

        def serialize(rows):
            return [_source_info(p, s) for p, s in rows]

        trace = {
            "arm": "table_rag",
            "pool": serialize(result["searches"][0]["ranked"]),
            "ranked": serialize(result["ranked"]),
            "context_tokens": tokens,
            "context_sha256": result["context"].context_hash,
        }
        row = {
            "id": q["id"],
            "group": q["group"],
            "question_sha256": q["question_sha256"],
            "traces": {"table_rag": trace},
            "contexts": {"table_rag": pages},
            "sql_results": result["sql_results"],
            "sql_attempts": result["sql_attempts"],
            "finish": result["finish"],
            "turns": result["turns"],
            "searches": [
                {**s, "ranked": serialize(s["ranked"])} for s in result["searches"]
            ],
            "retrieval_seconds": result["seconds"],
            "gpu_before": before,
            "answers": {},
        }
        write_json(results / (name + ".retrieved.json"), row)
        answer = generate_answer(
            client, q["question"], answer_pages(pages), result["sql_results"]
        )
        row["answers"]["table_rag"] = answer
        row["actual_wall_seconds"] = perf_counter() - started
        row["comparable_total_seconds"] = (
            result["seconds"] + answer["telemetry"]["seconds"]
        )
        row["gpu_after"] = gpu_state()
        write_json(results / (name + ".json"), row)
        print(
            q["group"],
            q["id"],
            f"total={row['comparable_total_seconds']:.2f}s",
            f"planner={len(row['turns'])}",
            f"searches={len(row['searches'])}",
            f"answer={answer['telemetry']['status']}",
            flush=True,
        )
        return row

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(one, selection["items"]))
    finally:
        client.close()
    check_runtime(inputs)
    write_json(
        OUTPUT / "run.json",
        {
            "status": "complete",
            "count": len(rows),
            "references_opened": False,
            "api_calls": sum(len(r["turns"]) + 1 for r in rows),
            "protocol_sha256": file_sha(OUTPUT / "protocol.json"),
            "files": {
                str(p.relative_to(OUTPUT)): file_sha(p)
                for p in sorted(results.glob("*.json"))
            },
            "gpu_end": gpu_state(),
        },
    )
    print("Six real responses saved; reference labels still unopened", flush=True)


if __name__ == "__main__":
    run()
