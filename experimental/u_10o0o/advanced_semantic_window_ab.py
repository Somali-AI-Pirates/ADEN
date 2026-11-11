"""Compare one IS dense representation change, keeping plans and selectors fixed."""

import argparse
import os
from pathlib import Path
from time import perf_counter

from app.evaluation.dataset import load_references
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o import advanced_field_roles
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_evidence_benchmark import BROAD, supported_plan
from experimental.u_10o0o.advanced_evidence_policy import merge_request_candidates
from experimental.u_10o0o.advanced_hg_lookup import location
from experimental.u_10o0o.advanced_hg_relations import HGRelations
from experimental.u_10o0o.advanced_is_windows import ISWindowDense
from experimental.u_10o0o.advanced_planned_retrieval import resolve
from experimental.u_10o0o.advanced_search import rerank_variants, retain_reranked_head
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    logger,
    read_json,
    read_jsonl,
    text_sha,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    load_inputs,
    loaded_runtime,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    score_item,
)
from experimental.u_10o0o.search import load_index, rank_pages

ARMS = ("control", "is_windows")
ARCHIVE = Path("artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01")


def source_info(rows):
    return [
        {"book_id": location(p)[0], "page": location(p)[1], "score": float(score)}
        for p, score in rows
    ]


def save(output, experiment, question, arm, result, linked, **extra):
    pool, expanded, context, _, route = result
    if (
        len(pool) > 20
        or len(expanded) > 20
        or len(context.final_chunks) > 8
        or context.token_count > 8000
    ):
        raise ValueError("Evidence budget exceeded.")
    trace = {
        "experiment": experiment,
        "id": question["id"],
        "arm": arm,
        "question_sha256": text_sha(question["question"]),
        "pool": source_info(pool),
        "ranked": source_info(expanded),
        "after_hg_pool": source_info(linked),
        "final_pages": [
            {"book_id": p.chunk.book_id, "page": p.chunk.page}
            for p in context.final_chunks
        ],
        "context_tokens": context.token_count,
        "context_sha256": context.context_hash,
        "routes": {"selection": route},
        **extra,
    }
    context_row = {
        "experiment": experiment,
        "id": question["id"],
        "arm": arm,
        "pages": [
            {"book_id": p.chunk.book_id, "page": p.chunk.page, "text": p.chunk.text}
            for p in context.final_chunks
        ],
    }
    append_json(output / "traces.jsonl", trace)
    append_json(output / "contexts.jsonl", context_row)
    return trace


def semantic_run(questions, plans, search, windows, lookup, storage, output, log):
    archived = {(r["id"], r["arm"]): r for r in read_jsonl(ARCHIVE / "traces.jsonl")}
    replay = []
    with HGRelations(pages=search.pages) as relations:
        for number, q in enumerate(questions, 1):
            original_id = q["id"].split(":", 1)[1]
            query_cache, score_cache = {}, {}

            def score_pages(text, rows):
                missing = [
                    item
                    for item in rows
                    if (text, location(item[0])) not in score_cache
                ]
                if missing:
                    ranked = rerank_variants(
                        search.reranker,
                        [{"question": text}],
                        {"score": [missing]},
                        lambda _: None,
                    )["score"][0]
                    score_cache.update(
                        {(text, location(p)): score for p, score in ranked}
                    )
                return sorted(
                    [(p, score_cache[text, location(p)]) for p, _ in rows],
                    key=lambda r: -r[1],
                )

            def query_rank(text, arm, expand=False):
                if text not in query_cache:
                    sparse = search.index.fielded(text, top_k=20)
                    embedding = search.embedder.encode_query(
                        [text], normalize_embeddings=True, show_progress_bar=False
                    )[0]
                    query_cache[text] = {
                        "sparse": sparse,
                        "vector": embedding,
                        "ranks": {},
                    }
                cache = query_cache[text]
                if arm not in cache["ranks"]:
                    dense = (
                        rank_pages(
                            cache["vector"], search.vectors, search.pages, top_k=20
                        )
                        if arm == "control"
                        else windows.rank(cache["vector"], top_k=20)
                    )
                    union = weighted_rrf(cache["sparse"], dense, (0.5, 0.5), top_k=40)
                    base = union[:20]
                    head = score_pages(text, base)
                    cache["ranks"][arm] = {
                        "dense": dense,
                        "union": union,
                        "base": base,
                        "head20": head,
                        "order20": retain_reranked_head(base, head),
                    }
                rank = cache["ranks"][arm]
                if expand and "head40" not in rank:
                    head = score_pages(text, rank["union"])
                    rank.update(
                        head40=head,
                        order40=retain_reranked_head(rank["base"], head)[:20],
                    )
                return rank

            plan, status = supported_plan(q, plans.get(original_id), lookup, "broad")
            steps = (
                list(resolve(q["question"], plan, "typed", lookup, relations, None))
                if plan
                else []
            )
            for arm in ARMS:
                original = query_rank(q["question"], arm)
                if not steps:
                    result, linked = ev.finish(
                        q["question"],
                        original["base"],
                        original["order20"],
                        lookup,
                        storage,
                    )
                else:
                    groups = []
                    for step in steps:
                        groups.append(
                            [(p, 0.0) for p in step.pages]
                            if step.pages
                            else query_rank(step.query, arm, True)["order40"]
                        )
                    # Reproduce the archived shared-score call order before depth40.
                    # The depth20 shadow only fills the same original-question cache.
                    shadow_groups = [
                        [(p, 0.0) for p in step.pages]
                        if step.pages
                        else query_cache[step.query]["ranks"][arm]["order20"]
                        for step in steps
                    ]
                    shadow_pool = merge_request_candidates(
                        original["base"], shadow_groups
                    )
                    score_pages(q["question"], shadow_pool)
                    pool = merge_request_candidates(original["base"], groups)
                    head = score_pages(q["question"], pool)
                    result, linked = ev.finish(
                        q["question"],
                        pool,
                        retain_reranked_head(pool, head),
                        lookup,
                        storage,
                    )
                trace = save(
                    output,
                    "semantic_planned",
                    q,
                    arm,
                    result,
                    linked,
                    plan_status=status,
                )
                if arm == "control":
                    old = archived[original_id, "requests40"]
                    check = {
                        "id": original_id,
                        "pool_equal": ev.sources(result[0])
                        == [tuple(p) for p in old["pool"]],
                        "expanded_equal": ev.sources(result[1])
                        == [tuple(p) for p in old["expanded"]],
                        "context_equal": trace["context_sha256"]
                        == old["context_sha256"],
                    }
                    replay.append(check)
                for i, (text, cache) in enumerate(query_cache.items()):
                    if arm not in cache["ranks"]:
                        continue
                    rank = cache["ranks"][arm]
                    append_json(
                        output / "request-ranks.jsonl",
                        {
                            "id": q["id"],
                            "arm": arm,
                            "query_index": i,
                            "query_sha256": text_sha(text),
                            "bm25": source_info(cache["sparse"]),
                            **{k: source_info(v) for k, v in rank.items()},
                        },
                    )
            if number % 10 == 0:
                log(f"Semantic planned comparison saved {number}/40.")
    write_json(output / "archive-replay.json", replay)
    if not all(
        all(r[key] for key in ("pool_equal", "expanded_equal", "context_equal"))
        for r in replay
    ):
        raise ValueError(
            "Archived/control rankings differ; restore the control before scoring."
        )


