"""Generate and judge sampled Advanced answers for retrieval-pattern experiments.

This helper is intentionally local to the personal experiment area. Result rows
contain scores, timings, token counts, and hashes only; prompts and model text
remain in memory for the generation and grading calls.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, TextIO

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.evaluation.graders import grade_judge, load_prompt
from app.generation.chain import generate_answer
from app.generation.citations import resolve_citations
from app.service.resources import _create_generation_model
from shared.contracts import ContextBundle
from shared.settings import PROJECT_ROOT, load_settings

MODEL_ID = "gpt-6-luna"
JUDGE_MAX_OUTPUT_TOKENS = 2048
WORKERS = 6
RESULTS_NAME = "answers.jsonl"

LogSink = Callable[[str], None] | TextIO


def _log(log: LogSink, message: str) -> None:
    if callable(log):
        log(message)
        return
    log.write(message + "\n")
    log.flush()


def _usage_tokens(usage: Mapping[str, Any] | None) -> dict[str, int | None]:
    usage = usage or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def _error_details(error: Exception) -> tuple[str, bool]:
    """Return a safe error label and whether provider trouble should stop the run."""
    if isinstance(error, APIStatusError):
        status = error.status_code
        return f"api_status_{status}", status in {
            401,
            403,
            404,
            408,
            429,
        } or status >= 500
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return type(error).__name__, True
    return type(error).__name__, False


def _record_chunks(context: ContextBundle) -> list[dict[str, Any]]:
    return [
        {
            "file_name": item.chunk.file_name,
            "page": item.chunk.page,
            "text": item.chunk.text,
            "source_id": f"S{rank}",
        }
        for rank, item in enumerate(context.final_chunks, start=1)
    ]


def _safe_criteria(
    criteria: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        name: {"value": score.get("value"), "status": score.get("status")}
        for name, score in criteria.items()
    }


def _stage_copy(stage: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(stage))
    result.update(latency_ms=0, input_tokens=0, output_tokens=0, calls=0, reused=True)
    return result


def _new_row(
    question: Mapping[str, str],
    variant: str,
    context: ContextBundle,
    order: int,
) -> dict[str, Any]:
    return {
        "split": question["split"],
        "question_id": question["id"],
        "variant": variant,
        "order_in_question": order,
        "context_sha256": context.context_hash,
        "context_tokens": context.token_count,
        "context_pages": len(context.final_chunks),
        "generation": {},
        "judge": {},
        "reused_from": None,
    }


def _skipped_row(
    question: Mapping[str, str],
    variant: str,
    context: ContextBundle,
    order: int,
) -> dict[str, Any]:
    row = _new_row(question, variant, context, order)
    row["generation"] = {
        "status": "skipped",
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "calls": 0,
        "error_type": None,
        "reused": False,
    }
    row["judge"] = {
        "status": "skipped",
        "latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "calls": 0,
        "error_type": None,
        "criteria": {},
        "has_factual_claims": None,
        "reused": False,
    }
    return row


def _run_question(
    index: int,
    question: Mapping[str, str],
    variants: Sequence[str],
    contexts: Mapping[str, Sequence[ContextBundle]],
    reference: Mapping[str, Any],
    model: Any,
    judge_client: Any,
    judge_template: str,
    stop: threading.Event,
) -> list[dict[str, Any]]:
    order = [*variants[index % len(variants) :], *variants[: index % len(variants)]]
    cache: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, Any]] = {}

    for position, variant in enumerate(order, start=1):
        context = contexts[variant][index]
        cached = cache.get(context.context_hash)
        if cached is not None:
            row = deepcopy(cached)
            row.update(
                variant=variant,
                order_in_question=position,
                reused_from=cached["variant"],
            )
            row["generation"] = _stage_copy(cached["generation"])
            row["judge"] = _stage_copy(cached["judge"])
            rows[variant] = row
            continue

        row = _new_row(question, variant, context, position)
        if stop.is_set():
            rows[variant] = _skipped_row(question, variant, context, position)
            continue

        started = perf_counter()
        attempts = []
        try:
            generated = generate_answer(
                question["question"],
                context,
                model,
                max_attempts=1,
                attempt_log=attempts,
            )
        except Exception as error:
            error_type, should_stop = _error_details(error)
            if should_stop:
                stop.set()
            row["generation"] = {
                "status": "error",
                "latency_ms": round((perf_counter() - started) * 1000),
                **_usage_tokens(attempts[-1] if attempts else None),
                "calls": 1,
                "error_type": error_type,
                "reused": False,
            }
            answer = None
        else:
            answer = generated.model_answer
            row["generation"] = {
                "status": answer.status,
                "latency_ms": round((perf_counter() - started) * 1000),
                **_usage_tokens(generated.usage),
                "calls": 1,
                "error_type": None,
                "reused": False,
            }

        if answer is None:
            judge_record = {
                "question": question["question"],
                "question_id": question["id"],
                "status": "error",
                "error_type": row["generation"]["error_type"],
                "answer": None,
                "reason": None,
                "citations": [],
                "final_context_chunks": _record_chunks(context),
            }
            started = perf_counter()
            criteria, claims, judge_usage = grade_judge(
                judge_record,
                reference,
                judge_fn=None,
                template=judge_template,
            )
            row["judge"] = {
                "status": "skipped_generation_error",
                "latency_ms": round((perf_counter() - started) * 1000),
                "input_tokens": None,
                "output_tokens": None,
                "calls": 0,
                "error_type": None,
                "criteria": _safe_criteria(criteria),
                "has_factual_claims": claims,
                "reused": False,
            }
        elif stop.is_set():
            row["judge"] = {
                "status": "skipped_provider_error",
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "calls": 0,
                "error_type": None,
                "criteria": {},
                "has_factual_claims": None,
                "reused": False,
            }
        else:
            citations = resolve_citations(answer.source_ids, context.source_map)
            judge_record = {
                "question": question["question"],
                "question_id": question["id"],
                "status": answer.status,
                "answer": answer.answer,
                "reason": answer.reason,
                "citations": [
                    citation.model_dump(mode="json") for citation in citations
                ],
                "final_context_chunks": _record_chunks(context),
            }
            judge_error: dict[str, Any] = {"call_attempted": False}

            def judge_fn(prompt: str) -> dict[str, Any]:
                if stop.is_set():
                    judge_error["stopped"] = True
                    raise RuntimeError("Benchmark stopped after a provider error")
                judge_error["call_attempted"] = True
                try:
                    response = judge_client.chat.completions.create(
                        model=MODEL_ID,
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"},
                        reasoning_effort="low",
                        max_completion_tokens=JUDGE_MAX_OUTPUT_TOKENS,
                        store=False,
                    )
                except Exception as error:
                    error_type, should_stop = _error_details(error)
                    judge_error["error_type"] = error_type
                    if should_stop:
                        stop.set()
                    raise
                usage = response.usage
                return {
                    "text": response.choices[0].message.content or "",
                    "input_tokens": getattr(usage, "prompt_tokens", None),
                    "output_tokens": getattr(usage, "completion_tokens", None),
                }

            started = perf_counter()
            criteria, claims, judge_usage = grade_judge(
                judge_record,
                reference,
                judge_fn=judge_fn,
                template=judge_template,
            )
            safe_criteria = _safe_criteria(criteria)
            criteria_finished = any(
                score["status"] in {"scored", "not_applicable"}
                for score in safe_criteria.values()
            )
            row["judge"] = {
                "status": (
                    "skipped_provider_error"
                    if judge_error.get("stopped")
                    else "error"
                    if judge_error.get("error_type")
                    else "complete"
                    if criteria_finished
                    else "unavailable"
                ),
                "latency_ms": round((perf_counter() - started) * 1000),
                **_usage_tokens(judge_usage),
                "calls": int(judge_error["call_attempted"]),
                "error_type": judge_error.get("error_type")
                or (judge_usage or {}).get("error"),
                "criteria": safe_criteria,
                "has_factual_claims": claims,
                "reused": False,
            }

        rows[variant] = row
        cache[context.context_hash] = row

    return [rows[variant] for variant in variants if variant in rows]


def _validate_inputs(
    questions: Sequence[Mapping[str, str]],
    contexts: Mapping[str, Sequence[ContextBundle]],
    references: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> tuple[str, ...]:
    if not questions:
        raise ValueError("At least one question is required.")
    variants = tuple(contexts)
    if not variants:
        raise ValueError("At least one context variant is required.")
    for variant in variants:
        if len(contexts[variant]) != len(questions):
            raise ValueError(
                "Every context variant must align with the ordered questions."
            )
    for question in questions:
        if not all(key in question for key in ("id", "split", "question")):
            raise ValueError("Each question requires id, split, and question fields.")
        try:
            references[question["split"]][question["id"]]
        except KeyError as error:
            raise ValueError(
                "A question is missing its split-specific reference."
            ) from error
    return variants


def _write_rows(output: TextIO, rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def _smoke_ok(rows: Sequence[Mapping[str, Any]]) -> bool:
    for row in rows:
        if row.get("reused_from") is not None:
            continue
        generation = row["generation"]
        judge = row["judge"]
        if generation.get("status") in {"error", "skipped"}:
            continue
        if (
            generation.get("input_tokens") is None
            or generation.get("output_tokens") is None
        ):
            continue
        if judge.get("status") != "complete":
            continue
        if judge.get("input_tokens") is None or judge.get("output_tokens") is None:
            continue
        if judge.get("has_factual_claims") is None:
            continue
        if any(
            item.get("status") == "scored"
            for item in judge.get("criteria", {}).values()
        ):
            return True
    return False


def _summary(
    rows: Sequence[Mapping[str, Any]], variants: Sequence[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": MODEL_ID,
        "rows": len(rows),
        "completed_questions": len(
            {(row["split"], row["question_id"]) for row in rows}
        ),
        "variants": {},
    }
    for variant in variants:
        variant_rows = [row for row in rows if row["variant"] == variant]
        summary: dict[str, Any] = {"rows": len(variant_rows), "reused_rows": 0}
        for stage_name in ("generation", "judge"):
            stages = [row[stage_name] for row in variant_rows]
            calls = sum(stage.get("calls", 0) for stage in stages)
            observed_input = [
                stage["input_tokens"]
                for stage in stages
                if stage.get("calls") and stage.get("input_tokens") is not None
            ]
            observed_output = [
                stage["output_tokens"]
                for stage in stages
                if stage.get("calls") and stage.get("output_tokens") is not None
            ]
            timed = [stage["latency_ms"] for stage in stages if stage.get("calls", 0)]
            summary[stage_name] = {
                "calls": calls,
                "statuses": dict(
                    Counter(stage.get("status", "unknown") for stage in stages)
                ),
                "input_tokens": sum(observed_input)
                if observed_input or not calls
                else None,
                "output_tokens": sum(observed_output)
                if observed_output or not calls
                else None,
                "calls_missing_usage": sum(
                    bool(stage.get("calls"))
                    and (
                        stage.get("input_tokens") is None
                        or stage.get("output_tokens") is None
                    )
                    for stage in stages
                ),
                "mean_latency_ms": round(sum(timed) / len(timed), 1) if timed else None,
            }
            summary["reused_rows"] = max(
                summary["reused_rows"],
                sum(bool(stage.get("reused")) for stage in stages),
            )
        summary["criteria"] = {}
        for criterion in ("fact", "condition", "evidence", "decision"):
            scores = [
                row["judge"].get("criteria", {}).get(criterion, {}).get("value")
                for row in variant_rows
                if row["judge"].get("criteria", {}).get(criterion, {}).get("status")
                == "scored"
            ]
            statuses = Counter(
                row["judge"]
                .get("criteria", {})
                .get(criterion, {})
                .get("status", "unavailable")
                for row in variant_rows
            )
            summary["criteria"][criterion] = {
                "statuses": dict(statuses),
                "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
            }
        result["variants"][variant] = summary
    return result


def run_answers(
    questions: Sequence[Mapping[str, str]],
    contexts: Mapping[str, Sequence[ContextBundle]],
    references: Mapping[str, Mapping[str, Mapping[str, Any]]],
    output_dir: str | Path,
    log: LogSink,
) -> list[dict[str, Any]]:
    """Generate and grade sampled answers, appending redacted rows to JSONL.

    The question order is supplied by the caller. Each variant's context list
    must align with it. A repeated context hash within one question reuses that
    question's generation and Judge result without making additional calls.
    """
    variants = _validate_inputs(questions, contexts, references)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = load_settings()
    if settings.generation_model != MODEL_ID:
        raise ValueError(f"This benchmark only supports {MODEL_ID}.")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result_path = destination / RESULTS_NAME
    _log(
        log,
        f"answer benchmark start questions={len(questions)} variants={len(variants)} model={MODEL_ID} workers={WORKERS}",
    )

    stop = threading.Event()
    completed_rows: list[dict[str, Any]] = []
    with (
        ExitStack() as cleanup,
        OpenAI(timeout=settings.model_timeout_seconds, max_retries=0) as judge_client,
        result_path.open("a", encoding="utf-8", buffering=1) as output,
    ):
        model = _create_generation_model(settings, cleanup)
        judge_template = load_prompt("judge.txt")

        def run_one(index: int) -> list[dict[str, Any]]:
            question = questions[index]
            return _run_question(
                index,
                question,
                variants,
                contexts,
                references[question["split"]][question["id"]],
                model,
                judge_client,
                judge_template,
                stop,
            )

        smoke_rows = run_one(0)
        _write_rows(output, smoke_rows)
        completed_rows.extend(smoke_rows)
        if stop.is_set() or not _smoke_ok(smoke_rows):
            _log(log, "answer benchmark stopped smoke=failed completed_questions=1")
            raise RuntimeError(
                "Answer benchmark smoke failed; partial safe results were preserved."
            )
        _log(
            log,
            f"answer benchmark smoke=passed completed_questions=1 rows={len(smoke_rows)}",
        )

        next_index = 1
        pending = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as workers:
            while next_index < len(questions) and len(pending) < WORKERS:
                pending[workers.submit(run_one, next_index)] = next_index
                next_index += 1

            while pending:
                future = next(as_completed(tuple(pending)))
                index = pending.pop(future)
                rows = future.result()
                _write_rows(output, rows)
                completed_rows.extend(rows)
                errors = sum(
                    row[stage].get("status") == "error"
                    for row in rows
                    for stage in ("generation", "judge")
                )
                _log(
                    log,
                    f"answer benchmark completed_questions={len(completed_rows) // len(variants)}/{len(questions)} "
                    f"question_number={index + 1} "
                    f"rows={len(rows)} errors={errors}",
                )
                if not stop.is_set() and next_index < len(questions):
                    pending[workers.submit(run_one, next_index)] = next_index
                    next_index += 1

    if stop.is_set():
        _log(log, f"answer benchmark stopped provider_error rows={len(completed_rows)}")
    else:
        _log(log, f"answer benchmark finished rows={len(completed_rows)}")
    (destination / "answer_summary.json").write_text(
        json.dumps(_summary(completed_rows, variants), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return completed_rows
