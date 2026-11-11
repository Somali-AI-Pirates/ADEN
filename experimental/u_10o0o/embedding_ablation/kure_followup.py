"""KURE-v2 candidate preservation and source-scope experiments, without API calls."""

import argparse
import re
from collections import Counter
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from experimental.u_10o0o import advanced_search, advanced_source_lookup
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    codes_in,
    file_sha,
    read_json,
    read_jsonl,
    text_sha,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    loaded_runtime,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
    KURE,
    MAX_TOKENS,
    MODELS,
    LegacyBaseline,
    _source_info,
)
from experimental.u_10o0o.search import top_pages

from .__main__ import DEFAULT_RUN, log, validate_inputs, validate_results
from .kure_v2 import MaxSimIndex, load_model

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-kure-v2-followup-20261111"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-kure-v2-followup-20261111"
ARMS = ("base20", "union40", "scope20", "scope40")
CASE_HEADER = re.compile(r"HG20\d{2}-\d+|HA\d{2}-[A-Z]\d+")
HA_SHIPPER = re.compile(r"대상(?:은| 화물은)\s+([가-힣A-Za-z0-9]+)의\s")


def key(page):
    return page.get("book_id") or Path(page["file_name"]).stem, int(page["page"])


def page_kind(page):
    text = page["text"].lstrip()
    if re.match(r"IS\d+\s*\|", text):
        return "policy_records"
    if CASE_HEADER.search(text[:400]):
        return "case_record"
    return "unknown_preserved"


class Scope:
    """A conservative source view; never asserts a contract applies to a query."""

    def __init__(self, pages, profiles):
        self.kinds = [page_kind(p) for p in pages]
        self.allowed = np.array([kind != "case_record" for kind in self.kinds])
        self.shippers = {value[0] for value in profiles.values() if value[0]}
        self.cargos = {value[1] for value in profiles.values() if value[1]}
        for p, kind in zip(pages, self.kinds, strict=True):
            if kind == "case_record":
                self.shippers.update(HA_SHIPPER.findall(p["text"][:700]))
        self.shippers = sorted(self.shippers, key=lambda v: (-len(v), v))
        self.cargos = sorted(self.cargos, key=lambda v: (-len(v), v))

    def decide(self, question):
        code = bool(codes_in(question))
        shipper = any(name in question for name in self.shippers)
        cargo = any(name in question for name in self.cargos)
        targeted = code or shipper or cargo
        return {
            "view": "full" if targeted else "general_policy_view",
            "code_present": code,
            "shipper_present": shipper,
            "cargo_present": cargo,
            "target_confirmed": False,
            "scope_confirmation_required": bool(
                not targeted and re.search(r"계약|특약", question)
            ),
        }


def candidates(engine, scope, question, scores, *, scoped, limit):
    decision = scope.decide(question)
    restricted = scoped and decision["view"] == "general_policy_view"
    if restricted:
        full = engine.index.fielded(question, top_k=len(engine.pages))
        sparse = [x for x in full if page_kind(x[0]) != "case_record"][:20]
        values = np.where(scope.allowed, scores, -np.inf)
        dense = [
            (p, s)
            for p, s in top_pages(values, engine.pages, top_k=20)
            if np.isfinite(s)
        ]
    else:
        sparse = engine.index.fielded(question, top_k=20)
        dense = top_pages(scores, engine.pages, top_k=20)
    pool = weighted_rrf(sparse, dense, (0.5, 0.5), top_k=limit)
    return pool, sparse, dense, {**decision, "filter_applied": restricted}


def pack(engine, question, arm, pool, reranked, decision):
    order = advanced_search.retain_reranked_head(pool, reranked)
    after_hg, selection, hg_route = advanced_source_lookup.complete_sources(
        question["question"],
        order,
        engine.lookup,
        engine.storage,
        engine.context_tokens,
    )
    selected, context, _, source_route, selection_route = selection
    trace = {
        "id": question["id"],
        "arm": arm,
        "question_sha256": text_sha(question["question"]),
        "pool": [_source_info(p, s) for p, s in pool],
        "post_rank_top20": [_source_info(p, s) for p, s in order[:20]],
        "ranked": [_source_info(p, s) for p, s in selected],
        "after_hg_pool": [_source_info(p, s) for p, s in after_hg],
        "reranked_pool": [_source_info(p, s) for p, s in reranked],
        "final_pages": [
            {"book_id": c.chunk.book_id, "page": c.chunk.page}
            for c in context.final_chunks
        ],
        "context_sha256": context.context_hash,
        "context_tokens": context.token_count,
        "routes": {
            "hg": hg_route,
            "source": source_route,
            "selection": selection_route,
        },
        "scope": decision,
        "work": engine._rerank_work(question["question"], pool),
    }
    packed = {
        "id": question["id"],
        "arm": arm,
        "pages": [
            {"book_id": c.chunk.book_id, "page": c.chunk.page, "text": c.chunk.text}
            for c in context.final_chunks
        ],
    }
    if len(packed["pages"]) > 8 or context.token_count > 8000:
        raise ValueError("Final context exceeds the frozen budget")
    return trace, packed


