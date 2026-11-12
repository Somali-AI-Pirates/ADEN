"""KURE-v3 only: live legacy vs source preservation on frozen question copies."""

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
    object_sha,
    file_sha,
    check_runtime,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.table_rag.agent import (
    INSTRUCTIONS,
    PRESERVATION_INSTRUCTIONS,
    TOOLS,
    MAX_STEPS,
    MODEL_ID,
    EFFORT,
)
from experimental.u_10o0o.table_rag_unified.agent import (
    UnifiedAgent,
    integrated_runtime,
)
from experimental.u_10o0o.table_rag_unified import agent as unified
from experimental.u_10o0o.table_rag_unified import evidence
from experimental.u_10o0o.table_rag_unified.index import DEFAULT, RecordIndex
from experimental.u_10o0o.table_rag.benchmark import runtime


from experimental.u_10o0o.table_rag.retrieval import Retriever, json_payload
from experimental.u_10o0o.table_rag.speed import V5, gpu_state, wait_gpu_available


class TableAgent:
    def __init__(self, retriever, client, *, preserve_evidence):
        self.agent = UnifiedAgent(
            retriever, client, db_first=True, verify_evidence=preserve_evidence
        )

    def run(self, *args, **kwargs):
        return self.agent.run(*args, **kwargs)


OUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-request-evidence-20261112/v1"