def regression_run(questions, metadata, search, windows, lookup, storage, output, log):
    for batch in batch_questions(questions, metadata):
        texts = [q["question"] for q in batch]
        sparse = [search.index.fielded(text, top_k=20) for text in texts]
        embeddings = search.embedder.encode_query(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        pools = {arm: [] for arm in ARMS}
        for vector, sparse_rank in zip(embeddings, sparse, strict=True):
            for arm in ARMS:
                dense = (
                    rank_pages(vector, search.vectors, search.pages, top_k=20)
                    if arm == "control"
                    else windows.rank(vector, top_k=20)
                )
                pools[arm].append(
                    weighted_rrf(sparse_rank, dense, (0.5, 0.5), top_k=20)
                )
        heads = rerank_variants(search.reranker, batch, pools, log)
        for i, q in enumerate(batch):
            for arm in ARMS:
                pool = pools[arm][i]
                order = retain_reranked_head(pool, heads[arm][i])
                result, linked = ev.finish(q["question"], pool, order, lookup, storage)
                save(output, "regression", q, arm, result, linked)
        log(f"Regression batch saved {len(batch)} questions for both arms.")


def score_saved(output, metadata, sources):
    manifest = read_json(output / "manifest.json")
    if manifest["status"] != "retrieval_complete" or manifest["results"] != 640:
        raise ValueError("Save every retrieval before opening references.")
    check_runtime(manifest["runtime"])
    for row in manifest["inputs"]:
        if file_sha(ROOT / row["path"]) != row["sha256"]:
            raise ValueError("Input changed.")
    for name in ("traces", "contexts"):
        if file_sha(output / f"{name}.jsonl") != manifest[f"{name}_sha256"]:
            raise ValueError("Result changed.")
    traces = read_jsonl(output / "traces.jsonl")
    context_rows = read_jsonl(output / "contexts.jsonl")
    contexts = {(r["experiment"], r["id"], r["arm"]): r for r in context_rows}
    trace_keys = {(r["experiment"], r["id"], r["arm"]) for r in traces}
    if (
        len(traces) != 640
        or len(context_rows) != 640
        or len(contexts) != 640
        or set(contexts) != trace_keys
    ):
        raise ValueError("Saved trace/context identities must be complete and unique.")
    for trace in traces:
        key = (trace["experiment"], trace["id"], trace["arm"])
        if [location(p) for p in contexts[key]["pages"]] != [
            location(p) for p in trace["final_pages"]
        ]:
            raise ValueError("Saved final context differs from retrieval source order.")
    refs = {s["suite"]: load_references(ROOT / s["references"]) for s in sources}
    pages, _ = load_index()
    page_map = {location(p): p for p in pages}
    meta = {r["id"]: r for r in metadata}
    by_experiment = {
        name: {arm: [] for arm in ARMS} for name in ("semantic_planned", "regression")
    }
    for trace in traces:
        m = meta[trace["id"]]
        key = (trace["experiment"], trace["id"], trace["arm"])
        row = score_item(
            m, refs[m["suite"]][m["question_id"]], trace, contexts[key], page_map
        )
        by_experiment[trace["experiment"]][trace["arm"]].append(row)
    summary = {
        name: {arm: aggregates(rows) for arm, rows in arms.items()}
        for name, arms in by_experiment.items()
    }
    diffs = []
    for experiment, arms in by_experiment.items():
        for b, a in zip(arms["control"], arms["is_windows"], strict=True):
            if b["id"] != a["id"]:
                raise ValueError("Pair mismatch.")
            row = {
                "experiment": experiment,
                "id": b["id"],
                "category": b["category"],
                "scored": b["scored"],
            }
            if b["scored"]:
                metric = b["complete_metric"]
                for stage in ("initial", "available", "final"):
                    row[stage] = {
                        "control": b["stages"][stage][metric],
                        "is_windows": a["stages"][stage][metric],
                    }
                delta = row["final"]["is_windows"] - row["final"]["control"]
                row["status"] = (
                    "improved"
                    if delta > 0
                    else "regressed"
                    if delta < 0
                    else "unchanged"
                )
            else:
                row["status"] = "not_scored"
            diffs.append(row)
    write_json(output / "per-question.json", by_experiment)
    write_json(output / "summary.json", summary)
    write_json(output / "differences.json", diffs)
    manifest.update(
        status="complete",
        references_read_after_retrieval=True,
        reference_fingerprints=[
            {"path": s["references"], "sha256": file_sha(ROOT / s["references"])}
            for s in sources
        ],
    )
    write_json(output / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[key] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    log = logger(output)
    questions, metadata, sources, counts = load_inputs()
    plans = {r["id"]: r for r in read_jsonl(BROAD / "luna_plans.jsonl")}
    pages, vectors = load_index()
    # Other work changed the shared default during this task. Freeze the archived
    # tokenizer in this isolated process; never rewrite the user's source files.
    advanced_field_roles.TOKENIZER_NAME = "bigram-id"
    search = ev.Search(pages, vectors)
    windows = ISWindowDense(pages, vectors, args.index_dir)
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    _ = search.index, search.embedder, search.reranker
    runtime = loaded_runtime()
    archive_config = read_json(ARCHIVE / "config.json")
    input_paths = [
        *(ROOT / s["path"] for s in sources),
        BROAD / "luna_plans.jsonl",
        *args.index_dir.glob("*"),
    ]
    input_paths += [
        ROOT / r["path"]
        for r in archive_config["inputs_and_code"]
        if not r["path"].endswith(".py")
    ]
    stamps = [
        {"path": str(p.resolve().relative_to(ROOT)), "sha256": file_sha(p)}
        for p in dict.fromkeys(input_paths)
        if p.is_file()
    ]
    manifest = {
        "status": "running",
        "arms": ARMS,
        "questions": {"semantic_planned": 40, "regression": 280},
        "inputs": stamps,
        "runtime": runtime,
        "external_api_calls": 0,
        "frozen_bm25_tokenizer": "bigram-id",
        "baseline_scope": "archived baseline at task start; preserve concurrently updated shared tokenizer",
        "single_change": "IS dense vectors: original 256-content-token windows, overlap64, max cosine to parent page",
        "fixed": "plans, queries, BM25, per-retriever20, RRF60/5:5, KURE512/fp16, Top3+RRF, lookup, final8/o200k8000",
        "timing_scope": "shared batched quality comparison; not online latency",
    }
    write_json(output / "manifest.json", manifest)
    begun = perf_counter()
    semantic = [q for q in questions if q["id"].startswith("semantic_queries:")]
    semantic_run(semantic, plans, search, windows, lookup, storage, output, log)
    regression_run(questions, metadata, search, windows, lookup, storage, output, log)
    traces = read_jsonl(output / "traces.jsonl")
    keys = {(r["experiment"], r["id"], r["arm"]) for r in traces}
    if len(traces) != 640 or len(keys) != 640:
        raise ValueError("Incomplete or duplicated results.")
    check_runtime(runtime)
    manifest.update(
        status="retrieval_complete",
        results=640,
        wall_seconds=perf_counter() - begun,
        traces_sha256=file_sha(output / "traces.jsonl"),
        contexts_sha256=file_sha(output / "contexts.jsonl"),
    )
    write_json(output / "manifest.json", manifest)
    log("All 640 retrieval results saved. Opening references for scoring.")
    score_saved(output, metadata, sources)
    log("Paired scores saved.")


if __name__ == "__main__":
    main()
