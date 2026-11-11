"""Compare the current rules with Luna interpretation on a shared ranked pool."""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from time import perf_counter

from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_hg_lookup import location
from experimental.u_10o0o.advanced_pair_context import (
    pair_requirements,
    requested_roles,
)
from experimental.u_10o0o.advanced_pair_source_lookup import pair_source_lookup
from experimental.u_10o0o.advanced_question_router import pack_decision, route_question
from experimental.u_10o0o.advanced_routing_rules import RoutingRuleStore
from experimental.u_10o0o.advanced_search import (
    evaluation_files,
    evaluation_questions,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_source_lookup import unique_lookup
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.prepare import PROJECT_ROOT
from experimental.u_10o0o.search import load_index

HOLDOUT = PROJECT_ROOT / "artifacts/u_10o0o/advanced-pair-holdout-20261109-data"
WORKERS = 6
TIMEOUT_SECONDS = 10.0
MAX_QUESTIONS = 250


def target_rows(roles_by_case):
    return [
        {"case": case, "roles": sorted(roles)} for case, roles in roles_by_case.items()
    ]


def applied_rule_targets(text, result, linked, lookup):
    hg_route, source_route, _ = result[-1].split("/")
    if source_route == "ambiguous":
        return "ambiguous", []
    if source_route in ("unique", "pair"):
        resolve = pair_source_lookup if source_route == "pair" else unique_lookup
        cases, pages = resolve(text, lookup)
        roles_by_case = {case: set() for case in cases}
        for page in pages:
            case, role = lookup.page_roles[location(page)]
            roles_by_case[case].add(role)
        return "verified", target_rows(roles_by_case)
    if pair := pair_requirements(text, lookup):
        return "verified", target_rows(pair)
    if hg_route in ("explicit_hg", "indirect_hg"):
        case, _ = lookup.page_roles[location(linked[0][0])]
        return "verified", target_rows({case: requested_roles(text, lookup.rules)})
    return "unresolved", []


def run_interpretations(questions, lookup, interpreter, output, log):
    def decide(question):
        started = perf_counter()
        decision = route_question(
            question["question"], lookup, interpreter, timeout_seconds=TIMEOUT_SECONDS
        )
        telemetry = interpreter.last_call if decision.provider_called else None
        return decision, telemetry, (perf_counter() - started) * 1000

    pilot = next(
        row
        for row in questions
        if route_question(row["question"], lookup).reason
        == "interpreter_not_configured"
    )
    decisions = {}
    with (output / "api-traces.jsonl").open("w", encoding="utf-8") as stream:

        def save(question, result):
            qid = question["id"]
            decision, telemetry, elapsed = result
            decisions[qid] = result
            stream.write(
                json.dumps(
                    {
                        "id": qid,
                        "split": question["split"],
                        "status": decision.status,
                        "reason": decision.reason,
                        "route_hint": decision.route,
                        "targets": target_rows(decision.targets_by_case),
                        "provider_called": decision.provider_called,
                        "telemetry": telemetry,
                        "decision_ms": elapsed,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            stream.flush()

        trial = decide(pilot)
        save(pilot, trial)
        if not trial[0].provider_called or trial[0].status in {
            "timeout",
            "interpretation_failure",
        }:
            detail = (trial[1] or {}).get("status", trial[0].status)
            raise RuntimeError(
                f"Luna 연결 확인 실패 ({detail}). 전체 호출을 시작하지 않았습니다."
            )
        log(f"Luna 연결 확인 완료 · {pilot['id']} · 이 응답을 본 비교에 재사용")
        remaining = iter(row for row in questions if row["id"] != pilot["id"])
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            # Keep the queued work bounded so a fatal API error can stop dispatch.
            pending = {
                executor.submit(decide, row): row for row in islice(remaining, WORKERS)
            }
            while pending:
                future = next(as_completed(pending))
                row = pending.pop(future)
                result = future.result()
                save(row, result)
                telemetry = result[1] or {}
                status = telemetry.get("api_status_code")
                if status in {401, 403, 404, 429}:
                    for outstanding, item in pending.items():
                        if not outstanding.cancel():
                            save(item, outstanding.result())
                    raise RuntimeError(f"Luna 호출을 중단했습니다 (HTTP {status}).")
                if next_row := next(remaining, None):
                    pending[executor.submit(decide, next_row)] = next_row
                if len(decisions) % 25 == 0:
                    log(f"Luna 해석 완료 · {len(decisions)}/{len(questions)}문항")
    return decisions


def provider_summary(decisions):
    calls = [
        telemetry
        for decision, telemetry, _ in decisions.values()
        if decision.provider_called and telemetry is not None
    ]
    times = [call["latency_ms"] for call in calls if call.get("latency_ms") is not None]
    tokens = {
        key: sum(call.get(key) or 0 for call in calls)
        for key in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cached_input_tokens",
        )
    }
    return {
        "calls": len(calls),
        "statuses": dict(Counter(call.get("status", "unknown") for call in calls)),
        "models": dict(Counter(call["model"] for call in calls if call.get("model"))),
        "latency": evaluation.latency(times),
        "tokens": tokens,
    }


def compare_luna(args, output, log):
    from experimental.u_10o0o.advanced_experiment import (
        artifact_path,
        experiment_config,
    )
    from experimental.u_10o0o.advanced_luna_interpreter import (
        DOMAIN_INSTRUCTIONS,
        MAX_OUTPUT_TOKENS,
        MODEL_ID,
        PROMPT_SHA256,
        REASONING_EFFORT,
        LunaInterpreter,
    )

    files = evaluation_files("categories")
    categories = evaluation_questions(files)
    files["pair_holdout"] = HOLDOUT / "questions.json", HOLDOUT / "references.json"
    holdout = evaluation_questions({"pair_holdout": files["pair_holdout"]})
    questions = categories + holdout
    if (
        (len(categories), len(holdout)) != (220, 30)
        or len({q["id"] for q in questions}) != MAX_QUESTIONS
        or len({q["question"] for q in questions}) != MAX_QUESTIONS
    ):
        raise ValueError("서로 다른 카테고리 220문항과 별도 검증 30문항이 필요합니다.")
    pages, vectors = load_index()
    rules = RoutingRuleStore(artifact_path(args.rules_db)).snapshot(args.rule_version)
    lookup = load_target_lookup(pages, rules=rules)
    storage = load_storage_lookup(pages)
    config = experiment_config()
    config.pop("external_api_calls")
    config.update(
        interpreter={
            "model": MODEL_ID,
            "reasoning_effort": REASONING_EFFORT,
            "prompt_sha256": PROMPT_SHA256,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "maximum_calls": MAX_QUESTIONS,
            "workers": WORKERS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "budget_usd": None,
            "retries": 0,
        },
        rules={"version": rules.version_id, "sha256": rules.content_sha256},
        question_inputs={split: evaluation.stamp(q) for split, (q, _) in files.items()},
        holdout_manifest=evaluation.stamp(HOLDOUT / "manifest.json"),
    )
    evaluation.write_json(output / "config.json", config)
    (output / "prompt.txt").write_text(DOMAIN_INSTRUCTIONS, encoding="utf-8")
    interpreter = LunaInterpreter()
    search = evaluation.Search(pages, vectors)
    retrievals, traces = {}, {}
    search_seconds = 0.0
    start = perf_counter()
    log("질문 해석 비교 시작 · 카테고리 220문항 · 별도 표본 30문항 · 답변 생성 0회")
    with ThreadPoolExecutor(max_workers=1) as dispatcher:
        future = dispatcher.submit(
            run_interpretations, questions, lookup, interpreter, output, log
        )
        prepared = []
        for batch in [*evaluation.batches(categories), holdout]:
            started = perf_counter()
            pools, heads = search.rank(batch, {"shared": vectors}, log)
            search_seconds += perf_counter() - started
            prepared.extend(
                (row, pool, retain_reranked_head(pool, head))
                for row, pool, head in zip(
                    batch, pools["shared"], heads["shared"], strict=True
                )
            )
        decisions = future.result()
    for question, pool, order in prepared:
        qid, text = question["id"], question["question"]
        started = perf_counter()
        baseline, linked = evaluation.finish(text, pool, order, lookup, storage)
        baseline_ms = (perf_counter() - started) * 1000
        status, targets = applied_rule_targets(text, baseline, linked, lookup)
        retrievals[qid, "rules"] = baseline
        traces[qid, "rules"] = {
            "status": status,
            "targets": targets,
            "decision_and_pack_ms": baseline_ms,
        }
        decision, telemetry, decision_ms = decisions[qid]
        model_targets = target_rows(decision.targets_by_case)
        applied = decision.targets and decision.status in (
            "verified",
            "partial",
            "ambiguous",
        )
        if applied:
            started = perf_counter()
            selected, context, route = pack_decision(text, order, lookup, decision)
            pack_ms = (perf_counter() - started) * 1000
            result = (
                pool,
                selected,
                context,
                tuple(decision.targets_by_case),
                f"structured/{route}",
            )
        else:
            result, pack_ms = baseline, baseline_ms
        retrievals[qid, "luna"] = result
        traces[qid, "luna"] = {
            "status": decision.status,
            "reason": decision.reason,
            "route_hint": decision.route,
            "targets": model_targets if applied else targets,
            "model_targets": model_targets if decision.provider_called else None,
            "applied_status": decision.status if applied else status,
            "used_rules_fallback": not bool(applied),
            "provider_called": decision.provider_called,
            "telemetry": telemetry,
            "decision_and_pack_ms": decision_ms + pack_ms,
        }
    evaluation.save_rankings(output, retrievals)
    write_rows(
        output / "traces.jsonl",
        ({"id": qid, "arm": arm, **trace} for (qid, arm), trace in traces.items()),
    )
    log("후보 순위·최종 문맥 지문 저장 · 참고 근거 채점 시작")
    config["references"] = {
        split: evaluation.stamp(r) for split, (_, r) in files.items()
    }
    config["external_api_calls"] = sum(
        t["provider_called"] for (qid, arm), t in traces.items() if arm == "luna"
    )
    evaluation.write_json(output / "config.json", config)
    from experimental.u_10o0o.advanced_luna_metrics import score_luna

    results = score_luna(output, questions, files, retrievals, traces, lookup)
    evaluation.write_json(
        output / "results.json",
        {
            "config": config,
            "scores": results,
            "shared_search": {
                "queries": search.searches,
                "reranked_queries": search.reranks,
                "batch_wall_seconds": search_seconds,
            },
            "luna_statuses": dict(Counter(d[0].status for d in decisions.values())),
            "provider": provider_summary(decisions),
            "total_wall_seconds": perf_counter() - start,
        },
    )
    log(f"검색 평가 완료 · GPT-6 Luna {config['external_api_calls']}회 · 답변 생성 0회")