def freeze():
    target = OUT / "protocol.json"
    if target.exists():
        raise FileExistsError("Frozen run exists")
    source = ROOT / "artifacts/u_10o0o/advanced-table-rag-db-first-20261112/v1"
    qs = [
        q
        for q in read_json(source / "questions.json")
        if q["split"].startswith("official_")
    ]
    assert len(qs) == 58
    write_json(OUT / "questions.json", qs)
    for name in ("references.json", "question-metadata.json"):
        copyfile(source / name, OUT / name)
    stamps = (
        runtime()
        + [
            {"path": str(p.relative_to(ROOT)), "sha256": file_sha(p)}
            for p in sorted(
                (ROOT / "experimental/u_10o0o/table_rag_unified").glob("*.py")
            )
            if not p.name.startswith("test_")
        ]
        + [
            {
                "path": str(Path(__file__).relative_to(ROOT)),
                "sha256": file_sha(__file__),
            }
        ]
    )
    for row in stamps:
        p = OUT / "executed-code" / row["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / row["path"], p)
    write_json(
        target,
        {
            "questions_sha256": file_sha(OUT / "questions.json"),
            "references_sha256": file_sha(OUT / "references.json"),
            "question_metadata_sha256": file_sha(OUT / "question-metadata.json"),
            "runtime_files": stamps,
            "arms": {
                "A": "legacy planner and round-robin merge",
                "B": "clarification instruction and observed selected-source preservation",
            },
            "embedding": "KURE-v3",
            "query_tokens": 2048,
            "reranker": "KURE nano",
            "reranker_tokens": 2048,
            "model": MODEL_ID,
            "reasoning": EFFORT,
            "workers": 4,
            "max_steps": MAX_STEPS,
            "prompts": {
                "A": object_sha([INSTRUCTIONS, TOOLS]),
                "B": object_sha([PRESERVATION_INSTRUCTIONS, TOOLS]),
            },
            "rrf": [0.5, 0.5, 60],
            "candidate_pages": 20,
            "final_pages": 8,
            "context_tokens": 8000,
            "vectors_sha256": file_sha(V5 / "vectors.json"),
            "store_sha256": file_sha(V5 / "store.json"),
            "api_retries": 0,
            "answer_generation": False,
            "authorization": "Existing .env key, no cost limit, same side conversation",
            "preparation_used_references": False,
            "retrieval_receives_question_only": True,
        },
    )
    protocol = read_json(target)
    new_tools = [t for t in unified.TOOLS if t["name"] != "finish"] + [
        unified.tool(
            "finish",
            evidence.EvidenceFinish,
            "각 요청의 근거 인용과 최종 출처를 함께 확인하고 종료한다.",
        )
    ]
    index = RecordIndex()
    protocol.update(
        question_count=58,
        arms={"A": "DB-first current", "B": "DB-first request evidence verification"},
        prompts={
            "A": object_sha([unified.INSTRUCTIONS, unified.TOOLS]),
            "B": object_sha([unified.INSTRUCTIONS + evidence.INSTRUCTIONS, new_tools]),
        },
        treatment="Request-level finish evidence, unread candidate previews and quoted-source retention validation",
        unified_index={
            "path": str(DEFAULT.relative_to(ROOT)),
            "database_sha256": index.manifest["database_sha256"],
        },
        review_all_outputs=True,
    )
    write_json(target, protocol)
    write_json(Path(__file__).parent / "protocol.json", protocol)
    write_json(
        OUT / "prompts.json",
        {
            "A": unified.INSTRUCTIONS,
            "B": unified.INSTRUCTIONS + evidence.INSTRUCTIONS,
            "A_tools": unified.TOOLS,
            "B_tools": new_tools,
        },
    )
    print("Frozen 58-question A/B; no references read", flush=True)


def run():
    protocol = read_json(OUT / "protocol.json")
    check_runtime(protocol["runtime_files"])
    assert file_sha(OUT / "questions.json") == protocol["questions_sha256"]
    assert file_sha(V5 / "vectors.json") == protocol["vectors_sha256"]
    assert file_sha(V5 / "store.json") == protocol["store_sha256"]
    qs = read_json(OUT / "questions.json")
    for arm in ("A", "B"):
        (OUT / arm / "results").mkdir(parents=True, exist_ok=True)
    wait_gpu_available()
    tick = perf_counter()
    retriever = Retriever(V5, device="cpu_gpu_rerank")
    load_seconds = perf_counter() - tick
    retriever.search(qs[0]["question"])
    client = StructuredLunaInterpreter().client
    agents = {
        a: TableAgent(retriever, client, preserve_evidence=a == "B") for a in ("A", "B")
    }
    tasks = [
        (arm, q)
        for i, q in enumerate(qs)
        for arm in (("A", "B") if i % 2 == 0 else ("B", "A"))
    ]

    def one(task):
        arm, q = task
        folder = OUT / arm / "results"
        key = object_sha(q["id"])
        dest = folder / (key + ".json")
        if dest.exists():
            row = read_json(dest)
            assert row["question_sha256"] == object_sha(q["question"]) and row[
                "protocol_sha256"
            ] == file_sha(OUT / "protocol.json")
            return row
        start = folder / (key + ".started.json")
        if start.exists():
            raise RuntimeError(
                "Unfinished paid request; no automatic retry: " + arm + q["id"]
            )
        write_json(
            start,
            {"id": q["id"], "arm": arm, "question_sha256": object_sha(q["question"])},
        )
        result = agents[arm].run(
            q["question"],
            checkpoint=lambda row: write_json(folder / (key + ".progress.json"), row),
        )
        pages = context_pages(result["context"])
        tokens = result["context"].token_count + _count_tokens(
            json_payload(result["sql_results"])
        )
        assert len(pages) <= 8 and tokens <= 8000 and len(result["ranked"]) <= 20

        def rows(v):
            return [_source_info(p, s) for p, s in v]

        row = {
            "id": q["id"],
            "question_id": q["question_id"],
            "split": q["split"],
            "arm": arm,
            "question_sha256": object_sha(q["question"]),
            "protocol_sha256": file_sha(OUT / "protocol.json"),
            "traces": {
                "table_rag": {
                    "pool": rows(result["searches"][0]["ranked"]),
                    "ranked": rows(result["ranked"]),
                    "context_tokens": tokens,
                    "context_sha256": result["context"].context_hash,
                }
            },
            "contexts": {"table_rag": pages},
            "finish": result["finish"],
            "turns": result["turns"],
            "sql_results": result["sql_results"],
            "sql_attempts": result["sql_attempts"],
            "selection_audit": result["selection_audit"],
            "searches": [
                {**s, "ranked": rows(s["ranked"])} for s in result["searches"]
            ],
            "retrieval_seconds": result["seconds"],
            "answers": {},
        }
        write_json(dest, row)
        if any(
            t.get("api", {}).get("http_status") in {401, 403, 429} for t in row["turns"]
        ):
            raise RuntimeError("Authentication/rate-limit stop; no retry")
        return row

    started = perf_counter()
    done = []
    try:
        for task in tasks[:2]:
            r = one(task)
            done.append(r)
            print(
                f"Smoke {r['arm']}: calls={len(r['turns'])} finish={r['finish']['status']}",
                flush=True,
            )
        with ThreadPoolExecutor(max_workers=4) as pool:
            for r in pool.map(one, tasks[2:]):
                done.append(r)
                if len(done) % 20 == 0:
                    print(
                        f"Saved {len(done)}/{len(tasks)} {r['arm']} {r['id']}",
                        flush=True,
                    )
    finally:
        client.close()
    check_runtime(protocol["runtime_files"])
    for arm in ("A", "B"):
        selected = [r for r in done if r["arm"] == arm]
        assert len(selected) == len(qs)
        write_json(
            OUT / arm / "run.json",
            {
                "status": "complete",
                "count": len(selected),
                "protocol_sha256": file_sha(OUT / "protocol.json"),
                "retrieval_read_references": False,
                "api_calls": sum(len(r["turns"]) for r in selected),
                "files": {
                    str((Path("results") / (object_sha(r["id"]) + ".json"))): file_sha(
                        OUT / arm / "results" / (object_sha(r["id"]) + ".json")
                    )
                    for r in selected
                },
            },
        )
    write_json(
        OUT / "run.json",
        {
            "status": "complete",
            "count": len(done),
            "model_load_seconds": load_seconds,
            "wall_seconds": perf_counter() - started,
            "gpu": gpu_state(),
        },
    )
    print(
        f"Complete:{len(done)} saved results. Labels not read by retrieval.", flush=True
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["freeze", "run"])
    args = p.parse_args()
    with integrated_runtime():
        globals()[args.command]()
