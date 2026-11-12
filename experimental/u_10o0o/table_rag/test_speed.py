"""Search identity and resource overlap without real models or API calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace as NS

import numpy as np
import pytest

from experimental.u_10o0o.table_rag import speed
from experimental.u_10o0o.table_rag.retrieval import Retriever


@pytest.fixture
def base(monkeypatch):
    pages = [
        {
            "book_id": "T",
            "file_name": "T.pdf",
            "page": i,
            "text": f"original source {i}",
        }
        for i in (1, 2)
    ]
    r = object.__new__(Retriever)
    r.lock, r.calls = Lock(), 0
    r.embedding_lock, r.reranking_lock = Lock(), Lock()
    r.pages = r.units = pages
    r.page_map = {("T", p["page"]): p for p in pages}
    r.vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    r.embedder = NS()
    r.engine = NS(lookup=None, storage=None)
    r.reranker_device = "cuda"
    r.store = NS(by_page={("T", 1): ["t1"], ("T", 2): ["t2"]})
    r.bm25 = NS(fielded=lambda q, top_k: [(pages[0], 1.0), (pages[1], 0.5)])

    class Reranker:
        max_length = 2048

        def tokenizer(self, queries, texts, **kwargs):
            return {"input_ids": [[1, 2, 3] for _ in texts]}

        def predict(self, pairs, **kwargs):
            return np.array([1.0 - 0.2 * i for i in range(len(pairs))])

    r.reranker = Reranker()

    def encode(*args, **kwargs):
        return np.array([[1.0, 0.0]], dtype=np.float32)

    def complete(q, ranked, *args):
        return ranked, (ranked, None, [], "fallback", None), "none"

    monkeypatch.setattr(speed, "encode", encode)
    monkeypatch.setattr("experimental.u_10o0o.table_rag.retrieval.encode", encode)
    monkeypatch.setattr(speed, "complete_sources", complete)
    monkeypatch.setattr(
        "experimental.u_10o0o.table_rag.retrieval.complete_sources", complete
    )
    return r


@pytest.mark.parametrize("arm", speed.ARMS)
def test_instrumented_search_preserves_original_contract(base, arm):
    original = base.search("question")
    measured = speed.ProfiledRetriever(base)
    measured.configure(arm)
    actual = measured.search("question")
    assert speed.search_signature(actual) == speed.search_signature(original)
    assert [s for _, s in actual["ranked"]] == [s for _, s in original["ranked"]]
    assert actual["seconds"] >= sum(actual["stage_seconds"].values()) - 0.01


@pytest.mark.parametrize("profiled", [False, True])
def test_split_allows_cpu_embedding_during_other_gpu_inference(
    base, monkeypatch, profiled
):
    gpu_started, second_encoded = Event(), Event()

    def encode(model, texts, **kwargs):
        if texts == ["second"]:
            assert gpu_started.is_set()
            second_encoded.set()
        return np.array([[1.0, 0.0]], dtype=np.float32)

    original_predict = base.reranker.predict

    def predict(pairs, **kwargs):
        if pairs[0][0] == "first":
            gpu_started.set()
            assert second_encoded.wait(5), "CPU work was blocked by GPU lock"
        return original_predict(pairs, **kwargs)

    monkeypatch.setattr(speed, "encode", encode)
    monkeypatch.setattr("experimental.u_10o0o.table_rag.retrieval.encode", encode)
    base.reranker.predict = predict
    r = speed.ProfiledRetriever(base) if profiled else base
    if profiled:
        r.configure("B")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(r.search, "first")
        assert gpu_started.wait(5)
        second = pool.submit(r.search, "second")
        assert len(first.result(timeout=5)["ranked"]) == 2
        assert len(second.result(timeout=5)["ranked"]) == 2


def test_sql_semantics_exclude_only_timing():
    a = {"status": "ok", "rows": [{"amount": "0.10"}], "seconds": 1}
    b = {**a, "seconds": 2}
    assert speed.semantic_sql(a) == speed.semantic_sql(b)
    assert speed.semantic_sql(a) != speed.semantic_sql(
        {**b, "rows": [{"amount": "0.11"}]}
    )


def test_timing_sample_is_label_independent():
    rows = {
        f"{group}:{i}": {"irrelevant_reference": i}
        for group in speed.GROUPS
        for i in range(6)
    }
    first = speed.select_sample(rows)
    second = speed.select_sample(dict(reversed(list(rows.items()))))
    assert first == second and len(set(first)) == 15


def test_replay_reconstructs_saved_context_without_model_api(base):
    r = speed.ProfiledRetriever(base)
    result = r.search("fixture question")
    _, context, _ = r.context([result["ranked"]])
    stored = {
        **result,
        "ranked": [speed._source_info(p, s) for p, s in result["ranked"]],
    }
    saved = {
        "id": "fixture:1",
        "searches": [stored],
        "sql_attempts": [],
        "turns": [{"tool": "finish", "status": "finished"}],
        "finish": {"selected_sources": []},
        "traces": {"table_rag": {"context_sha256": context.context_hash}},
        "sql_results": [],
    }
    actual = speed.replay(r, saved)
    assert actual["same_saved_context"] and actual["same_saved_searches"]
    assert actual["sql_equal"] and actual["same_saved_sql"]
    assert actual["tokens"] <= 8000


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_default_retriever_serializes_each_shared_model(base, monkeypatch, device):
    from collections import Counter
    from contextlib import contextmanager
    from time import sleep

    active, peak = Counter(), Counter()
    monitor = Lock()

    @contextmanager
    def model_call(name):
        with monitor:
            active[name] += 1
            active["total"] += 1
            peak[name] = max(peak[name], active[name])
            peak["total"] = max(peak["total"], active["total"])
        try:
            sleep(0.01)
            yield
        finally:
            with monitor:
                active[name] -= 1
                active["total"] -= 1

    def encode(*args, **kwargs):
        with model_call("embedding"):
            return np.array([[1.0, 0.0]], dtype=np.float32)

    original = base.reranker.predict

    def predict(pairs, **kwargs):
        with model_call("reranking"):
            return original(pairs, **kwargs)

    base.reranker_device = device
    base.reranker.predict = predict
    monkeypatch.setattr("experimental.u_10o0o.table_rag.retrieval.encode", encode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(base.search, [f"query {i}" for i in range(12)]))
    assert len(results) == base.calls == 12
    assert peak["embedding"] == peak["reranking"] == 1
    if device == "cpu":
        assert peak["total"] == 1
