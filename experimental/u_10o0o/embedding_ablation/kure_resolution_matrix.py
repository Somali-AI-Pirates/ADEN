"""Complete the 2x2 query/reranker token comparison without changing saved runs."""

import argparse
from time import perf_counter
from unittest.mock import patch

import numpy as np
import torch

from experimental.u_10o0o.pattern_retrieval.common import (
    append_json,
    check_runtime,
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import loaded_runtime
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    score_item,
)

from . import kure_resolution as base
from . import kure_resolution_review as review

EXTRA = {"F": (64, 1024, 20, "head3"), "G": (128, 1024, 20, "head3")}
ARMS = {**base.ARMS, **EXTRA}


def check(output, *, complete=True):
    frozen, manifest = base.check(base.DEFAULT_RUN, output, complete=True)
    if complete:
        extra = read_json(output / "matrix-manifest.json")
        if extra["status"] != "retrieved":
            raise ValueError("Save all extra conditions before scoring")
        check_runtime(extra["runtime_files"])
        for a in EXTRA:
            for name in ("traces", "contexts"):
                if file_sha(output / a / f"{name}.jsonl") != extra["results"][a][name]:
                    raise ValueError("Extra retrieval changed")
    return frozen, manifest


def retrieve(output):
    check(output, complete=False)
    if any((output / a).exists() for a in EXTRA):
        raise FileExistsError("Preserve saved extra conditions")
    questions = read_json(output / "questions.json")
    saved = {a: read_jsonl(output / a / "traces.jsonl") for a in ("A", "B", "D", "E")}
    engine, _ = base.common.make_engine()
    engine.reranker.max_seq_length = 1024
    pages = {base.common.key(p): p for p in engine.pages}
    runtime = loaded_runtime()
    for a in EXTRA:
        (output / a).mkdir()
    added = 0
    with patch.dict(base.ARMS, EXTRA):
        for i, q in enumerate(questions):
            cache = {
                base.common.key(p): float(p["score"])
                for a in ("D", "E")
                for p in saved[a][i]["reranked_pool"]
            }
            for a, source in (("F", "A"), ("G", "B")):
                t = saved[source][i]
                pool = [(pages[base.common.key(p)], p["score"]) for p in t["pool"]]
                missing = [p for p, _ in pool if base.common.key(p) not in cache]
                if missing:
                    values = engine.reranker.predict(
                        [(q["question"], p["text"]) for p in missing],
                        batch_size=base.config()["rerank_batch_size"],
                        show_progress_bar=False,
                    )
                    cache.update(
                        zip(
                            [base.common.key(p) for p in missing],
                            map(float, values),
                            strict=True,
                        )
                    )
                    added += len(missing)
                ranked = sorted(
                    ((p, cache[base.common.key(p)]) for p, _ in pool),
                    key=lambda x: -x[1],
                )
                trace, context = base.pack(engine, q, a, pool, ranked, t["scope"])
                for field in ("sparse_candidates", "dense_candidates"):
                    if field in t:
                        trace[field] = t[field]
                append_json(output / a / "traces.jsonl", trace)
                append_json(output / a / "contexts.jsonl", context)
            if (i + 1) % 40 == 0:
                base.log(f"Saved F/G {i + 1}/280")
    check_runtime(runtime)
    write_json(
        output / "matrix-manifest.json",
        {
            "status": "retrieved",
            "arms": EXTRA,
            "runtime_files": runtime,
            "additional_rerank_pairs": added,
            "protocol_sha256": file_sha(base.RECORDS / "token-matrix.md"),
            "external_api_calls": 0,
            "results": {
                a: {
                    n: file_sha(output / a / f"{n}.jsonl")
                    for n in ("traces", "contexts")
                }
                for a in EXTRA
            },
        },
    )
    check(output)
    base.log(f"Additional token matrix saved; new pairs={added}")