def make_engine():
    engine = LegacyBaseline()
    engine._ensure_corpus()
    engine.reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    return engine, Scope(engine.pages, engine.lookup.profiles)


def prepare(run, output):
    frozen = validate_inputs(run)
    validate_results(run, "kure_v2", frozen)
    output.mkdir(exist_ok=False)
    questions = read_json(run / "questions.json")
    write_json(output / "questions.json", questions)
    sample = []
    for source in frozen["question_sources"]:
        sample.extend(
            [x["id"] for x in frozen["items"] if x["suite"] == source["suite"]][:5]
        )
    write_json(
        output / "manifest.json",
        {
            "status": "prepared",
            "source_run": str(run.relative_to(ROOT)),
            "source_frozen_sha256": file_sha(run / "frozen.json"),
            "questions_sha256": file_sha(output / "questions.json"),
            "protocol_sha256": file_sha(RECORDS / "protocol.md"),
            "timing_ids": sample,
            "timing_repeats": 3,
            "arms": ARMS,
            "external_api_calls": 0,
            "evaluation_role": "development_regression; previously inspected questions",
        },
    )
    log("Prepared four conditions and the 30-question timing sample")


def check_prepared(run, output):
    frozen = validate_inputs(run)
    manifest = read_json(output / "manifest.json")
    for path, expected in [
        (run / "frozen.json", manifest["source_frozen_sha256"]),
        (output / "questions.json", manifest["questions_sha256"]),
        (RECORDS / "protocol.md", manifest["protocol_sha256"]),
    ]:
        if file_sha(path) != expected:
            raise ValueError(f"Frozen input changed: {path.name}")
    return frozen, manifest


def retrieve(run, output):
    frozen, manifest = check_prepared(run, output)
    if any((output / arm).exists() for arm in ARMS):
        raise FileExistsError("Use a new output directory; never overwrite results")
    if file_sha(output / "source-audit.json") != manifest["audit_sha256"]:
        raise ValueError("Pre-run review changed")
    questions = read_json(output / "questions.json")
    for arm in ARMS:
        (output / arm).mkdir()
    started = perf_counter()
    index = MaxSimIndex(run)
    queries = np.load(run / "kure_v2/question-vectors.npy", allow_pickle=False)
    scores = np.concatenate(
        [
            index.score(torch.from_numpy(queries[i : i + 4]).to("cuda")).cpu().numpy()
            for i in range(0, len(queries), 4)
        ]
    )
    del index
    torch.cuda.empty_cache()
    np.save(output / "dense-scores.npy", scores, allow_pickle=False)
    engine, scope = make_engine()
    runtime = loaded_runtime()
    by_id = {q["id"]: i for i, q in enumerate(questions)}
    old, _ = validate_results(run, "kure_v2", frozen)
    old_by_id = {t["id"]: t for t in old}
    replay_differences = []
    count = 0
    new_pairs = 0
    for batch in batch_questions(questions, frozen["items"]):
        pools = {a: [] for a in ARMS}
        details = {a: [] for a in ARMS}
        for q in batch:
            score = scores[by_id[q["id"]]]
            for scoped in (False, True):
                pool, sparse, dense, decision = candidates(
                    engine, scope, q["question"], score, scoped=scoped, limit=40
                )
                for arm in ("scope20", "scope40") if scoped else ("base20", "union40"):
                    rows = pool[:20] if arm.endswith("20") else pool
                    pools[arm].append(rows)
                    details[arm].append((sparse, dense, decision))
        new_pairs += sum(
            len({id(p) for a in ARMS for p, _ in pools[a][i]})
            for i in range(len(batch))
        )
        ranked = advanced_search.rerank_variants(engine.reranker, batch, pools, log)
        for arm in ARMS:
            for i, q in enumerate(batch):
                sparse, dense, decision = details[arm][i]
                trace, context = pack(
                    engine, q, arm, pools[arm][i], ranked[arm][i], decision
                )
                trace["sparse_top20"] = [_source_info(p, s) for p, s in sparse]
                trace["dense_top20"] = [_source_info(p, s) for p, s in dense]
                if arm == "base20":
                    prev = old_by_id[q["id"]]
                    for field in ("pool", "ranked", "final_pages"):
                        if [key(x) for x in trace[field]] != [
                            key(x) for x in prev[field]
                        ]:
                            replay_differences.append({"id": q["id"], "field": field})
                    if trace["context_sha256"] != prev["context_sha256"]:
                        replay_differences.append(
                            {"id": q["id"], "field": "context_hash"}
                        )
                append_json(output / arm / "traces.jsonl", trace)
                append_json(output / arm / "contexts.jsonl", context)
        count += len(batch)
        log(f"Saved four conditions for {count}/280 questions")
    check_runtime(runtime)
    check_prepared(run, output)
    manifest.update(
        status="retrieved",
        runtime_files=runtime,
        wall_seconds=perf_counter() - started,
        page_kind_counts=dict(Counter(scope.kinds)),
        baseline_replay_differences=replay_differences,
        rerank_unique_pairs_computed=new_pairs,
        results={
            a: {f: file_sha(output / a / f"{f}.jsonl") for f in ("traces", "contexts")}
            for a in ARMS
        },
        references_read_during_retrieval=False,
    )
    write_json(output / "manifest.json", manifest)
    log(f"Retrieval finished; replay differences: {len(replay_differences)}")


