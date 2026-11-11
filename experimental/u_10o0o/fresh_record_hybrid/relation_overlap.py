"""Replay frozen rankings with E relation fusion or F initial Top-10 quotas.

No BM25, dense search, query embedding, or reference loading occurs here.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import retrieval as r
from .models import DEFAULT_HUB_CACHE, KureReranker

DEFAULT_D_RESULTS = Path(
    "artifacts/u_10o0o/advanced-fresh-page-relation-20261111/run-D-v1/results.jsonl"
)

# Frozen D fingerprint, verified before this experiment; never derived at replay.
DEFAULT_D_RESULTS_SHA256 = (
    "df1970791bff1fe6edb99568d0795b92250bc4345076770eaf2973f6d9d7fad8"
)


def validate_d_run(
    rows: Sequence[dict[str, Any]],
    questions: Sequence[r.Question],
    manifest: Mapping[str, Any],
    fingerprints: Mapping[str, Any],
) -> None:
    if manifest.get("conditions") != ["D"]:
        raise ValueError("Replay requires a completed D-only source run")
    if manifest.get("summary", {}).get("result_rows") != len(questions):
        raise ValueError("D manifest is incomplete or has a different question count")
    expected = {q.question_id: r.sha256_text(q.text) for q in questions}
    actual = [row["id"] for row in rows]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("D results must contain exactly one row per frozen question")
    for row in rows:
        if row["condition"] != "D" or row["question_sha256"] != expected[row["id"]]:
            raise ValueError(
                "Source result does not match condition D and the question"
            )
    if manifest["inputs"] != fingerprints:
        raise ValueError("D input fingerprint mismatch")
    fixed = {
        "bm25": {
            "k1": r.BM25_K1,
            "b": r.BM25_B,
            "tokenizer": "Korean words+bigram+complete code",
        },
        "dense": {"metric": "exact cosine", "top_k": r.RETRIEVER_TOP_K},
        "sparse_top_k": r.RETRIEVER_TOP_K,
        "rrf_k": r.RRF_K,
        "candidate_limit": r.CANDIDATE_LIMIT,
        "max_related_events": r.MAX_RELATED_EVENTS,
        "max_related_units": r.MAX_RELATED_UNITS,
        "rerank_batch_size": 8,
        "max_model_tokens": r.MAX_MODEL_TOKENS,
        "context": {
            "tokenizer": "o200k_base",
            "max_sources": r.MAX_FINAL_SOURCES,
            "max_tokens": r.MAX_CONTEXT_TOKENS,
        },
    }
    for key, value in fixed.items():
        if manifest.get(key) != value:
            raise ValueError(f"D has unsupported settings: {key}")


def quota_candidates(sparse_top: Sequence[str], dense_top: Sequence[str]) -> list[str]:
    """Reserve each retriever's Top 10; fill and order with the unchanged RRF."""
    all_ids = set(sparse_top) | set(dense_top)
    ranked = r.reciprocal_rank_fusion([sparse_top, dense_top], limit=len(all_ids))
    selected = set(sparse_top[:10]) | set(dense_top[:10])
    target = min(r.CANDIDATE_LIMIT, len(ranked))
    for unit_id in ranked:
        if len(selected) >= target:
            break
        selected.add(unit_id)
    return [unit_id for unit_id in ranked if unit_id in selected]


