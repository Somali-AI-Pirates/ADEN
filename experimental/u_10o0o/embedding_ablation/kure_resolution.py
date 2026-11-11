"""Separate KURE query length, ranking policy, reranker length and candidate caps."""

import argparse
import shutil
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np
import torch

from experimental.u_10o0o import advanced_search
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import loaded_runtime
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.search import top_pages

from . import kure_followup as common
from .__main__ import DEFAULT_RUN, log, validate_inputs
from .kure_v2 import MaxSimIndex, load_model

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-kure-resolution-20261111"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-kure-resolution-20261111"
SETTINGS = Path(__file__).with_name("kure_resolution_settings.json")
ARMS = {
    "A": (64, 512, 20, "head3"),
    "B": (128, 512, 20, "head3"),
    "C": (128, 512, 20, "full"),
    "D": (128, 1024, 20, "full"),
    "E": (128, 1024, 50, "full"),
}


def config():
    value = read_json(SETTINGS)
    expected = (128, 64, 1024, 20, 50, 8, 8000)
    fields = (
        "query_max_tokens",
        "query_expansion_floor",
        "reranker_max_tokens",
        "retriever_top_k",
        "expanded_retriever_top_k",
        "final_pages",
        "context_tokens",
    )
    if tuple(value[k] for k in fields) != expected:
        raise ValueError("This frozen comparison supports the recorded settings only.")
    return value


def configure_query(model, limit):
    """Keep the trained 64-token floor, allowing complete longer queries up to limit."""
    model[0].query_expansion = {
        "strategy": "fixed" if limit == 64 else "min",
        "length": 64,
        "attend": False,
    }
    model[0].query_length = limit


def query_lengths(model, questions, limit):
    prompt = model.prompts["query"]
    lengths = [
        len(model.tokenizer(prompt + q["question"], truncation=False)["input_ids"])
        for q in questions
    ]
    if limit > 64 and any(n > limit for n in lengths):
        raise ValueError(
            f"Query needs {max(lengths)} tokens; increase the supported cap before running."
        )
    return lengths


def candidate_pool(engine, scope, question, scores, top_k):
    if top_k == 20:
        return common.candidates(engine, scope, question, scores, scoped=True, limit=40)
    decision = scope.decide(question)
    restricted = decision["view"] == "general_policy_view"
    sparse = engine.index.fielded(
        question, top_k=len(engine.pages) if restricted else top_k
    )
    if restricted:
        sparse = [x for x in sparse if common.page_kind(x[0]) != "case_record"][:top_k]
        scores = np.where(scope.allowed, scores, -np.inf)
    dense = [
        (p, s)
        for p, s in top_pages(scores, engine.pages, top_k=top_k)
        if np.isfinite(s)
    ]
    pool = weighted_rrf(sparse, dense, (0.5, 0.5), top_k=2 * top_k)
    return pool, sparse, dense, {**decision, "filter_applied": restricted}


def pack(engine, q, arm, pool, ranked, decision):
    policy = ARMS[arm][3]
    if policy == "full":
        with patch.object(
            advanced_search, "retain_reranked_head", lambda _, rows: rows
        ):
            trace, context = common.pack(engine, q, arm, pool, ranked, decision)
    else:
        trace, context = common.pack(engine, q, arm, pool, ranked, decision)
    lengths = engine.reranker.tokenizer(
        [q["question"]] * len(pool),
        [p["text"] for p, _ in pool],
        truncation=False,
        verbose=False,
    )["input_ids"]
    cap = ARMS[arm][1]
    trace["work"] = {
        "rerank_pairs": len(pool),
        "rerank_raw_tokens": sum(map(len, lengths)),
        "rerank_tokens": sum(min(len(t), cap) for t in lengths),
        "rerank_truncated": sum(len(t) > cap for t in lengths),
    }
    trace["configuration"] = dict(
        zip(
            (
                "query_max_tokens",
                "reranker_max_tokens",
                "retriever_top_k",
                "rank_policy",
            ),
            ARMS[arm],
            strict=True,
        )
    )
    return trace, context


