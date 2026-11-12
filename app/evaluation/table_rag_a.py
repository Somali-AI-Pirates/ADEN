"""Run and grade saved TableRAG requests without regenerating their answers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from app.evaluation.dataset import load_questions, load_references
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
from app.evaluation.report import write_artifacts, write_json, write_report
from app.evaluation.runner import grade_all
from app.evaluation.metrics import evidence_diagnostics, retrieval_metrics, score
from app.evaluation.scripts._common import base_manifest, load_env, now_id, sha256_file
from app.observability.records import compute_context_hash, summarize_usage

RETRIEVAL_MODE = "table_rag"
DEFAULT_DATA_DIRECTORY = "artifacts/advanced-table-rag"
DEFAULT_ARTIFACTS_DIRECTORY = "artifacts/experiments/table-rag"


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
                row = json.loads(line)
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
) -> list[dict[str, Any]]:
    """Run only on question text and append each result before the next request."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with output_path.open("x", encoding="utf-8") as stream:
        for question in questions:
            request_id = uuid.uuid4().hex
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
            stream.write(
                json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
            )
            stream.flush()
            records.append(record)
    return records


def grade_saved_runs(
    records: Sequence[Mapping[str, Any]],
    references: Mapping[str, Mapping[str, Any]] | None,
    *,
    k: int = 8,
    judge_fn: JudgeFn | None = None,
    faithfulness_fn: FaithfulnessFn | None = None,
) -> list[dict[str, Any]]:
    if references is not None:
        graded = grade_all(
            records,
            references,
            k=k,
            judge_fn=judge_fn,
            faithfulness_fn=faithfulness_fn,
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
            }
        )
    return graded


def _run_command(args: Any) -> None:
    from app.rag.service import load_service

    experiment_id = args.experiment_id or now_id("table-rag")
    dataset_id = "advanced"
    questions_path = Path(args.questions).resolve()
    question_ids = (
        [value.strip() for value in args.ids.split(",")] if args.ids else None
    )
    questions = load_questions(questions_path, question_ids)
    data_directory = Path(args.data_directory).resolve()
    corpus_manifest_path = data_directory / "corpus.json"
    config_hash = sha256_file(corpus_manifest_path)
    artifact_root = Path(args.artifacts_dir).resolve()
    if Path(experiment_id).name != experiment_id or experiment_id in {"", ".", ".."}:
        raise SystemExit("Experiment ID must be a directory name")
    artifact_dir = (artifact_root / experiment_id).resolve()
    if artifact_dir.parent != artifact_root:
        raise SystemExit("Experiment output must be inside the artifacts directory")
    artifact_dir.mkdir(parents=True, exist_ok=False)

    manifest = base_manifest(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        config_hash=config_hash,
        sample=False,
    )
    manifest["table_rag"] = {
        "data_directory": str(data_directory),
        "corpus_manifest_sha256": config_hash,
        "questions_file": questions_path.name,
        "questions_sha256": sha256_file(questions_path),
        "question_ids": [question["id"] for question in questions],
        "run_status": "running",
    }
    write_json(artifact_dir / "manifest.json", manifest)

    async def run() -> None:
        service = None
        try:
            service = load_service(data_directory)
            manifest["service"] = dict(getattr(service, "manifest", {}))
            run_config_hash = getattr(service, "config_hash", config_hash)
            manifest["config_hash"] = run_config_hash
            write_json(artifact_dir / "manifest.json", manifest)
            records = await run_questions(
                questions,
                service,
                experiment_id=experiment_id,
                dataset_id=dataset_id,
                config_hash=run_config_hash,
                output_path=artifact_dir / "runs.jsonl",
            )
            manifest["table_rag"]["run_status"] = "complete"
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
                write_json(artifact_dir / "manifest.json", manifest)

    asyncio.run(run())
    print(f"Question-only run saved to {artifact_dir}")


def _grade_command(args: Any) -> None:
    artifact_dir = Path(args.run_directory).resolve()
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
    references_path = Path(args.references).resolve() if args.references else None
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

    grade_id = args.grade_id or now_id("grade")
    if Path(grade_id).name != grade_id or grade_id in {"", ".", ".."}:
        raise SystemExit("Grade ID must be a directory name")
    grade_dir = (artifact_dir / "grades" / grade_id).resolve()
    if grade_dir.parent != artifact_dir / "grades":
        raise SystemExit("Grade output must be inside the run's grades directory")
    grade_dir.mkdir(parents=True, exist_ok=False)
    manifest["grade_id"] = grade_id
    manifest["runs_sha256"] = sha256_file(runs_path)

    if args.judge_model or args.ragas_model or args.publish_langfuse_scores:
        load_env()
    judge_fn = make_openai_judge(args.judge_model) if args.judge_model else None
    faithfulness_fn = (
        make_ragas_faithfulness(args.ragas_model) if args.ragas_model else None
    )
    k = args.k
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
    }

    scores = grade_saved_runs(
        records,
        references,
        k=k,
        judge_fn=judge_fn,
        faithfulness_fn=faithfulness_fn,
    )
    write_artifacts(grade_dir, manifest, scores=scores)
    write_report(grade_dir, manifest, runs=records, scores=scores)
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
):
    from app.evaluation.publishing import GROUPS, plan_publication, score_items
    from app.observability.tracing import Tracer

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
    load_env()
    tracer = Tracer.from_env()
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


def _publish_command(args):
    grade_dir = Path(args.grade_directory).resolve()
    artifact_dir = grade_dir.parent.parent
    manifest = json.loads((grade_dir / "manifest.json").read_text(encoding="utf-8"))
    records = read_jsonl(artifact_dir / "runs.jsonl")
    scores = read_jsonl(grade_dir / "scores.jsonl")
    _publish_grade(
        grade_dir,
        artifact_dir,
        manifest,
        records,
        scores,
        evaluation_set=args.evaluation_set,
        groups=args.publish_groups,
        verify=args.verify,
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
    run_parser.add_argument("--data-directory", default=DEFAULT_DATA_DIRECTORY)
    run_parser.add_argument("--artifacts-dir", default=DEFAULT_ARTIFACTS_DIRECTORY)
    run_parser.set_defaults(handler=_run_command)

    grade_parser = commands.add_parser("grade", help="Grade a saved run.")
    grade_parser.add_argument("--run-directory", required=True)
    grade_parser.add_argument(
        "--references",
        help="Optional reference dataset for retrieval metrics and Judge",
    )
    grade_parser.add_argument(
        "--reference-id", help="Dataset question ID corresponding to one saved request"
    )
    grade_parser.add_argument("--grade-id", help="New grade directory name")
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
