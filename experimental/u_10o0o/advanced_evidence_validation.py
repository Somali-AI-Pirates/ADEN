"""Validate the frozen request-40 path on new questions with isolated timing."""

import argparse
import json
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from openai import OpenAI

from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_evidence_benchmark import (
    read_json,
    read_jsonl,
    supported_plan,
)
from experimental.u_10o0o.advanced_evidence_policy import merge_request_candidates
from experimental.u_10o0o.advanced_hg_lookup import location
from experimental.u_10o0o.advanced_hg_relations import HGRelations
from experimental.u_10o0o.advanced_identifier_diagnostic import actual_confirmation
from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    MODEL_ID,
    REASONING_EFFORT,
)
from experimental.u_10o0o.advanced_planned_retrieval import resolve, rule_plan
from experimental.u_10o0o.advanced_query_plan import PROMPT, Plan, request_plan
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    HYBRID_WEIGHTS,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import VECTORS_PATH
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index, rank_pages

ARMS = ("baseline", "requests40")


def retrieve(row, record, arm, search, lookup, storage, relations):
    """Caches belong to one arm and one question; no cross-arm model reuse."""
    started = perf_counter()
    question = row["question"]
    query_cache, score_cache = {}, {}
    counts = Counter()

    def score(text, rows):
        missing = [r for r in rows if (text, location(r[0])) not in score_cache]
        if missing:
            ranked = rerank_variants(
                search.reranker,
                [{"question": text}],
                {"score": [missing]},
                lambda _: None,
            )["score"][0]
            score_cache.update({(text, location(p)): s for p, s in ranked})
            counts["kure_pairs"] += len(missing)
            counts["kure_calls"] += 1
        return sorted(
            [(p, score_cache[text, location(p)]) for p, _ in rows],
            key=lambda item: -item[1],
        )

    def query(text, expand=False):
        if text not in query_cache:
            sparse = search.index.fielded(text, top_k=CANDIDATES)
            embedding = search.embedder.encode_query(
                [text], normalize_embeddings=True, show_progress_bar=False
            )[0]
            dense = rank_pages(
                embedding, search.vectors, search.pages, top_k=CANDIDATES
            )
            union = weighted_rrf(sparse, dense, HYBRID_WEIGHTS, top_k=40)
            base = union[:CANDIDATES]
            head = score(text, base)
            query_cache[text] = {
                "base": base,
                "union": union,
                "order20": retain_reranked_head(base, head),
            }
            counts["searches"] += 1
        cached = query_cache[text]
        if expand and "order40" not in cached:
            head = score(text, cached["union"])
            cached["order40"] = retain_reranked_head(cached["base"], head)[:CANDIDATES]
        return cached

    original = query(question)
    steps, status = [], "baseline"
    if arm != "baseline":
        plan, status = supported_plan(row, record, lookup, "fresh")
        if plan:
            steps = list(resolve(question, plan, "typed", lookup, relations, None))
    if steps:
        groups = [
            [(p, 0.0) for p in step.pages]
            if step.pages
            else query(step.query, expand=True)["order40"]
            for step in steps
        ]
        pool = merge_request_candidates(original["base"], groups)
        order = retain_reranked_head(pool, score(question, pool))
    else:
        pool, order = original["base"], original["order20"]
    result, _ = ev.finish(question, pool, order, lookup, storage)
    elapsed_ms = (perf_counter() - started) * 1000
    cases = (
        sorted({case for step in steps for case in step.cases})
        if steps
        else actual_confirmation(question, result, lookup)
    )
    trace = {
        "id": row["id"],
        "split": row["split"],
        "arm": arm,
        "plan_status": status,
        "local_ms": elapsed_ms,
        **counts,
        "resolved_cases": cases,
        "request_sources": [ev.sources([(p, 0.0) for p in s.pages]) for s in steps],
        "pool": ev.sources(result[0]),
        "expanded": ev.sources(result[1]),
        "packed": ev.packed_sources(result[2]),
        "tokens": result[2].token_count,
        "context_sha256": result[2].context_hash,
        "route": result[4],
    }
    if (
        len(result[0]) > 20
        or len(result[1]) > 20
        or len(result[2].final_chunks) > 8
        or result[2].token_count > 8000
    ):
        raise AssertionError("Frozen evidence budget exceeded.")
    return result, trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plans", type=Path, help="Use existing question-only plans.")
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    questions_path = args.input_dir / "questions.json"
    references_path = args.input_dir / "references.json"
    questions = read_json(questions_path)
    if not questions or len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Expected nonempty, uniquely identified questions.")
    if any(set(q) != {"id", "split", "question"} for q in questions):
        raise ValueError("Question input must not contain reference fields.")
    from experimental.u_10o0o.advanced_case_sources import CURATED
    from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX
    from experimental.u_10o0o.advanced_role_context import FACTS

    inputs = [
        questions_path,
        references_path,
        *args.input_dir.glob("*.json"),
        CACHE_PATH,
        VECTORS_PATH,
        *sorted(CURATED.rglob("*.json*")),
        *sorted(p for p in CASE_INDEX.rglob("*") if p.is_file()),
        *sorted(p for p in FACTS.rglob("*") if p.is_file()),
        *sorted(Path(__file__).parent.glob("*.py")),
        Path("app/generation/context.py"),
        Path("shared/contracts.py"),
    ]
    if args.plans:
        inputs.append(args.plans)
    stamps = [ev.stamp(p) for p in dict.fromkeys(inputs)]
    config = {
        "arms": ARMS,
        "model": MODEL_ID,
        "reasoning_effort": REASONING_EFFORT,
        "prompt": PROMPT,
        "schema": Plan.model_json_schema(),
        "timeout_seconds": 25,
        "retries": 0,
        "external_api_calls": 0 if args.plans else len(questions),
        "request_data": "question and fixed instructions/schema only",
        "inputs_and_code": stamps,
        "candidate_per_retriever": 20,
        "candidate_per_request": 40,
        "merged_candidates": 20,
        "final_pages": 8,
        "final_tokens": 8000,
        "timing": "independent per-arm computations, alternating order, warm models",
    }
    ev.write_json(output / "config.json", config)
    plans = {}
    if args.plans:
        plan_rows = read_jsonl(args.plans)
        plans = {p["id"]: p for p in plan_rows}
        if (
            len(plans) != len(plan_rows)
            or not {q["id"] for q in questions} <= plans.keys()
        ):
            raise ValueError("Plans do not uniquely cover the frozen questions.")
    else:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    config["plan_mode"] = "cached" if args.plans else "live_serial"

    prepared = perf_counter()
    pages, vectors = load_index()
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search = ev.Search(pages, vectors)
    _ = search.index, search.embedder, search.reranker
    retrievals, traces = {}, []
    with ExitStack() as stack:
        relations = stack.enter_context(HGRelations(pages=pages))
        client = plan_stream = None
        if not args.plans:
            client = stack.enter_context(
                OpenAI(base_url=API_BASE_URL, max_retries=0, timeout=25)
            )
            plan_stream = stack.enter_context(
                (output / "luna_plans.jsonl").open("x", encoding="utf-8", buffering=1)
            )
        config["relations"] = relations.provenance
        config["preparation_seconds"] = perf_counter() - prepared
        warming = perf_counter()
        warm_plan = plans.get(questions[0]["id"]) or {
            "status": "warmup_rule_plan",
            "plan": rule_plan(questions[0]["question"], lookup).model_dump(),
        }
        for arm in ARMS:
            retrieve(
                questions[0],
                warm_plan,
                arm,
                search,
                lookup,
                storage,
                relations,
            )
        config["warmup_seconds"] = perf_counter() - warming
        log("Models and both paths warmed; independent timed retrieval starts.")
        for index, row in enumerate(questions):
            for arm in ARMS if index % 2 == 0 else ARMS[::-1]:
                wall_started = perf_counter()
                if arm == "requests40" and not args.plans:
                    record = request_plan(client, row)
                    plans[row["id"]] = record
                    plan_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                result, trace = retrieve(
                    row, plans.get(row["id"]), arm, search, lookup, storage, relations
                )
                trace["wall_ms"] = (perf_counter() - wall_started) * 1000
                trace["plan_ms"] = (
                    0 if arm == "baseline" else plans[row["id"]]["seconds"] * 1000
                )
                trace["serial_total_ms"] = trace["local_ms"] + trace["plan_ms"]
                traces.append(trace)
                retrievals[row["id"], arm] = result
                with (output / "traces.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
                with (output / "contexts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "id": row["id"],
                                "arm": arm,
                                "pages": [
                                    {
                                        "book_id": c.chunk.book_id,
                                        "page": c.chunk.page,
                                        "text": c.chunk.text,
                                    }
                                    for c in result[2].final_chunks
                                ],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            log(f"Retrieval saved {index + 1}/{len(questions)}.")
    ev.write_json(
        output / "plans-fingerprint.json",
        ev.stamp(args.plans or output / "luna_plans.jsonl"),
    )
    log("All retrievals saved; opening references for scoring.")
    references = read_json(references_path)
    scores, summaries = [], defaultdict(Counter)
    for row in questions:
        requirements = [
            r for task in references[row["id"]] for r in task["requirements"]
        ]
        ref = {
            "evidence_requirements": requirements,
            "evidence_groups": [r["primary"] for r in requirements],
        }
        for arm in ARMS:
            result = ev.score_one(ref, retrievals[row["id"], arm], peer=False)
            scores.append(
                {"id": row["id"], "split": row["split"], "arm": arm, **result}
            )
            group = summaries[f"{row['split']}/{arm}"]
            group["count"] += 1
            for metric in (
                "candidate_complete",
                "expanded_complete",
                "final_complete",
                "page_recall_at_20",
                "page_recall_at_8",
            ):
                group[metric] += result[metric]
    for group in summaries.values():
        for metric in ("page_recall_at_20", "page_recall_at_8"):
            group[metric] /= group["count"]
    write_rows(output / "scores.jsonl", scores)
    ev.write_json(output / "summary.json", dict(summaries))
    ev.write_json(
        output / "latency.json",
        {
            arm: {
                metric: ev.latency([t[metric] for t in traces if t["arm"] == arm])
                for metric in ("local_ms", "plan_ms", "serial_total_ms", "wall_ms")
            }
            for arm in ARMS
        },
    )
    if stamps != [ev.stamp(Path(s["path"])) for s in stamps]:
        raise RuntimeError("Frozen inputs or code changed during execution.")
    ev.write_json(output / "config.json", config)
    log("Complete; baseline remains unchanged.")


if __name__ == "__main__":
    main()