def prepare(run, output):
    frozen, old = common.validate_saved(run, common.OUTPUT)
    config()
    output.mkdir(exist_ok=False)
    shutil.copyfile(run / "questions.json", output / "questions.json")
    (output / "A").mkdir()
    for name, values in zip(("traces", "contexts"), old["scope40"], strict=True):
        for row in values:
            append_json(output / "A" / f"{name}.jsonl", {**row, "arm": "A"})
    manifest = {
        "status": "prepared",
        "arms": ARMS,
        "settings": config(),
        "settings_sha256": file_sha(SETTINGS),
        "protocol_sha256": file_sha(RECORDS / "protocol.md"),
        "source_frozen_sha256": file_sha(run / "frozen.json"),
        "source_followup_sha256": file_sha(common.OUTPUT / "manifest.json"),
        "questions_sha256": file_sha(output / "questions.json"),
        "timing_ids": read_json(common.OUTPUT / "manifest.json")["timing_ids"],
        "references_sha256": read_json(run / "reference-freeze.json")["references"],
        "external_api_calls": 0,
        "evaluation_role": "development_regression_with_self_reviewed_sources",
        "results": {
            "A": {
                n: file_sha(output / "A" / f"{n}.jsonl") for n in ("traces", "contexts")
            }
        },
    }
    from . import kure_resolution_review as review

    review.seed(frozen, output)
    manifest["pre_review_sha256"] = file_sha(output / "pre-review.json")
    write_json(output / "manifest.json", manifest)
    log("Prepared five conditions and fixed pre-run evidence review")


def check(run, output, *, complete=False):
    frozen = validate_inputs(run)
    m = read_json(output / "manifest.json")
    for path, digest in (
        (SETTINGS, m["settings_sha256"]),
        (RECORDS / "protocol.md", m["protocol_sha256"]),
        (run / "frozen.json", m["source_frozen_sha256"]),
        (common.OUTPUT / "manifest.json", m["source_followup_sha256"]),
        (output / "questions.json", m["questions_sha256"]),
        (output / "pre-review.json", m["pre_review_sha256"]),
    ):
        if file_sha(path) != digest:
            raise ValueError(f"Frozen input changed: {path}")
    if complete:
        if m["status"] != "retrieved":
            raise ValueError("Save every retrieval before opening references.")
        check_runtime(m["runtime_files"])
        ids = [q["id"] for q in read_json(output / "questions.json")]
        for arm in ARMS:
            pairs = []
            for name in ("traces", "contexts"):
                path = output / arm / f"{name}.jsonl"
                if file_sha(path) != m["results"][arm][name]:
                    raise ValueError("Saved retrieval changed")
                rows = read_jsonl(path)
                if [r["id"] for r in rows] != ids:
                    raise ValueError("Missing or reordered questions")
                pairs.append(rows)
            for t, c in zip(*pairs, strict=True):
                if (
                    len(t["pool"]) > ARMS[arm][2] * 2
                    or len(c["pages"]) > 8
                    or t["context_tokens"] > 8000
                ):
                    raise ValueError("Budget exceeded")
                if [common.key(x) for x in t["final_pages"]] != [
                    common.key(x) for x in c["pages"]
                ]:
                    raise ValueError("Context sources differ")
    return frozen, m


