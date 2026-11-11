"""Replay missing lexical and dense ranks for the frozen 40 semantic questions.

Run from the repository root:
  .venv/bin/python -m experimental.u_10o0o.advanced_semantic_rank_replay
  .venv/bin/python -m experimental.u_10o0o.advanced_semantic_rank_replay --output-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ranks-v2

Scope contract: compute only fielded BM25 and CPU dense ranks needed to restore
missing diagnostics; preserve the frozen 20/40 RRF source order; do not open
references, score results, load KURE, or make network/API calls. Unsupported plans
retain only their original query. Existing search and plan-resolution helpers are
the supported implementation path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

# Force offline model loading before importing any Hugging Face-backed module.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from experimental.u_10o0o import advanced_evidence_benchmark as benchmark
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o import advanced_field_roles
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_field_roles import FIELD_POOL
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX
from experimental.u_10o0o.advanced_hg_relations import (
    HG_RELATIONS_SNAPSHOT,
    HGRelations,
)
from experimental.u_10o0o.advanced_planned_retrieval import resolve
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_search import HYBRID_WEIGHTS
from experimental.u_10o0o.advanced_snapshots import HG_SNAPSHOT
from experimental.u_10o0o.advanced_structured_store import STRUCTURED_SNAPSHOT
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.hybrid import RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index, rank_pages

RAW_RUN = (
    PROJECT_ROOT / "artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ranks-v2"
)
EXPECTED_IDS = tuple(f"AS-SEM-{number:02}" for number in range(1, 41))
DENSE_DEPTH = 1000
FROZEN_TOKENIZER = "bigram-id"
FROZEN_TOKENIZER_CODE_PATHS = (
    "experimental/u_10o0o/advanced_field_roles.py",
    "experimental/u_10o0o/bm25.py",
)
CODE_PATHS = (
    "experimental/u_10o0o/advanced_semantic_rank_replay.py",
    "experimental/u_10o0o/advanced_evidence_benchmark.py",
    "experimental/u_10o0o/advanced_plan_ab.py",
    "experimental/u_10o0o/advanced_search.py",
    "experimental/u_10o0o/advanced_field_roles.py",
    "experimental/u_10o0o/advanced_patterns.py",
    "experimental/u_10o0o/bm25.py",
    "experimental/u_10o0o/advanced_experiment_eval.py",
    "experimental/u_10o0o/advanced_planned_retrieval.py",
    "experimental/u_10o0o/advanced_query_plan.py",
    "experimental/u_10o0o/advanced_target_lookup.py",
    "experimental/u_10o0o/advanced_hg_relations.py",
    "experimental/u_10o0o/advanced_hg_lookup.py",
    "experimental/u_10o0o/advanced_structured_store.py",
    "experimental/u_10o0o/advanced_snapshots.py",
    "experimental/u_10o0o/advanced_role_context.py",
    "experimental/u_10o0o/advanced_case_sources.py",
    "experimental/u_10o0o/hybrid.py",
    "experimental/u_10o0o/search.py",
    "experimental/u_10o0o/embeding.py",
    "experimental/u_10o0o/prepare.py",
    "app/evaluation/dataset.py",
    "app/ingestion/layout.py",
)
SNAPSHOT_PATHS = {
    "hg_code_relations": HG_RELATIONS_SNAPSHOT,
    "structured": STRUCTURED_SNAPSHOT,
    "hg_relations": HG_SNAPSHOT,
    "curated_sources": CURATED,
    "case_sources": CASE_INDEX,
    "role_facts": FACTS,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def fingerprint(path: Path) -> dict:
    path = Path(path).resolve()
    if path.is_file():
        return {
            "path": display_path(path),
            "kind": "file",
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    if not path.is_dir():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    file_count = size_bytes = 0
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if not child.is_file() or child.name.endswith(("-wal", "-shm", "-journal")):
            continue
        relative = child.relative_to(path).as_posix().encode("utf-8")
        child_hash = sha256_file(child)
        child_size = child.stat().st_size
        digest.update(relative + b"\0" + child_size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(child_hash))
        file_count += 1
        size_bytes += child_size
    return {
        "path": display_path(path),
        "kind": "tree",
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def archived_code_hashes(path: Path, code_paths: tuple[str, ...]) -> dict[str, str]:
    config = json.loads(path.read_text(encoding="utf-8"))
    hashes = {
        row["path"]: row["sha256"]
        for row in config.get("inputs_and_code", [])
        if isinstance(row, dict) and row.get("path") in code_paths
    }
    if set(hashes) != set(code_paths):
        raise ValueError(
            "Archived baseline config must stamp both tokenizer source files."
        )
    return hashes


def source_order(ranked: list[tuple[dict, float]]) -> list[list]:
    return [[Path(page["file_name"]).stem, page["page"]] for page, _ in ranked]


def ranked_rows(ranked: list[tuple[dict, float]]) -> list[dict]:
    return [
        {
            "rank": index,
            "book_id": Path(page["file_name"]).stem,
            "page": page["page"],
            "chunk_id": page.get("chunk_id"),
            "score": float(score),
        }
        for index, (page, score) in enumerate(ranked, start=1)
    ]


def query_plan(question: str, steps: list) -> list[dict]:
    queries = [{"query_text": question, "kind": "original", "step_indices": []}]
    by_text = {question: queries[0]}
    for step_index, step in enumerate(steps):
        if step.pages:
            continue
        saved = by_text.get(step.query)
        if saved is None:
            saved = {
                "query_text": step.query,
                "kind": "unresolved_step",
                "step_indices": [],
            }
            by_text[step.query] = saved
            queries.append(saved)
        saved["step_indices"].append(step_index)
    return queries


def archived_stats_by_index(row: dict) -> dict[int, dict]:
    stats = row.get("query_stats", [])
    by_index = {item.get("query_index"): item for item in stats}
    if len(by_index) != len(stats) or None in by_index:
        raise ValueError(f"Invalid archived query indices for {row.get('id')}.")
    return by_index


def output_dir_arg() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def run(output: Path) -> dict:
    if Path.cwd().resolve() != PROJECT_ROOT:
        raise RuntimeError("Run this CLI from the repository root.")
    questions, plan_path, _references_path, source_paths = benchmark.load_inputs(
        "broad"
    )
    semantic = [row for row in questions if row["id"].startswith("AS-SEM-")]
    semantic_ids = tuple(row["id"] for row in semantic)
    if semantic_ids != EXPECTED_IDS:
        raise ValueError(
            "Broad inputs must contain the frozen AS-SEM-01 through AS-SEM-40 set."
        )
    plans = benchmark.read_jsonl(plan_path)
    plans_by_id = {row["id"]: row for row in plans}
    if len(plans_by_id) != len(plans) or not set(semantic_ids) <= plans_by_id.keys():
        raise ValueError(
            "Cached broad plans must uniquely cover all semantic questions."
        )

    query_plan_inputs = list(
        dict.fromkeys([*source_paths, benchmark.BROAD / "questions.json"])
    )
    raw_inputs = [
        RAW_RUN / "config.json",
        RAW_RUN / "traces.jsonl",
        RAW_RUN / "workload.jsonl",
    ]
    cache_inputs = [CACHE_PATH, VECTORS_PATH]
    code_inputs = [PROJECT_ROOT / path for path in CODE_PATHS]
    input_hashes = {
        "questions_and_plan": [fingerprint(path) for path in query_plan_inputs],
        "baseline_artifacts": [fingerprint(path) for path in raw_inputs],
        "page_cache_and_vectors": [fingerprint(path) for path in cache_inputs],
        "relevant_code": [fingerprint(path) for path in code_inputs],
    }
    tokenizer_source_hashes = {
        "baseline_config": archived_code_hashes(
            RAW_RUN / "config.json", FROZEN_TOKENIZER_CODE_PATHS
        ),
        "replay_sources": {
            source_path: sha256_file(PROJECT_ROOT / source_path)
            for source_path in FROZEN_TOKENIZER_CODE_PATHS
        },
    }
    tokenizer_module_default = advanced_field_roles.TOKENIZER_NAME
    snapshot_stamps_before = {
        name: fingerprint(path) for name, path in SNAPSHOT_PATHS.items()
    }

    workload = read_jsonl(RAW_RUN / "workload.jsonl")
    workload_by_id = {row["id"]: row for row in workload}
    if len(workload_by_id) != len(workload):
        raise ValueError("Archived workload IDs are duplicated.")
    if not set(semantic_ids) <= workload_by_id.keys():
        raise ValueError("Archived workload does not cover all semantic questions.")
    for qid in semantic_ids:
        if workload_by_id[qid].get("split") != "semantic_queries":
            raise ValueError(f"Archived workload split mismatch for {qid}.")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    ranks_path = output / "ranks.jsonl"
    run_started = datetime.now(UTC).isoformat()
    manifest = {
        "experiment": "advanced-semantic-first-miss-ranks-v1",
        "status": "running",
        "started_at": run_started,
        "scope": {
            "suite": "broad",
            "question_prefix": "AS-SEM-",
            "questions_expected": 40,
        },
        "ranking": {
            "bm25_pool": FIELD_POOL,
            "bm25_tokenizer": FROZEN_TOKENIZER,
            "bm25_top20_prefix_assertion": True,
            "dense_depth": DENSE_DEPTH,
            "rrf_inputs_per_retriever": 20,
            "rrf_top_k": 40,
            "rrf_k": RRF_K,
            "hybrid_weights": list(HYBRID_WEIGHTS),
            "embedding_model": MODEL_ID,
            "embedding_revision": MODEL_REVISION,
            "embedding_device": "cpu",
        },
        "frozen_tokenizer": {
            "selected": FROZEN_TOKENIZER,
            "module_default_at_replay": tokenizer_module_default,
            "reason": "Reproduce the broad-01 baseline tokenizer after the current module default changed.",
            "applied_before_search_index_materialization": True,
            "baseline_and_replay_source_sha256": tokenizer_source_hashes,
        },
        "execution_boundary": {
            "references_opened": False,
            "scores_opened": False,
            "kure_loaded": False,
            "external_api_calls": 0,
            "hf_hub_offline": os.environ["HF_HUB_OFFLINE"] == "1",
            "transformers_offline": os.environ["TRANSFORMERS_OFFLINE"] == "1",
            "query_text_storage": "local_only",
        },
        "input_hashes": input_hashes,
        "snapshot_stamps_before": snapshot_stamps_before,
        "results_file": ranks_path.name,
        "questions_completed": 0,
        "query_records_completed": 0,
        "archive_mismatches": 0,
        "invariant_failures": [],
    }
    write_json(output / "manifest.json", manifest)

    pages, vectors = load_index()
    search = ev.Search(pages, vectors)
    advanced_field_roles.TOKENIZER_NAME = FROZEN_TOKENIZER
    try:
        index = search.index
    finally:
        advanced_field_roles.TOKENIZER_NAME = tokenizer_module_default
    embedder = search.embedder
    lookup = load_target_lookup(pages)
    per_question: list[dict] = []
    total_queries = archive_mismatches = 0
    invariant_failures: list[str] = []
    run_started_clock = perf_counter()

    with (
        HGRelations(pages=pages) as relations,
        ranks_path.open("w", encoding="utf-8") as stream,
    ):
        relation_provenance = relations.provenance
        for question_number, row in enumerate(semantic, start=1):
            qid = row["id"]
            plan, plan_status = benchmark.supported_plan(
                row, plans_by_id.get(qid), lookup, "broad"
            )
            steps = (
                resolve(row["question"], plan, "typed", lookup, relations, None)
                if plan
                else []
            )
            queries = query_plan(row["question"], list(steps))
            archive_by_index = archived_stats_by_index(workload_by_id[qid])
            question_query_failures = []
            records_for_question = 0

            for query_index, query in enumerate(queries):
                text = query["query_text"]
                archived = archive_by_index.get(query_index)
                mapping_ok = archived is not None and archived.get("is_original") == (
                    query["kind"] == "original"
                )
                fielded80 = index.fielded(text, top_k=FIELD_POOL)
                fielded20 = index.fielded(text, top_k=20)
                prefix_ok = ranked_rows(fielded80[:20]) == ranked_rows(fielded20)
                if len(fielded80) > FIELD_POOL:
                    invariant_failures.append(f"{qid}:{query_index}:bm25_pool_exceeded")
                    prefix_ok = False
                if not prefix_ok:
                    invariant_failures.append(
                        f"{qid}:{query_index}:bm25_prefix20_mismatch"
                    )

                query_vector = embedder.encode_query(
                    [text], normalize_embeddings=True, show_progress_bar=False
                )[0]
                dense1000 = rank_pages(
                    query_vector, vectors, pages, top_k=min(DENSE_DEPTH, len(pages))
                )
                union40 = weighted_rrf(
                    fielded80[:20], dense1000[:20], HYBRID_WEIGHTS, top_k=40
                )
                union_sources = source_order(union40)
                rrf20_sources = source_order(union40[:20])
                union_match = mapping_ok and union_sources == archived.get("union")
                rrf20_match = mapping_ok and rrf20_sources == archived.get("rrf20")
                if not mapping_ok or not union_match or not rrf20_match:
                    archive_mismatches += 1
                    question_query_failures.append(query_index)
                comparison = {
                    "query_index_mapping_match": mapping_ok,
                    "union40_exact_source_order_match": union_match,
                    "rrf20_exact_source_order_match": rrf20_match,
                    "bm25_top20_prefix_match": prefix_ok,
                    "archived_query_index_present": archived is not None,
                }
                query_status = (
                    "archive_match" if all(comparison.values()) else "archive_mismatch"
                )
                record = {
                    "id": qid,
                    "query_index": query_index,
                    "query_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "kind": query["kind"],
                    "step_indices": query["step_indices"],
                    "status": query_status,
                    "plan_status": plan_status,
                    "query_text": text,
                    "comparison": comparison,
                    "fielded_top80": ranked_rows(fielded80),
                    "dense_top1000": ranked_rows(dense1000),
                    "rrf_union40": ranked_rows(union40),
                    "rrf20": ranked_rows(union40[:20]),
                }
                stream.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                stream.flush()
                total_queries += 1
                records_for_question += 1

            if len(archive_by_index) != len(queries):
                question_query_failures.append("query_count")
                archive_mismatches += 1
            question_status = (
                "archive_match" if not question_query_failures else "archive_mismatch"
            )
            per_question.append(
                {
                    "id": qid,
                    "status": question_status,
                    "plan_status": plan_status,
                    "queries_expected": len(queries),
                    "query_records_written": records_for_question,
                    "failed_query_indices": question_query_failures,
                }
            )
            manifest.update(
                questions_completed=question_number,
                query_records_completed=total_queries,
                archive_mismatches=archive_mismatches,
                invariant_failures=invariant_failures,
            )
            write_json(output / "manifest.json", manifest)
            print(f"Replayed {question_number}/40 semantic questions.", flush=True)

    snapshot_stamps_after = {
        name: fingerprint(path) for name, path in SNAPSHOT_PATHS.items()
    }
    input_hashes_after = {
        "questions_and_plan": [fingerprint(path) for path in query_plan_inputs],
        "baseline_artifacts": [fingerprint(path) for path in raw_inputs],
        "page_cache_and_vectors": [fingerprint(path) for path in cache_inputs],
        "relevant_code": [fingerprint(path) for path in code_inputs],
    }
    snapshot_stamps_unchanged = snapshot_stamps_before == snapshot_stamps_after
    inputs_unchanged = input_hashes == input_hashes_after
    if not snapshot_stamps_unchanged:
        invariant_failures.append("snapshot_stamps_changed")
    if not inputs_unchanged:
        invariant_failures.append("input_hashes_changed_during_replay")
    complete = len(per_question) == 40 and all(
        row["query_records_written"] == row["queries_expected"] for row in per_question
    )
    successful = (
        complete
        and archive_mismatches == 0
        and not invariant_failures
        and all(row["status"] == "archive_match" for row in per_question)
    )
    manifest.update(
        status="complete" if complete else "incomplete",
        outcome="pass" if successful else "fail",
        replay_ok=successful,
        results_sha256=sha256_file(ranks_path),
        finished_at=datetime.now(UTC).isoformat(),
        elapsed_seconds=round(perf_counter() - run_started_clock, 3),
        questions_expected=40,
        questions_completed=len(per_question),
        all_40_complete=complete,
        query_records_completed=total_queries,
        archive_mismatches=archive_mismatches,
        invariant_failures=invariant_failures,
        snapshots_unchanged=snapshot_stamps_unchanged,
        snapshot_stamps_after=snapshot_stamps_after,
        inputs_unchanged=inputs_unchanged,
        input_hashes_after=input_hashes_after,
        relation_provenance=relation_provenance,
        question_status=per_question,
    )
    write_json(output / "manifest.json", manifest)
    print(
        f"Finished: questions={len(per_question)}/40, queries={total_queries}, "
        f"archive_mismatches={archive_mismatches}, snapshots_unchanged={snapshot_stamps_unchanged}, "
        f"outcome={manifest['outcome']}",
        flush=True,
    )
    if not successful:
        raise SystemExit(1)
    return manifest


def main() -> None:
    args = output_dir_arg()
    run(args.output_dir)


if __name__ == "__main__":
    main()
