"""Frozen KURE-v2 TableRAG comparison: 280 live retrievals, six live answers."""

import argparse
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
    file_sha,
    object_sha,
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
from experimental.u_10o0o.table_rag.kure_v2 import KureV2Retriever, build, GPU_FRACTION
from experimental.u_10o0o.table_rag.retrieval import Retriever, json_payload
from experimental.u_10o0o.table_rag.speed import (
    V5,
    gpu_state,
    wait_gpu_available,
    search_signature,
)

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-kure-v2-20261111/v1"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-kure-v2-20261111"
SIX = ROOT / "artifacts/u_10o0o/advanced-table-rag-e2e-six-20261111/v1"


def selected_questions():
    saved = saved_results(V5)
    questions = read_json(V5 / "questions.json")
    selected = read_json(SIX / "selection.json")["items"]
    six_ids = [q["id"] for q in selected]
    if (
        len(questions) != 280
        or len(set(q["id"] for q in questions)) != 280
        or len(six_ids) != 6
    ):
        raise ValueError("Unexpected frozen question count")
    for q in questions:
        if object_sha(q["question"]) != saved[q["id"]]["question_sha256"]:
            raise ValueError("Question text differs from v5")
    by_id = {q["id"]: q for q in questions}
    return (
        [by_id[rid] for rid in six_ids]
        + [q for q in questions if q["id"] not in six_ids],
        six_ids,
        saved,
    )


