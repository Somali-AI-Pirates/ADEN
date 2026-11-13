"""Run and grade saved TableRAG requests without regenerating their answers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.rag.config import resolve_project_path
from typing import Any, Protocol

from app.evaluation.dataset import load_questions, load_references
from app.evaluation.checkpoints import (
    assert_inputs_unchanged, atomic_json, digest_json, evaluator_files, evaluation_lock,
    fingerprint_files, load_grade_checkpoint, runtime_files, save_grade_checkpoint,
    validate_run_prefix,
)
from app.evaluation.table_rag_metrics import table_rag_metrics
from app.evaluation.graders import (
    CRITERIA,
    JUDGE_SCHEMA_VERSION,
    PROMPTS_DIR,
    FaithfulnessFn,
    JudgeFn,
    grade_faithfulness,
    make_openai_judge,
    make_ragas_faithfulness,
)
from app.evaluation.publishing import publish_evaluation_scores
from app.evaluation.reference_context import attach_reference_pages
from app.evaluation.report import write_artifacts, write_report
from app.evaluation.runner import grade_all
from app.evaluation.metrics import evidence_diagnostics, retrieval_metrics, score
from app.evaluation.scripts._common import base_manifest, load_env, now_id, sha256_file
from app.observability.records import compute_context_hash, summarize_usage
from shared.settings import connection_settings

RETRIEVAL_MODE = "table_rag"
DEFAULT_ARTIFACTS_DIRECTORY = "artifacts/experiments/table-rag"
DEFAULT_LANGFUSE_BASE_URL = "https://cloud.langfuse.com"


class EvaluationService(Protocol):
    manifest: Mapping[str, Any]

    async def ainvoke(self, question: str, *, request_id: str) -> Any: ...

    async def aclose(self) -> None: ...


def _json_default(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _usage_record(
    raw_usage: Mapping[str, Any] | None, attempts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    usage = summarize_usage(attempts)
    if isinstance(raw_usage, Mapping):
        if not attempts:
            for key in ("input_tokens", "output_tokens"):
                value = raw_usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key] = value
            if raw_usage.get("status") in {
                "complete", "partial", "unknown", "no_attempts"
            }:
                usage["status"] = raw_usage["status"]
        for key in ("logical_calls", "provider_attempts", "missing_usage"):
            value = raw_usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
    return usage


def _source_fields(source_id: str) -> dict[str, Any]:
    book_id, separator, page_part = source_id.rpartition(":p")
    if not separator or not book_id or not page_part.isdigit():
        raise ValueError(f"Invalid source ID: {source_id!r}")
    return {"source_id": source_id, "book_id": book_id, "page": int(page_part)}


def _failure_record(
    *,
    experiment_id: str,
    question_id: str,
    question: str,
    request_id: str,
    dataset_id: str,
    config_hash: str,
    error: BaseException,
    latency_ms: int,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    usage = metadata.get("usage")
    usage_attempts = usage.get("attempts") if isinstance(usage, Mapping) else None
    attempts = list(metadata.get("attempts") or usage_attempts or [])
    usage = _usage_record(usage, attempts)

    return {
        "experiment_id": experiment_id,
        "question_id": question_id,
        "request_id": request_id,
        "dataset_id": dataset_id,
        "retrieval_mode": RETRIEVAL_MODE,
        "config_hash": config_hash,
        "question": question,
        "retrieved_candidates": metadata.get("retrieved_candidates"),
        "retrieval_observations": metadata.get("retrieval_observations"),
        "final_context_chunks": metadata.get("final_context_chunks"),
        "final_context_hash": metadata.get("final_context_hash"),
        "model_context_text": metadata.get("model_context_text"),
        "model_context_hash": metadata.get("model_context_hash"),
        "status": "error",
        "answer": None,
        "reason": None,
        "citations": [],
        "latency_ms": latency_ms,
        "attempts": attempts,
        "usage": dict(usage),
        "trace_id": metadata.get("trace_id"),
        "langfuse_trace_id": metadata.get("langfuse_trace_id")
        or metadata.get("trace_id"),
        "sql_results": list(metadata.get("sql_results") or []),
        "included_sql_results": list(metadata.get("included_sql_results") or []),
        "diagnostics": dict(metadata.get("diagnostics") or {}),
        "error_type": type(error).__name__,
    }


def _evaluation_record(
    result: Any,
    *,
    experiment_id: str,
    question: Mapping[str, str],
    request_id: str,
    dataset_id: str,
    config_hash: str,
    latency_ms: int,
) -> dict[str, Any]:
    to_record = getattr(result, "to_record", None)
    if callable(to_record):
        raw = to_record()
    elif isinstance(result, Mapping):
        raw = dict(result)
    else:
        raise TypeError("RAG result must expose to_record() or be a mapping")

    if not isinstance(raw, Mapping):
        raise TypeError("RAG result to_record() must return a mapping")
    record: dict[str, Any] = dict(raw)
    record.update(
        experiment_id=experiment_id,
        question_id=question["id"],
        request_id=request_id,
        dataset_id=dataset_id,
        retrieval_mode=RETRIEVAL_MODE,
        config_hash=config_hash,
        question=question["question"],
    )
    record.setdefault("status", "error")
    record.setdefault("answer", None)
    record.setdefault("reason", None)
    record.setdefault("citations", [])
    record.setdefault("latency_ms", latency_ms)
    record.setdefault("attempts", [])
    usage = record.get("usage")
    usage_attempts = usage.get("attempts") if isinstance(usage, Mapping) else None
    if not record["attempts"] and isinstance(usage_attempts, list):
        record["attempts"] = list(usage_attempts)
    else:
        record["attempts"] = list(record["attempts"])
    record.setdefault("langfuse_trace_id", record.get("trace_id"))
    record.setdefault("error_type", None)
    record.setdefault("model_context_text", record.get("context_text"))
    record.setdefault("model_context_hash", record.get("context_hash"))

    candidates = record.get("retrieved_candidates")
    if candidates is None:
        candidate_ids = record.get("candidate_source_ids")
        if candidate_ids is not None:
            candidates = []
            for source_id in candidate_ids:
                candidates.append(
                    {
                        "chunk_id": source_id,
                        "text": "",
                        "file_name": "",
                        **_source_fields(source_id),
                    }
                )
    if candidates is None and record["status"] != "error":
        candidates = []
    if candidates is not None:
        candidates = list(candidates)
    record["retrieved_candidates"] = candidates

    final_chunks = record.get("final_context_chunks")
    if final_chunks is None and record["status"] == "error":
        record["final_context_hash"] = None
    else:
        final_chunks = list(final_chunks or [])
        record["final_context_chunks"] = final_chunks
        expected_hash = compute_context_hash(final_chunks)
        recorded_hash = record.get("final_context_hash")
        if recorded_hash is not None and recorded_hash != expected_hash:
            raise ValueError(
                "RAG result final_context_hash does not match final_context_chunks"
            )
        record["final_context_hash"] = expected_hash
    if record["model_context_text"] is not None:
        expected_model_hash = hashlib.sha256(
            record["model_context_text"].encode("utf-8")
        ).hexdigest()
        recorded_model_hash = record.get("model_context_hash")
        if (
            recorded_model_hash is not None
            and recorded_model_hash != expected_model_hash
        ):
            raise ValueError(
                "RAG result model_context_hash does not match model_context_text"
            )
        record["model_context_hash"] = expected_model_hash
    record.setdefault("final_context_hash", None)
    record.setdefault("sql_results", [])
    record.setdefault("diagnostics", {})
    record["usage"] = _usage_record(record.get("usage"), record["attempts"])
    return record


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"{path}:{line_number}: incomplete saved row; recover the saved request before resuming") from None
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: expected a JSON object")
                rows.append(row)
    return rows


async def run_questions(
    questions: Sequence[Mapping[str, str]],
    service: Any,
    *,
    experiment_id: str,
    dataset_id: str,
    config_hash: str,
    output_path: Path,
    resume: bool = False,
    input_guard=None,
    stop_after: int | None = None,
) -> list[dict[str, Any]]:
    """Run only on question text and append each result before the next request."""
    if stop_after is not None and stop_after < 1:
        raise ValueError("stop_after must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = read_jsonl(output_path) if resume and output_path.exists() else []
    validate_run_prefix(records, questions, experiment_id=experiment_id, config_hash=config_hash)
    # A complete last JSON object may survive a crash without its newline.
    # Repair only that separator after parsing every saved row successfully.
    if resume and output_path.exists() and output_path.stat().st_size:
        with output_path.open("rb") as existing:
            existing.seek(-1, os.SEEK_END)
            needs_separator = existing.read(1) != b"\n"
        if needs_separator:
            with output_path.open("ab") as existing:
                existing.write(b"\n")
                existing.flush()
                os.fsync(existing.fileno())
    pending_path = output_path.with_name("generation-inflight.json")
    if pending_path.exists():
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        completed = any(row["request_id"] == pending.get("request_id") for row in records)
        if not completed:
            raise ValueError(
                "An interrupted generation has an uncertain outcome. Inspect the saved "
                "request identified in generation-inflight.json before retrying; "
                "automatic replay could charge for the same question twice."
            )
        pending_path.unlink()
    mode = "a" if resume and output_path.exists() else "x"
    with output_path.open(mode, encoding="utf-8") as stream:
        remaining = questions[len(records):]
        if stop_after is not None:
            remaining = remaining[:stop_after]
        for question in remaining:
            if input_guard is not None:
                input_guard()
            request_id = uuid.uuid4().hex
            atomic_json(pending_path, {
                "question_id": question["id"], "request_id": request_id,
                "experiment_id": experiment_id, "config_hash": config_hash,
                "state": "outcome_uncertain_until_saved",
            })
            started = time.perf_counter()
            try:
                result = await service.ainvoke(
                    question["question"],
                    request_id=request_id,
                )
                latency_ms = round((time.perf_counter() - started) * 1000)
                record = _evaluation_record(
                    result,
                    experiment_id=experiment_id,
                    question=question,
                    request_id=request_id,
                    dataset_id=dataset_id,
                    config_hash=config_hash,
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                record = _failure_record(
                    experiment_id=experiment_id,
                    question_id=question["id"],
                    question=question["question"],
                    request_id=request_id,
                    dataset_id=dataset_id,
                    config_hash=config_hash,
                    error=exc,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    metadata=getattr(exc, "rag_metadata", None),
                )
            if input_guard is not None:
                input_guard()
            stream.write(
                json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
            records.append(record)
            pending_path.unlink()
            print(f"Saved {len(records)}/{len(questions)}: {question['id']} ({record['status']})", flush=True)
    return records


def grade_saved_runs(
    records: Sequence[Mapping[str, Any]],
    references: Mapping[str, Mapping[str, Any]] | None,
    *,
    k: int = 8,
    judge_fn: JudgeFn | None = None,
    faithfulness_fn: FaithfulnessFn | None = None,
    diagnose_fn: JudgeFn | None = None,
) -> list[dict[str, Any]]:
    if references is not None:
        graded = grade_all(
            records,
            references,
            k=k,
            judge_fn=judge_fn,
            faithfulness_fn=faithfulness_fn,
            diagnose_fn=diagnose_fn,
        )
        for record, result in zip(records, graded, strict=True):
            result["table_rag_metrics"] = table_rag_metrics(
                references[record["question_id"]], record, k=k
            )
        return graded

    if judge_fn is not None:
        raise ValueError("Judge 평가에는 참고 답안이 필요합니다")
    unavailable = score(None, "unavailable", "참고 답안이 없어 Judge를 실행하지 않음")
    graded = []
    for record in records:
        faithfulness, faithfulness_usage = grade_faithfulness(
            record, has_claims=None, faithfulness_fn=faithfulness_fn
        )
        details = faithfulness_usage.pop("_details", {}) if faithfulness_usage else {}
        no_reference: dict[str, Any] = {}
        graded.append(
            {
                "experiment_id": record["experiment_id"],
                "question_id": record["question_id"],
                "request_id": record["request_id"],
                "retrieval_mode": record["retrieval_mode"],
                "status": record["status"],
                "retrieval": retrieval_metrics(
                    no_reference, record.get("final_context_chunks"), k
                ),
                "evidence_diagnostics": evidence_diagnostics(no_reference, record, k),
                "faithfulness": faithfulness,
                "judge": {criterion: unavailable for criterion in CRITERIA},
                "has_factual_claims": None,
                "grading_usage": {"judge": None, "faithfulness": faithfulness_usage},
                "table_rag_metrics": table_rag_metrics(no_reference, record, k=k),
                "reference_status": "not_provided",
                "evaluation_details": {"faithfulness": details},
            }
        )
    return graded


def _run_command(args: Any) -> None:
    if getattr(args, "resume", False) and not args.experiment_id:
        raise SystemExit("--resume requires the original --experiment-id")
    args.experiment_id = args.experiment_id or now_id("table-rag")
    with evaluation_lock(resolve_project_path(args.artifacts_dir), args.experiment_id):
        _run_command_locked(args)


def _run_command_locked(args: Any) -> None:
    from app.rag.config import load_table_rag_settings
    from app.rag.bootstrap import load_service

    settings = load_table_rag_settings(data_directory=args.data_directory, config_path=getattr(args, "config", None))
    langfuse_base_url = _validated_langfuse_base_url(
        settings.credentials.langfuse_base_url
    )
    experiment_id = args.experiment_id or now_id("table-rag")
    dataset_id = "advanced"
    questions_path = resolve_project_path(args.questions)
    question_ids = (
        [value.strip() for value in args.ids.split(",")] if args.ids else None
    )
    questions = load_questions(questions_path, question_ids)
    data_directory = settings.data_directory
    corpus_manifest_path = data_directory / "corpus.json"
    config_hash = sha256_file(corpus_manifest_path)
    artifact_root = resolve_project_path(args.artifacts_dir)
    if Path(experiment_id).name != experiment_id or experiment_id in {"", ".", ".."}:
        raise SystemExit("Experiment ID must be a directory name")
    artifact_dir = (artifact_root / experiment_id).resolve()
    if artifact_dir.parent != artifact_root:
        raise SystemExit("Experiment output must be inside the artifacts directory")
    resume = getattr(args, "resume", False)
    if resume and not args.experiment_id:
        raise SystemExit("--resume requires the original --experiment-id")
    previous = None
    if resume:
        previous_path = artifact_dir / "manifest.json"
        if not previous_path.is_file():
            raise SystemExit("The run to resume has no manifest")
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    else:
        artifact_dir.mkdir(parents=True, exist_ok=False)

    manifest = base_manifest(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        config_hash=config_hash,
        sample=False,
    )
    manifest["table_rag"] = {
        "data_directory": str(data_directory),
        # The endpoint is safe configuration metadata, not a credential. Keep
        # it with the run so later grading targets the same Langfuse endpoint.
        "langfuse_base_url": langfuse_base_url,
        "corpus_manifest_sha256": config_hash,
        "questions_file": questions_path.name,
        "questions_sha256": sha256_file(questions_path),
        "question_ids": [question["id"] for question in questions],
        "run_status": "running",
    }
    public_settings = settings.public_manifest()
    frozen_inputs = fingerprint_files([*runtime_files(), questions_path, corpus_manifest_path])
    if previous is not None:
        old = previous.get("table_rag") or {}
        if (old.get("questions_sha256") != manifest["table_rag"]["questions_sha256"]
                or old.get("question_ids") != manifest["table_rag"]["question_ids"]
                or old.get("runtime_settings") != public_settings
                or old.get("frozen_inputs") != frozen_inputs):
            raise SystemExit("Run inputs changed; use a new experiment ID")
        manifest = previous
    manifest["table_rag"]["frozen_inputs"] = frozen_inputs
    manifest["table_rag"]["runtime_settings"] = public_settings
    manifest["table_rag"]["run_status"] = "running"
    atomic_json(artifact_dir / "manifest.json", manifest)

    def input_guard():
        assert_inputs_unchanged(frozen_inputs)
        current = load_table_rag_settings(
            data_directory=args.data_directory, config_path=getattr(args, "config", None)
        )
        if current.public_manifest() != public_settings:
            raise ValueError("Runtime settings changed during the frozen benchmark")

    async def run() -> None:
        service = None
        try:
            service = await load_service(settings=settings)
            service_manifest = dict(getattr(service, "manifest", {}))
            if previous is not None and manifest.get("service") != service_manifest:
                raise ValueError("Service/data/model fingerprint changed; start a new run")
            manifest["service"] = service_manifest
            run_config_hash = getattr(service, "config_hash", config_hash)
            manifest["config_hash"] = run_config_hash
            atomic_json(artifact_dir / "manifest.json", manifest)
            records = await run_questions(
                questions,
                service,
                experiment_id=experiment_id,
                dataset_id=dataset_id,
                config_hash=run_config_hash,
                output_path=artifact_dir / "runs.jsonl",
                resume=resume,
                input_guard=input_guard,
                stop_after=getattr(args, "stop_after", None),
            )
            manifest["table_rag"]["run_status"] = (
                "complete" if len(records) == len(questions) else "partial"
            )
            manifest["table_rag"]["run_count"] = len(records)
            manifest["table_rag"]["error_count"] = sum(
                record["status"] == "error" for record in records
            )
        except Exception as exc:
            manifest["table_rag"]["run_status"] = "error"
            manifest["table_rag"]["run_error_type"] = type(exc).__name__
            raise
        finally:
            try:
                if service is not None:
                    await service.aclose()
            finally:
                atomic_json(artifact_dir / "manifest.json", manifest)

    asyncio.run(run())
    print(f"Question-only run saved to {artifact_dir}")


def _grade_command(args: Any) -> None:
    if getattr(args, "resume", False) and not args.grade_id:
        raise SystemExit("--resume requires the original --grade-id")
    args.grade_id = args.grade_id or now_id("grade")
    with evaluation_lock(resolve_project_path(args.run_directory), "grade:" + args.grade_id):
        _grade_command_locked(args)


def _grade_command_locked(args: Any) -> None:
    artifact_dir = resolve_project_path(args.run_directory)
    manifest_path = artifact_dir / "manifest.json"
    runs_path = artifact_dir / "runs.jsonl"
    if not manifest_path.is_file() or not runs_path.is_file():
        raise SystemExit("--run-directory must contain manifest.json and runs.jsonl")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = read_jsonl(runs_path)

    run_metadata = manifest.get("table_rag") or manifest.get("table_rag_a") or {}
    expected_ids = run_metadata.get(
        "question_ids", [record["question_id"] for record in records]
    )
    if (
        run_metadata.get("run_status") != "complete"
        or [record["question_id"] for record in records] != expected_ids
    ):
        raise SystemExit("Finish saving every selected question before grading")
    references_path = resolve_project_path(args.references) if args.references else None
    references = load_references(references_path) if references_path else None
    question_ids = {record["question_id"] for record in records}
    if args.reference_id and references is None:
        raise SystemExit("--reference-id requires --references")
    if args.reference_id:
        if len(records) != 1:
            raise SystemExit("--reference-id only applies to one saved request")
        if args.reference_id not in references:
            raise SystemExit(f"Reference question ID not found: {args.reference_id}")
        references = {records[0]["question_id"]: references[args.reference_id]}
    elif references is not None:
        missing = sorted(question_ids - set(references))
        if missing:
            raise SystemExit(
                f"References are missing question IDs: {missing}. "
                "Use --reference-id to map one saved request to a dataset question."
            )
    if references is None and args.judge_model:
        raise SystemExit("Judge needs references. Pass --references or omit --judge-model.")

    derived_facts_path = None
    derived_facts_sha256 = None
    if getattr(args, "derived_facts", None):
        if references is None:
            raise SystemExit("--derived-facts requires --references")
        from app.evaluation.reference_facts import attach_reviewed_facts
        derived_facts_path = resolve_project_path(args.derived_facts)
        derived_facts_sha256 = attach_reviewed_facts(references, derived_facts_path, references_path)

    reference_pages_sha256 = None
    if args.judge_model and references is not None:
        from app.rag.config import DEFAULT_DATA_DIRECTORY

        references = {qid: references[qid] for qid in question_ids}
        service = manifest.get("service") or {}
        bundle_directory = (
            args.data_directory
            or run_metadata.get("data_directory")
            or (service.get("runtime_settings") or {}).get("data_directory")
            or DEFAULT_DATA_DIRECTORY
        )
        try:
            reference_pages_sha256 = attach_reference_pages(
                references,
                resolve_project_path(bundle_directory),
                snapshot_sha256=(
                    service.get("snapshot_sha256")
                    or run_metadata.get("corpus_manifest_sha256")
                ),
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error

    publication_settings = (
        _resolve_publication_settings(manifest, config_path=args.config)
        if args.publish_langfuse_scores
        else None
    )

    grade_id = args.grade_id or now_id("grade")
    if Path(grade_id).name != grade_id or grade_id in {"", ".", ".."}:
        raise SystemExit("Grade ID must be a directory name")
    grade_dir = (artifact_dir / "grades" / grade_id).resolve()
    if grade_dir.parent != artifact_dir / "grades":
        raise SystemExit("Grade output must be inside the run's grades directory")
    from app.evaluation.model_runtime import EvaluationPolicy, EvaluationSession, load_evaluation_policy

    if args.judge_model or args.ragas_model:
        load_env()
        policy = load_evaluation_policy(args.config)
    else:
        policy = EvaluationPolicy()
    manifest["grade_id"] = grade_id
    manifest["runs_sha256"] = sha256_file(runs_path)
    k = args.k
    profile = getattr(args, "faithfulness_profile", "advanced")
    inputs = [*evaluator_files(), runs_path, manifest_path]
    if references_path:
        inputs.append(references_path)
    if derived_facts_path:
        inputs.append(derived_facts_path)
    frozen_inputs = fingerprint_files(inputs)
    manifest["benchmark"] = {
        "k": k,
        "modes": [RETRIEVAL_MODE],
        "question_ids": [record["question_id"] for record in records],
        "selection_reason": "Saved TableRAG request grading",
        "judge": args.judge_model,
        "faithfulness": args.ragas_model,
        "judge_prompt": sha256_file(PROMPTS_DIR / "judge.txt") if references else None,
        "judge_schema": JUDGE_SCHEMA_VERSION if references else None,
        "references_file": references_path.name if references_path else None,
        "references_sha256": sha256_file(references_path) if references_path else None,
        "reference_id": args.reference_id,
        "reference_status": "provided" if references else "not_provided",
        "derived_facts_sha256": derived_facts_sha256,
        "evaluation_policy": policy.to_mapping(),
        "faithfulness_profile": profile,
        "diagnose_failures": bool(getattr(args, "diagnose_failures", False)),
        "frozen_inputs": frozen_inputs,
        "evaluator_inputs_sha256": digest_json(frozen_inputs),
        "evaluator_code_sha256": digest_json(fingerprint_files(evaluator_files())),
    }
    if reference_pages_sha256 is not None:
        manifest["benchmark"]["reference_pages_sha256"] = reference_pages_sha256
    conditions_hash = digest_json({
        "benchmark": manifest["benchmark"], "runs_sha256": manifest["runs_sha256"]
    })
    previous_grade = None
    if getattr(args, "resume", False):
        saved_manifest = grade_dir / "manifest.json"
        if not saved_manifest.is_file():
            raise SystemExit("The grade to resume has no manifest")
        previous_grade = json.loads(saved_manifest.read_text(encoding="utf-8"))
        if previous_grade.get("grading_conditions_sha256") != conditions_hash:
            raise SystemExit("Grading inputs changed; use a new grade ID")
    else:
        grade_dir.mkdir(parents=True, exist_ok=False)
    manifest["grading_conditions_sha256"] = conditions_hash
    manifest["grading_status"] = "running"
    scores = []
    with EvaluationSession(policy) as session:
        previous_effective = (
            ((previous_grade or {}).get("benchmark") or {}).get("evaluation_effective") or {}
        ).get("effective_reasoning_effort")
        if previous_effective:
            session.restore_effective_effort(previous_effective)
        judge_fn = (
            make_openai_judge(args.judge_model, session=session) if args.judge_model else None
        )
        faithfulness_fn = (
            make_ragas_faithfulness(args.ragas_model, session=session, profile=profile)
            if args.ragas_model else None
        )
        try:
            atomic_json(grade_dir / "manifest.json", manifest)
            for record in records:
                assert_inputs_unchanged(frozen_inputs)
                if (args.judge_model or args.ragas_model) and load_evaluation_policy(args.config) != policy:
                    raise ValueError("Evaluator settings changed during grading")
                saved = load_grade_checkpoint(grade_dir, record, conditions_hash)
                if saved is None:
                    graded = grade_saved_runs(
                        [record], references, k=k, judge_fn=judge_fn,
                        faithfulness_fn=faithfulness_fn,
                        diagnose_fn=judge_fn if getattr(args, "diagnose_failures", False) else None,
                    )
                    if len(graded) != 1:
                        raise ValueError("Each saved response must produce exactly one grade")
                    saved = graded[0]
                    assert_inputs_unchanged(frozen_inputs)
                    # Persist the accepted effort before any durable score can exist.
                    manifest["benchmark"]["evaluation_effective"] = session.to_manifest()
                    atomic_json(grade_dir / "manifest.json", manifest)
                    save_grade_checkpoint(grade_dir, record, conditions_hash, saved)
                scores.append(saved)
                manifest["graded_count"] = len(scores)
                manifest["benchmark"]["evaluation_effective"] = session.to_manifest()
                atomic_json(grade_dir / "manifest.json", manifest)
                print(f"Graded {len(scores)}/{len(records)}: {record['question_id']}", flush=True)
            manifest["grading_status"] = "complete"
        except BaseException as error:
            manifest["grading_status"] = "interrupted"
            manifest["grading_error_type"] = type(error).__name__
            raise
        finally:
            manifest["benchmark"]["evaluation_effective"] = session.to_manifest()
            atomic_json(grade_dir / "manifest.json", manifest)
    write_artifacts(grade_dir, manifest, scores=scores)
    write_report(grade_dir, manifest, runs=records, scores=scores)
    if getattr(args, "final_benchmark_report", False):
        from app.evaluation.benchmark import load_final_benchmark, write_benchmark_report
        inventory = load_final_benchmark()
        write_benchmark_report(grade_dir / "final-benchmark", inventory, records, scores)
    if args.publish_langfuse_scores:
        _publish_grade(
            grade_dir,
            artifact_dir,
            manifest,
            records,
            scores,
            evaluation_set=args.evaluation_set,
            groups=args.publish_groups,
            verify=True,
            settings=publication_settings,
        )
    print(f"Scores and report saved to {grade_dir}")


def _publish_grade(
    grade_dir,
    artifact_dir,
    manifest,
    records,
    scores,
    *,
    evaluation_set=None,
    groups=None,
    verify=True,
    settings,
):
    from app.evaluation.publishing import GROUPS, plan_publication, score_items

    plan = manifest.get("langfuse_scores")
    if plan is None:
        namespace = (
            evaluation_set or f"{manifest['experiment_id']}-{manifest['grade_id']}"
        )
        selected_groups = GROUPS if groups is None or "all" in groups else groups
        plan = plan_publication(
            manifest,
            evaluation_set=namespace,
            groups=selected_groups,
            artifacts_dir=artifact_dir.parent,
            request_ids=[record["request_id"] for record in records],
            skip_same_experiment=False,
            exclude_manifest=grade_dir / "manifest.json",
        )
        manifest["langfuse_scores"] = plan
        manifest["langfuse_score_timestamp"] = datetime.now(timezone.utc).isoformat()
        write_artifacts(grade_dir, manifest, scores=scores)
    elif evaluation_set or groups:
        raise SystemExit(
            "Re-publishing uses the original evaluation set and metric groups"
        )
    if manifest["runs_sha256"] != sha256_file(artifact_dir / "runs.jsonl"):
        raise SystemExit("Question run has changed since grading")
    tracer = _publication_tracer(settings)
    try:
        manifest["langfuse_scores"] = publish_evaluation_scores(
            tracer, records, scores, manifest
        )
        if verify:
            stamp = datetime.fromisoformat(manifest["langfuse_score_timestamp"])
            by_request = {record["request_id"]: record for record in records}
            expected_by_trace = defaultdict(list)
            for row in scores:
                record = by_request.get(row["request_id"])
                trace_id = record.get("langfuse_trace_id") if record else None
                if not trace_id:
                    continue
                for metric, item in score_items(row, plan["groups"]).items():
                    name = f"{plan['evaluation_set']}.{metric}"
                    score_id = row.get("langfuse_score_ids", {}).get(name)
                    if score_id is None:
                        continue
                    expected_by_trace[trace_id].append({
                        "score_id": score_id,
                        "name": name,
                        "value": item["value"],
                        "timestamp": stamp,
                    })

            verification = Counter()
            readback_errors = Counter()
            for trace_id, expected in expected_by_trace.items():
                checked_scores = tracer.verify_scores(
                    trace_id, expected, timeout_s=5.0, interval_s=0.5
                )
                for checked in checked_scores.values():
                    verification[checked["status"]] += 1
                    if checked["status"] == "failed":
                        readback_errors[(checked.get("error"), checked.get("status_code"))] += 1
            manifest["langfuse_scores"]["readback_status_counts"] = dict(verification)
            if readback_errors:
                manifest["langfuse_scores"]["readback_errors"] = [
                    {"error": error, "status_code": status_code, "count": count}
                    for (error, status_code), count in sorted(readback_errors.items())
                ]
    finally:
        tracer.shutdown()
    write_artifacts(grade_dir, manifest, scores=scores)
    print(f"Langfuse scores: {manifest['langfuse_scores']['status']}")
    if verify:
        readback = manifest["langfuse_scores"].get("readback_status_counts", {})
        print(f"Langfuse score readback: {json.dumps(readback, sort_keys=True)}")


def _validated_langfuse_base_url(value: str | None) -> str:
    """Return the effective, credential-free Langfuse endpoint for run metadata."""
    base_url = value or DEFAULT_LANGFUSE_BASE_URL
    try:
        connection_settings({"connections": {"langfuse_base_url": base_url}})
    except (TypeError, ValueError):
        raise SystemExit(
            "Langfuse base URL must be an HTTP(S) URL without credentials or query parameters"
        ) from None
    return base_url


def _run_langfuse_base_url(manifest: Mapping[str, Any]) -> str | None:
    metadata = manifest.get("table_rag") or manifest.get("table_rag_a") or {}
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("langfuse_base_url")
    return _validated_langfuse_base_url(value) if value is not None else None


def _resolve_publication_settings(
    manifest: Mapping[str, Any], *, config_path: str | None
):
    """Resolve and validate the publishing target before dotenv enters process env."""
    from app.rag.config import load_table_rag_settings

    settings = load_table_rag_settings(config_path=config_path)
    configured_base_url = _validated_langfuse_base_url(
        settings.credentials.langfuse_base_url
    )
    run_base_url = _run_langfuse_base_url(manifest)

    if run_base_url is not None and configured_base_url != run_base_url:
        raise SystemExit(
            "The selected Langfuse endpoint differs from the saved run. "
            "Select the run's original settings with --config or ADEN_CONFIG."
        )

    return settings


def _publication_tracer(settings):
    """Build Langfuse from the already resolved credentials and endpoint."""
    from app.observability.tracing import Tracer

    return Tracer.from_credentials(
        public_key=settings.credentials.langfuse_public_key,
        secret_key=settings.credentials.langfuse_secret_key,
        base_url=settings.credentials.langfuse_base_url or DEFAULT_LANGFUSE_BASE_URL,
    )


def _publish_command(args):
    grade_dir = resolve_project_path(args.grade_directory)
    artifact_dir = grade_dir.parent.parent
    manifest = json.loads((grade_dir / "manifest.json").read_text(encoding="utf-8"))
    records = read_jsonl(artifact_dir / "runs.jsonl")
    scores = read_jsonl(grade_dir / "scores.jsonl")
    settings = _resolve_publication_settings(manifest, config_path=args.config)
    _publish_grade(
        grade_dir,
        artifact_dir,
        manifest,
        records,
        scores,
        evaluation_set=args.evaluation_set,
        groups=args.publish_groups,
        verify=args.verify,
        settings=settings,
    )


def parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run TableRAG questions and grade saved request outputs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Run questions and save outputs.")
    run_parser.add_argument("--questions", required=True)
    run_parser.add_argument("--ids")
    run_parser.add_argument("--experiment-id")
    run_parser.add_argument("--resume", action="store_true", help="Resume only unchanged frozen inputs.")
    run_parser.add_argument("--stop-after", type=int, help="Save this many new responses, then pause the unchanged full selection.")
    run_parser.add_argument("--config", help="Runtime YAML (relative to the repository root).")
    run_parser.add_argument("--data-directory")
    run_parser.add_argument("--artifacts-dir", default=DEFAULT_ARTIFACTS_DIRECTORY)
    run_parser.set_defaults(handler=_run_command)

    grade_parser = commands.add_parser("grade", help="Grade a saved run.")
    grade_parser.add_argument("--run-directory", required=True)
    grade_parser.add_argument(
        "--references",
        help="Optional reference dataset for retrieval metrics and Judge",
    )
    grade_parser.add_argument("--derived-facts", help="Independently reviewed evaluation-only fact sidecar.")
    grade_parser.add_argument(
        "--reference-id", help="Dataset question ID corresponding to one saved request"
    )
    grade_parser.add_argument("--grade-id", help="New grade directory name")
    grade_parser.add_argument("--resume", action="store_true", help="Reuse completed, input-matched grades.")
    grade_parser.add_argument("--faithfulness-profile", choices=("default", "advanced"), default="advanced")
    grade_parser.add_argument("--diagnose-failures", action="store_true",
                              help="Trace failed factual claims through recorded retrieval stages.")
    grade_parser.add_argument("--final-benchmark-report", action="store_true",
                              help="Validate and report the fixed Advanced 280-question suite.")
    grade_parser.add_argument(
        "--data-directory", help="Path to the saved run's TableRAG bundle if it moved."
    )
    grade_parser.add_argument(
        "--config",
        help="Runtime YAML for Langfuse score publishing; must match the saved run target.",
    )
    grade_parser.add_argument("--k", type=int, default=8)
    grade_parser.add_argument("--judge-model")
    grade_parser.add_argument("--ragas-model")
    grade_parser.add_argument(
        "--publish-langfuse-scores",
        action="store_true",
        help="Publish scores, then verify one score batch per trace for up to 5 seconds",
    )
    grade_parser.add_argument("--evaluation-set")
    grade_parser.add_argument(
        "--publish-groups",
        nargs="+",
        choices=("retrieval", "faithfulness", "judge", "evidence", "all"),
        default=("retrieval", "faithfulness", "judge", "evidence"),
    )
    grade_parser.set_defaults(handler=_grade_command)

    publish_parser = commands.add_parser(
        "publish", help="Publish or retry a saved grade."
    )
    publish_parser.add_argument("--grade-directory", required=True)
    publish_parser.add_argument(
        "--config",
        help="Runtime YAML for Langfuse score publishing; must match the saved run target.",
    )
    publish_parser.add_argument("--evaluation-set")
    publish_parser.add_argument(
        "--publish-groups",
        nargs="+",
        choices=("retrieval", "faithfulness", "judge", "evidence", "all"),
    )
    publish_parser.add_argument(
        "--verify", action="store_true", default=True,
        help="Read the score batch per trace from Langfuse for up to 5 seconds (default: enabled)",
    )
    publish_parser.add_argument(
        "--no-verify", action="store_false", dest="verify",
        help="Skip Langfuse score readback",
    )
    publish_parser.set_defaults(handler=_publish_command)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "grade" and args.k <= 0:
        raise SystemExit("--k must be a positive integer")
    args.handler(args)
