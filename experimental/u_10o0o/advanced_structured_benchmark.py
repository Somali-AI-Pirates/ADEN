"""Compare typed SQLite retrieval with the current Advanced baseline."""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from itertools import islice
from time import perf_counter

from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_luna_benchmark import (
    HOLDOUT,
    applied_rule_targets,
)
from experimental.u_10o0o.advanced_luna_metrics import score_luna
from experimental.u_10o0o.advanced_routing_rules import RoutingRuleStore
from experimental.u_10o0o.advanced_search import (
    evaluation_files,
    evaluation_questions,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_structured_route import assemble, verify
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.search import load_index

LIVE = ("luna_json", "luna")
FATAL_STATUS = {401, 403, 404, 429}
WORKERS = 8
TIMEOUT_SECONDS = 10.0


def run_luna(questions, adapter, pilot_id, pilot_check, output, log):
    """Save each paid attempt as it finishes and stop dispatch on fatal errors."""
    pilot = next(q for q in questions if q["id"] == pilot_id)
    proposals = {}
    pending_jobs = iter(
        (q, arm) for q in questions for arm in LIVE if q["id"] != pilot_id
    )
    with (output / "api-traces.jsonl").open("w", encoding="utf-8") as stream:

        def save(q, arm, call):
            proposals[q["id"], arm] = call
            stream.write(
                json.dumps(
                    {
                        "id": q["id"],
                        "arm": arm,
                        "status": call.status,
                        "telemetry": call.telemetry.as_dict(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()

        for arm in ("luna", "luna_json"):
            call = adapter.invoke(
                pilot["question"], mode="tool" if arm == "luna" else "json"
            )
            save(pilot, arm, call)
            pilot_check(arm, call)
            log(f"실제 호출 연결 확인 · {arm} · 본 비교에 재사용")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:

            def submit(q, arm):
                return pool.submit(
                    adapter.invoke,
                    q["question"],
                    mode="tool" if arm == "luna" else "json",
                )

            pending = {
                submit(q, arm): (q, arm) for q, arm in islice(pending_jobs, WORKERS)
            }
            while pending:
                task = next(as_completed(pending))
                q, arm = pending.pop(task)
                call = task.result()
                save(q, arm, call)
                code = call.telemetry.api_status_code
                if code in FATAL_STATUS:
                    for active, (question, condition) in pending.items():
                        if not active.cancel():
                            save(question, condition, active.result())
                    raise RuntimeError(f"Luna 비교 중단 (HTTP {code}).")
                if item := next(pending_jobs, None):
                    pending[submit(*item)] = item
                if len(proposals) % 50 == 0:
                    log(f"Luna 호출 {len(proposals)}/{len(questions) * 2}회 완료")
    return proposals


def _rank(search, questions, vectors, log):
    prepared, elapsed = {}, {}
    for batch in evaluation.batches(questions):
        started = perf_counter()
        pools, heads = search.rank(batch, {"shared": vectors}, log)
        latency = (perf_counter() - started) * 1000 / len(batch)
        for row, pool, head in zip(
            batch, pools["shared"], heads["shared"], strict=True
        ):
            prepared[row["id"]] = pool, retain_reranked_head(pool, head)
            elapsed[row["id"]] = latency
    return prepared, elapsed


def _safe_facts(facts):
    return [
        {
            "field": fact.field,
            "state": fact.state,
            "family": fact.family,
            "cost_kind": fact.cost_kind,
            "source": asdict(fact.source),
        }
        for fact in facts
    ]


def _structured_result(decision, search_result):
    if decision.skip_search:
        context, facts = assemble(decision)
        if len(facts) == len(decision.facts):
            return ([], [], context, (), "db_only"), facts, False
    baseline = search_result()
    pool, selected, original, cases, route = baseline
    context, facts = assemble(decision, original)
    return (pool, selected, context, cases, f"{decision.status}/{route}"), facts, True


def _score_retrieval(reference, retrieval):
    metrics = evaluation.score_one(reference, retrieval)
    if "final_complete" not in metrics:
        return metrics
    original = []
    for item in retrieval[2].final_chunks:
        if item.retriever == "sqlite":
            continue
        if item.retriever == "sqlite+document":
            _, text = item.chunk.text.split("\n\n원본 문서 근거\n", 1)
            item = replace(item, chunk=replace(item.chunk, text=text))
        original.append(item)
    coverage = fact_coverage(
        reference
        if "evidence_requirements" in reference
        else {
            "evidence_requirements": [
                {"primary": group} for group in reference["evidence_groups"] if group
            ]
        },
        original,
    )
    metrics.update(
        final_complete=coverage["all_facts"], final_facts=coverage["required_facts_hit"]
    )
    return metrics


def _summary(rows):
    return {
        "questions": len(rows),
        "statuses": dict(Counter(row["status"] for row in rows)),
        "provider_statuses": dict(
            Counter(
                row["provider_status"] for row in rows if row.get("provider_status")
            )
        ),
        "api_calls": sum(bool(row["telemetry"]) for row in rows),
        "tool_requests": sum(row["tool_requested"] for row in rows),
        "validated_tool_requests": sum(
            row["tool_requested"] and row["sql_queries"] > 0 for row in rows
        ),
        "sql_questions": sum(row["sql_queries"] > 0 for row in rows),
        "sql_queries": sum(row["sql_queries"] for row in rows),
        "search_calls": sum(row["search"] for row in rows),
        "search_skipped": sum(not row["search"] for row in rows),
        "database_fact_questions": sum(bool(row["facts"]) for row in rows),
        "tokens": {
            key: sum((row["telemetry"] or {}).get(key) or 0 for row in rows)
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cached_input_tokens",
            )
        },
        "model_latency": evaluation.latency(
            [
                call["latency_ms"]
                for row in rows
                if (call := row["telemetry"]) and call.get("latency_ms") is not None
            ]
        ),
        "validation_and_pack_latency": evaluation.latency(
            [row["local_ms"] for row in rows]
        ),
        "validation_and_sql_latency": evaluation.latency(
            [row["verify_ms"] for row in rows if row.get("verify_ms") is not None]
        ),
        "warm_end_to_end_sample": evaluation.latency(
            [row["warm_sample_ms"] for row in rows if row["warm_sample_ms"] is not None]
        ),
        "warm_sample_by_path": {
            name: evaluation.latency(
                [
                    row["warm_sample_ms"]
                    for row in rows
                    if row["warm_sample_ms"] is not None and row["search"] == searches
                ]
            )
            for name, searches in (("database_only", False), ("with_search", True))
        },
    }


def compare_structured(args, output, log):
    from experimental.u_10o0o.advanced_experiment import (
        artifact_path,
        experiment_config,
    )
    from experimental.u_10o0o.advanced_structured_luna import (
        PROMPT_SHA256,
        SCHEMA_SHA256,
        StructuredLuna,
    )

    files = evaluation_files("categories")
    categories = evaluation_questions(files)
    files["pair_holdout"] = HOLDOUT / "questions.json", HOLDOUT / "references.json"
    existing = categories + evaluation_questions(
        {"pair_holdout": files["pair_holdout"]}
    )
    typed_questions = artifact_path(args.typed_gold) / "questions.json"
    typed_references = artifact_path(args.typed_gold) / "references.json"
    typed = evaluation_questions({"typed": (typed_questions, typed_references)})
    questions = existing + typed
    if (len(categories), len(existing), len(typed)) != (220, 250, 30) or len(
        {q["id"] for q in questions}
    ) != 280:
        raise ValueError("카테고리 220, 별도 30, 구조화 값 30문항이 필요합니다.")

    pages, vectors = load_index()
    rules = RoutingRuleStore(artifact_path(args.rules_db)).snapshot(args.rule_version)
    lookup = load_target_lookup(pages, rules=rules)
    storage = load_storage_lookup(pages)
    db_path = artifact_path(args.structured_snapshot)
    arms = ("rules", "rules_db", *LIVE) if args.live_luna else ("rules", "rules_db")
    config = experiment_config()
    config.update(
        arms=arms,
        structured_snapshot=evaluation.stamp(db_path / "manifest.json"),
        rules={"version": rules.version_id, "sha256": rules.content_sha256},
        question_inputs={
            **{split: evaluation.stamp(q) for split, (q, _) in files.items()},
            "typed": evaluation.stamp(typed_questions),
        },
        interpreter={
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
            "prompt_sha256": PROMPT_SHA256,
            "schema_sha256": SCHEMA_SHA256,
            "timeout_seconds": TIMEOUT_SECONDS,
            "retries": 0,
            "workers": WORKERS,
            "maximum_calls": len(questions) * 2 if args.live_luna else 0,
        },
    )
    evaluation.write_json(output / "config.json", config)
    search = evaluation.Search(pages, vectors)
    with StructuredStore(db_path) as store:
        pilots = [q for q in typed if "실제 양하" in q["question"]]
        if not pilots:
            raise ValueError("실제 양하 날짜를 확인하는 구조화 연결 표본이 필요합니다.")
        pilot = pilots[0]

        def pilot_check(arm, call):
            decision = verify(pilot["question"], lookup, store, proposal=call.proposal)
            if (
                not decision.facts
                or not decision.queries
                or (arm == "luna" and not call.tool_requested)
            ):
                raise RuntimeError(
                    f"실제 SQLite 연결 확인 실패 ({arm}, {call.status}, {decision.status})."
                )

        log(f"구조화 비교 시작 · {len(questions)}문항 · 답변 생성 0회")
        proposals = (
            run_luna(questions, StructuredLuna(), pilot["id"], pilot_check, output, log)
            if args.live_luna
            else {}
        )
        rankings, search_times = _rank(search, existing, vectors, log)
        # Unbatched timing on a frozen subset measures actual search latency.
        for q in typed:
            started = perf_counter()
            rankings[q["id"]] = search.one(q["question"])
            search_times[q["id"]] = (perf_counter() - started) * 1000
        retrievals, traces, packed_facts = {}, {}, {}
        for q in questions:
            qid, text = q["id"], q["question"]
            pool, order = rankings[qid]
            started = perf_counter()
            baseline, linked = evaluation.finish(text, pool, order, lookup, storage)
            baseline_status, baseline_targets = applied_rule_targets(
                text, baseline, linked, lookup
            )
            baseline_ms = (perf_counter() - started) * 1000
            retrievals[qid, "rules"] = baseline
            packed_facts[qid, "rules"] = ()
            traces[qid, "rules"] = {
                "status": baseline_status,
                "targets": baseline_targets,
                "facts": [],
                "search": True,
                "tool_requested": False,
                "sql_queries": 0,
                "telemetry": None,
                "local_ms": baseline_ms,
                "decision_and_pack_ms": baseline_ms,
                "warm_sample_ms": baseline_ms + search_times[qid]
                if q["split"] == "typed"
                else None,
            }
            for arm in arms[1:]:
                started = perf_counter()
                call = proposals.get((qid, arm))
                decision = verify(
                    text,
                    lookup,
                    store,
                    rules=arm == "rules_db",
                    proposal=call.proposal
                    if call and (arm != "luna" or call.tool_requested)
                    else None,
                )
                verify_ms = (perf_counter() - started) * 1000
                result, facts, ran_search = _structured_result(
                    decision, lambda: baseline
                )
                local_ms = (perf_counter() - started) * 1000 + (
                    baseline_ms if ran_search else 0
                )
                telemetry = call.telemetry.as_dict() if call else None
                model_ms = (telemetry or {}).get("latency_ms") or 0.0
                targets = (
                    {row["case"]: row["roles"] for row in baseline_targets}
                    if ran_search and decision.status not in {"ambiguous", "conflict"}
                    else {}
                )
                for case, roles in decision.targets:
                    targets[case] = sorted(set(targets.get(case, ())) | set(roles))
                retrievals[qid, arm] = result
                packed_facts[qid, arm] = facts
                traces[qid, arm] = {
                    "status": decision.status,
                    "targets": [
                        {"case": case, "roles": list(roles)}
                        for case, roles in targets.items()
                    ],
                    "facts": _safe_facts(facts),
                    "search": ran_search,
                    "tool_requested": bool(call and call.tool_requested),
                    "sql_queries": decision.queries,
                    "telemetry": telemetry,
                    "provider_called": bool(call),
                    "provider_status": call.status if call else None,
                    "local_ms": local_ms,
                    "verify_ms": verify_ms,
                    "decision_and_pack_ms": local_ms + model_ms,
                    "warm_sample_ms": (
                        local_ms + model_ms + (search_times[qid] if ran_search else 0)
                        if q["split"] == "typed"
                        else None
                    ),
                }
        evaluation.save_rankings(output, retrievals)
        write_rows(
            output / "traces.jsonl",
            (
                {
                    "id": qid,
                    "arm": arm,
                    **{k: v for k, v in row.items() if k != "targets"},
                }
                for (qid, arm), row in traces.items()
            ),
        )
    log("검색·조회 결과 저장 완료 · 로컬 참고 근거 채점")
    config["references"] = {
        **{split: evaluation.stamp(r) for split, (_, r) in files.items()},
        "typed": evaluation.stamp(typed_references),
    }
    config["external_api_calls"] = len(proposals)
    evaluation.write_json(output / "config.json", config)
    scores = score_luna(
        output,
        existing,
        files,
        retrievals,
        traces,
        lookup,
        comparison_arm="luna" if args.live_luna else "rules_db",
        retrieval_scorer=_score_retrieval,
    )
    from experimental.u_10o0o.advanced_structured_gold import score_typed_gold

    typed_scores = score_typed_gold(
        output, typed_references, arms, retrievals, traces, packed_facts
    )
    stats = {arm: _summary([traces[q["id"], arm] for q in questions]) for arm in arms}
    evaluation.write_json(
        output / "results.json",
        {
            "config": config,
            "scores": scores,
            "typed": typed_scores,
            "stats": stats,
            "physical_shared_search_queries": search.searches,
            "physical_shared_kure_queries": search.reranks,
        },
    )
    log(f"최종 검색·조회 비교 완료 · Luna {len(proposals)}회 · 답변 생성 0회")
