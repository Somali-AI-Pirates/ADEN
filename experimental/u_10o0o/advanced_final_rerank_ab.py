"""Replay fixed requests40 pools with only the final original-query KURE toggled."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.evaluation.dataset import load_references
from app.generation.context import build_context
from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_evidence_benchmark import (
    load_inputs,
    read_json,
    read_jsonl,
)
from experimental.u_10o0o.advanced_hg_lookup import location
from experimental.u_10o0o.advanced_search import (
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.prepare import CACHE_PATH

SOURCE = Path("artifacts/u_10o0o/advanced-evidence-preservation-20261110")
ARMS = ("rerank_on", "rerank_off")


def cohorts():
    broad, _, refs, _ = load_inputs("broad")
    return [
        ("broad", broad, refs, SOURCE / "broad-01"),
        (
            "reused24",
            read_json(SOURCE / "fresh-validation/questions.json"),
            SOURCE / "fresh-validation/references.json",
            SOURCE / "fresh-01",
        ),
    ]


def restore_rows(sources, pages, *, limit=20):
    keys = [tuple(key) for key in sources]
    if len(keys) != len(set(keys)) or len(keys) > limit:
        raise ValueError(
            "Saved candidates must be unique and within their stage limit."
        )
    return [(pages[key], 0.0) for key in keys]


def restored_fallback(saved, pages):
    """Keep the first baseline KURE and its final selection intact."""
    context = build_context(
        _context_candidates(restore_rows(saved["packed"], pages)),
        FINAL_K,
        DEFAULT_CONTEXT_TOKENS,
        _count_tokens,
    )
    if context.context_hash != saved["context_sha256"]:
        raise ValueError("Fallback context differs from the frozen source run.")
    return (
        restore_rows(saved["pool"], pages),
        restore_rows(saved["expanded"], pages),
        context,
        (),
        saved["route"],
    )


def compare_pool(
    question, pool, reranker, lookup, storage, score_batches, *, off_first=False
):
    # Replay the historical score-cache batches. fp16 scores can change near ties
    # if the same pairs are submitted in a different padded batch.
    score_cache = {}
    replay_started = perf_counter()
    for batch in score_batches:
        missing = [r for r in batch if location(r[0]) not in score_cache]
        if missing:
            ranked = rerank_variants(
                reranker, [{"question": question}], {"fixed": [missing]}, lambda _: None
            )["fixed"][0]
            score_cache.update({location(p): score for p, score in ranked})
    replay_ms = (perf_counter() - replay_started) * 1000
    preceding_pairs = len(score_cache)
    started = perf_counter()
    missing = [r for r in pool if location(r[0]) not in score_cache]
    if missing:
        ranked = rerank_variants(
            reranker, [{"question": question}], {"fixed": [missing]}, lambda _: None
        )["fixed"][0]
        score_cache.update({location(p): score for p, score in ranked})
    raw = sorted(
        [(p, score_cache[location(p)]) for p, _ in pool], key=lambda row: -row[1]
    )
    on_order = retain_reranked_head(pool, raw)
    rerank_ms = (perf_counter() - started) * 1000
    if set(ev.sources(on_order)) != set(ev.sources(pool)):
        raise AssertionError("Second KURE must only change ordering.")
    results = {}
    for arm in ARMS[::-1] if off_first else ARMS:
        order = on_order if arm == "rerank_on" else pool
        begun = perf_counter()
        result, linked = ev.finish(question, pool, order, lookup, storage)
        results[arm] = {
            "retrieval": result,
            "post_rerank": ev.sources(order),
            "post_hg": ev.sources(linked),
            "selection_ms": (perf_counter() - begun) * 1000,
        }
    return results, raw, rerank_ms, len(missing), replay_ms, preceding_pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    bundles = cohorts()
    stamps_by_path = {}
    saved_by_cohort = {}
    batches_by_cohort = {}
    for name, questions, _, directory in bundles:
        old = read_json(directory / "config.json")
        for stamp in old["inputs_and_code"]:
            previous = stamps_by_path.setdefault(stamp["path"], stamp)
            if previous != stamp:
                raise ValueError(f"Source runs disagree on {stamp['path']}.")
        old_traces = read_jsonl(directory / "traces.jsonl")
        old_by_key = {(r["id"], r["arm"]): r for r in old_traces}
        saved = [r for r in old_traces if r["arm"] == "requests40"]
        batches_by_cohort[name] = {
            q["id"]: [old_by_key[q["id"], "baseline"]["pool"]] for q in questions
        }
        if name == "broad":
            workload_path = directory / "workload.jsonl"
            stamp = ev.stamp(workload_path)
            stamps_by_path[stamp["path"]] = stamp
            workload = {r["id"]: r for r in read_jsonl(workload_path)}
            for q in questions:
                qid = q["id"]
                original = next(
                    r for r in workload[qid]["query_stats"] if r["is_original"]
                )
                if original["head40"]:
                    batches_by_cohort[name][qid].append(original["union"])
                batches_by_cohort[name][qid].append(
                    old_by_key[qid, "requests20"]["pool"]
                )
        by_id = {r["id"]: r for r in saved}
        if len(by_id) != len(saved) or set(by_id) != {q["id"] for q in questions}:
            raise ValueError("Saved requests40 traces must exactly cover each cohort.")
        saved_by_cohort[name] = by_id
        for path in (directory / "config.json", directory / "traces.jsonl"):
            stamp = ev.stamp(path)
            stamps_by_path[stamp["path"]] = stamp
    for stamp in stamps_by_path.values():
        if ev.stamp(Path(stamp["path"])) != stamp:
            raise ValueError(f"Frozen source input changed: {stamp['path']}.")
    stamp = ev.stamp(Path(__file__))
    stamps_by_path[stamp["path"]] = stamp
    stamps = list(stamps_by_path.values())
    config = {
        "arms": ARMS,
        "questions": sum(len(q) for _, q, _, _ in bundles),
        "fixed_merged_candidates": 20,
        "final_pages": FINAL_K,
        "final_tokens": DEFAULT_CONTEXT_TOKENS,
        "on_policy": "original-question KURE top3 promoted; merged order retained in tail",
        "off_policy": "merged candidate order; same finish() and HG source lookup",
        "fallback_policy": "restore saved final context unchanged; do not disable first KURE",
        "input_scores": "0.0 placeholder metadata; finish selects by order, not float score",
        "external_api_calls": 0,
        "embedding_calls": 0,
        "new_candidate_searches": 0,
        "timing": "warm incremental second-KURE stage after historical score cache; not full request latency",
        "score_replay": "historical original-query batches: baseline20, optional original union, broad requests20 missing, then merged requests40 missing",
        "replay_overhead": "preceding batches are reconstruction overhead, not claimed ON/OFF savings",
        "inputs_and_code": stamps,
    }
    ev.write_json(output / "config.json", config)
    pages = read_json(CACHE_PATH)
    by_source = {location(p): p for p in pages}
    if len(by_source) != len(pages):
        raise ValueError("Corpus source pages are not unique.")
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search = ev.Search(pages, None)
    reranker = search.reranker
    first = next(
        (q, saved_by_cohort[c][q["id"]], c)
        for c, qs, _, _ in bundles
        for q in qs
        if saved_by_cohort[c][q["id"]]["plan_status"] == "supported"
    )
    warm_pool = restore_rows(first[1]["pool"], by_source)
    compare_pool(
        first[0]["question"],
        warm_pool,
        reranker,
        lookup,
        storage,
        [
            restore_rows(b, by_source, limit=40)
            for b in batches_by_cohort[first[2]][first[0]["id"]]
        ],
    )
    log("Source fingerprints verified; second-KURE and selectors warmed.")

    retrievals, traces, mismatches = {}, [], []
    number = 0
    for cohort, questions, _, _ in bundles:
        for row in questions:
            number += 1
            qid = row["id"]
            saved = saved_by_cohort[cohort][qid]
            pool = restore_rows(saved["pool"], by_source)
            active = saved["plan_status"] == "supported"
            raw, rerank_ms = [], 0.0
            missing_pairs, replay_ms, preceding_pairs = 0, 0.0, 0
            if active:
                (results, raw, rerank_ms, missing_pairs, replay_ms, preceding_pairs) = (
                    compare_pool(
                        row["question"],
                        pool,
                        reranker,
                        lookup,
                        storage,
                        [
                            restore_rows(b, by_source, limit=40)
                            for b in batches_by_cohort[cohort][qid]
                        ],
                        off_first=number % 2 == 0,
                    )
                )
            else:
                frozen = restored_fallback(saved, by_source)
                results = {
                    arm: {
                        "retrieval": frozen,
                        "post_rerank": None,
                        "post_hg": None,
                        "selection_ms": 0.0,
                    }
                    for arm in ARMS
                }
            on = results["rerank_on"]["retrieval"]
            reproduced = (
                on[2].context_hash == saved["context_sha256"]
                and ev.sources(on[1]) == [tuple(k) for k in saved["expanded"]]
                and on[4] == saved["route"]
            )
            if not reproduced:
                mismatches.append(
                    {
                        "cohort": cohort,
                        "id": qid,
                        "context_equal": on[2].context_hash == saved["context_sha256"],
                        "expanded_equal": ev.sources(on[1])
                        == [tuple(k) for k in saved["expanded"]],
                        "route_equal": on[4] == saved["route"],
                    }
                )
            for arm in ARMS:
                record = results[arm]
                result = record["retrieval"]
                context = result[2]
                if ev.sources(result[0]) != [tuple(k) for k in saved["pool"]]:
                    raise AssertionError("Fixed merged candidate order changed.")
                if (
                    len(result[1]) > 20
                    or len(context.final_chunks) > FINAL_K
                    or context.token_count > DEFAULT_CONTEXT_TOKENS
                ):
                    raise AssertionError("Evidence budget exceeded.")
                trace = {
                    "cohort": cohort,
                    "id": qid,
                    "split": row["split"],
                    "arm": arm,
                    "treatment_applied": active,
                    "plan_status": saved["plan_status"],
                    "pool": ev.sources(pool),
                    "post_rerank": record["post_rerank"],
                    "post_hg": record["post_hg"],
                    "expanded": ev.sources(result[1]),
                    "packed": ev.packed_sources(context),
                    "tokens": context.token_count,
                    "token_exclusions": len(context.excluded_chunk_ids),
                    "context_sha256": context.context_hash,
                    "route": result[4],
                    "raw_kure_ranking": [
                        {"source": location(p), "score": score} for p, score in raw
                    ]
                    if arm == "rerank_on"
                    else [],
                    "second_kure_pairs": missing_pairs
                    if active and arm == "rerank_on"
                    else 0,
                    "second_kure_ms": rerank_ms if arm == "rerank_on" else 0.0,
                    "selection_ms": record["selection_ms"],
                    "score_replay_ms": replay_ms if arm == "rerank_on" else 0.0,
                    "score_replay_pairs": preceding_pairs if arm == "rerank_on" else 0,
                    "old_on_reproduced": reproduced,
                }
                traces.append(trace)
                retrievals[cohort, qid, arm] = result
                with (output / "traces.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
                with (output / "contexts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "cohort": cohort,
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
            if number % 20 == 0 or number == config["questions"]:
                log(f"Retrieval saved {number}/{config['questions']}.")
    ev.write_json(output / "reproduction-mismatches.json", mismatches)
    if mismatches:
        raise RuntimeError(
            "ON did not reproduce frozen source ordering; inspect saved traces before scoring."
        )
    log(
        "All retrievals saved; ON reproduction verified; opening references for scoring."
    )
    scores, changes, groups = [], [], defaultdict(Counter)
    trace_map = {(t["cohort"], t["id"], t["arm"]): t for t in traces}
    for cohort, questions, references_path, _ in bundles:
        references = (
            {
                split: load_references(path)
                for split, (_, path) in references_path.items()
            }
            if isinstance(references_path, dict)
            else read_json(references_path)
        )
        for row in questions:
            qid, split = row["id"], row["split"]
            ref = references[split][qid] if cohort == "broad" else references[qid]
            if cohort != "broad":
                requirements = [r for task in ref for r in task["requirements"]]
                ref = {
                    "evidence_requirements": requirements,
                    "evidence_groups": [r["primary"] for r in requirements],
                }
            paired = {}
            for arm in ARMS:
                metrics = ev.score_one(
                    ref, retrievals[cohort, qid, arm], peer=split.startswith("peer_")
                )
                paired[arm] = metrics
                scores.append(
                    {
                        "cohort": cohort,
                        "id": qid,
                        "split": split,
                        "arm": arm,
                        "treatment_applied": trace_map[cohort, qid, arm][
                            "treatment_applied"
                        ],
                        **metrics,
                    }
                )
                group = groups[f"{cohort}/{split}/{arm}"]
                group["count"] += 1
                for metric in (
                    "candidate_complete",
                    "expanded_complete",
                    "final_complete",
                    "page_recall_at_20",
                    "page_recall_at_8",
                ):
                    group[metric] += metrics[metric]
            changes.append(
                {
                    "cohort": cohort,
                    "id": qid,
                    "split": split,
                    "treatment_applied": trace_map[cohort, qid, "rerank_on"][
                        "treatment_applied"
                    ],
                    **{
                        f"{stage}_delta_off_minus_on": int(
                            paired["rerank_off"][f"{stage}_complete"]
                        )
                        - int(paired["rerank_on"][f"{stage}_complete"])
                        for stage in ("candidate", "expanded", "final")
                    },
                }
            )
    for group in groups.values():
        for metric in ("page_recall_at_20", "page_recall_at_8"):
            group[metric] /= group["count"]
    write_rows(output / "scores.jsonl", scores)
    write_rows(output / "changes.jsonl", changes)
    ev.write_json(output / "summary.json", dict(groups))
    active_traces = [t for t in traces if t["treatment_applied"]]
    ev.write_json(
        output / "stage-latency.json",
        {
            arm: {
                field: ev.latency([t[field] for t in active_traces if t["arm"] == arm])
                for field in ("second_kure_ms", "selection_ms", "score_replay_ms")
            }
            for arm in ARMS
        },
    )
    config.update(
        treatment_questions=sum(
            t["treatment_applied"] for t in traces if t["arm"] == "rerank_on"
        ),
        fallback_questions=sum(
            not t["treatment_applied"] for t in traces if t["arm"] == "rerank_on"
        ),
        second_kure_pairs=sum(t["second_kure_pairs"] for t in traces),
        score_replay_pairs=sum(t["score_replay_pairs"] for t in traces),
        old_on_reproduced_questions=config["questions"],
    )
    if stamps != [ev.stamp(Path(s["path"])) for s in stamps]:
        raise RuntimeError("Frozen source inputs or code changed during execution.")
    ev.write_json(output / "config.json", config)
    log("Complete; baseline remains unchanged.")


if __name__ == "__main__":
    main()
