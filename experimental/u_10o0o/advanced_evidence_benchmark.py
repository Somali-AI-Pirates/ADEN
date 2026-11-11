"""Compare retrieval cutoffs and request-aware packing with a frozen baseline."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.evaluation.dataset import load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_evidence_policy import (
    merge_request_candidates,
    repack_request_context,
)
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, location
from experimental.u_10o0o.advanced_hg_relations import HGRelations
from experimental.u_10o0o.advanced_identifier_diagnostic import (
    actual_confirmation,
    evidence_reference,
)
from experimental.u_10o0o.advanced_plan_ab import files_for_run
from experimental.u_10o0o.advanced_planned_retrieval import resolve, rule_plan
from experimental.u_10o0o.advanced_query_plan import Plan
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    HYBRID_WEIGHTS,
    evaluation_questions,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import VECTORS_PATH
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index, rank_pages

ROOT = Path("artifacts/u_10o0o")
IDENTITY = ROOT / "advanced-identifier-diagnostic-20261110"
HOLDOUT = ROOT / "advanced-plan-holdout-20261110"
BROAD = ROOT / "advanced-plan-ab-20261110"
ARMS = ("baseline", "requests20", "requests20_pack", "requests40", "requests40_pack")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load_inputs(suite):
    """Open query-only inputs and plans; return unopened reference locations."""
    if suite == "pilot":
        questions = [
            {
                "id": row["id"],
                "split": "pilot",
                "question": row["question"],
                "literal_is_query": row["literal_is_query"],
            }
            for row in read_json(IDENTITY / "mixed-inputs.json")
        ]
        plan_path = HOLDOUT / "live/local_plans.jsonl"
        return (
            questions,
            plan_path,
            IDENTITY / "mixed-references.json",
            [
                IDENTITY / "mixed-inputs.json",
                plan_path,
            ],
        )
    if suite == "holdout":
        plan_path = HOLDOUT / "live/local_plans.jsonl"
        return (
            read_json(HOLDOUT / "questions.json"),
            plan_path,
            HOLDOUT / "references.json",
            [
                HOLDOUT / "questions.json",
                plan_path,
            ],
        )
    if suite == "identity":
        questions = [
            {**row, "split": "identity"}
            for row in read_json(IDENTITY / "questions.json")
        ]
        return (
            questions,
            None,
            IDENTITY / "references.json",
            [IDENTITY / "questions.json"],
        )
    files = files_for_run()
    questions = evaluation_questions(files)
    if questions != read_json(BROAD / "questions.json"):
        raise ValueError("Cached plans no longer match the evaluation questions.")
    plan_path = BROAD / "luna_plans.jsonl"
    return questions, plan_path, files, [*(q for q, _ in files.values()), plan_path]


def supported_plan(row, record, lookup, suite):
    if record is None:
        if suite != "identity":
            return None, "missing_plan"
        plan = rule_plan(row["question"], lookup)
    elif not record.get("plan"):
        return None, record.get("status", "missing_plan")
    else:
        plan = Plan.model_validate(record["plan"])
    if any(
        t.after or t.use != "none" or t.condition != "none" or t.kind == "case_set"
        for t in plan.tasks
    ):
        return None, "unsupported_dependency_or_set"
    if any(t.anchor not in row["question"] for t in plan.tasks):
        return None, "invalid_anchor"
    if suite == "pilot":
        rules = [t for t in plan.tasks if t.kind == "rule"]
        if len(rules) != 1 or row["literal_is_query"] not in row["question"]:
            raise ValueError("Pilot must expose one literal rule request.")
        plan = plan.model_copy(deep=True)
        for t in plan.tasks:
            if t.kind == "rule":
                t.anchor = row["literal_is_query"]
                t.query = row["literal_is_query"]
    return plan, "supported"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=("pilot", "holdout", "identity", "broad"), required=True
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    questions, plan_path, references_path, inputs = load_inputs(args.suite)
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Question IDs are not unique.")
    plan_rows = read_jsonl(plan_path) if plan_path else []
    plans = {r["id"]: r for r in plan_rows}
    if len(plans) != len(plan_rows):
        raise ValueError("Cached plan IDs are duplicated.")
    if plan_path and not {q["id"] for q in questions} <= plans.keys():
        raise ValueError("Cached plans do not cover the frozen questions.")
    reference_paths = (
        [ref for _, ref in references_path.values()]
        if isinstance(references_path, dict)
        else [references_path]
    )
    sources = [
        *inputs,
        *reference_paths,
        CACHE_PATH,
        VECTORS_PATH,
        CURATED / "manifest.json",
        CURATED / "sources.jsonl",
        *sorted((CURATED / "records").glob("*.jsonl")),
        *sorted(p for p in CASE_INDEX.rglob("*") if p.is_file()),
        *sorted(p for p in FACTS.rglob("*") if p.is_file()),
        *sorted(Path(__file__).parent.glob("advanced_*.py")),
        *[
            Path(__file__).with_name(f)
            for f in (
                "bm25.py",
                "hybrid.py",
                "embeding.py",
                "search.py",
                "context_evidence.py",
            )
        ],
        Path("app/generation/context.py"),
        Path("app/ingestion/layout.py"),
        Path("app/ingestion/curation.py"),
        Path("shared/contracts.py"),
    ]
    stamps = [ev.stamp(p) for p in dict.fromkeys(sources)]
    if load_inputs(args.suite)[0] != questions or (
        plan_path and read_jsonl(plan_path) != plan_rows
    ):
        raise RuntimeError("Query or plan inputs changed during initial loading.")
    config = {
        "suite": args.suite,
        "questions": len(questions),
        "arms": ARMS,
        "inputs_and_code": stamps,
        "candidate_per_retriever": 20,
        "candidate_per_request": [20, 40],
        "merged_candidates": 20,
        "final_pages": 8,
        "final_tokens": 8000,
        "external_api_calls": 0,
        "scores": "common query-page KURE scores reused across candidate arms",
        "timing": "local incremental shared execution; not independent end-to-end arm latency",
        "query_policy": "literal rule clause diagnostic"
        if args.suite == "pilot"
        else (
            "existing rule_plan"
            if args.suite == "identity"
            else "cached validated model rewrite"
        ),
        "baseline": "current personal advanced_search; no Luna HG router",
    }
    ev.write_json(output / "config.json", config)
    pages, vectors = load_index()
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search = ev.Search(pages, vectors)
    _ = search.index, search.embedder, search.reranker
    log(f"Prepared {len(pages)} pages for {len(questions)} questions.")
    retrievals, traces, workload = {}, [], []
    group_records, logical_work = {}, {}
    started = perf_counter()
    with HGRelations(pages=pages) as relations:
        config["relations"] = relations.provenance
        ev.write_json(output / "config.json", config)
        for number, row in enumerate(questions, 1):
            qid, question = row["id"], row["question"]
            query_cache, score_cache = {}, {}
            actual_pairs = actual_searches = actual_rerank_calls = 0
            query_stats = []

            def score_pages(text, rows):
                nonlocal actual_pairs, actual_rerank_calls
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
                    actual_pairs += len(missing)
                    actual_rerank_calls += 1
                    score_cache.update({(text, location(p)): s for p, s in ranked})
                return sorted(
                    [(p, score_cache[text, location(p)]) for p, _ in rows],
                    key=lambda item: -item[1],
                )

            def query_rank(text, expand=False):
                nonlocal actual_searches
                if text not in query_cache:
                    begun = perf_counter()
                    sparse = search.index.fielded(text, top_k=CANDIDATES)
                    embedding = search.embedder.encode_query(
                        [text], normalize_embeddings=True, show_progress_bar=False
                    )[0]
                    dense = rank_pages(embedding, vectors, pages, top_k=CANDIDATES)
                    from experimental.u_10o0o.hybrid import weighted_rrf

                    union = weighted_rrf(sparse, dense, HYBRID_WEIGHTS, top_k=40)
                    base = union[:CANDIDATES]
                    head = score_pages(text, base)
                    query_cache[text] = {
                        "base": base,
                        "union": union,
                        "head20": head,
                        "order20": retain_reranked_head(base, head),
                        "base_ms": (perf_counter() - begun) * 1000,
                    }
                    actual_searches += 1
                cached = query_cache[text]
                if expand and "head40" not in cached:
                    begun = perf_counter()
                    head = score_pages(text, cached["union"])
                    cached.update(
                        head40=head,
                        order40=retain_reranked_head(cached["base"], head)[:CANDIDATES],
                        extra_ms=(perf_counter() - begun) * 1000,
                    )
                return cached

            original_rank = query_rank(question)
            baseline, linked = ev.finish(
                question,
                original_rank["base"],
                original_rank["order20"],
                lookup,
                storage,
            )
            retrievals[qid, "baseline"] = baseline
            plan, status = supported_plan(row, plans.get(qid), lookup, args.suite)
            steps = (
                resolve(question, plan, "typed", lookup, relations, None)
                if plan
                else []
            )
            steps = list(steps)
            for step in steps:
                if not step.pages:
                    query_rank(step.query, expand=True)
            for depth in (20, 40):
                arm = f"requests{depth}"
                if not steps:
                    retrievals[qid, arm] = retrievals[qid, arm + "_pack"] = baseline
                    continue
                nomination_groups, packing_groups = [], []
                for step in steps:
                    if step.pages:
                        group = [(p, 0.0) for p in step.pages]
                        nomination_groups.append(group)
                        packing_groups.append(group)
                    else:
                        rank = query_cache[step.query]
                        nomination_groups.append(rank[f"order{depth}"])
                        packing_groups.append(rank[f"head{depth}"])
                pool = merge_request_candidates(
                    original_rank["base"], nomination_groups
                )
                # Same mixed-question scoring and same source selector for both depth arms.
                head = score_pages(question, pool)
                order = retain_reranked_head(pool, head)
                ordinary, _ = ev.finish(question, pool, order, lookup, storage)
                packed = repack_request_context(ordinary, packing_groups)
                if ev.sources(ordinary[0]) != ev.sources(packed[0]) or ev.sources(
                    ordinary[1]
                ) != ev.sources(packed[1]):
                    raise AssertionError("Packing changed the shared candidate pool.")
                retrievals[qid, arm] = ordinary
                retrievals[qid, arm + "_pack"] = packed
                group_records[qid, arm] = group_records[qid, arm + "_pack"] = [
                    ev.sources(group) for group in packing_groups
                ]
                pairs = {(question, location(p)) for p, _ in original_rank["base"]}
                for step in steps:
                    if not step.pages:
                        ranks = query_cache[step.query]
                        pairs.update(
                            (step.query, location(p))
                            for p, _ in (
                                ranks["base"] if depth == 20 else ranks["union"]
                            )
                        )
                pairs.update((question, location(p)) for p, _ in pool)
                logical_work[qid, arm] = logical_work[qid, arm + "_pack"] = len(pairs)
            for text, cached in query_cache.items():
                query_stats.append(
                    {
                        "query_index": len(query_stats),
                        "is_original": text == question,
                        "rrf20": ev.sources(cached["base"]),
                        "union": ev.sources(cached["union"]),
                        "head20": ev.sources(cached["head20"]),
                        "head40": ev.sources(cached.get("head40", [])),
                        "base_ms": cached["base_ms"],
                        "extra_ms": cached.get("extra_ms", 0),
                    }
                )
            for arm in ARMS:
                result = retrievals[qid, arm]
                pool, expanded, context, cases, route = result
                if (
                    len(pool) > 20
                    or len(expanded) > 20
                    or len(context.final_chunks) > 8
                    or context.token_count > 8000
                ):
                    raise AssertionError("Shared evidence budget exceeded.")
                trace = {
                    "id": qid,
                    "split": row["split"],
                    "arm": arm,
                    "pool": ev.sources(pool),
                    "expanded": ev.sources(expanded),
                    "packed": ev.packed_sources(context),
                    "context_sha256": context.context_hash,
                    "tokens": context.token_count,
                    "route": route,
                    "plan_status": status,
                    "request_groups": group_records.get((qid, arm), []),
                    "logical_kure_pairs": logical_work.get(
                        (qid, arm), len(original_rank["base"])
                    ),
                    "logical_searches": len(query_cache)
                    if arm != "baseline" and steps
                    else 1,
                    "historical_plan_seconds": plans.get(qid, {}).get("seconds", 0.0),
                    "resolved_cases": (
                        actual_confirmation(question, result, lookup)
                        if arm == "baseline" or not steps
                        else sorted({case for step in steps for case in step.cases})
                    ),
                    "requested_role_sources": [
                        ev.sources([(p, 0.0) for p in step.pages]) for step in steps
                    ],
                }
                traces.append(trace)
                with (output / "traces.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
                with (output / "contexts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "id": qid,
                                "arm": arm,
                                "pages": [
                                    {
                                        "book_id": c.chunk.book_id,
                                        "page": c.chunk.page,
                                        "text": c.chunk.text,
                                    }
                                    for c in context.final_chunks
                                ],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            workload.append(
                {
                    "id": qid,
                    "split": row["split"],
                    "plan_status": status,
                    "request_groups": group_records.get((qid, arm), []),
                    "logical_kure_pairs": logical_work.get(
                        (qid, arm), len(original_rank["base"])
                    ),
                    "logical_searches": len(query_cache)
                    if arm != "baseline" and steps
                    else 1,
                    "historical_plan_seconds": plans.get(qid, {}).get("seconds", 0.0),
                    "tasks": len(steps),
                    "verified_tasks": sum(bool(s.pages) for s in steps),
                    "actual_searches_shared": actual_searches,
                    "actual_kure_pairs_shared": actual_pairs,
                    "actual_rerank_calls_shared": actual_rerank_calls,
                    "query_stats": query_stats,
                }
            )
            if number % 10 == 0 or number == len(questions):
                log(f"Retrieval saved {number}/{len(questions)}.")
    write_rows(output / "workload.jsonl", workload)
    log("All retrievals saved; opening references for scoring.")
    if isinstance(references_path, dict):
        references = {
            split: load_references(path) for split, (_, path) in references_path.items()
        }
    else:
        references = read_json(references_path)
    scores, summaries, changes = [], defaultdict(Counter), []
    trace_map = {(t["id"], t["arm"]): t for t in traces}
    for row in questions:
        qid, split = row["id"], row["split"]
        ref = (
            references[split][qid]
            if isinstance(references_path, dict)
            else references[qid]
        )
        if args.suite == "pilot":
            groups = [ref["hg_evidence"], ref["is_evidence"]]
            ref = {
                "evidence_groups": groups,
                "evidence_requirements": [{"primary": group} for group in groups],
            }
        if args.suite == "holdout":
            requirements = [req for task in ref for req in task["requirements"]]
            ref = {
                "evidence_requirements": requirements,
                "evidence_groups": [req["primary"] for req in requirements],
            }
        if args.suite == "identity" and not ref["sufficient"]:
            for arm in ARMS:
                cases = trace_map[qid, arm]["resolved_cases"]
                scores.append(
                    {
                        "id": qid,
                        "split": "insufficient",
                        "arm": arm,
                        "single_confirmed": len(cases) == 1,
                        "possible_cases": len(ref["possible_case_ids"]),
                    }
                )
            continue
        if args.suite == "identity":
            ref = evidence_reference(
                [c for c in ref["source_citations"] if c["kind"] == "requested_fact"]
            )
        results = {}
        for arm in ARMS:
            result = ev.score_one(
                ref, retrievals[qid, arm], peer=split.startswith("peer_")
            )
            results[arm] = result
            scores.append({"id": qid, "split": split, "arm": arm, **result})
            group = summaries[f"{split}/{arm}"]
            group["count"] += 1
            for metric in ("candidate_complete", "expanded_complete", "final_complete"):
                if metric in result:
                    group[metric] += int(result[metric])
            for metric in ("page_recall_at_20", "page_recall_at_8"):
                if metric in result:
                    group[metric] += result[metric]
        for arm in ARMS[1:]:
            if "final_complete" in results["baseline"]:
                changes.append(
                    {
                        "id": qid,
                        "split": split,
                        "arm": arm,
                        "candidate_delta": int(results[arm]["candidate_complete"])
                        - int(results["baseline"]["candidate_complete"]),
                        "expanded_delta": int(results[arm]["expanded_complete"])
                        - int(results["baseline"]["expanded_complete"]),
                        "final_delta": int(results[arm]["final_complete"])
                        - int(results["baseline"]["final_complete"]),
                    }
                )
    write_rows(output / "scores.jsonl", scores)
    write_rows(output / "changes.jsonl", changes)
    for group in summaries.values():
        for metric in ("page_recall_at_20", "page_recall_at_8"):
            if metric in group:
                group[metric] /= group["count"]
    ev.write_json(output / "summary.json", dict(summaries))
    if stamps != [ev.stamp(Path(item["path"])) for item in stamps]:
        raise RuntimeError("Frozen code or inputs changed during execution.")
    config["elapsed_seconds"] = perf_counter() - started
    config["actual_searches_shared"] = sum(
        r["actual_searches_shared"] for r in workload
    )
    config["actual_kure_pairs_shared"] = sum(
        r["actual_kure_pairs_shared"] for r in workload
    )
    ev.write_json(output / "config.json", config)
    log("Complete; baseline remains unchanged.")


if __name__ == "__main__":
    main()