def replay_candidates(
    query: str,
    saved: Mapping[str, Any],
    pages: Sequence[r.Unit],
    records: Sequence[r.Unit],
    relations: Mapping[str, Any],
    source_order: Mapping[str, int],
    patterns: Any,
    *,
    condition: str = "E",
) -> r._ConditionState:
    if condition not in ("E", "F"):
        raise ValueError("Replay condition must be E or F")
    base = saved["hybrid_unit_ids"]
    page_ids = {doc.unit_id for doc in pages}
    if len(base) != min(r.CANDIDATE_LIMIT, len(page_ids)) or len(set(base)) != len(
        base
    ):
        raise ValueError("Invalid frozen D hybrid candidate list")
    if not set(base) <= page_ids:
        raise ValueError("D hybrid candidates are outside the raw-page corpus")
    for key in ("bm25_top50", "dense_top50"):
        ranked = saved[key]
        if (
            len(ranked) != min(r.RETRIEVER_TOP_K, len(page_ids))
            or len(set(ranked)) != len(ranked)
            or not set(ranked) <= page_ids
        ):
            raise ValueError("Invalid frozen D top-50 ranking")
    if (
        r.reciprocal_rank_fusion(
            [saved["bm25_top50"], saved["dense_top50"]], limit=r.CANDIDATE_LIMIT
        )
        != base
    ):
        raise ValueError("Frozen D hybrid does not match its saved RRF inputs")
    d_candidates, d_new, d_metadata = r._relation_condition_candidates(
        "D",
        query,
        base,
        pages,
        relations,
        source_order,
        patterns,
        relation_docs=records,
    )
    if (
        d_candidates != saved["candidate_unit_ids"]
        or d_new != saved["related_unit_ids"]
        or d_metadata != saved["relation"]
    ):
        raise ValueError(f"D relation replay mismatch for {saved['id']}")
    initial = (
        quota_candidates(saved["bm25_top50"], saved["dense_top50"])
        if condition == "F"
        else list(base)
    )
    candidates, ranked, metadata = r._relation_condition_candidates(
        "E",
        query,
        initial,
        pages,
        relations,
        source_order,
        patterns,
        relation_docs=records,
    )
    added = metadata.get("added_unit_ids", [])
    if condition == "E" and added != d_new:
        raise ValueError("E reselected D's frozen new pages")
    if set(ranked) - set(initial) != set(added):
        raise ValueError("Relation ranking does not match its new-page selection")
    if metadata.get("event_ids") != d_metadata.get("event_ids"):
        raise ValueError("E changed the resolved event list")
    if len(ranked) != len(set(ranked)) or len(ranked) > len(initial) + len(added):
        raise ValueError("Invalid E relation ranking")
    return r._ConditionState(
        condition=condition,
        sparse_top=saved["bm25_top50"],
        dense_top=saved["dense_top50"],
        hybrid=initial,
        candidates=candidates,
        related=ranked,
        relation=metadata,
    )