def freeze(output, questions, six_ids):
    path = output / "protocol.json"
    if path.exists():
        raise FileExistsError(
            "Run already started; never automatically repeat paid calls"
        )
    records = runtime()
    for relative in (
        "embedding_ablation/kure_v2.py",
        "embedding_ablation/kure_resolution.py",
    ):
        name = "experimental/u_10o0o/" + relative
        if name not in {r["path"] for r in records}:
            records.append({"path": name, "sha256": file_sha(ROOT / name)})
    for r in records:
        target = output / "executed-code" / r["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / r["path"], target)
    protocol = {
        "source_run_sha256": file_sha(V5 / "run.json"),
        "previous_six_run_sha256": file_sha(SIX / "run.json"),
        "questions_sha256": object_sha(questions),
        "six_answer_ids": six_ids,
        "workers": 4,
        "model": MODEL_ID,
        "effort": EFFORT,
        "embedding": "nlpai-lab/KURE-v2",
        "embedding_revision": "3431f86d399d666083890dbb882aced6708873bc",
        "query_max_tokens": 128,
        "query_expansion_floor": 64,
        "reranker_max_tokens": 1024,
        "reranker_dtype": "float32",
        "reranker_batch_size": 4,
        "gpu_fraction": GPU_FRACTION,
        "gpu_lock": "shared by query encoding, MaxSim and reranking",
        "sparse_top_k": 50,
        "dense_top_k": 50,
        "rrf_weights": [0.5, 0.5],
        "rrf_constant": 60,
        "candidate_pages": 20,
        "final_pages": 8,
        "context_tokens": 8000,
        "vector_manifest_sha256": file_sha(output / "vectors-manifest.json"),
        "prompt_sha256": object_sha([INSTRUCTIONS, ANSWER_INSTRUCTIONS, TOOLS]),
        "runtime_files": records,
        "references_opened": False,
        "max_api_calls": 1686,
        "answer_cache_used": False,
        "api_retries": 0,
    }
    write_json(path, protocol)
    write_json(output / "questions.json", questions)
    return protocol


def validate_default(questions, saved, output):
    # Pure extraction of shared methods must not change the existing default.
    retriever = Retriever(V5, device="cpu_gpu_rerank")
    checks = []
    for q in questions[:6]:
        result = retriever.search(q["question"])
        checks.append(
            {
                "id": q["id"],
                "same_saved_initial": search_signature(result)
                == search_signature(saved[q["id"]]["searches"][0], stored=True),
            }
        )
    write_json(output / "default-parity.json", checks)
    if not all(c["same_saved_initial"] for c in checks):
        raise ValueError("Default search changed before comparison")


def run(output):
    import gc
    import torch

    questions, six_ids, saved = selected_questions()
    protocol = freeze(output, questions, six_ids)
    wait_gpu_available()
    validate_default(questions, saved, output)
    gc.collect()
    torch.cuda.empty_cache()
    retriever = KureV2Retriever(output)
    # Warm the real new backend without paid requests or reference labels.
    warm = [retriever.search(q["question"]) for q in questions[:2]]
    write_json(
        output / "warmup.json",
        [
            {"id": q["id"], "query_input": r["query_input"], "work": r["work"]}
            for q, r in zip(questions[:2], warm, strict=True)
        ],
    )
    client = StructuredLunaInterpreter().client
    agent = TableAgent(retriever, client)
    result_dir = output / "results"
    result_dir.mkdir(exist_ok=False)

    def one(q):
        name = object_sha(q["id"])
        path = result_dir / (name + ".json")
        write_json(
            result_dir / (name + ".started.json"),
            {"id": q["id"], "question_sha256": object_sha(q["question"])},
        )
        gpu_before = gpu_state() if q["id"] in six_ids else None
        started = perf_counter()
        result = agent.run(
            q["question"],
            checkpoint=lambda d: write_json(result_dir / (name + ".progress.json"), d),
        )
        pages = context_pages(result["context"])
        tokens = result["context"].token_count + _count_tokens(
            json_payload(result["sql_results"])
        )
        if tokens > 8000 or len(pages) > 8:
            raise ValueError("Final evidence exceeded fixed budget")

        def serialize(rows):
            return [_source_info(p, s) for p, s in rows]

        row = {
            "id": q["id"],
            "question_sha256": object_sha(q["question"]),
            "traces": {
                "table_rag": {
                    "arm": "table_rag",
                    "pool": serialize(result["searches"][0]["ranked"]),
                    "ranked": serialize(result["ranked"]),
                    "context_tokens": tokens,
                    "context_sha256": result["context"].context_hash,
                }
            },
            "contexts": {"table_rag": pages},
            "sql_results": result["sql_results"],
            "sql_attempts": result["sql_attempts"],
            "finish": result["finish"],
            "turns": result["turns"],
            "searches": [
                {**s, "ranked": serialize(s["ranked"])} for s in result["searches"]
            ],
            "retrieval_seconds": result["seconds"],
            "answers": {},
        }
        write_json(result_dir / (name + ".retrieved.json"), row)
        if q["id"] in six_ids:
            answer = generate_answer(
                client, q["question"], answer_pages(pages), result["sql_results"]
            )
            row["answers"]["table_rag"] = answer
            row["actual_wall_seconds"] = perf_counter() - started
            row["total_seconds"] = result["seconds"] + answer["telemetry"]["seconds"]
            row["gpu_before"] = gpu_before
            row["gpu_after"] = gpu_state()
        write_json(path, row)
        return row

    completed = []
    try:
        for group in (questions[:6], questions[6:]):
            with ThreadPoolExecutor(max_workers=4) as pool:
                for row in pool.map(one, group):
                    completed.append(row)
                    if (
                        len(completed) <= 6
                        or len(completed) % 20 == 0
                        or len(completed) == 280
                    ):
                        print(
                            f"Completed {len(completed)}/280 {row['id']} planner={len(row['turns'])} searches={len(row['searches'])}",
                            flush=True,
                        )
    finally:
        client.close()
    check_runtime(protocol["runtime_files"])
    write_json(
        output / "run.json",
        {
            "status": "complete",
            "count": len(completed),
            "references_opened": False,
            "api_calls": sum(len(r["turns"]) + len(r["answers"]) for r in completed),
            "protocol_sha256": file_sha(output / "protocol.json"),
            "files": {
                str(
                    (result_dir / (object_sha(r["id"]) + ".json")).relative_to(output)
                ): file_sha(result_dir / (object_sha(r["id"]) + ".json"))
                for r in completed
            },
            "gpu_end": gpu_state(),
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        },
    )
    print(
        "All 280 retrievals and six answers saved; references remain unopened",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.command == "build":
        build(args.output.resolve())
    else:
        run(args.output.resolve())
