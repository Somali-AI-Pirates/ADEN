"""Compare the archived Advanced baseline and Luna parallel retrieval."""

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from openai import OpenAI

from app.evaluation.dataset import load_references
from app.generation.context import build_context
from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clause_binding import roles_in
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_experiment import experiment_config, start_run
from experimental.u_10o0o.advanced_graph_benchmark import ARCHIVE
from experimental.u_10o0o.advanced_hg_fallback_replay import (
    _indexed_rows,
    _restore,
    _verified_sources,
)
from experimental.u_10o0o.advanced_hg_partition import attach_verified_sources
from experimental.u_10o0o.advanced_hg_relations import (
    HG_RELATIONS_SNAPSHOT,
    HGRelations,
)
from experimental.u_10o0o.advanced_hg_lookup import matched_case
from experimental.u_10o0o.advanced_pair_context import pair_requirements
from experimental.u_10o0o.advanced_planned_retrieval import (
    TASK_RERANK_DEPTH,
    case_scope,
    make_archive,
    merge_pool,
    rerank_tasks,
    resolve,
    search_queries,
)
from experimental.u_10o0o.advanced_query_plan import (
    API_BASE_URL,
    MODEL_ID,
    PROMPT,
    REASONING_EFFORT,
    ROLE_NAMES,
    Plan,
    request_plan,
)
from experimental.u_10o0o.advanced_search import (
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    evaluation_files,
    evaluation_questions,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.context_evidence import _count_tokens, _source_key
from experimental.u_10o0o.prepare import PROJECT_ROOT
from experimental.u_10o0o.search import load_index

PREVIOUS = PROJECT_ROOT / "artifacts/u_10o0o/advanced-planned-retrieval-20261109"
RUN = Path("artifacts/u_10o0o/advanced-plan-ab-20261110")
ARMS = ("baseline", "parallel")
SEMANTIC_ARCHIVE = PROJECT_ROOT / "artifacts/u_10o0o/advanced-semantic-queries-20261109"
PEER_ROUTING_ARCHIVE = (
    Path(__file__).with_name("records") / "advanced-hg-partition-peer-20261109"
)
STOP_STATUSES = {"http_401", "http_403", "http_429"}


def files_for_run():
    return {
        **evaluation_files("peer"),
        "semantic_queries": evaluation_files("categories")["semantic_queries"],
    }


def collect(destination, previous=PREVIOUS):
    files = files_for_run()
    questions = evaluation_questions(files)
    if len(questions) != 344 or len({q["id"] for q in questions}) != 344:
        raise ValueError("동료 304문항과 일상 표현 40문항의 ID를 확인하세요.")
    previous_config = json.loads((previous / "planner_config.json").read_bytes())
    fixed = {
        "model": MODEL_ID,
        "reasoning_effort": REASONING_EFFORT,
        "schema": Plan.model_json_schema(),
        "prompt": PROMPT,
    }
    if any(previous_config[key] != value for key, value in fixed.items()):
        raise ValueError("이전 계획의 모델, 지침 또는 스키마가 달라졌습니다.")
    old_questions = {
        row["id"]: row for row in json.loads((previous / "questions.json").read_bytes())
    }
    recorded = _indexed_rows(previous / "luna_plans.jsonl", ("id",))
    reused = {}
    for row in questions:
        qid = row["id"]
        if qid in old_questions and row != old_questions[qid]:
            raise ValueError(f"보관 계획에 해당하는 질문이 달라졌습니다. {qid}")
        if record := recorded.get((qid,)):
            if record["status"] == "completed" and record["plan"]:
                Plan.model_validate(record["plan"])
                reused[qid] = {**record, "origin": "reused"}
    pending = [row for row in questions if row["id"] not in reused]

    output, log = start_run(destination)
    evaluation.write_json(output / "questions.json", questions)
    evaluation.write_json(
        output / "planner_config.json",
        {
            **fixed,
            "question_inputs": {
                split: evaluation.stamp(path) for split, (path, _) in files.items()
            },
            "reused_input": evaluation.stamp(previous / "luna_plans.jsonl"),
            "reused": len(reused),
            "maximum_new_requests": len(pending),
            "budget_limit_usd": None,
            "timeout_seconds": 25,
            "max_retries": 0,
            "request_data": "question and fixed instructions and schema",
        },
    )
    log(f"계획 수집 시작 · 보관 {len(reused)}건 · 새 요청 최대 {len(pending)}건")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    with (
        OpenAI(base_url=API_BASE_URL, max_retries=0, timeout=25) as client,
        (output / "luna_plans.jsonl").open(
            "x", encoding="utf-8", buffering=1
        ) as stream,
    ):

        def record(result):
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")

        for result in reused.values():
            record(result)
        if not pending:
            return
        pilot = request_plan(client, pending[0])
        record({**pilot, "origin": "live"})
        if pilot["plan"] is None:
            raise RuntimeError(f"첫 계획 요청 실패 ({pilot['status']})")
        log(f"계획 형식 확인 · {pilot['seconds']:.2f}초 · 1/{len(pending)}")
        with ThreadPoolExecutor(max_workers=8) as executor:
            for start in range(1, len(pending), 8):
                batch = pending[start : start + 8]
                results = list(
                    executor.map(lambda row: request_plan(client, row), batch)
                )
                for result in results:
                    record({**result, "origin": "live"})
                if any(result["status"] in STOP_STATUSES for result in results):
                    raise RuntimeError(
                        "API 권한 또는 호출 한도 응답으로 계획 수집 중단"
                    )
                if start % 40 == 1 or start + len(batch) == len(pending):
                    log(f"Luna 신규 계획 {start + len(batch)}/{len(pending)}")


def finish(question, pool, order, lookup, storage, verified, route):
    if route == "mixed":
        expanded, context, label = attach_verified_sources(
            question,
            pool,
            order,
            lookup,
            verified,
            requested_roles={case: ROLES for case in verified},
            mixed=True,
            allow_deferred_search_pages=True,
        )
        return pool, expanded, context, tuple(verified), label
    result, _ = evaluation.finish(question, pool, order, lookup, storage)
    return result


def parallel_search(
    row, original, plan, search, lookup, storage, relations, verified, route
):
    question = row["question"]
    steps = resolve(question, plan, "parallel", lookup, relations, None) if plan else []
    queries = list(dict.fromkeys(step.query for step in steps))
    if not queries:
        return original, steps, (0, 0, 0)
    initial = search_queries(search, queries)
    ranks = rerank_tasks(search, initial, queries, lambda _: None)
    task_pairs = sum(min(TASK_RERANK_DEPTH, len(initial[query])) for query in queries)
    pool = merge_pool(original[0], steps, ranks)
    changed = evaluation.sources(pool) != evaluation.sources(original[0])
    if not changed:
        return original, steps, (len(queries), len(queries), task_pairs)
    ranked = rerank_variants(
        search.reranker, [row], {"parallel": [pool]}, lambda _: None
    )["parallel"][0]
    order = retain_reranked_head(pool, ranked)
    result = finish(question, pool, order, lookup, storage, verified, route)
    return result, steps, (len(queries), len(queries) + 1, task_pairs + len(pool))


def compare(source, destination):
    files = files_for_run()
    questions = evaluation_questions(files)
    if json.loads((source / "questions.json").read_bytes()) != questions:
        raise ValueError("계획과 현재 비교의 질문이 다릅니다.")
    plans = {
        qid: row
        for (qid,), row in _indexed_rows(source / "luna_plans.jsonl", ("id",)).items()
    }
    if plans.keys() != {row["id"] for row in questions}:
        raise ValueError("계획 호출 기록이 문항 전체를 덮지 않습니다.")

    output, log = start_run(destination)
    config = experiment_config()
    config.update(
        arms=ARMS,
        plan_records=evaluation.stamp(source / "luna_plans.jsonl"),
        planner=evaluation.stamp(source / "planner_config.json"),
        relation_snapshot=evaluation.stamp(HG_RELATIONS_SNAPSHOT / "manifest.json"),
        question_inputs={split: evaluation.stamp(q) for split, (q, _) in files.items()},
        peer_baseline=evaluation.stamp(ARCHIVE / "peer/rankings.jsonl"),
        semantic_baseline=evaluation.stamp(SEMANTIC_ARCHIVE / "rankings.jsonl"),
        peer_route_timing=evaluation.stamp(PEER_ROUTING_ARCHIVE / "api-traces.jsonl"),
        timing="warm per-question local replay plus historical or live API durations",
    )
    evaluation.write_json(output / "config.json", config)
    pages, vectors = load_index()
    page_map = {_source_key(page): page for page in pages}
    archived, routes, old_scores = make_archive(questions, page_map)
    semantic = _indexed_rows(SEMANTIC_ARCHIVE / "rankings.jsonl", ("id", "arm"))
    archived.update(
        {
            qid: _restore(row, page_map)
            for (qid, arm), row in semantic.items()
            if arm == "rrf_55"
        }
    )
    peer_api = _indexed_rows(PEER_ROUTING_ARCHIVE / "api-traces.jsonl", ("id",))
    if len(archived) != len(questions) or len(peer_api) != 304:
        raise ValueError("기준선 출처와 라우팅 기록 범위를 확인하세요.")
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search = evaluation.Search(pages, vectors)
    warmup = [row for row in questions if archived[row["id"]][0]][:3]
    for row in warmup:
        search.one(row["question"])
    log("기준선 문맥 복원 및 검색 워밍업 완료")
    results, traces, predictions = {}, [], {}
    with HGRelations(pages=pages) as relations:
        for count, row in enumerate(questions, 1):
            qid, question = row["id"], row["question"]
            original, logged = archived[qid], routes.get(qid, {})
            route = logged.get("route", "full_fallback")
            record = plans[qid]
            plan = Plan.model_validate(record["plan"]) if record["plan"] else None
            verified = {}
            start = perf_counter()
            if route in {"lookup", "mixed"}:
                verified, _ = _verified_sources(logged, lookup, relations)
            if route == "lookup":
                packed = build_context(
                    original[2].final_chunks,
                    FINAL_K,
                    DEFAULT_CONTEXT_TOKENS,
                    _count_tokens,
                )
                if packed.context_hash != original[2].context_hash:
                    raise ValueError(f"기존 DB 문맥 지문이 달라졌습니다. {qid}")
                replayed = original
            else:
                pool, order = search.one(question)
                replayed = finish(
                    question, pool, order, lookup, storage, verified, route
                )
            baseline_ms = (perf_counter() - start) * 1000
            start = perf_counter()
            if route == "lookup":
                parallel, steps, calls = original, [], (0, 0, 0)
            else:
                parallel, steps, calls = parallel_search(
                    row,
                    original,
                    plan,
                    search,
                    lookup,
                    storage,
                    relations,
                    verified,
                    route,
                )
            added_ms = (perf_counter() - start) * 1000
            results[qid, "baseline"] = original
            results[qid, "parallel"] = parallel
            traces.append(
                {
                    "id": qid,
                    "split": row["split"],
                    "route": route,
                    "root_candidates_reproduced": evaluation.sources(replayed[0])
                    == evaluation.sources(original[0]),
                    "root_context_reproduced": replayed[2].context_hash
                    == original[2].context_hash,
                    "plan_origin": record["origin"],
                    "plan_status": record["status"],
                    "planned_tasks": len(plan.tasks) if plan else 0,
                    "executed_tasks": len(steps),
                    "baseline_queries": int(route != "lookup"),
                    "parallel_added_queries": calls[0],
                    "parallel_added_rerank_groups": calls[1],
                    "baseline_rerank_pairs": len(replayed[0]),
                    "parallel_added_rerank_pairs": calls[2],
                    "plan_anchors_in_question": sum(
                        s.anchor in question for s in plan.tasks
                    )
                    if plan
                    else 0,
                    "baseline_local_ms": baseline_ms,
                    "parallel_extra_local_ms": added_ms,
                    "router_api_ms": peer_api.get((qid,), {})
                    .get("telemetry", {})
                    .get("latency_ms"),
                    "planner_api_ms": record["seconds"] * 1000,
                }
            )
            predictions[qid] = recognition(
                question, plan, steps, replayed, verified, lookup, relations
            )
            if count % 25 == 0 or count == len(questions):
                log(f"검색 비교 {count}/{len(questions)}")

    evaluation.save_rankings(output, results)
    write_rows(output / "traces.jsonl", traces)
    score_run(
        output,
        config,
        files,
        questions,
        results,
        traces,
        predictions,
        lookup,
        old_scores,
    )
    log("검색 결과 저장 및 오프라인 채점 완료")


def recognition(question, plan, steps, baseline, verified, lookup, relations):
    """Trace comparable HG keys and record roles without reading reference labels."""
    cases = set(verified)
    route = baseline[4].split("/")
    if not verified:
        if route[1:2] in (["unique"], ["pair"]):
            cases.update(baseline[3])
        if route[0] == "explicit_hg":
            cases.add(matched_case(question, lookup.cases))
        if route[0] == "indirect_hg":
            cases.add(lookup.profile_index.candidate(question)[0])
    cases.discard(None)
    requested = pair_requirements(question, lookup) if len(cases) > 1 else {}
    roles = {
        (case, role)
        for case in cases
        for role in (requested.get(case, ()) if len(cases) > 1 else roles_in(question))
    }
    parallel_cases, parallel_roles = set(cases), set(roles)
    for step, executed in zip(plan.tasks if plan and steps else [], steps, strict=True):
        if (
            step.kind != "case"
            or step.anchor not in question
            or executed.query != step.query
        ):
            continue
        for case in case_scope(step.anchor, step, lookup, relations, None):
            parallel_cases.add(case)
            parallel_roles.update((case, ROLE_NAMES[role]) for role in step.roles)
    return (cases, roles), (parallel_cases, parallel_roles)


def score_recognition(ref, lookup, predictions):
    if ref.get("expected_action") != "answer":
        return None
    # Only count groups whose citation alternatives imply the same HG case/role.
    gold_roles = set()
    for group in ref["evidence_groups"]:
        pairs = {lookup.page_roles.get(_source_key(cite)) for cite in group}
        if len(pairs) == 1 and None not in pairs:
            gold_roles.update(pairs)
    if not gold_roles:
        return None
    gold_cases = {case for case, _ in gold_roles}
    return {
        arm: {
            "questions": 1,
            "targets_expected": len(gold_cases),
            "targets_found": len(cases & gold_cases),
            "targets_extra": len(cases - gold_cases),
            "targets_exact": int(cases == gold_cases),
            "roles_expected": len(gold_roles),
            "roles_found": len(roles & gold_roles),
            "roles_extra": len(roles - gold_roles),
            "roles_exact": int(roles == gold_roles),
        }
        for arm, (cases, roles) in zip(ARMS, predictions, strict=True)
    }


def summarize(rows):
    result = {
        arm: evaluation.summarize([row["arms"][arm] for row in rows]) for arm in ARMS
    }
    result["changes"] = {
        stage: dict(
            Counter(
                row["arms"]["parallel"][f"{stage}_complete"]
                - row["arms"]["baseline"][f"{stage}_complete"]
                for row in rows
            )
        )
        for stage in ("candidate", "expanded", "final")
    }
    result["recognition"] = {
        arm: dict(
            sum(
                (
                    Counter(row["hg_recognition"][arm])
                    for row in rows
                    if row["hg_recognition"]
                ),
                Counter(),
            )
        )
        for arm in ARMS
    }
    return result


def summarize_timings(traces):
    elapsed = evaluation.latency

    def shared(row):
        return row["router_api_ms"] or 0

    def added(row):
        return row["parallel_extra_local_ms"]

    return {
        "router_api": elapsed(
            [r["router_api_ms"] for r in traces if r["router_api_ms"] is not None]
        ),
        "planner_api": elapsed([r["planner_api_ms"] for r in traces]),
        "baseline_local": elapsed([r["baseline_local_ms"] for r in traces]),
        "parallel_local": elapsed([r["baseline_local_ms"] + added(r) for r in traces]),
        "baseline_combined": elapsed(
            [r["baseline_local_ms"] + shared(r) for r in traces]
        ),
        "parallel_combined": elapsed(
            [
                r["baseline_local_ms"] + added(r) + shared(r) + r["planner_api_ms"]
                for r in traces
            ]
        ),
        "calls": {
            "shared_router_archived": sum(
                r["router_api_ms"] is not None for r in traces
            ),
            "planner_reused": sum(r["plan_origin"] == "reused" for r in traces),
            "planner_live": sum(r["plan_origin"] == "live" for r in traces),
            "planner_status": dict(Counter(r["plan_status"] for r in traces)),
            **{
                key: sum(r[key] for r in traces)
                for key in (
                    "baseline_queries",
                    "parallel_added_queries",
                    "baseline_rerank_pairs",
                    "parallel_added_rerank_groups",
                    "parallel_added_rerank_pairs",
                    "planned_tasks",
                    "executed_tasks",
                    "plan_anchors_in_question",
                )
            },
        },
        "root_context_reproduced": sum(r["root_context_reproduced"] for r in traces),
        "root_candidates_reproduced": sum(
            r["root_candidates_reproduced"] for r in traces
        ),
    }


def score_run(
    output, config, files, questions, results, traces, predictions, lookup, old_scores
):
    references = {split: load_references(ref) for split, (_, ref) in files.items()}
    config["references"] = {
        split: evaluation.stamp(ref) for split, (_, ref) in files.items()
    }
    evaluation.write_json(output / "config.json", config)
    old_semantic = _indexed_rows(SEMANTIC_ARCHIVE / "scores.jsonl", ("id",))
    trace_by_id = {r["id"]: r for r in traces}
    cohorts = defaultdict(list)
    score_rows = []
    for row in questions:
        qid, split = row["id"], row["split"]
        peer = split.startswith("peer_")
        ref = references[split][qid]
        scores = {
            arm: evaluation.score_one(ref, results[qid, arm], peer=peer) for arm in ARMS
        }
        if peer and scores["baseline"] != old_scores[qid]:
            raise ValueError(f"동료 기준선 점수가 다릅니다. {qid}")
        if not peer and any(
            scores["baseline"][f"{stage}_complete"]
            != old_semantic[qid,]["arms"]["rrf_55"][f"{stage}_complete"]
            for stage in ("candidate", "expanded", "final")
        ):
            raise ValueError(f"일상 표현 기준선 점수가 다릅니다. {qid}")
        point = {
            "id": qid,
            "split": split,
            "arms": scores,
            "hg_recognition": score_recognition(ref, lookup, predictions[qid]),
        }
        score_rows.append(point)
        name = "peer" if peer else "semantic"
        cohorts[name].append(point)
        variant = split if peer else ref["variant"]
        cohorts[variant].append(point)
        if peer:
            cohorts[f"peer_plan_{trace_by_id[qid]['plan_origin']}"].append(point)
            if (
                split == "peer_multi_doc"
                and len(
                    {
                        cite["book_id"]
                        for group in ref["evidence_groups"]
                        for cite in group
                    }
                )
                == 4
            ):
                cohorts["peer_four_doc"].append(point)
    write_rows(output / "scores.jsonl", score_rows)
    evaluation.write_json(
        output / "results.json",
        {
            "cohorts": {name: summarize(rows) for name, rows in cohorts.items()},
            "timing": {
                name: summarize_timings([trace_by_id[r["id"]] for r in rows])
                for name, rows in cohorts.items()
            },
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("collect", "compare"))
    parser.add_argument("--run-dir", type=Path, default=RUN)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.stage == "collect":
        collect(args.run_dir)
    else:
        compare(args.run_dir, args.output_dir or args.run_dir / "retrieval")


if __name__ == "__main__":
    main()
