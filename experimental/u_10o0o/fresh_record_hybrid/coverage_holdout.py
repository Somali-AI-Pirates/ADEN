"""Run a question-only four-arm evaluation against frozen fresh-record policies.

The runner reads only id/question pairs, frozen raw A inputs/vectors, and the
relation index. It shares each query embedding and A-side BM25/dense rankings
between E and F, then adds the frozen final-coverage selection without models.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import final_coverage as fc
from . import relation_overlap as replay
from . import retrieval as r
from .models import DEFAULT_HUB_CACHE, KureReranker, SnowflakeEmbedder

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path("artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111")
HOLDOUT_ROOT = Path("artifacts/u_10o0o/advanced-fresh-holdout-20261111")
POLICY_FREEZE_PATH = Path(
    "experimental/u_10o0o/records/advanced-fresh-holdout-20261111/policy-freeze.json"
)
POLICY_FREEZE_SHA256 = (
    "f2ebb1b0d9f81e446a2c5e797dde6b142d83479eefcc57c591b68cc3cf10eb47"
)
EXISTING_EVALUATION_ROOT = Path(
    "artifacts/u_10o0o/advanced-fresh-existing-evaluation-20261111"
)
DEFAULT_EXISTING_OUTPUT_DIR = EXISTING_EVALUATION_ROOT / "run-v1"
DEFAULT_NEW_HOLDOUT_DIR = HOLDOUT_ROOT / "run-v1"

POLICY_PATHS = {
    "experimental/u_10o0o/fresh_record_hybrid/retrieval.py",
    "experimental/u_10o0o/fresh_record_hybrid/relation_overlap.py",
    "experimental/u_10o0o/fresh_record_hybrid/final_coverage.py",
    "experimental/u_10o0o/fresh_record_hybrid/models.py",
    "experimental/u_10o0o/fresh_record_hybrid/scoring.py",
}
SOURCE_PATHS = {
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl",
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl",
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/relation_index.json",
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/raw-manifest.json",
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/vectors-v1/A.vectors.npy",
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/vectors-v1/A.progress.json",
}
CONDITIONS = ("E", "E_coverage", "F", "F_coverage")
TIMING_NOTE = (
    "BM25, A dense top-50, query embedding, and base RRF are computed once "
    "per question and attributed to E and F; these are not independent "
    "four-arm online runs."
)


@dataclass(frozen=True)
class HoldoutQuestion:
    question_id: str | int
    text: str


@dataclass
class SearchContext:
    pages: list[r.Unit]
    records: list[r.Unit]
    page_map: dict[str, dict[str, Any]]
    source_order: dict[str, int]
    relation_index: Mapping[str, Any]
    vectors: np.ndarray
    docs_by_id: dict[str, r.Unit]
    sparse: r.BM25Index
    dense: r.DenseIndex
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]]
    record_links: Mapping[tuple[str, str], Mapping[str, Mapping[str, Any]]]
    encoding: Any


def _repo_path(root: Path, relative: str | Path) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else root / path


def sha256_path(path: str | Path) -> str:
    return r.sha256_file(path)


def validate_policy_freeze_manifest(manifest: Mapping[str, Any]) -> None:
    expected_top_keys = {
        "format",
        "policy_files",
        "source_files",
        "conditions",
        "candidate_limit",
        "max_sources",
        "max_context_tokens",
        "rrf_k",
        "sparse_top_k",
        "dense_top_k",
        "embedding_batch_size",
        "rerank_batch_size",
        "dtype",
        "embedding_model",
        "embedding_revision",
        "reranker_model",
        "reranker_revision",
    }
    if set(manifest) != expected_top_keys:
        raise ValueError("Unsupported policy-freeze manifest fields")
    if manifest.get("format") != "fresh-holdout-policy-v1":
        raise ValueError("Unsupported policy-freeze manifest format")
    if set(manifest.get("policy_files", {})) != POLICY_PATHS:
        raise ValueError("Policy-freeze file set does not match the runner")
    if set(manifest.get("source_files", {})) != SOURCE_PATHS:
        raise ValueError("Policy-freeze source set does not match the runner")
    expected_values = {
        "conditions": list(CONDITIONS),
        "candidate_limit": 20,
        "max_sources": 8,
        "max_context_tokens": 8000,
        "rrf_k": 60,
        "sparse_top_k": 50,
        "dense_top_k": 50,
        "embedding_batch_size": 4,
        "rerank_batch_size": 8,
        "dtype": "float32",
        "embedding_model": "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        "embedding_revision": "55ec6e9358a56d56af759bc8372e970caf8c305f",
        "reranker_model": "nlpai-lab/KURE-Reranker-nano",
        "reranker_revision": "8514b9e113f3b27c441ed9c2b50f81d80388b2fd",
    }
    for key, expected in expected_values.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Policy-freeze setting mismatch: {key}")
    for group in ("policy_files", "source_files"):
        values = manifest[group]
        if not isinstance(values, Mapping) or any(
            not isinstance(path, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for path, digest in values.items()
        ):
            raise ValueError(f"Invalid SHA-256 values in {group}")


def verify_policy_freeze(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    freeze_path = _repo_path(root, POLICY_FREEZE_PATH)
    if sha256_path(freeze_path) != POLICY_FREEZE_SHA256:
        raise ValueError("Pinned policy-freeze SHA-256 mismatch")
    try:
        manifest = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read policy freeze: {freeze_path}") from exc
    if not isinstance(manifest, dict):
        raise TypeError("Policy-freeze manifest must be a JSON object")
    validate_policy_freeze_manifest(manifest)
    for relative, expected in manifest["policy_files"].items():
        path = _repo_path(root, relative)
        if sha256_path(path) != expected:
            raise ValueError(f"Frozen policy SHA-256 mismatch: {relative}")
    for relative, expected in manifest["source_files"].items():
        path = _repo_path(root, relative)
        if sha256_path(path) != expected:
            raise ValueError(f"Frozen source SHA-256 mismatch: {relative}")
    return manifest


def load_holdout_questions(path: str | Path) -> list[HoldoutQuestion]:
    questions: list[HoldoutQuestion] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict) or set(row) != {"id", "question"}:
                raise ValueError(
                    "Holdout rows must contain exactly id and question; "
                    "extra fields are rejected"
                )
            question_id = row["id"]
            if isinstance(question_id, bool) or not isinstance(question_id, (str, int)):
                raise TypeError("Holdout question id must be a string or integer")
            text = row["question"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Holdout question must be a nonempty string")
            key = json.dumps(question_id, ensure_ascii=False, sort_keys=True)
            if key in seen:
                raise ValueError(f"Duplicate holdout question id {question_id!r}")
            seen.add(key)
            questions.append(HoldoutQuestion(question_id, text))
    if not questions:
        raise ValueError("No holdout questions found")
    return questions


def verify_questions_digest(path: str | Path, expected: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
        raise ValueError("--questions-sha256 must be a 64-character SHA-256")
    actual = sha256_path(path)
    if actual.lower() != expected.lower():
        raise ValueError("Holdout questions SHA-256 mismatch")
    return actual


def resolve_output_dir(
    root: Path, evaluation_kind: str, output_dir: str | Path | None
) -> Path:
    if evaluation_kind not in {"existing_regression", "new_holdout"}:
        raise ValueError("evaluation kind must be existing_regression or new_holdout")
    if output_dir is None:
        default = (
            DEFAULT_EXISTING_OUTPUT_DIR
            if evaluation_kind == "existing_regression"
            else DEFAULT_NEW_HOLDOUT_DIR
        )
        output = root / default
    else:
        candidate = Path(output_dir)
        output = candidate if candidate.is_absolute() else root / candidate
    output = output.resolve()
    allowed_roots = (
        (root / HOLDOUT_ROOT).resolve(),
        (root / EXISTING_EVALUATION_ROOT).resolve(),
    )
    if not any(allowed_root in output.parents for allowed_root in allowed_roots):
        raise ValueError("Output directory must be inside a dedicated evaluation root")
    return output


def ensure_fresh_output_dir(path: str | Path) -> Path:
    output = Path(path)
    if output.exists():
        raise FileExistsError(
            f"Output path already exists; refusing overwrite: {output}"
        )
    return output


def build_search_context(
    pages: list[r.Unit],
    records: list[r.Unit],
    page_map: dict[str, dict[str, Any]],
    source_order: dict[str, int],
    relation_index: Mapping[str, Any],
    vectors: np.ndarray,
    encoding: Any,
) -> SearchContext:
    ordered_pages = sorted(pages, key=lambda item: item.unit_id)
    ordered_records = sorted(records, key=lambda item: item.unit_id)
    docs_by_id = {doc.unit_id: doc for doc in ordered_pages}
    if len(docs_by_id) != len(ordered_pages):
        raise ValueError("Duplicate raw A page unit ids")
    if vectors.ndim != 2 or vectors.shape[0] != len(ordered_pages):
        raise ValueError("A vector matrix does not match sorted raw A pages")
    patterns = r._code_patterns(relation_index)
    return SearchContext(
        pages=ordered_pages,
        records=ordered_records,
        page_map=page_map,
        source_order=source_order,
        relation_index=relation_index,
        vectors=vectors,
        docs_by_id=docs_by_id,
        sparse=r.BM25Index(ordered_pages),
        dense=r.DenseIndex(ordered_pages, vectors),
        code_patterns=patterns,
        record_links=fc.build_record_links(
            ordered_records,
            relation_index,
            page_map,
            patterns=patterns,
        ),
        encoding=encoding,
    )


def _f_state(
    question: str,
    e_state: r._ConditionState,
    context: SearchContext,
) -> tuple[r._ConditionState, dict[str, float]]:
    started = time.perf_counter()
    quota_started = time.perf_counter()
    initial = replay.quota_candidates(e_state.sparse_top, e_state.dense_top)
    quota_ms = r._timed_ms(quota_started)
    relation_started = time.perf_counter()
    candidates, related, relation = r._relation_condition_candidates(
        "E",
        question,
        initial,
        context.pages,
        context.relation_index,
        context.source_order,
        context.code_patterns,
        relation_docs=context.records,
    )
    relation_ms = r._timed_ms(relation_started)
    if relation.get("event_ids") != e_state.relation.get("event_ids"):
        raise RuntimeError("F changed the frozen E relation resolution")
    state = r._ConditionState(
        condition="F",
        sparse_top=e_state.sparse_top,
        dense_top=e_state.dense_top,
        hybrid=initial,
        candidates=candidates,
        related=related,
        relation=relation,
    )
    return state, {
        "quota_candidates_ms": quota_ms,
        "relation_policy_ms": relation_ms,
        "candidate_policy_ms": r._timed_ms(started),
    }


def _assert_coverage_preserves_upstream(
    base: Mapping[str, Any],
    coverage: Mapping[str, Any],
) -> None:
    for key in (
        "bm25_top50",
        "dense_top50",
        "hybrid_unit_ids",
        "candidate_unit_ids",
        "candidate_source_ids",
        "related_unit_ids",
        "reranked_unit_ids",
        "rerank_order",
        "rerank",
        "relation",
        "query_embedding",
    ):
        if coverage.get(key) != base.get(key):
            raise RuntimeError(f"Coverage changed upstream result field: {key}")
    if coverage.get("upstream_reused", {}).get("new_model_calls") != 0:
        raise RuntimeError("Coverage selection unexpectedly added model calls")


def _result_is_complete(row: Mapping[str, Any]) -> None:
    if len(row["candidate_unit_ids"]) != r.CANDIDATE_LIMIT:
        raise RuntimeError("Holdout arm did not produce exactly 20 candidates")
    if len(set(row["candidate_unit_ids"])) != r.CANDIDATE_LIMIT:
        raise RuntimeError("Holdout arm produced duplicate candidates")
    if len(row["reranked_unit_ids"]) != r.CANDIDATE_LIMIT:
        raise RuntimeError("Holdout arm did not rerank all 20 candidates")
    if len(row["final_sources"]) > r.MAX_FINAL_SOURCES:
        raise RuntimeError("Holdout context exceeds the 8-source limit")
    if row["context_tokens"] > r.MAX_CONTEXT_TOKENS:
        raise RuntimeError("Holdout context exceeds the 8000-token limit")


def run_questions(
    questions: Sequence[HoldoutQuestion],
    context: SearchContext,
    embedder: Any,
    reranker: Any,
    *,
    rerank_batch_size: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if rerank_batch_size != 8:
        raise ValueError("Frozen holdout rerank batch size is 8")
    all_rows: list[dict[str, Any]] = []
    retrieval_wall_ms = 0.0
    selection_wall_ms = 0.0
    run_started = time.perf_counter()
    for question_index, question in enumerate(questions):
        retrieval_started = time.perf_counter()
        e_state, query_embedding, shared_timings = r._build_condition_state(
            "E",
            question.text,
            context.pages,
            context.vectors,
            context.sparse,
            embedder,
            context.relation_index,
            context.source_order,
            dense_index=context.dense,
            code_patterns=context.code_patterns,
            relation_docs=context.records,
        )
        shared_search = {
            key: float(shared_timings[key])
            for key in ("sparse", "embed_query", "dense", "rrf")
        }
        f_state, f_candidate_timings = _f_state(question.text, e_state, context)
        if e_state.sparse_top != f_state.sparse_top:
            raise RuntimeError("E and F did not share the sparse top-50")
        if e_state.dense_top != f_state.dense_top:
            raise RuntimeError("E and F did not share the dense top-50")
        if e_state.relation.get("event_ids") != f_state.relation.get("event_ids"):
            raise RuntimeError("E and F did not share relation resolution")
        if len(e_state.candidates) != r.CANDIDATE_LIMIT:
            raise RuntimeError("E did not produce exactly 20 candidates")
        if len(f_state.candidates) != r.CANDIDATE_LIMIT:
            raise RuntimeError("F did not produce exactly 20 candidates")

        states = {"E": e_state, "F": f_state}
        branch_policy_ms = {
            "E": float(shared_timings["relation_fusion"]),
            "F": float(f_candidate_timings["candidate_policy_ms"]),
        }
        results: dict[str, dict[str, Any]] = {}
        rerank_order = ("E", "F") if question_index % 2 == 0 else ("F", "E")
        for condition in rerank_order:
            timings = {
                **shared_search,
                "relation_fusion": branch_policy_ms[condition],
            }
            branch_started = time.perf_counter()
            result = r._rerank_condition(
                states[condition],
                question.text,
                context.docs_by_id,
                context.page_map,
                reranker,
                context.encoding,
                rerank_batch_size=rerank_batch_size,
                query_embedding=query_embedding,
                timings=timings,
                overall_start=branch_started,
            )
            rerank_ms = float(result["timings_ms"]["rerank"])
            restore_ms = float(result["timings_ms"]["restore"])
            shared_total = sum(shared_search.values())
            full_attributed = round(
                shared_total + branch_policy_ms[condition] + rerank_ms + restore_ms,
                3,
            )
            result["id"] = question.question_id
            result["question_sha256"] = r.sha256_text(question.text)
            result["timing_attribution"] = {
                "shared_search_ms": round(shared_total, 3),
                "shared_components_ms": {
                    key: round(value, 3) for key, value in shared_search.items()
                },
                "branch_candidate_policy_ms": round(branch_policy_ms[condition], 3),
                "quota_candidates_ms": (
                    round(f_candidate_timings["quota_candidates_ms"], 3)
                    if condition == "F"
                    else 0.0
                ),
                "relation_policy_ms": (
                    round(f_candidate_timings["relation_policy_ms"], 3)
                    if condition == "F"
                    else round(branch_policy_ms[condition], 3)
                ),
                "rerank_execution_order": list(rerank_order),
                "rerank_ms": round(rerank_ms, 3),
                "restore_ms": round(restore_ms, 3),
                "full_retrieval_attributed_ms": full_attributed,
                "query_embeddings_for_question": 1,
                "reranker_calls_for_question": 2,
                "basis": TIMING_NOTE,
            }
            result["timings_ms"]["total"] = full_attributed
            result["timings_ms"]["full_retrieval_attributed"] = full_attributed
            result["timings_ms"]["query_embedding_shared"] = round(
                shared_search["embed_query"], 3
            )
            result["candidate_policy"] = (
                "E relation fusion"
                if condition == "E"
                else "F quota then E relation fusion"
            )
            _result_is_complete(result)
            results[condition] = result
        retrieval_wall_ms += r._timed_ms(retrieval_started)

        plan_started = time.perf_counter()
        plan = fc.plan_requests(
            question.text, context.relation_index, context.code_patterns
        )
        plan_ms = r._timed_ms(plan_started)
        selection_started = time.perf_counter()
        e_coverage = fc.select_context(
            results["E"],
            plan,
            context.record_links,
            context.docs_by_id,
            context.page_map,
            context.encoding,
        )
        f_coverage = fc.select_context(
            results["F"],
            plan,
            context.record_links,
            context.docs_by_id,
            context.page_map,
            context.encoding,
        )
        for baseline, coverage in (
            (results["E"], e_coverage),
            (results["F"], f_coverage),
        ):
            _assert_coverage_preserves_upstream(baseline, coverage)
            if len(coverage["candidate_unit_ids"]) != r.CANDIDATE_LIMIT:
                raise RuntimeError("Coverage arm changed the 20-candidate set")
            if len(coverage["final_sources"]) > r.MAX_FINAL_SOURCES:
                raise RuntimeError("Coverage arm exceeds the 8-source limit")
            if coverage["context_tokens"] > r.MAX_CONTEXT_TOKENS:
                raise RuntimeError("Coverage arm exceeds the 8000-token limit")
            selection_ms = float(coverage["final_selection"]["selection_ms"])
            coverage["added_selection_timing_ms"] = {
                "request_plan_shared_ms": round(plan_ms, 3),
                "selection_and_restore_ms": round(selection_ms, 3),
                "total_attributed_ms": round(plan_ms + selection_ms, 3),
                "new_model_calls": 0,
                "shared_request_plan_computed_once": True,
            }
            coverage["timings_ms"]["request_plan_shared_ms"] = round(plan_ms, 3)
        selection_wall_ms += r._timed_ms(selection_started) + plan_ms
        all_rows.extend((results["E"], e_coverage, results["F"], f_coverage))
        if len(all_rows) != (question_index + 1) * len(CONDITIONS):
            raise RuntimeError("Holdout question did not produce all four arms")
        if [row["condition"] for row in all_rows[-4:]] != list(CONDITIONS):
            raise RuntimeError("Holdout arm ordering is unstable")
        completed = question_index + 1
        if completed % 10 == 0:
            elapsed_s = time.perf_counter() - run_started
            print(
                f"evaluation progress questions={completed}/{len(questions)} "
                f"results={len(all_rows)} elapsed_s={elapsed_s:.1f}",
                flush=True,
            )
    return all_rows, {
        "query_retrieval_wall_ms": round(retrieval_wall_ms, 3),
        "added_selection_wall_ms": round(selection_wall_ms, 3),
    }


def _load_frozen_inputs(
    root: Path,
) -> tuple[
    list[r.Unit],
    list[r.Unit],
    dict[str, dict[str, Any]],
    dict[str, int],
    dict[str, Any],
]:
    pages_path = _repo_path(root, EXPERIMENT_ROOT / "pages.jsonl")
    units_path = _repo_path(root, EXPERIMENT_ROOT / "units.jsonl")
    relations_path = _repo_path(root, EXPERIMENT_ROOT / "relation_index.json")
    pages, page_map, source_order = r.load_pages(pages_path)
    records = r.load_record_units(units_path)
    try:
        relation_index = json.loads(relations_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot read frozen relation index: {relations_path}"
        ) from exc
    if not isinstance(relation_index, dict):
        raise TypeError("Frozen relation index must be a JSON object")
    return pages, records, page_map, source_order, relation_index


def write_output_bundle(
    output_dir: str | Path,
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> Path:
    output = ensure_fresh_output_dir(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.partial-", dir=output.parent))
    try:
        results_path = stage / "results.jsonl"
        with results_path.open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        saved_manifest = {
            **dict(manifest),
            "results_sha256": sha256_path(results_path),
            "results_rows": len(rows),
        }
        manifest_path = stage / "run-manifest.json"
        with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                saved_manifest,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if output.exists():
            raise FileExistsError(
                f"Output path appeared during run; refusing overwrite: {output}"
            )
        os.rename(stage, output)
        return output
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _run(args: argparse.Namespace) -> Path:
    root = PROJECT_ROOT
    output = resolve_output_dir(root, args.evaluation_kind, args.output_dir)
    ensure_fresh_output_dir(output)

    preflight_started = time.perf_counter()
    freeze = verify_policy_freeze(root)
    freeze_check_ms = r._timed_ms(preflight_started)
    questions_path = Path(args.questions).resolve()
    question_load_started = time.perf_counter()
    question_file_sha = verify_questions_digest(questions_path, args.questions_sha256)
    questions = load_holdout_questions(questions_path)
    question_input_ms = r._timed_ms(question_load_started)

    data_started = time.perf_counter()
    pages, records, page_map, source_order, relation_index = _load_frozen_inputs(root)
    data_load_ms = r._timed_ms(data_started)

    model_started = time.perf_counter()
    embedder = SnowflakeEmbedder(
        device="cuda",
        threads=4,
        cache_root=Path(args.cache_root),
    )
    reranker = KureReranker(
        device="cuda",
        threads=4,
        cache_root=Path(args.cache_root),
    )
    model_load_ms = r._timed_ms(model_started)

    vector_started = time.perf_counter()
    vector_path = _repo_path(root, EXPERIMENT_ROOT / "vectors-v1")
    vectors, vector_progress = r.load_vector_index(
        "A",
        sorted(pages, key=lambda item: item.unit_id),
        vector_path,
        embedder,
    )
    vector_load_ms = r._timed_ms(vector_started)

    index_started = time.perf_counter()
    encoding = r._context_encoding()
    context = build_search_context(
        pages,
        records,
        page_map,
        source_order,
        relation_index,
        vectors,
        encoding,
    )
    index_build_ms = r._timed_ms(index_started)

    run_started = time.perf_counter()
    rows, run_timings = run_questions(
        questions,
        context,
        embedder,
        reranker,
        rerank_batch_size=8,
    )
    run_wall_ms = r._timed_ms(run_started)

    expected_rows = len(questions) * len(CONDITIONS)
    if len(rows) != expected_rows:
        raise RuntimeError(
            f"Expected {expected_rows} complete holdout rows, received {len(rows)}"
        )
    id_question_sha = r._hash_json(
        [{"id": q.question_id, "question": q.text} for q in questions]
    )
    manifest = {
        "format": "four-arm-evaluation-v1",
        "evaluation_kind": args.evaluation_kind,
        "created_utc": datetime.now(UTC).isoformat(),
        "policy_freeze": {
            "path": POLICY_FREEZE_PATH.as_posix(),
            "sha256": POLICY_FREEZE_SHA256,
            "format": freeze["format"],
        },
        "inputs": {
            "source_files": dict(freeze["source_files"]),
            "questions_file_sha256": question_file_sha,
            "id_question_sha256": id_question_sha,
            "questions_count": len(questions),
        },
        "conditions": list(CONDITIONS),
        "settings": {
            "candidate_limit": r.CANDIDATE_LIMIT,
            "max_sources": r.MAX_FINAL_SOURCES,
            "max_context_tokens": r.MAX_CONTEXT_TOKENS,
            "rrf_k": r.RRF_K,
            "sparse_top_k": r.RETRIEVER_TOP_K,
            "dense_top_k": r.RETRIEVER_TOP_K,
            "embedding_batch_size": 4,
            "rerank_batch_size": 8,
            "device": "cuda",
            "dtype": "float32",
            "threads": 4,
            "offline_only": True,
            "warmup": "No separate warmup; first question is executed once for E/F.",
        },
        "models": {
            "embedding": embedder.metadata(),
            "reranker": reranker.metadata(),
        },
        "timing_basis": TIMING_NOTE,
        "timings_ms": {
            "policy_and_source_hash_gate": round(freeze_check_ms, 3),
            "question_input_load": round(question_input_ms, 3),
            "frozen_data_load": round(data_load_ms, 3),
            "model_startup": round(model_load_ms, 3),
            "vector_index_load": round(vector_load_ms, 3),
            "index_startup": round(index_build_ms, 3),
            "query_execution_wall": round(run_wall_ms, 3),
            **run_timings,
        },
        "vector_progress": {
            "corpus": "A",
            "status": vector_progress.get("status"),
            "fingerprint": vector_progress.get("fingerprint"),
            "vectors_sha256": vector_progress.get("vectors_sha256"),
        },
        "summary": {
            "questions": len(questions),
            "results": len(rows),
            "conditions": list(CONDITIONS),
            "complete": True,
        },
    }
    return write_output_bundle(output, rows, manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run frozen E, E+coverage, F, and F+coverage on id/question-only "
            "evaluation input using local A-page indexes and models."
        )
    )
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--questions-sha256", required=True)
    parser.add_argument(
        "--evaluation-kind",
        choices=("existing_regression", "new_holdout"),
        default="existing_regression",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_HUB_CACHE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = _run(args)
    print(output / "results.jsonl")
    print(output / "run-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
