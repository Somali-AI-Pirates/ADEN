"""Run the frozen TableRAG A/B on all colleague questions; never load labels."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copyfile
from time import perf_counter

from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
from experimental.u_10o0o.advanced_search import evaluation_files, evaluation_questions
from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    check_runtime,
    file_sha,
    object_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.table_rag.agent import (
    EFFORT,
    INSTRUCTIONS,
    MAX_STEPS,
    MODEL_ID,
    TOOLS,
    TableAgent,
)
from experimental.u_10o0o.table_rag.benchmark import runtime
from experimental.u_10o0o.table_rag.kure_v2 import KureV2Retriever
from experimental.u_10o0o.table_rag.retrieval import Retriever, json_payload
from experimental.u_10o0o.table_rag.speed import V5, gpu_state, wait_gpu_available

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-peer-20261111/v2"
B_ASSETS = ROOT / "artifacts/u_10o0o/advanced-table-rag-kure-v2-20261111/v1"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-peer-20261111"


def prepare():
    if (OUTPUT / "protocol.json").exists():
        raise FileExistsError("Protocol exists; do not overwrite a started experiment")
    files = evaluation_files("peer")
    qs = evaluation_questions(files)
    if len(qs) != 304:
        raise ValueError("Expected 304 colleague questions")
    questions = [
        {
            "id": q["split"] + ":" + q["id"],
            "question_id": q["id"],
            "split": q["split"],
            "question": q["question"],
        }
        for q in qs
    ]
    if len({q["id"] for q in questions}) != 304:
        raise ValueError("Duplicate question identity")
    # Put a multi-document question first for the real connection check.
    questions.sort(key=lambda q: (q["split"] != "peer_multi_doc", q["id"]))
    stamps = runtime()
    stamps.append(
        {
            "path": str(Path(__file__).relative_to(ROOT)),
            "sha256": file_sha(Path(__file__)),
        }
    )
    for row in stamps:
        target = OUTPUT / "executed-code" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / row["path"], target)
    protocol = {
        "question_count": 304,
        "questions_sha256": object_sha(questions),
        "sources": [
            {
                "split": k,
                "questions": str(q.relative_to(ROOT)),
                "questions_sha256": file_sha(q),
                "references": str(r.relative_to(ROOT)),
                "references_sha256": file_sha(r),
            }
            for k, (q, r) in files.items()
        ],
        "runtime_files": stamps,
        "model": MODEL_ID,
        "reasoning": EFFORT,
        "prompt_sha256": object_sha([INSTRUCTIONS, TOOLS]),
        "max_steps": MAX_STEPS,
        "max_planner_calls": 2 * 304 * (MAX_STEPS + 1),
        "workers": 4,
        "api_retries": 0,
        "answer_generation": False,
        "candidate_pages": 20,
        "final_pages": 8,
        "context_tokens": 8000,
        "rrf": [0.5, 0.5, 60],
        "references_opened_for_scoring": False,
        "arms": {
            "A": {
                "embedding": "KURE-v3",
                "query_tokens": 2048,
                "reranker_tokens": 2048,
                "device": "cpu_gpu_rerank",
                "assets": str(V5.relative_to(ROOT)),
                "vectors_sha256": file_sha(V5 / "vectors.json"),
            },
            "B": {
                "embedding": "KURE-v2",
                "query_tokens": 128,
                "reranker_tokens": 1024,
                "device": "cuda",
                "assets": str(B_ASSETS.relative_to(ROOT)),
                "vectors_sha256": file_sha(B_ASSETS / "vectors-manifest.json"),
            },
        },
        "review_policy": (
            "Save both arms before opening labels. Report exact page/document "
            "metrics separately from post-hoc final-context sufficiency review. "
            "Do not use candidates outside final context or other events' values."
        ),
        "authorization": "Existing local .env key; no cost limit; retrieval only",
    }
    write_json(OUTPUT / "questions.json", questions)
    write_json(OUTPUT / "protocol.json", protocol)
    print(
        "Frozen 304 colleague questions; reference bytes hashed, labels not parsed",
        flush=True,
    )


def run(arm):
    import torch

    protocol = read_json(OUTPUT / "protocol.json")
    check_runtime(protocol["runtime_files"])
    qs = read_json(OUTPUT / "questions.json")
    assert object_sha(qs) == protocol["questions_sha256"]
    out = OUTPUT / arm
    out.mkdir(exist_ok=False)
    result_dir = out / "results"
    result_dir.mkdir()
    write_json(out / "started.json", {"arm": arm, "gpu": gpu_state()})
    wait_gpu_available()
    tick = perf_counter()
    retriever = (
        Retriever(V5, device="cpu_gpu_rerank")
        if arm == "A"
        else KureV2Retriever(B_ASSETS)
    )
    load_seconds = perf_counter() - tick
    retriever.search(qs[0]["question"])  # Local warmup; no API.
    client = StructuredLunaInterpreter().client
    agent = TableAgent(retriever, client)

    def serialize(rows):
        return [_source_info(p, s) for p, s in rows]

    def one(q):
        name = object_sha(q["id"])
        write_json(
            result_dir / (name + ".started.json"),
            {"id": q["id"], "question_sha256": object_sha(q["question"])},
        )
        result = agent.run(
            q["question"],
            checkpoint=lambda d: write_json(result_dir / (name + ".progress.json"), d),
        )
        pages = context_pages(result["context"])
        tokens = result["context"].token_count + _count_tokens(
            json_payload(result["sql_results"])
        )
        if tokens > 8000 or len(pages) > 8:
            raise ValueError("Final evidence budget exceeded")
        row = {
            "id": q["id"],
            "question_id": q["question_id"],
            "split": q["split"],
            "question_sha256": object_sha(q["question"]),
            "traces": {
                "table_rag": {
                    "arm": arm,
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
        write_json(result_dir / (name + ".json"), row)
        http_statuses = [t.get("api", {}).get("http_status") for t in result["turns"]]
        if any(code in {401, 403, 429} for code in http_statuses):
            raise RuntimeError("API authentication or rate-limit stop; no retry")
        return row

    completed = []
    wall = perf_counter()
    try:
        first = one(qs[0])
        completed.append(first)
        print(
            f"{arm} smoke 1/304: calls={len(first['turns'])} "
            f"searches={len(first['searches'])} finish={first['finish']}",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            for row in executor.map(one, qs[1:]):
                completed.append(row)
                if len(completed) % 20 == 0 or len(completed) == len(qs):
                    print(
                        f"{arm} completed {len(completed)}/304 {row['id']}", flush=True
                    )
    finally:
        client.close()
    check_runtime(protocol["runtime_files"])
    write_json(
        out / "run.json",
        {
            "status": "complete",
            "arm": arm,
            "count": len(completed),
            "protocol_sha256": file_sha(OUTPUT / "protocol.json"),
            "references_opened": False,
            "model_load_seconds": load_seconds,
            "wall_seconds": perf_counter() - wall,
            "api_calls": sum(len(r["turns"]) for r in completed),
            "files": {
                str(
                    (result_dir / (object_sha(r["id"]) + ".json")).relative_to(out)
                ): file_sha(result_dir / (object_sha(r["id"]) + ".json"))
                for r in completed
            },
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "gpu_end": gpu_state(),
        },
    )
    print(f"{arm}: complete; references remain unopened", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "A", "B"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    else:
        run(args.command)