def validate_saved(run, output):
    frozen, manifest = check_prepared(run, output)
    if manifest["status"] != "retrieved":
        raise ValueError("All four conditions must finish before scoring")
    check_runtime(manifest["runtime_files"])
    saved = {}
    expected = [x["id"] for x in read_json(output / "questions.json")]
    for arm in ARMS:
        rows = []
        for name in ("traces", "contexts"):
            path = output / arm / f"{name}.jsonl"
            if file_sha(path) != manifest["results"][arm][name]:
                raise ValueError("Result fingerprint mismatch")
            values = read_jsonl(path)
            if [x["id"] for x in values] != expected:
                raise ValueError("Incomplete or reordered results")
            rows.append(values)
        for trace, context in zip(*rows, strict=True):
            if len(trace["pool"]) > (20 if arm.endswith("20") else 40):
                raise ValueError("Candidate budget exceeded")
            if len(context["pages"]) > 8 or trace["context_tokens"] > 8000:
                raise ValueError("Context budget exceeded")
            if [key(x) for x in trace["final_pages"]] != [
                key(x) for x in context["pages"]
            ]:
                raise ValueError("Sources differ from the actual context")
        saved[arm] = rows
    return frozen, saved


def timing(run, output):
    _, manifest = check_prepared(run, output)
    if (output / "timing.json").exists():
        raise FileExistsError("Do not overwrite timing")
    engine, scope = make_engine()
    model, index = load_model(), MaxSimIndex(run)
    questions = {q["id"]: q for q in read_json(output / "questions.json")}
    sample = [questions[id] for id in manifest["timing_ids"]]
    samples = []

    def once(q, arm):
        torch.cuda.synchronize()
        start = perf_counter()
        vector = model.encode_query(
            [q["question"]], batch_size=1, show_progress_bar=False
        )[0]
        torch.cuda.synchronize()
        encoded = perf_counter()
        scores = index.score(vector.to(torch.float16)[None])[0].cpu().numpy()
        searched = perf_counter()
        pool, _, _, decision = candidates(
            engine,
            scope,
            q["question"],
            scores,
            scoped=arm.startswith("scope"),
            limit=20 if arm.endswith("20") else 40,
        )
        fused = perf_counter()
        values = engine.reranker.predict(
            [(q["question"], p["text"]) for p, _ in pool],
            batch_size=32,
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
            "sparse_scope_fusion_ms": (fused - searched) * 1000,
            "rerank_ms": (reranked - fused) * 1000,
            "context_ms": (end - reranked) * 1000,
            "total_ms": (end - start) * 1000,
            "pairs": len(pool),
            "context_sha256": trace["context_sha256"],
            "final_pages": [key(p) for p in context["pages"]],
        }

    for q in sample[:3]:
        for arm in ARMS:
            once(q, arm)
    for repeat in range(manifest["timing_repeats"]):
        for i, q in enumerate(sample):
            offset = (repeat + i) % len(ARMS)
            for arm in ARMS[offset:] + ARMS[:offset]:
                samples.append({**once(q, arm), "repeat": repeat})
        log(f"Online timing repeat {repeat + 1}/3 finished")
    metrics = {
        arm: {
            field: {
                "p50": float(
                    np.percentile([r[field] for r in samples if r["arm"] == arm], 50)
                ),
                "p95": float(
                    np.percentile([r[field] for r in samples if r["arm"] == arm], 95)
                ),
            }
            for field in (
                "encode_ms",
                "dense_ms",
                "sparse_scope_fusion_ms",
                "rerank_ms",
                "context_ms",
                "total_ms",
            )
        }
        for arm in ARMS
    }
    write_json(
        output / "timing.json",
        {
            "scope": "warm batch-1 encode through context; no request cache; excludes load",
            "warmup_per_arm": 3,
            "repeats": 3,
            "samples": samples,
            "metrics": metrics,
            "runtime_files": loaded_runtime(),
        },
    )
    log("Saved online timing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "command", choices=("prepare", "audit", "retrieve", "score", "timing", "report")
    )
    args = parser.parse_args()
    torch.set_num_threads(8)
    if args.command in ("audit", "score", "report"):
        from .kure_followup_report import audit, report, score

        {"audit": audit, "score": score, "report": report}[args.command](
            args.run, args.output
        )
    else:
        {"prepare": prepare, "retrieve": retrieve, "timing": timing}[args.command](
            args.run, args.output
        )


if __name__ == "__main__":
    main()
