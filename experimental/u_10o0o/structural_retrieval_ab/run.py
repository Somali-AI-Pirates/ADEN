"""Freeze all arms, run retrieval, and save outputs before opening score references."""

import argparse
import asyncio
import copy
import time
from .common import (
    OUT,
    REC,
    PRIOR,
    PACKAGE,
    BUNDLE,
    ROOT,
    read,
    write,
    file_sha,
    activate,
    check_frozen,
    key,
)
from .paragraphs import augmented
from .retrieval import WideRetriever


def tasks(arms=("A", "R", "P")):
    result = []
    for q in read(OUT / "questions.json"):
        for repeat in range(1, 4 if q["split"] == "official_additional" else 2):
            order = list(arms)
            offset = (len(result) // len(arms)) % len(arms)
            for arm in order[offset:] + order[:offset]:
                result.append((q, repeat, arm))
    return result


def freeze():
    questions = read(OUT / "questions.json")
    assert len(questions) == 320 and len({q["id"] for q in questions}) == 320
    spec = {
        "code": {p.name: file_sha(p) for p in sorted(PACKAGE.glob("*.py"))},
        "inputs": {
            n: file_sha(OUT / n)
            for n in [
                "questions.json",
                "rubric.json",
                "holdout-reviewed.json",
                "paragraph-index/units.json",
                "paragraph-index/vectors.npy",
                "paragraph-index/manifest.json",
            ]
        },
        "bundle": {
            p.name: file_sha(p)
            for p in sorted(BUNDLE.iterdir())
            if p.is_file() and p.suffix in {".json", ".npy", ".sqlite3"}
        },
        "dependencies": {
            str(p.relative_to(ROOT)): file_sha(p)
            for p in sorted((PACKAGE.parent / "additional_evidence_ab").glob("*.py"))
        },
        "runtime_revision": read(PRIOR / "protocol.json")["revision"],
        "prior_protocol_sha256": file_sha(PRIOR / "protocol.json"),
        "arms": ["A", "S", "V", "R", "P"],
        "count_per_arm": 380,
        "new_graph_runs": 1050,
        "reused_graph_runs": 90,
        "new_selector_calls": 760,
        "model": "gpt-6-luna",
        "reasoning": "medium",
        "device": "cpu_gpu_rerank",
        "final_pages": 8,
        "final_tokens": 8000,
        "final_candidates": 20,
        "R_rerank_limit": 100,
        "S_V_search_replay": True,
        "answer_generation": False,
        "workers": 2,
        "auto_retry": False,
        "human_review": False,
        "quality_results_read_before_freeze": False,
    }
    path = OUT / "protocol.json"
    if path.exists():
        assert read(path) == spec, "Frozen protocol differs"
    else:
        write(path, spec)
        write(REC / "protocol.json", spec)
    check_frozen()
    return spec


def reuse_controls():
    for q, repeat, arm in tasks(("A",)):
        if q["split"] != "official_additional":
            continue
        path = OUT / "runs" / (key(repeat, arm, q["id"]) + ".json")
        old = PRIOR / "runs" / f"r{repeat}-A-{q['question_id']}.json"
        previous = read(old)
        assert previous["question"] == q["question"]
        row = copy.deepcopy(previous)
        row.update(
            key=path.stem,
            question_id=q["id"],
            original_question_id=q["question_id"],
            split=q["split"],
            reused_from=str(old.relative_to(PRIOR)),
            reused_sha256=file_sha(old),
            new_api_calls=0,
            protocol_sha256=file_sha(OUT / "protocol.json"),
        )
        row["usage"] = {
            k: v for k, v in row["usage"].items() if k not in {"calls", "attempts"}
        }
        if path.exists():
            assert read(path) == row
        else:
            write(path, row)


async def run(limit=None):
    protocol = check_frozen()
    activate()
    reuse_controls()
    from app.rag.corpus import load_corpus
    from app.rag.graph import TableRagGraph
    from app.rag.model import load_model, ModelCalls
    from app.rag.records import RecordIndex
    from app.rag.retrieval import load_retriever, TableRetriever
    from app.rag.service import _retrieval_record
    from app.rag.evidence import token_count

    corpus = load_corpus(BUNDLE)
    base = load_retriever(corpus, device=protocol["device"])
    pc = augmented(corpus)
    paragraph = TableRetriever(
        pc,
        embedder=base.embedder,
        reranker=base.reranker,
        device=base.device,
        embedding_device=base.embedding_device,
        reranker_device=base.reranker_device,
    )
    paragraph._embedding_lock = base._embedding_lock
    paragraph._reranker_lock = base._reranker_lock
    retrievers = {"A": base, "R": WideRetriever(base), "P": paragraph}
    model = load_model()
    index = RecordIndex(BUNDLE)
    graphs = {
        arm: TableRagGraph(
            search=r.search,
            page_lookup=corpus.pages_by_source.get,
            table_store=corpus.table_store,
            record_index=index,
            plan_overhead_tokens=model.planner_overhead(token_count),
        )
        for arm, r in retrievers.items()
    }
    planned = tasks()
    pending = [
        t
        for t in planned
        if not (OUT / "runs" / (key(t[1], t[2], t[0]["id"]) + ".json")).exists()
    ]
    if limit:
        pending = pending[:limit]
    semaphore = asyncio.Semaphore(2)
    stop = asyncio.Event()
    completed = 0

    async def one(q, repeat, arm):
        nonlocal completed
        k = key(repeat, arm, q["id"])
        path = OUT / "runs" / (k + ".json")
        marker = path.with_suffix(".started.json")
        if marker.exists():
            raise RuntimeError("Interrupted run requires inspection: " + k)
        async with semaphore:
            if stop.is_set():
                return
            write(
                marker, {"key": k, "protocol_sha256": file_sha(OUT / "protocol.json")}
            )
            calls = ModelCalls(None)
            tick = time.perf_counter()

            async def plan(history, *, finish_only):
                return await model.plan(history, finish_only=finish_only, calls=calls)

            row = {
                "key": k,
                "repeat": repeat,
                "arm": arm,
                "question_id": q["id"],
                "original_question_id": q["question_id"],
                "split": q["split"],
                "question": q["question"],
                "protocol_sha256": file_sha(OUT / "protocol.json"),
            }
            try:
                result = await graphs[arm].run(
                    q["question"], planner=plan, request_id=k
                )
                candidates = tuple(
                    corpus.pages_by_source[s]
                    for s in result.evidence.candidate_source_ids
                )
                row.update(
                    _retrieval_record(
                        result, candidates, token_count(result.evidence.model_input)
                    )
                )
                row.update(
                    status=result.status,
                    termination_reason=result.termination_reason,
                    finish=result.finish.model_dump(),
                )
                assert (
                    len(candidates) <= 20
                    and len(result.evidence.pages) <= 8
                    and result.evidence.token_count <= 8000
                )
            except Exception as exc:
                row.update(status="execution_error", error_class=type(exc).__name__)
                stop.set()
            row.update(
                latency_seconds=time.perf_counter() - tick,
                model_calls=calls.calls,
                new_api_calls=len(calls.calls),
                usage={
                    k: v
                    for k, v in calls.summary().items()
                    if k not in {"calls", "attempts"}
                },
            )
            assert len(calls.calls) <= 6
            write(path, row)
            completed += 1
            if any(
                c.get("error_class")
                in {"AuthenticationError", "PermissionDeniedError", "RateLimitError"}
                for c in calls.calls
            ):
                stop.set()
            print(f"Saved {completed}/{len(pending)} {k} {row['status']}", flush=True)

    try:
        await asyncio.gather(*(one(*t) for t in pending))
    finally:
        await model.aclose()
        index.close()
    count = sum(
        (OUT / "runs" / (key(n, a, q["id"]) + ".json")).exists() for q, n, a in planned
    )
    write(
        OUT / "retrieval-status.json",
        {
            "complete": count == 1140 and not stop.is_set(),
            "count": count,
            "expected": 1140,
            "references_read": False,
        },
    )
    print("Graph runs saved", count, flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["freeze", "run"])
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    freeze() if a.command == "freeze" else asyncio.run(run(a.limit))


if __name__ == "__main__":
    main()