def retrieve(run, output):
    _, manifest = check(run, output)
    if any((output / arm).exists() for arm in ARMS if arm != "A"):
        raise FileExistsError("Choose a new output; do not overwrite partial results")
    settings = config()
    questions = read_json(output / "questions.json")
    old = read_jsonl(output / "A/traces.jsonl")
    engine, scope = common.make_engine()
    model = load_model()
    configure_query(model, settings["query_max_tokens"])
    lengths = query_lengths(model, questions, settings["query_max_tokens"])
    changed = [i for i, n in enumerate(lengths) if n > 64]
    dense = np.load(common.OUTPUT / "dense-scores.npy").copy()
    index = MaxSimIndex(run)
    encode_started = perf_counter()
    vectors = model.encode_query(
        [questions[i]["question"] for i in changed],
        batch_size=16,
        show_progress_bar=False,
    )
    for start in range(0, len(changed), settings["query_batch_size"]):
        indices = changed[start : start + settings["query_batch_size"]]
        rows = vectors[start : start + len(indices)]
        padded = torch.nn.utils.rnn.pad_sequence(rows, batch_first=True).to(
            torch.float16
        )
        dense[indices] = index.score(padded).cpu().numpy()
    torch.cuda.synchronize()
    query_seconds = perf_counter() - encode_started
    np.save(output / "dense-scores.npy", dense, allow_pickle=False)
    write_json(
        output / "query-lengths.json",
        [
            {
                "id": q["id"],
                "tokens": n,
                "old_truncated": n > 64,
                "new_truncated": False,
            }
            for q, n in zip(questions, lengths, strict=True)
        ],
    )
    del model, index, vectors
    torch.cuda.empty_cache()
    for arm in ARMS:
        if arm != "A":
            (output / arm).mkdir()
    runtime = loaded_runtime()
    # Same original question/text pairs have the same reranker input despite a new query vector.
    cache = {
        (512, t["id"], common.key(p)): float(p["score"])
        for t in old
        for p in t["reranked_pool"]
    }
    new_pairs = defaultdict(int)
    started = perf_counter()
    for start in range(0, len(questions), 10):
        batch = questions[start : start + 10]
        details = {}
        for i, q in enumerate(batch, start):
            for k in (20, 50):
                details[q["id"], k] = candidate_pool(
                    engine, scope, q["question"], dense[i], k
                )
        for cap in (512, 1024):
            active = [a for a in ARMS if a != "A" and ARMS[a][1] == cap]
            pairs, identities = [], []
            seen = set(cache)
            for a in active:
                for q in batch:
                    for p, _ in details[q["id"], ARMS[a][2]][0]:
                        ident = (cap, q["id"], common.key(p))
                        if ident not in seen:
                            seen.add(ident)
                            identities.append(ident)
                            pairs.append((q["question"], p["text"]))
            engine.reranker.max_seq_length = cap
            if pairs:
                values = engine.reranker.predict(
                    pairs,
                    batch_size=settings["rerank_batch_size"],
                    show_progress_bar=False,
                )
                cache.update(zip(identities, map(float, values), strict=True))
                new_pairs[str(cap)] += len(pairs)
            for a in active:
                for q in batch:
                    pool, sparse, vector, decision = details[q["id"], ARMS[a][2]]
                    ranked = sorted(
                        ((p, cache[cap, q["id"], common.key(p)]) for p, _ in pool),
                        key=lambda x: -x[1],
                    )
                    trace, context = pack(engine, q, a, pool, ranked, decision)
                    trace["sparse_candidates"] = [_source_info(p, s) for p, s in sparse]
                    trace["dense_candidates"] = [_source_info(p, s) for p, s in vector]
                    append_json(output / a / "traces.jsonl", trace)
                    append_json(output / a / "contexts.jsonl", context)
        log(
            f"Saved B–E for {min(start + 10, len(questions))}/{len(questions)} questions"
        )
    check_runtime(runtime)
    check(run, output)
    manifest.update(
        status="retrieved",
        runtime_files=runtime,
        query_reencoded=len(changed),
        query_encode_and_search_seconds=query_seconds,
        new_rerank_pairs=dict(new_pairs),
        retrieval_seconds=perf_counter() - started,
        references_opened=False,
        results={
            a: {n: file_sha(output / a / f"{n}.jsonl") for n in ("traces", "contexts")}
            for a in ARMS
        },
    )
    write_json(output / "manifest.json", manifest)
    check(run, output, complete=True)
    log("All retrievals saved; scoring and source review can now open references")