def score(output):
    frozen, _ = check(output)
    if (output / "matrix-scores.json").exists():
        raise FileExistsError("Preserve scores")
    data = read_json(output / "original-scores.json")
    refs, pages = review.references(frozen), review.source_pages()
    for a in EXTRA:
        ts, cs = (read_jsonl(output / a / f"{n}.jsonl") for n in ("traces", "contexts"))
        data["rows"][a] = [
            score_item(m, refs[m["suite"]][m["question_id"]], t, c, pages)
            for m, t, c in zip(frozen["items"], ts, cs, strict=True)
        ]
        data["summary"][a] = aggregates(data["rows"][a])
        data["pre_review"][a] = [
            dict(
                id=r["id"],
                **review.semantic_result(r, c, read_json(output / "pre-review.json")),
            )
            for r, c in zip(data["rows"][a], cs, strict=True)
        ]
    qs = {q["id"]: q["question"] for q in read_json(output / "questions.json")}
    packet = {}
    for a in ARMS:
        cs = read_jsonl(output / a / "contexts.jsonl")
        for r, c in zip(data["rows"][a], cs, strict=True):
            if not r["scored"] or r["stages"]["final"][r["complete_metric"]]:
                continue
            row = packet.setdefault(
                r["id"],
                {
                    "question": qs[r["id"]],
                    "reference": refs[r["suite"]][r["question_id"]],
                    "contexts": {},
                    "stages": {},
                },
            )
            row["contexts"][a] = c["pages"]
            row["stages"][a] = {
                s: v[r["complete_metric"]] for s, v in r["stages"].items()
            }
    for a, b, field in (
        ("A", "F", "pool"),
        ("B", "G", "pool"),
        ("D", "G", "reranked_pool"),
    ):
        left, right = (read_jsonl(output / x / "traces.jsonl") for x in (a, b))
        equal = sum(x[field] == y[field] for x, y in zip(left, right, strict=True))
        if equal != len(qs):
            raise ValueError(f"Uncontrolled comparison {a}/{b}/{field}: {equal}")
        data["controlled_comparisons"][f"{a}_{b}_{field}"] = equal
    write_json(output / "matrix-scores.json", data)
    write_json(output / "matrix-failure-review-packet.json", packet)
    base.log(f"Seven conditions scored; {len(packet)} distinct failed questions")


def timing(output):
    _, manifest = check(output)
    target = output / "matrix-timing.json"
    if target.exists():
        raise FileExistsError("Preserve timing")
    engine, scope = base.common.make_engine()
    model, index = base.load_model(), base.MaxSimIndex(base.DEFAULT_RUN)
    byid = {q["id"]: q for q in read_json(output / "questions.json")}
    sample = [byid[q] for q in manifest["timing_ids"]]
    samples = []

    def once(q, arm):
        limit, cap, k, _ = ARMS[arm]
        base.configure_query(model, limit)
        base.query_lengths(model, [q], limit)
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
        pool, _, _, decision = base.candidate_pool(
            engine, scope, q["question"], scores, k
        )
        fused = perf_counter()
        values = engine.reranker.predict(
            [(q["question"], p["text"]) for p, _ in pool],
            batch_size=base.config()["rerank_batch_size"],
            show_progress_bar=False,
        )
        ranked = sorted(
            zip([p for p, _ in pool], map(float, values), strict=True),
            key=lambda x: -x[1],
        )
        torch.cuda.synchronize()
        reranked = perf_counter()
        trace, context = base.pack(engine, q, arm, pool, ranked, decision)
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
            "final_pages": [base.common.key(p) for p in context["pages"]],
        }

    arms = list(ARMS)
    with patch.dict(base.ARMS, EXTRA):
        for q in sample[:3]:
            for arm in arms:
                once(q, arm)
        for repeat in range(3):
            for i, q in enumerate(sample):
                rotate = (repeat + i) % len(arms)
                for arm in arms[rotate:] + arms[:rotate]:
                    samples.append({**once(q, arm), "repeat": repeat})
                if (i + 1) % 10 == 0:
                    base.log(f"Timing {repeat + 1}/3: {i + 1}/{len(sample)}")
    fields = (
        "encode_ms",
        "dense_ms",
        "sparse_fusion_ms",
        "rerank_ms",
        "context_ms",
        "total_ms",
    )
    write_json(
        target,
        {
            "scope": "fresh query to context, GPU resident; seven conditions interleaved",
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
    base.log("Seven-condition timing complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("retrieve", "score", "timing"))
    args = parser.parse_args()
    torch.set_num_threads(base.config()["cpu_threads"])
    globals()[args.command](base.OUTPUT)


if __name__ == "__main__":
    main()
