"""Isolated live HG ON/OFF experiment; reuse the current TableRAG pipeline."""

import argparse
from contextvars import ContextVar
from importlib import import_module
from pathlib import Path
from shutil import copyfile
from time import perf_counter

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    object_sha,
    write_json,
)
from experimental.u_10o0o.table_rag import retrieval
from experimental.u_10o0o.table_rag.agent import (
    TableAgent,
    INSTRUCTIONS,
    TOOLS,
    MODEL_ID,
    EFFORT,
    MAX_STEPS,
)
from experimental.u_10o0o.table_rag.benchmark import runtime
from experimental.u_10o0o.table_rag.speed import V5

NAME = "advanced-table-rag-hg-ablation-20261111"
OUT = ROOT / "artifacts/u_10o0o" / NAME / "v1"
RECORDS = Path(__file__).parent
BASE = "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111"
SOURCE = ROOT / "artifacts/u_10o0o/advanced-table-rag-official-category-20261111/v1"
ARM = ContextVar("hg_ablation_arm", default="A")
TRACE = ContextVar("hg_ablation_trace", default=None)
ORIGINAL_COMPLETE = retrieval.complete_sources


def controlled_complete(question, ranked, lookup, storage, context_tokens):
    """Only toggle the existing post-rerank native HG/source enrichment."""
    started = perf_counter()
    if ARM.get() == "B":
        value = ranked, (ranked, None, [], "disabled", None), "disabled"
    else:
        value = ORIGINAL_COMPLETE(question, ranked, lookup, storage, context_tokens)
    elapsed = perf_counter() - started
    trace = TRACE.get()
    if trace is not None:
        trace.append(
            {
                "enabled": ARM.get() == "A",
                "seconds": elapsed,
                "before": [retrieval.source_id(p) for p, _ in ranked],
                "after": [retrieval.source_id(p) for p, _ in value[1][0]],
                "route": value[2],
            }
        )
    return value


class ObservedRetriever(retrieval.Retriever):
    def search(self, question):
        traces = []
        token = TRACE.set(traces)
        try:
            result = super().search(question)
            assert len(traces) == 1
            result["hg_control"] = traces[0]
            return result
        finally:
            TRACE.reset(token)


class HGAgent(TableAgent):
    def __init__(self, retriever, client, *, preserve_evidence):
        self.ablation_arm = "B" if preserve_evidence else "A"
        super().__init__(retriever, client, preserve_evidence=False)

    def run(self, question, checkpoint=None):
        token = ARM.set(self.ablation_arm)
        try:
            return super().run(question, checkpoint)
        finally:
            ARM.reset(token)


def freeze():
    if OUT.exists():
        raise FileExistsError("Do not overwrite an existing run")
    OUT.mkdir(parents=True)
    for name in ("questions.json", "references.json", "question-metadata.json"):
        copyfile(SOURCE / name, OUT / name)
    copyfile(
        ROOT / BASE.replace(".", "/") / "dataset-manifest.json",
        RECORDS / "dataset-manifest.json",
    )
    stamps = runtime() + [
        {
            "path": str(Path(__file__).relative_to(ROOT)),
            "sha256": file_sha(__file__),
        },
        {
            "path": str(
                (ROOT / BASE.replace(".", "/") / "run_benchmark.py").relative_to(ROOT)
            ),
            "sha256": file_sha(ROOT / BASE.replace(".", "/") / "run_benchmark.py"),
        },
    ]
    for row in stamps:
        target = OUT / "executed-code" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / row["path"], target)
    write_json(
        OUT / "protocol.json",
        {
            "questions_sha256": file_sha(OUT / "questions.json"),
            "references_sha256": file_sha(OUT / "references.json"),
            "question_metadata_sha256": file_sha(OUT / "question-metadata.json"),
            "runtime_files": stamps,
            "arms": {
                "A": "current TableRAG, native HG ON",
                "B": "same TableRAG, native HG OFF",
            },
            "only_changed_step": "post-KURE complete_sources_with_dates bypass in every search",
            "sqlite_preserved": True,
            "live_planner_each_arm": True,
            "preserve_evidence": False,
            "model": MODEL_ID,
            "reasoning": EFFORT,
            "max_steps": MAX_STEPS,
            "prompt_sha256": object_sha([INSTRUCTIONS, TOOLS]),
            "vectors_sha256": file_sha(V5 / "vectors.json"),
            "store_sha256": file_sha(V5 / "store.json"),
            "embedding": "KURE-v3",
            "embedding_device": "cpu",
            "embedding_dtype": "bfloat16",
            "reranker": "KURE nano",
            "reranker_device": "cuda",
            "reranker_dtype": "float32",
            "reranker_batch": 4,
            "cpu_threads": 2,
            "workers": 4,
            "query_max_tokens": 2048,
            "reranker_max_tokens": 2048,
            "sparse_top": 50,
            "dense_top": 50,
            "rrf": [0.5, 0.5, 60],
            "candidate_pages": 20,
            "final_pages": 8,
            "context_tokens": 8000,
            "answer_generation": False,
            "api_retries": 0,
            "authorization": "Current side-conversation user approved existing key and no monetary cap",
            "retrieval_receives_question_only": True,
            "retrieval_reads_references": False,
            "latency": "Warm models; query embedding through final context including Luna, SQLite and lock wait; four concurrent requests",
            "semantic_review": "Both outputs for every strict miss, plus previously documented ambiguous items; final returned pages/retained SQL only",
        },
    )
    print(
        "Frozen HG ON/OFF: 278 questions per arm; fresh Luna plans; answer generation excluded",
        flush=True,
    )


def run():
    old = import_module(BASE + ".run_benchmark")
    old.OUT = OUT
    old.Retriever = ObservedRetriever
    old.TableAgent = HGAgent
    retrieval.complete_sources = controlled_complete
    try:
        old.run()
    finally:
        retrieval.complete_sources = ORIGINAL_COMPLETE


def score():
    old = import_module(BASE + ".score")
    old.OUT, old.RECORDS = OUT, RECORDS
    old.main()


def review():
    old = import_module(BASE + ".review")
    old.OUTPUT, old.RECORDS = OUT, RECORDS
    old.main()


def selfcheck():
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    page = {"book_id": "AS001", "page": 1, "file_name": "AS001.pdf", "text": "sample"}
    ranked = [(page, 0.5)]
    expected = (ranked, (ranked, None, [], "test", None), "test")

    def one(arm):
        token = ARM.set(arm)
        traces = []
        trace_token = TRACE.set(traces)
        try:
            with patch(
                __name__ + ".ORIGINAL_COMPLETE", return_value=expected
            ) as original:
                result = controlled_complete("sample", ranked, None, None, 8000)
                assert original.call_count == int(arm == "A")
                assert result[1][0] is ranked
                assert traces[0]["enabled"] is (arm == "A")
                assert result[2] == ("test" if arm == "A" else "disabled")
        finally:
            TRACE.reset(trace_token)
            ARM.reset(token)

    one("A")
    one("B")

    def isolated(arm):
        t = ARM.set(arm)
        try:
            return ARM.get()
        finally:
            ARM.reset(t)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(isolated, ["A", "B"] * 10)) == ["A", "B"] * 10
    assert ARM.get() == "A"
    assert not HGAgent(None, object(), preserve_evidence=True).preserve_evidence
    assert not HGAgent(None, object(), preserve_evidence=False).preserve_evidence
    print("HG bypass, ON forwarding, arm isolation, unchanged planner policy: passed")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["freeze", "run", "score", "review", "selfcheck"])
    globals()[p.parse_args().command]()