def replay(
    *,
    d_results: Path,
    results: Path,
    d_results_sha256: str = DEFAULT_D_RESULTS_SHA256,
    condition: str = "E",
    pages_path: Path = r.DEFAULT_PAGES,
    units_path: Path = r.DEFAULT_UNITS,
    relations_path: Path = r.DEFAULT_RELATIONS,
    questions_path: Path = r.DEFAULT_QUESTIONS,
    device: str = "cuda",
    threads: int = 4,
    cache_root: Path = DEFAULT_HUB_CACHE,
) -> dict[str, Any]:
    if condition not in ("E", "F"):
        raise ValueError("Replay condition must be E or F")
    if results.suffix != ".jsonl":
        raise ValueError(
            "Results must use a .jsonl path; manifest/temp names are reserved"
        )
    partial = results.with_suffix(results.suffix + ".partial")
    manifest_path = results.parent / "run-manifest.json"
    if results.exists() or partial.exists() or manifest_path.exists():
        raise FileExistsError(
            "Choose a new result path; completed/partial runs are preserved"
        )
    if threads < 1:
        raise ValueError("threads must be positive")
    if r.sha256_file(d_results) != d_results_sha256:
        raise ValueError("Frozen D results checksum mismatch")
    source_manifest_path = d_results.parent / "run-manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text())
    questions = r.load_questions(questions_path)
    saved_rows = r.jsonl_rows(d_results)
    fingerprints = r._input_fingerprints(
        pages_path, units_path, relations_path, questions
    )
    validate_d_run(saved_rows, questions, source_manifest, fingerprints)
    model_code = r.sha256_file(Path(r.__file__).with_name("models.py"))
    if source_manifest["code_sha256"]["models.py"] != model_code:
        raise ValueError("Model wrapper changed since the frozen D run")
    pages, page_map, source_order = r.load_pages(pages_path)
    records = r.load_record_units(units_path)
    relations = json.loads(relations_path.read_text())
    patterns = r._code_patterns(relations)
    docs_by_id = {doc.unit_id: doc for doc in pages}
    saved = {row["id"]: row for row in saved_rows}
    encoding = r._context_encoding()

    start = time.perf_counter()
    reranker = KureReranker(device=device, threads=threads, cache_root=cache_root)
    model_load_seconds = time.perf_counter() - start
    if reranker.metadata() != source_manifest["reranker_model"]:
        raise ValueError(f"{condition} reranker settings differ from the frozen D run")
    from threadpoolctl import threadpool_limits

    def run_question(question: r.Question) -> dict[str, Any]:
        start = time.perf_counter()
        previous = saved[question.question_id]
        state = replay_candidates(
            question.text,
            previous,
            pages,
            records,
            relations,
            source_order,
            patterns,
            condition=condition,
        )
        timings = {"relation_replay": r._timed_ms(start)}
        row = r._rerank_condition(
            state,
            question.text,
            docs_by_id,
            page_map,
            reranker,
            encoding,
            rerank_batch_size=8,
            query_embedding=previous["query_embedding"],
            timings=timings,
            overall_start=start,
        )
        quota = set(previous["bm25_top50"][:10]) | set(previous["dense_top50"][:10])
        return {
            "id": question.question_id,
            "question_sha256": previous["question_sha256"],
            **row,
            "new_related_unit_ids": state.relation.get("added_unit_ids", []),
            "initial_rankings_reused": condition == "E",
            "top50_rankings_reused": True,
            **(
                {
                    "initial_quota": {
                        "bm25_top10": previous["bm25_top50"][:10],
                        "dense_top10": previous["dense_top50"][:10],
                        "rrf_fill": [uid for uid in state.hybrid if uid not in quota],
                        "order": "original full RRF order",
                    }
                }
                if condition == "F"
                else {}
            ),
            "query_embedding_reused": True,
        }

    results.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "fresh-relation-overlap-replay-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "conditions": [condition],
        "source_D_results": str(d_results),
        "source_D_results_sha256": d_results_sha256,
        "source_D_manifest_sha256": r.sha256_file(source_manifest_path),
        "inputs": fingerprints,
        "models_code_sha256": model_code,
        "code_sha256": {
            p.name: r.sha256_file(p) for p in [Path(__file__), Path(r.__file__)]
        },
        "reranker_model": reranker.metadata(),
        "device": device,
        "threads": threads,
        "rrf_k": r.RRF_K,
        "candidate_limit": r.CANDIDATE_LIMIT,
        "context": source_manifest["context"],
        "model_load_seconds": model_load_seconds,
        "policy": (
            "F: union BM25 Top10 and dense Top10, fill to20 and order by original RRF; "
            "apply unchanged E relation selection/fusion to the changed initial set."
            if condition == "F"
            else "Freeze D new-page list; include verified hybrid overlaps in the "
            "same event/role/source ordering; equal RRF; unique final candidates."
        ),
        "timing_boundary": "Frozen Top50/query embedding reused; initial selection, "
        "relation replay, one rerank and original restore measured. "
        "Not a controlled end-to-end speed comparison.",
    }
    r.write_json_atomic(manifest_path, manifest)
    count = 0
    with threadpool_limits(limits=threads):
        warmup_start = time.perf_counter()
        warmup = run_question(questions[0])
        warmup_seconds = time.perf_counter() - warmup_start
        measured_start = time.perf_counter()
        with partial.open("x", encoding="utf-8") as handle:
            for question in questions:
                row = run_question(question)
                handle.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                handle.flush()
                count += 1
                if count % 10 == 0 or count == len(questions):
                    print(
                        f"{condition} replay: {count}/{len(questions)} questions",
                        flush=True,
                    )
            os.fsync(handle.fileno())
        elapsed = time.perf_counter() - measured_start
    os.replace(partial, results)
    summary = {
        "question_count": count,
        "result_rows": count,
        "measured_rerank_pairs": sum(
            len(row["candidate_unit_ids"]) for row in r.jsonl_rows(results)
        ),
        "warmup_rerank_pairs": warmup["rerank"]["pairs"],
        "query_embedding_calls": 0,
        "document_embedding_calls": 0,
        "bm25_calls": 0,
        "dense_search_calls": 0,
        "paid_API_calls": 0,
        "warmup_seconds": warmup_seconds,
        "measured_seconds": elapsed,
        "rss_peak_kb": r._rss_peak_kb(),
        "results_sha256": r.sha256_file(results),
    }
    manifest["summary"] = summary
    r.write_json_atomic(manifest_path, manifest)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=("E", "F"), default="E")
    parser.add_argument("--d-results", type=Path, default=DEFAULT_D_RESULTS)
    parser.add_argument(
        "--d-results-sha256",
        default=DEFAULT_D_RESULTS_SHA256,
        help="Trusted frozen D checksum; defaults to this experiment's D",
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--pages-path", type=Path, default=r.DEFAULT_PAGES)
    parser.add_argument("--units-path", type=Path, default=r.DEFAULT_UNITS)
    parser.add_argument("--relations-path", type=Path, default=r.DEFAULT_RELATIONS)
    parser.add_argument("--questions-path", type=Path, default=r.DEFAULT_QUESTIONS)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_HUB_CACHE)
    print(json.dumps(replay(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
