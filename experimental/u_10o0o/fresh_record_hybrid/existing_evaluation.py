"""Prepare and score the existing Advanced regression suites after retrieval."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from experimental.u_10o0o.context_evidence import _normalize

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = Path("experimental/u_10o0o/evaluation/advanced_scenarios")
INPUTS = (
    (
        "official/development",
        Path("data/advanced/evaluation/development_questions.json"),
        Path("data/advanced/evaluation/development.json"),
    ),
    (
        "official/additional",
        Path("data/advanced/evaluation/additional_questions.json"),
        Path("data/advanced/evaluation/additional.json"),
    ),
    (
        "categories/scenarios",
        SCENARIOS / "retrieval_questions.json",
        SCENARIOS / "retrieval_references.json",
    ),
    (
        "categories/work_rules",
        SCENARIOS / "work_rules/questions.json",
        SCENARIOS / "work_rules/references.json",
    ),
    (
        "categories/semantic_queries",
        SCENARIOS / "semantic_queries/questions.json",
        SCENARIOS / "semantic_queries/references.json",
    ),
    (
        "categories/negative_control",
        SCENARIOS / "negative_control_questions.json",
        SCENARIOS / "negative_control_references.json",
    ),
)
CONDITIONS = ("E", "E_coverage", "F", "F_coverage")
FIXED_PAIR_FIELDS = (
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
    "new_related_unit_ids",
)
FRESH_PAGES = Path(
    "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl"
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_question_file(path: Path) -> list[dict[str, str]]:
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise ValueError(f"Question source must be a JSON list: {path}")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Question row must be an object: {path}")
        question_id, question = row.get("id"), row.get("question")
        if (
            not isinstance(question_id, str)
            or not isinstance(question, str)
            or not question.strip()
        ):
            raise ValueError(f"Invalid id/question row in {path}")
        if question_id in seen:
            raise ValueError(f"Duplicate question ID in {path}: {question_id}")
        seen.add(question_id)
        result.append({"id": question_id, "question": question})
    return result


def prepare(output_dir: str | Path, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    output = Path(output_dir)
    if not output.is_absolute():
        output = root / output
    if output.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing evaluation inputs: {output}"
        )

    questions: list[dict[str, str]] = []
    source_manifest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for suite, question_relative, reference_relative in INPUTS:
        question_path = root / question_relative
        reference_path = root / reference_relative
        rows = _read_question_file(question_path)
        collisions = seen.intersection(row["id"] for row in rows)
        if collisions:
            raise ValueError(
                f"Question IDs collide across suites: {sorted(collisions)}"
            )
        seen.update(row["id"] for row in rows)
        # Hash only. Reference contents remain unopened until score() has passed
        # the complete four-arm result gate.
        source_manifest.append(
            {
                "suite": suite,
                "count": len(rows),
                "ids": [row["id"] for row in rows],
                "questions": {
                    "path": str(question_relative),
                    "sha256": sha256_file(question_path),
                },
                "references": {
                    "path": str(reference_relative),
                    "sha256": sha256_file(reference_path),
                },
            }
        )
        questions.extend(rows)

    if len(questions) != 280:
        raise ValueError(f"Expected 280 existing questions, found {len(questions)}")
    encoded = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in questions
    ).encode("utf-8")
    manifest = {
        "format": "existing-advanced-evaluation-v1",
        "purpose": "existing_regression",
        "question_count": len(questions),
        "source_files": source_manifest,
        "questions_sha256": hashlib.sha256(encoded).hexdigest(),
        "question_contract": "Each retrieval row contains only id and question.",
        "reference_access": "Reference files were hashed but not parsed during prepare.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (stage / "questions.jsonl").write_bytes(encoded)
        (stage / "dataset-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL {path}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(
                        f"JSONL row must be an object: {path}:{line_number}"
                    )
                rows.append(row)
    return rows


def _source_id(citation: Mapping[str, Any]) -> str:
    book_id, page = citation.get("book_id"), citation.get("page")
    if (
        not isinstance(book_id, str)
        or isinstance(page, bool)
        or not isinstance(page, int)
    ):
        raise ValueError("Evidence citation needs book_id and integer page")
    return f"{book_id}:{page:03d}"


def _compact(text: str) -> str:
    return _normalize(text)


def _bundle_covered(bundle: Any, source_texts: Mapping[str, str]) -> bool:
    if not isinstance(bundle, list) or not bundle:
        return False
    for citation in bundle:
        if not isinstance(citation, dict):
            return False
        source_id = _source_id(citation)
        quote = citation.get("quote")
        if not isinstance(quote, str) or not _compact(quote):
            return False
        if source_id not in source_texts or _compact(quote) not in _compact(
            source_texts[source_id]
        ):
            return False
    return True


def _requirements_coverage(
    requirements: Sequence[Mapping[str, Any]], source_texts: Mapping[str, str]
) -> tuple[list[bool], list[bool]]:
    full_hits: list[bool] = []
    primary_hits: list[bool] = []
    for requirement in requirements:
        primary = _bundle_covered(requirement.get("primary"), source_texts)
        alternatives = requirement.get("alternatives", [])
        if not isinstance(alternatives, list):
            alternatives = []
        full_hits.append(
            primary
            or any(_bundle_covered(bundle, source_texts) for bundle in alternatives)
        )
        primary_hits.append(primary)
    return full_hits, primary_hits


def _official_coverage(
    groups: Sequence[Sequence[Mapping[str, Any]]], source_ids: set[str]
) -> list[bool]:
    hits = []
    for group in groups:
        if not isinstance(group, list) or not group:
            raise ValueError("Official evidence_groups must contain nonempty OR groups")
        hits.append(any(_source_id(citation) in source_ids for citation in group))
    return hits


def _category(reference: Mapping[str, Any], suite: str) -> tuple[str, bool]:
    if suite.startswith("official/"):
        return suite, True
    if suite == "categories/scenarios":
        scenario = reference.get("scenario")
        if isinstance(scenario, int) and 1 <= scenario <= 7:
            return f"scenario/S{scenario:02}", True
        if scenario == 8:
            return f"negative/{reference.get('variant', 'unknown')}", False
        raise ValueError(f"Unknown scenario category for {reference.get('id')}")
    if suite == "categories/negative_control":
        return f"negative/{reference.get('variant', 'unknown')}", False
    if suite == "categories/work_rules":
        return f"work_rules/{reference.get('variant', 'unknown')}", True
    if suite == "categories/semantic_queries":
        return f"semantic/{reference.get('variant', 'unknown')}", True
    raise ValueError(f"Unknown suite: {suite}")


def _validate_model_metadata(
    models: Mapping[str, Any], frozen_policy: Mapping[str, Any]
) -> None:
    embedding = models.get("embedding", {})
    reranker = models.get("reranker", {})
    if (
        embedding.get("model_id") != frozen_policy.get("embedding_model")
        or embedding.get("revision") != frozen_policy.get("embedding_revision")
        or embedding.get("device") != "cuda"
        or embedding.get("dtype") != "torch.float32"
        or embedding.get("max_tokens") != 2048
        or embedding.get("normalize_embeddings") is not True
        or reranker.get("model_id") != frozen_policy.get("reranker_model")
        or reranker.get("revision") != frozen_policy.get("reranker_revision")
        or reranker.get("device") != "cuda"
        or reranker.get("dtype") != "torch.float32"
        or reranker.get("max_pair_tokens") != 2048
    ):
        raise ValueError("Retrieval model metadata does not match the frozen policy")


def _validate_complete_results(
    result_rows: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    expected_ids = {question["id"] for question in questions}
    expected = {
        (question_id, condition)
        for question_id in expected_ids
        for condition in CONDITIONS
    }
    keyed: dict[tuple[str, str], Mapping[str, Any]] = {}
    qhash = {
        question["id"]: hashlib.sha256(question["question"].encode("utf-8")).hexdigest()
        for question in questions
    }
    for row in result_rows:
        key = (row.get("id"), row.get("condition"))
        if key not in expected or key in keyed:
            raise ValueError(f"Unexpected or duplicate result row: {key}")
        if row.get("question_sha256") != qhash[key[0]]:
            raise ValueError(f"Question text hash mismatch: {key[0]}")
        keyed[key] = row
    if keyed.keys() != expected:
        raise ValueError(
            f"Four-arm results incomplete: expected {len(expected)}, got {len(keyed)}"
        )

    for question_id in expected_ids:
        for coverage, baseline in (("E_coverage", "E"), ("F_coverage", "F")):
            before, after = (
                keyed[(question_id, baseline)],
                keyed[(question_id, coverage)],
            )
            for field in FIXED_PAIR_FIELDS:
                if before.get(field) != after.get(field):
                    raise ValueError(f"{coverage} changed {field} for {question_id}")
    return keyed


def score_existing(
    results_path: str | Path,
    questions_path: str | Path,
    manifest_path: str | Path,
    pages_path: str | Path,
    output_dir: str | Path,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Score only after a complete, hash-matched four-arm result file exists."""
    output = Path(output_dir)
    if not output.is_absolute():
        output = root / output
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite scores: {output}")

    manifest = _load_json(Path(manifest_path))
    if (
        manifest.get("purpose") != "existing_regression"
        or manifest.get("question_count") != 280
    ):
        raise ValueError(
            "Dataset manifest is missing or has the wrong evaluation scope"
        )
    questions_path = Path(questions_path)
    if sha256_file(questions_path) != manifest.get("questions_sha256"):
        raise ValueError("Prepared question file hash mismatch")
    questions = _read_jsonl(questions_path)
    if len(questions) != manifest.get("question_count") or any(
        set(row) != {"id", "question"} for row in questions
    ):
        raise ValueError("Prepared question file violates its frozen contract")

    # This complete-row and question-hash gate happens before any reference is parsed.
    results = _read_jsonl(results_path)
    keyed = _validate_complete_results(results, questions)
    results_path = Path(results_path)
    pages_path = Path(pages_path)
    run_manifest_path = results_path.parent / "run-manifest.json"
    run_manifest = _load_json(run_manifest_path)
    if (
        run_manifest.get("format") != "four-arm-evaluation-v1"
        or run_manifest.get("evaluation_kind") != "existing_regression"
        or run_manifest.get("summary", {}).get("complete") is not True
        or run_manifest.get("summary", {}).get("questions") != len(questions)
        or run_manifest.get("summary", {}).get("results") != len(results)
        or run_manifest.get("inputs", {}).get("questions_file_sha256")
        != manifest.get("questions_sha256")
        or run_manifest.get("inputs", {}).get("questions_count") != len(questions)
        or run_manifest.get("results_rows") != len(results)
        or run_manifest.get("results_sha256") != sha256_file(results_path)
        or run_manifest.get("summary", {}).get("conditions") != list(CONDITIONS)
        or run_manifest.get("inputs", {}).get("id_question_sha256")
        != hashlib.sha256(
            json.dumps(
                [{"id": row["id"], "question": row["question"]} for row in questions],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    ):
        raise ValueError("Retrieval run manifest is missing or incomplete")

    expected_policy_path = Path(
        "experimental/u_10o0o/records/advanced-fresh-holdout-20261111/policy-freeze.json"
    )
    expected_policy_sha256 = (
        "f2ebb1b0d9f81e446a2c5e797dde6b142d83479eefcc57c591b68cc3cf10eb47"
    )
    policy_metadata = run_manifest.get("policy_freeze", {})
    if (
        policy_metadata.get("path") != expected_policy_path.as_posix()
        or policy_metadata.get("sha256") != expected_policy_sha256
        or sha256_file(root / expected_policy_path) != expected_policy_sha256
    ):
        raise ValueError("Retrieval run does not use the pinned frozen policy")
    frozen_policy = _load_json(root / expected_policy_path)
    settings = run_manifest.get("settings", {})
    if (
        frozen_policy.get("conditions") != list(CONDITIONS)
        or run_manifest.get("conditions") != list(CONDITIONS)
        or frozen_policy.get("candidate_limit") != 20
        or frozen_policy.get("max_sources") != 8
        or frozen_policy.get("max_context_tokens") != 8000
        or frozen_policy.get("rrf_k") != 60
        or frozen_policy.get("sparse_top_k") != 50
        or frozen_policy.get("dense_top_k") != 50
        or frozen_policy.get("embedding_batch_size") != 4
        or frozen_policy.get("rerank_batch_size") != 8
        or frozen_policy.get("dtype") != "float32"
        or settings.get("candidate_limit") != 20
        or settings.get("max_sources") != 8
        or settings.get("max_context_tokens") != 8000
        or settings.get("rrf_k") != 60
        or settings.get("sparse_top_k") != 50
        or settings.get("dense_top_k") != 50
        or settings.get("embedding_batch_size") != 4
        or settings.get("rerank_batch_size") != 8
        or settings.get("dtype") != "float32"
        or settings.get("device") != "cuda"
        or settings.get("threads") != 4
    ):
        raise ValueError(
            "Retrieval run settings do not match the frozen four-arm policy"
        )
    frozen_page_hash = frozen_policy.get("source_files", {}).get(FRESH_PAGES.as_posix())
    if (
        not frozen_page_hash
        or sha256_file(pages_path) != frozen_page_hash
        or run_manifest.get("inputs", {})
        .get("source_files", {})
        .get(FRESH_PAGES.as_posix())
        != frozen_page_hash
    ):
        raise ValueError("Scoring pages do not match the frozen raw-page input")

    source_files = manifest.get("source_files", [])
    if source_files:
        reference_files = [
            {
                "suite": source["suite"],
                "path": source["references"]["path"],
                "sha256": source["references"]["sha256"],
            }
            for source in source_files
        ]
    else:
        reference_files = manifest.get("reference_files", [])
    if len(reference_files) != len(INPUTS):
        raise ValueError("Dataset manifest has an invalid reference-file list")
    expected_sources = {
        suite: (questions_relative.as_posix(), references_relative.as_posix())
        for suite, questions_relative, references_relative in INPUTS
    }
    seen_suites: set[str] = set()
    for source in source_files:
        suite = source.get("suite")
        if suite not in expected_sources or suite in seen_suites:
            raise ValueError("Dataset manifest suite set does not match frozen inputs")
        seen_suites.add(suite)
        expected_q, expected_ref = expected_sources[suite]
        if (
            source.get("questions", {}).get("path") != expected_q
            or source.get("references", {}).get("path") != expected_ref
        ):
            raise ValueError(f"Dataset input path mismatch: {suite}")
        qpath = root / expected_q
        if sha256_file(qpath) != source["questions"]["sha256"]:
            raise ValueError(f"Question source hash mismatch: {expected_q}")
        source_questions = _read_question_file(qpath)
        if source.get("count") != len(source_questions) or source.get("ids") != [
            row["id"] for row in source_questions
        ]:
            raise ValueError(f"Question source manifest mismatch: {suite}")
    if seen_suites != set(expected_sources):
        raise ValueError("Dataset manifest is missing a frozen suite")
    references: dict[str, tuple[dict[str, Any], str]] = {}
    for source in reference_files:
        path = root / source["path"]
        if sha256_file(path) != source["sha256"]:
            raise ValueError(f"Reference source hash mismatch: {source['path']}")
        rows = _load_json(path)
        if not isinstance(rows, list):
            raise ValueError(f"Reference source must be a JSON list: {source['path']}")
        for reference in rows:
            question_id = reference.get("id")
            if question_id in references:
                raise ValueError(f"Duplicate reference ID: {question_id}")
            references[question_id] = (reference, source["suite"])
    if references.keys() != {row["id"] for row in questions}:
        missing = {row["id"] for row in questions} - references.keys()
        extra = references.keys() - {row["id"] for row in questions}
        raise ValueError(
            f"Reference/question mismatch: missing={len(missing)}, extra={len(extra)}"
        )

    page_rows = _read_jsonl(pages_path)
    page_texts = {row["source_id"]: row["text"] for row in page_rows}
    if len(page_texts) != len(page_rows):
        raise ValueError("Duplicate source page IDs")
    scores: list[dict[str, Any]] = []
    for question in questions:
        question_id = question["id"]
        reference, suite = references[question_id]
        category, evidence_expected = _category(reference, suite)
        official_groups = reference.get("evidence_groups", [])
        requirements = reference.get("evidence_requirements", [])
        if suite.startswith("official/"):
            scoreable = isinstance(official_groups, list) and bool(official_groups)
        elif evidence_expected:
            scoreable = isinstance(requirements, list) and bool(requirements)
        else:
            scoreable = False

        if suite.startswith("official/") and scoreable:
            for group in official_groups:
                if not isinstance(group, list) or not group:
                    raise ValueError(
                        f"Malformed official evidence group: {question_id}"
                    )
                for citation in group:
                    if _source_id(citation) not in page_texts:
                        raise ValueError(
                            f"Official reference page absent: {question_id}"
                        )
        elif scoreable:
            for requirement in requirements:
                for bundle in [
                    requirement.get("primary", []),
                    *requirement.get("alternatives", []),
                ]:
                    if not isinstance(bundle, list) or not bundle:
                        raise ValueError(
                            f"Malformed category evidence bundle: {question_id}"
                        )
                    for citation in bundle:
                        sid = _source_id(citation)
                        quote = citation.get("quote")
                        if (
                            sid not in page_texts
                            or not isinstance(quote, str)
                            or _compact(quote) not in _compact(page_texts[sid])
                        ):
                            raise ValueError(
                                f"Category reference quote/source absent: {question_id}"
                            )

        expected_primary_pages: set[str] = set()
        official_page_union: set[str] = set()
        if suite.startswith("official/") and scoreable:
            for group in official_groups:
                if not isinstance(group, list) or not group:
                    raise ValueError(
                        f"Malformed official evidence group: {question_id}"
                    )
                official_page_union.update(_source_id(citation) for citation in group)
        elif scoreable:
            for requirement in requirements:
                primary = requirement.get("primary", [])
                if not isinstance(primary, list) or not primary:
                    raise ValueError(f"Missing primary evidence bundle: {question_id}")
                expected_primary_pages.update(_source_id(item) for item in primary)

        arm_scores: dict[str, Any] = {}
        for condition in CONDITIONS:
            row = keyed[(question_id, condition)]
            if (
                len(row.get("candidate_unit_ids", [])) != 20
                or len(row.get("candidate_source_ids", [])) != 20
                or len(row.get("final_sources", [])) > 8
                or isinstance(row.get("context_tokens"), bool)
                or not isinstance(row.get("context_tokens"), int)
                or row["context_tokens"] > 8000
            ):
                raise ValueError(
                    f"Saved result violates retrieval budget: {question_id}/{condition}"
                )
            candidate_ids = row.get("candidate_source_ids")
            final_sources = row.get("final_sources")
            if not isinstance(candidate_ids, list) or len(candidate_ids) != len(
                set(candidate_ids)
            ):
                raise ValueError(
                    f"Invalid candidate source list: {question_id}/{condition}"
                )
            if any(source_id not in page_texts for source_id in candidate_ids):
                raise ValueError(
                    f"Candidate source absent from fresh pages: {question_id}/{condition}"
                )
            if not isinstance(final_sources, list):
                raise ValueError(f"Invalid final sources: {question_id}/{condition}")
            final_texts: dict[str, str] = {}
            for part in final_sources:
                source_id, text = part.get("source_id"), part.get("text")
                if source_id in final_texts or not isinstance(text, str):
                    raise ValueError(
                        f"Invalid/duplicate final source: {question_id}/{condition}"
                    )
                if source_id not in page_texts or not page_texts[source_id].startswith(
                    text
                ):
                    raise ValueError(
                        f"Final source is not a fresh page prefix: {question_id}/{condition}"
                    )
                final_texts[source_id] = text

            metrics: dict[str, Any] = {
                "candidate_pages": len(candidate_ids),
                "final_pages": len(final_texts),
                "context_tokens": row.get("context_tokens"),
                "timings_ms": row.get("timings_ms", {}),
                "candidate_complete": None,
                "final_complete": None,
                "candidate_fact_hits": None,
                "final_fact_hits": None,
                "candidate_fact_count": None,
                "final_fact_count": None,
                "candidate_fact_mask": None,
                "final_fact_mask": None,
                "candidate_primary_page_recall": None,
                "final_primary_page_recall": None,
                "candidate_primary_page_hits": None,
                "candidate_primary_page_count": None,
                "final_primary_page_hits": None,
                "final_primary_page_count": None,
                "candidate_evidence_group_recall": None,
                "final_evidence_group_recall": None,
                "candidate_referenced_page_recall": None,
                "candidate_referenced_page_hits": None,
                "candidate_referenced_page_count": None,
                "final_referenced_page_recall": None,
                "final_referenced_page_hits": None,
                "final_referenced_page_count": None,
            }
            if scoreable:
                if suite.startswith("official/"):
                    candidate_hits = _official_coverage(
                        official_groups, set(candidate_ids)
                    )
                    final_hits = _official_coverage(official_groups, set(final_texts))
                    metrics.update(
                        candidate_complete=all(candidate_hits),
                        final_complete=all(final_hits),
                        candidate_fact_hits=sum(candidate_hits),
                        final_fact_hits=sum(final_hits),
                        candidate_fact_count=len(official_groups),
                        final_fact_count=len(official_groups),
                        candidate_fact_mask=candidate_hits,
                        final_fact_mask=final_hits,
                        candidate_primary_page_hits=None,
                        candidate_primary_page_count=None,
                        final_primary_page_hits=None,
                        final_primary_page_count=None,
                        candidate_evidence_group_recall=(
                            sum(candidate_hits) / len(official_groups)
                        ),
                        final_evidence_group_recall=(
                            sum(final_hits) / len(official_groups)
                        ),
                        candidate_referenced_page_recall=(
                            len(official_page_union.intersection(candidate_ids))
                            / len(official_page_union)
                            if official_page_union
                            else None
                        ),
                        candidate_referenced_page_hits=len(
                            official_page_union.intersection(candidate_ids)
                        ),
                        candidate_referenced_page_count=len(official_page_union),
                        final_referenced_page_recall=(
                            len(official_page_union.intersection(final_texts))
                            / len(official_page_union)
                            if official_page_union
                            else None
                        ),
                        final_referenced_page_hits=len(
                            official_page_union.intersection(final_texts)
                        ),
                        final_referenced_page_count=len(official_page_union),
                    )
                else:
                    candidate_page_texts = {
                        source_id: page_texts[source_id] for source_id in candidate_ids
                    }
                    candidate_hits, candidate_primary = _requirements_coverage(
                        requirements, candidate_page_texts
                    )
                    final_hits, final_primary = _requirements_coverage(
                        requirements, final_texts
                    )
                    canonical = expected_primary_pages
                    candidate_primary_pages = canonical.intersection(candidate_ids)
                    final_primary_pages = canonical.intersection(final_texts)
                    metrics.update(
                        candidate_complete=bool(candidate_hits) and all(candidate_hits),
                        final_complete=bool(final_hits) and all(final_hits),
                        candidate_fact_hits=sum(candidate_hits),
                        final_fact_hits=sum(final_hits),
                        candidate_fact_count=len(requirements),
                        final_fact_count=len(requirements),
                        candidate_fact_mask=candidate_hits,
                        final_fact_mask=final_hits,
                        candidate_primary_bundle_hits=sum(candidate_primary),
                        final_primary_bundle_hits=sum(final_primary),
                        candidate_primary_page_hits=len(candidate_primary_pages),
                        candidate_primary_page_count=len(canonical),
                        final_primary_page_hits=len(final_primary_pages),
                        final_primary_page_count=len(canonical),
                        candidate_primary_page_recall=(
                            len(candidate_primary_pages) / len(canonical)
                            if canonical
                            else None
                        ),
                        final_primary_page_recall=(
                            len(final_primary_pages) / len(canonical)
                            if canonical
                            else None
                        ),
                        candidate_evidence_group_recall=(
                            sum(candidate_hits) / len(requirements)
                            if requirements
                            else None
                        ),
                        final_evidence_group_recall=(
                            sum(final_hits) / len(requirements)
                            if requirements
                            else None
                        ),
                    )
            arm_scores[condition] = metrics

        scores.append(
            {
                "id": question_id,
                "suite": suite,
                "category": category,
                "scoreable": scoreable,
                "negative_control": category.startswith("negative/"),
                "arms": arm_scores,
            }
        )

    summary = _summarize(scores)
    pairs = _pair_changes(scores)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (stage / "scores.json").write_text(
            json.dumps(scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "changes.json").write_text(
            json.dumps(pairs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        stamp = {
            "format": "existing-advanced-score-v1",
            "question_sha256": manifest["questions_sha256"],
            "results_sha256": sha256_file(results_path),
            "pages_sha256": sha256_file(pages_path),
            "reference_files": reference_files,
            "question_count": len(questions),
            "result_rows": len(results),
            "conditions": list(CONDITIONS),
            "summary_groups": sorted(summary),
            "external_api_calls": 0,
        }
        (stage / "score-manifest.json").write_text(
            json.dumps(stamp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return summary


def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    groups["all_280"] = list(rows)
    groups["official_60"] = [
        row for row in rows if row["suite"].startswith("official/")
    ]
    groups["categories_220"] = [
        row for row in rows if row["suite"].startswith("categories/")
    ]
    groups["categories_160"] = [
        row
        for row in rows
        if row["suite"] in {"categories/scenarios", "categories/negative_control"}
    ]
    groups["categories_180"] = [
        row
        for row in rows
        if row["suite"]
        in {
            "categories/scenarios",
            "categories/negative_control",
            "categories/work_rules",
        }
    ]
    groups["official_scoreable"] = [
        row for row in rows if row["suite"].startswith("official/") and row["scoreable"]
    ]
    groups["category_positive_200"] = [
        row
        for row in rows
        if row["suite"].startswith("categories/") and row["scoreable"]
    ]
    groups["negative_controls_20"] = [row for row in rows if row["negative_control"]]
    for category in sorted({row["category"] for row in rows}):
        groups[category] = [row for row in rows if row["category"] == category]

    summaries = {}
    for name, selected in groups.items():
        arm_data = {}
        for arm in CONDITIONS:
            scored = [row["arms"][arm] for row in selected if row["scoreable"]]
            all_arms = [row["arms"][arm] for row in selected]
            arm_data[arm] = {
                "questions": len(selected),
                "scoreable_questions": len(scored),
                "candidate_complete": sum(
                    bool(row["candidate_complete"]) for row in scored
                ),
                "final_complete": sum(bool(row["final_complete"]) for row in scored),
                "candidate_fact_hits": sum(
                    row["candidate_fact_hits"] or 0 for row in scored
                ),
                "final_fact_hits": sum(row["final_fact_hits"] or 0 for row in scored),
                "candidate_fact_count": sum(
                    row["candidate_fact_count"] or 0 for row in scored
                ),
                "final_fact_count": sum(row["final_fact_count"] or 0 for row in scored),
                "candidate_primary_page_hits": sum(
                    row["candidate_primary_page_hits"] or 0 for row in scored
                ),
                "candidate_primary_page_count": sum(
                    row["candidate_primary_page_count"] or 0 for row in scored
                ),
                "final_primary_page_hits": sum(
                    row["final_primary_page_hits"] or 0 for row in scored
                ),
                "final_primary_page_count": sum(
                    row["final_primary_page_count"] or 0 for row in scored
                ),
                "candidate_primary_page_recall_micro": (
                    sum(row["candidate_primary_page_hits"] or 0 for row in scored)
                    / sum(row["candidate_primary_page_count"] or 0 for row in scored)
                    if sum(row["candidate_primary_page_count"] or 0 for row in scored)
                    else None
                ),
                "final_primary_page_recall_micro": (
                    sum(row["final_primary_page_hits"] or 0 for row in scored)
                    / sum(row["final_primary_page_count"] or 0 for row in scored)
                    if sum(row["final_primary_page_count"] or 0 for row in scored)
                    else None
                ),
                "candidate_evidence_group_hits": sum(
                    row["candidate_fact_hits"] or 0 for row in scored
                ),
                "candidate_evidence_group_count": sum(
                    row["candidate_fact_count"] or 0 for row in scored
                ),
                "final_evidence_group_hits": sum(
                    row["final_fact_hits"] or 0 for row in scored
                ),
                "final_evidence_group_count": sum(
                    row["final_fact_count"] or 0 for row in scored
                ),
                "candidate_referenced_page_hits": sum(
                    row["candidate_referenced_page_hits"] or 0 for row in scored
                ),
                "candidate_referenced_page_count": sum(
                    row["candidate_referenced_page_count"] or 0 for row in scored
                ),
                "final_referenced_page_hits": sum(
                    row["final_referenced_page_hits"] or 0 for row in scored
                ),
                "final_referenced_page_count": sum(
                    row["final_referenced_page_count"] or 0 for row in scored
                ),
                "mean_context_tokens": (
                    sum(row["context_tokens"] or 0 for row in all_arms) / len(all_arms)
                    if all_arms
                    else None
                ),
                "timing_ms_p50": {},
                "timing_ms_p95": {},
            }
            timing_names = sorted(
                {key for row in all_arms for key in row["timings_ms"]}
            )
            for key in timing_names:
                values = [
                    row["timings_ms"][key]
                    for row in all_arms
                    if key in row["timings_ms"]
                ]
                arm_data[arm]["timing_ms_p50"][key] = _percentile(values, 50)
                arm_data[arm]["timing_ms_p95"][key] = _percentile(values, 95)
        summaries[name] = {
            "questions": len(selected),
            "scoreable_questions": sum(row["scoreable"] for row in selected),
            "arms": arm_data,
        }
    return summaries


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(float(value) for value in values)
    position = (len(values) - 1) * percentile / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _pair_changes(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    changes: dict[str, list[dict[str, Any]]] = {}
    for baseline, updated in (("E", "E_coverage"), ("F", "F_coverage")):
        pair: list[dict[str, Any]] = []
        for row in rows:
            if not row["scoreable"]:
                continue
            before, after = row["arms"][baseline], row["arms"][updated]
            pair.append(
                {
                    "id": row["id"],
                    "suite": row["suite"],
                    "category": row["category"],
                    "candidate_complete_before": before["candidate_complete"],
                    "candidate_complete_after": after["candidate_complete"],
                    "final_complete_before": before["final_complete"],
                    "final_complete_after": after["final_complete"],
                    "final_facts_before": before["final_fact_hits"],
                    "final_facts_after": after["final_fact_hits"],
                    "final_fact_count": before["final_fact_count"],
                    "gained_fact_indices": [
                        index + 1
                        for index, (left, right) in enumerate(
                            zip(
                                before["final_fact_mask"],
                                after["final_fact_mask"],
                                strict=True,
                            )
                        )
                        if right and not left
                    ],
                    "lost_fact_indices": [
                        index + 1
                        for index, (left, right) in enumerate(
                            zip(
                                before["final_fact_mask"],
                                after["final_fact_mask"],
                                strict=True,
                            )
                        )
                        if left and not right
                    ],
                }
            )
        changes[f"{baseline}_to_{updated}"] = pair
    return changes


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser(
        "prepare", help="Freeze question-only input and file hashes."
    )
    prepare_parser.add_argument("--output-dir", required=True, type=Path)
    score_parser = sub.add_parser(
        "score", help="Score only after all four arms are saved."
    )
    score_parser.add_argument("--results", required=True, type=Path)
    score_parser.add_argument("--questions", required=True, type=Path)
    score_parser.add_argument("--manifest", required=True, type=Path)
    score_parser.add_argument("--pages", type=Path, default=PROJECT_ROOT / FRESH_PAGES)
    score_parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output_dir)
    else:
        result = score_existing(
            args.results, args.questions, args.manifest, args.pages, args.output_dir
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