def timing(run, output):
    _, manifest = check(run, output, complete=True)
    if (output / "timing.json").exists():
        raise FileExistsError("Keep timing immutable")
    settings = config()
    engine, scope = common.make_engine()
    model, index = load_model(), MaxSimIndex(run)
    by_id = {q["id"]: q for q in read_json(output / "questions.json")}
    sample = [by_id[q] for q in manifest["timing_ids"]]
    samples = []

    def once(q, arm):
        limit, cap, k, _ = ARMS[arm]
        configure_query(model, limit)
        query_lengths(model, [q], limit)
        engine.reranker.max_seq_length = cap
        torch.cuda.synchronize()
        start = perf_counter()
        vector = model.encode_query(
            [q["question"]], batch_size=1, show_progress_bar=False
        )[0].to(torch.float16)
        torch.cuda.synchronize()
        encoded = perf_counter()
        scores = index.score(vector[None])[0].cpu().numpy()
        searched = perf_counter()
        pool, _, _, decision = candidate_pool(engine, scope, q["question"], scores, k)
        fused = perf_counter()
        values = engine.reranker.predict(
            [(q["question"], p["text"]) for p, _ in pool],
            batch_size=settings["rerank_batch_size"],
            show_progress_bar=False,
        )
        ranked = sorted(
            zip([p for p, _ in pool], map(float, values), strict=True),
            key=lambda x: -x[1],
        )
        torch.cuda.synchronize()
        reranked = perf_counter()
        trace, context = pack(engine, q, arm, pool, ranked, decision)
        end = perf_counter()
        return {
            "id": q["id"],
            "arm": arm,
            "encode_ms": (encoded - start) * 1000,
            "dense_ms": (searched - encoded) * 1000,
            "sparse_fusion_ms": (fused - searched) * 1000,
            "rerank_ms": (reranked - fused) * 1000,
            "context_ms": (end - reranked) * 1000,
            "total_ms": (end - start) * 1000,
            "pairs": len(pool),
            "context_sha256": trace["context_sha256"],
            "final_pages": [common.key(p) for p in context["pages"]],
        }

    arms = list(ARMS)
    for q in sample[:3]:
        for arm in arms:
            once(q, arm)
    for repeat in range(3):
        for i, q in enumerate(sample):
            rotate = (repeat + i) % len(arms)
            for arm in arms[rotate:] + arms[:rotate]:
                samples.append({**once(q, arm), "repeat": repeat})
            if (i + 1) % 10 == 0:
                log(f"Timing {repeat + 1}/3: {i + 1}/{len(sample)}")
    fields = (
        "encode_ms",
        "dense_ms",
        "sparse_fusion_ms",
        "rerank_ms",
        "context_ms",
        "total_ms",
    )
    write_json(
        output / "timing.json",
        {
            "scope": "fresh query encoding through context; GPU resident; no answer generation",
            "samples": samples,
            "metrics": {
                a: {
                    f: {
                        f"p{p}": float(
                            np.percentile([r[f] for r in samples if r["arm"] == a], p)
                        )
                        for p in (50, 95)
                    }
                    for f in fields
                }
                for a in arms
            },
            "warmup": 3,
            "repeats": 3,
            "external_api_calls": 0,
        },
    )
    log("Timing complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "command", choices=("prepare", "retrieve", "score", "timing", "report")
    )
    args = parser.parse_args()
    torch.set_num_threads(config()["cpu_threads"])
    if args.command in ("score", "report"):
        from . import kure_resolution_review

        getattr(kure_resolution_review, args.command)(args.run, args.output)
    else:
        globals()[args.command](args.run, args.output)


if __name__ == "__main__":
    main()
