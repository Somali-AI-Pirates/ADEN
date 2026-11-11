"""Run the two Advanced retrieval arms as paired live requests on a frozen holdout."""

import argparse
import json
from collections import Counter
from math import comb
from pathlib import Path
from time import perf_counter

from openai import OpenAI

from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_experiment import experiment_config, start_run
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_luna import HGLuna, PROMPT_SHA256, SCHEMA_SHA256
from experimental.u_10o0o.advanced_hg_partition import attach_verified_sources
from experimental.u_10o0o.advanced_hg_relations import (
    HG_RELATIONS_SNAPSHOT,
    HGRelations,
)
from experimental.u_10o0o.advanced_hg_route import decide
from experimental.u_10o0o.advanced_plan_ab import ARMS, parallel_search, recognition
from experimental.u_10o0o.advanced_query_plan import (
    API_BASE_URL,
    MODEL_ID,
    PROMPT,
    REASONING_EFFORT,
    ROLE_NAMES,
    Plan,
    request_plan,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.search import load_index

FATAL_HTTP = {400, 401, 403, 404, 429}
STAGES = ("candidate", "expanded", "final")


def run_arm(row, arm, router, client, search, lookup, storage, relations):
    question = row["question"]
    started = perf_counter()
    call = router.invoke(question)
    router_ms = (perf_counter() - started) * 1000
    if call.telemetry.api_status_code in FATAL_HTTP:
        raise RuntimeError(f"HG 라우팅 중단 ({call.telemetry.api_status_code})")
    record, plan, planner_ms = None, None, 0.0
    if arm == "parallel":
        plan_started = perf_counter()
        record = request_plan(client, row)
        planner_ms = (perf_counter() - plan_started) * 1000
        if record["status"] in {f"http_{status}" for status in FATAL_HTTP}:
            raise RuntimeError(f"계획 수집 중단 ({record['status']})")
        if record["plan"]:
            plan = Plan.model_validate(record["plan"])

    decision = decide(
        question, lookup, relations, call.proposal, tool_requested=call.tool_requested
    )
    verified = {
        case.case_id: dict(zip(ROLES, relations.pages_for(case.case_id), strict=True))
        for case in decision.cases
    }
    pool, order = ([], []) if decision.path == "lookup" else search.one(question)
    roles = {}
    if decision.path in {"lookup", "mixed"}:
        roles = {
            case.case_id: ROLES if len(verified) == 1 else (case.roles or ROLES)
            for case in decision.cases
        }
        expanded, context, route = attach_verified_sources(
            question,
            pool,
            order,
            lookup,
            verified,
            requested_roles=roles,
            mixed=decision.path == "mixed",
            allow_deferred_search_pages=True,
        )
        original = pool, expanded, context, tuple(verified), f"{decision.path}/{route}"
    else:
        original, _ = evaluation.finish(question, pool, order, lookup, storage)
    extra_queries, extra_groups, extra_pairs = 0, 0, 0
    result, steps = original, []
    if arm == "parallel" and decision.path != "lookup":
        result, steps, (extra_queries, extra_groups, extra_pairs) = parallel_search(
            row,
            original,
            plan,
            search,
            lookup,
            storage,
            relations,
            verified,
            decision.path,
        )
    total_ms = (perf_counter() - started) * 1000

    predictions = recognition(
        question, plan, steps, original, verified, lookup, relations
    )
    trace = {
        "id": row["id"],
        "split": row["split"],
        "arm": arm,
        "router_status": call.status,
        "route": decision.path,
        "route_reason": decision.reason,
        "resolved_case_count": len(verified),
        "plan_status": record["status"] if record else None,
        "plan_tasks": len(plan.tasks) if plan else 0,
        "executed_tasks": len(steps),
        "router_calls": int(call.telemetry.attempted),
        "plan_calls": int(record is not None),
        "searches": int(bool(pool)) + extra_queries,
        "rerank_groups": int(bool(pool)) + extra_groups,
        "rerank_pairs": len(pool) + extra_pairs,
        "router_ms": router_ms,
        "planner_ms": planner_ms,
        "local_ms": total_ms - router_ms - planner_ms,
        "total_ms": total_ms,
        "live_context_sha256": result[2].context_hash,
    }
    return (
        result,
        trace,
        predictions[ARMS.index(arm)],
        record,
        (decision.path if verified else "full_fallback", tuple(roles.items())),
        (original, verified),
    )


def run(data, destination):
    manifest = json.loads((data / "manifest.json").read_bytes())
    questions = json.loads((data / "questions.json").read_bytes())
    for name in ("questions.json", "references.json", "gold-review.json"):
        if evaluation.stamp(data / name)["sha256"] != manifest[name]["sha256"]:
            raise ValueError(f"고정 입력의 지문이 달라졌습니다. {name}")
    splits = Counter(row["split"] for row in questions)
    if (
        splits != {"holdout_single": 12, "holdout_complex": 12}
        or len({r["id"] for r in questions}) != 24
    ):
        raise ValueError("고정 표본의 문항 수 또는 ID를 확인하세요.")
    if any(set(row) != {"id", "split", "question"} for row in questions):
        raise ValueError("검색 입력에는 질문 ID, 묶음, 질문만 허용합니다.")

    output, log = start_run(destination)
    config = experiment_config()
    config.update(
        arms=ARMS,
        dataset=evaluation.stamp(data / "manifest.json"),
        relation_snapshot=evaluation.stamp(HG_RELATIONS_SNAPSHOT / "manifest.json"),
        planner={
            "model": MODEL_ID,
            "reasoning_effort": REASONING_EFFORT,
            "prompt": PROMPT,
            "schema": Plan.model_json_schema(),
        },
        router={"prompt_sha256": PROMPT_SHA256, "schema_sha256": SCHEMA_SHA256},
        max_new_api_requests=72,
        max_retries=0,
        concurrency=1,
        order="baseline first for odd positions, parallel first for even positions",
    )
    evaluation.write_json(output / "config.json", config)
    pages, vectors = load_index()
    search = evaluation.Search(pages, vectors)
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search.one(questions[0]["question"])
    log("로컬 검색 워밍업 완료 · 실제 요청 비교 시작")
    results, traces, predictions, route_agreement = {}, [], {}, {}
    router = HGLuna()
    with (
        router.client,
        OpenAI(base_url=API_BASE_URL, max_retries=0, timeout=25) as client,
        HGRelations(pages=pages) as relations,
        (output / "live_calls.jsonl").open(
            "x", encoding="utf-8", buffering=1
        ) as stream,
        (output / "local_plans.jsonl").open(
            "x", encoding="utf-8", buffering=1
        ) as plans,
    ):
        for i, row in enumerate(questions, 1):
            qid, routes, inputs = row["id"], {}, {}
            for arm in ARMS if i % 2 else ARMS[::-1]:
                result, trace, prediction, plan, route, original = run_arm(
                    row, arm, router, client, search, lookup, storage, relations
                )
                trace["first_arm"] = ARMS[0] if i % 2 else ARMS[1]
                stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
                if plan is not None:
                    plans.write(json.dumps(plan, ensure_ascii=False) + "\n")
                results[qid, arm], predictions[qid, arm] = result, prediction
                routes[arm], inputs[arm] = route, (original, plan)
                traces.append(trace)
            route_agreement[qid] = routes["baseline"] == routes["parallel"]
            if not route_agreement[qid]:
                (original, verified), _ = inputs["baseline"]
                _, plan_record = inputs["parallel"]
                path = routes["baseline"][0]
                plan = (
                    Plan.model_validate(plan_record["plan"])
                    if plan_record["plan"]
                    else None
                )
                result, steps = original, []
                if path != "lookup":
                    result, steps, _ = parallel_search(
                        row,
                        original,
                        plan,
                        search,
                        lookup,
                        storage,
                        relations,
                        verified,
                        path,
                    )
                results[qid, "parallel"] = result
                _, predictions[qid, "parallel"] = recognition(
                    row["question"], plan, steps, original, verified, lookup, relations
                )
            if i == 1:
                pilot = next(t for t in traces if t["arm"] == "parallel")
                if pilot["plan_status"] != "completed":
                    raise RuntimeError("첫 문항의 계획 응답을 확인할 수 없습니다.")
            if i == 1 or i % 4 == 0:
                log(f"비교 완료 {i}/{len(questions)}문항")
    config["external_api_calls"] = sum(
        t["router_calls"] + t["plan_calls"] for t in traces
    )
    evaluation.write_json(output / "config.json", config)
    evaluation.save_rankings(output, results)
    refs = json.loads((data / "references.json").read_bytes())
    points = score(questions, refs, results, predictions, route_agreement)
    write_rows(output / "scores.jsonl", points)
    evaluation.write_json(output / "results.json", summarize(points, traces))
    log("순위 저장 후 참고 근거 채점 완료")


def score(questions, refs, results, predictions, route_agreement):
    rows = []
    for row in questions:
        qid = row["id"]
        requests = refs[qid]
        reference = {
            "evidence_requirements": [
                req for task in requests for req in task["requirements"]
            ],
        }
        reference["evidence_groups"] = [
            req["primary"] for req in reference["evidence_requirements"]
        ]
        expected = {
            (task["hg_case"], ROLE_NAMES[task["hg_role"]])
            for task in requests
            if task["hg_case"]
        }
        targets = {case for case, _ in expected}
        scores = {}
        for arm in ARMS:
            result = results[qid, arm]
            metrics = evaluation.score_one(reference, result)
            cases, roles = predictions[qid, arm]
            if expected:
                metrics.update(
                    target_exact=int(cases == targets),
                    target_found=len(cases & targets),
                    target_expected=len(targets),
                    target_extra=len(cases - targets),
                    role_exact=int(roles == expected),
                    role_found=len(roles & expected),
                    role_expected=len(expected),
                    role_extra=len(roles - expected),
                )
            materials = (
                [p for p, _ in result[0]],
                [p for p, _ in result[1]],
                result[2],
            )
            for stage, material in zip(STAGES, materials, strict=True):
                metrics[f"{stage}_requests"] = sum(
                    fact_coverage(
                        {"evidence_requirements": task["requirements"]}, material
                    )["all_facts"]
                    for task in requests
                )
            metrics["requests"] = len(requests)
            scores[arm] = metrics
        rows.append(
            {
                "id": qid,
                "split": row["split"],
                "arms": scores,
                "route_agreement": route_agreement[qid],
                "hg_scored": bool(expected),
            }
        )
    return rows


def summarize(points, traces):
    groups = {
        "all": points,
        "holdout_single": [p for p in points if p["split"] == "holdout_single"],
        "holdout_complex": [p for p in points if p["split"] == "holdout_complex"],
        "same_route": [p for p in points if p["route_agreement"]],
    }
    results = {}
    for name, rows in groups.items():
        ids = {row["id"] for row in rows}
        changes = {"+1": [], "-1": []}
        for row in rows:
            delta = (
                row["arms"]["parallel"]["final_complete"]
                - row["arms"]["baseline"]["final_complete"]
            )
            if delta:
                changes[f"{delta:+}"].append(row["id"])
        wins, losses = len(changes["+1"]), len(changes["-1"])
        paired_p = min(
            1.0,
            2
            * sum(comb(wins + losses, i) for i in range(min(wins, losses) + 1))
            / 2 ** (wins + losses),
        )
        arms = {}
        for arm in ARMS:
            timings = [t for t in traces if t["id"] in ids and t["arm"] == arm]
            arms[arm] = {
                "evidence": evaluation.summarize([r["arms"][arm] for r in rows]),
                "calls": {
                    key: sum(t[key] for t in timings)
                    for key in (
                        "router_calls",
                        "plan_calls",
                        "searches",
                        "rerank_groups",
                        "rerank_pairs",
                    )
                },
                "latency": {
                    key: evaluation.latency([t[key] for t in timings])
                    for key in ("total_ms", "local_ms", "router_ms", "planner_ms")
                },
                "statuses": {
                    key: dict(Counter(t[key] or "none" for t in timings))
                    for key in ("router_status", "plan_status", "route")
                },
            }
        results[name] = {
            "questions": len(rows),
            "hg_scored": sum(r["hg_scored"] for r in rows),
            "paired_final_ids": changes,
            "paired_exact_p_two_sided": paired_p,
            "arms": arms,
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run(args.data, args.output_dir)


if __name__ == "__main__":
    main()
