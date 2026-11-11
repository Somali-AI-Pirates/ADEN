"""Run paired routing cases with deterministic, local interpretation responses."""

import json
from collections import Counter
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o import advanced_question_router
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_question_router import (
    fast_lookup,
    pack_decision,
    route_question,
)
from experimental.u_10o0o.advanced_routing_rules import RoutingRuleStore
from experimental.u_10o0o.advanced_snapshots import HGStore
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.search import load_index


class FixtureInterpreter:
    def __init__(self, row):
        self.question = row["question"]
        self.response = row["response"]
        self.calls = 0

    def invoke(self, question, *, timeout_seconds, rules_version=None):
        if question != self.question:
            raise ValueError("가짜 해석기는 원래 질문만 받습니다.")
        self.calls += 1
        return self.response


def target(anchor, field, raw, *roles):
    return {
        "anchor": anchor,
        "constraints": [{"field": field, "raw": raw, "polarity": "include"}],
        "roles": list(roles),
    }


def demo_fixtures(lookup):
    first, second = sorted(lookup.cases)[:2]
    direct = f"예약번호 {first}의 {ROLES[3]}를 보여줘"
    mixed = f"예약번호 {first}의 {ROLES[3]}를 보고 적용 예외도 설명해줘"
    left, right = f"{first} 현장 기록", f"{second} 청구 기록"
    return [
        {
            "id": "routing-direct",
            "question": direct,
            "response": {
                "route": "lookup",
                "targets": [target(direct, "case_id", first, ROLES[3])],
            },
            "expected_roles": [[first, ROLES[3]]],
            "expected_provider_calls": 0,
            "expect_direct": True,
        },
        {
            "id": "routing-mixed",
            "question": mixed,
            "response": {
                "route": "mixed",
                "targets": [target(mixed, "case_id", first, ROLES[3])],
            },
            "expected_roles": [[first, ROLES[3]]],
            "expected_provider_calls": 1,
            "expect_direct": False,
        },
        {
            "id": "routing-two",
            "question": f"{left}과 {right}을 각각 보여줘",
            "response": {
                "route": "lookup",
                "targets": [
                    target(left, "case_id", first, ROLES[1]),
                    target(right, "case_id", second, ROLES[3]),
                ],
            },
            "expected_roles": [[first, ROLES[1]], [second, ROLES[3]]],
            "expected_provider_calls": 1,
            "expect_direct": False,
        },
        {
            "id": "routing-documents",
            "question": "냉동 장비 반납 접수에 필요한 서류는 뭐야?",
            "response": {"route": "documents", "targets": []},
            "expected_roles": [],
            "expected_provider_calls": 1,
            "expect_direct": False,
        },
        {
            "id": "routing-invalid",
            "question": "저쪽 건은 현장에 접수됐어?",
            "response": {"route": "made_up", "targets": []},
            "expected_roles": [],
            "expected_status": "interpretation_failure",
            "expected_provider_calls": 1,
            "expect_direct": False,
        },
    ]


def load_fixtures(path, lookup):
    rows = json.loads(path.read_bytes()) if path else demo_fixtures(lookup)
    if not isinstance(rows, list) or not rows:
        raise ValueError("가짜 해석 입력은 비어 있지 않은 JSON 목록이어야 합니다.")
    ids = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"].strip()
            or row["id"] in ids
            or not isinstance(row.get("question"), str)
            or not row["question"].strip()
            or "response" not in row
            or ("expect_direct" in row and type(row["expect_direct"]) is not bool)
        ):
            raise ValueError(
                "가짜 해석 입력에 고유 id, question, response가 필요합니다."
            )
        ids.add(row["id"])
    return rows


def apply_interpretation(text, base, order, lookup, fake):
    decision = route_question(text, lookup, fake, rules=lookup.rules)
    info = {
        "interpretation": decision.status,
        "interpretation_reason": decision.reason,
        "route_hint": decision.route,
        "candidate_cases_count": len(decision.candidate_case_ids),
        "targets": [
            {"case": target.case_id, "roles": list(target.roles)}
            for target in decision.targets
        ],
    }
    if not decision.targets:
        return base, info
    selected, context, route = pack_decision(text, order, lookup, decision)
    pool, *_ = base
    cases = tuple(target.case_id for target in decision.targets)
    return (pool, selected, context, cases, f"structured/{route}"), info


def measure(row, search, lookup, storage, *, interpret, fast=None, prepared=None):
    fake = FixtureInterpreter(row)
    text = row["question"]
    started = perf_counter()
    before_search, before_rerank = search.searches, search.reranks
    direct = (
        fast_lookup(text, lookup, fast.pages_for, rules=lookup.rules) if fast else None
    )
    if direct is not None and direct.status == "storage_error":
        raise ValueError("HG 조회 우선 실험에서 저장소 오류가 발생했습니다.")
    if direct is not None and direct.context is not None:
        result = ([], direct.order, direct.context, (direct.case_id,), "sqlite_direct")
        detail = {
            "interpretation": "bypassed",
            "targets": [{"case": direct.case_id, "roles": list(direct.roles)}],
        }
    else:
        pool, order = prepared if prepared is not None else search.one(text)
        result, _ = evaluation.finish(text, pool, order, lookup, storage)
        detail = {"interpretation": "legacy_rules", "targets": []}
        if interpret:
            result, detail = apply_interpretation(text, result, order, lookup, fake)
    if direct is not None:
        detail["fast_lookup_status"] = direct.status
        detail["fast_lookup_reason"] = direct.reason
    detail.update(
        search_calls=search.searches - before_search,
        rerank_calls=search.reranks - before_rerank,
        shared_search=prepared is not None,
        provider_calls=fake.calls,
        elapsed_ms=(perf_counter() - started) * 1000,
        final_pages=len(result[2].final_chunks),
    )
    packed = {
        lookup.page_roles.get(source) for source in evaluation.packed_sources(result[2])
    }
    requested = {
        (target["case"], role)
        for target in detail["targets"]
        for role in target["roles"]
    }
    detail["packed_verified_roles"] = len(requested & packed)
    detail["requested_verified_roles"] = len(requested)
    detail["evidence_status"] = "not_requested"
    if requested:
        detail["evidence_status"] = (
            "covered" if requested <= packed else "insufficient_source"
        )
    return result, detail


def check_fixtures(fixtures, traces, retrievals, arm):
    validated = Counter()
    for row in fixtures:
        qid = row["id"]
        key = qid, arm
        detail = traces[key]
        if "expected_roles" in row:
            expected = {tuple(pair) for pair in row["expected_roles"]}
            actual = {
                (target["case"], role)
                for target in detail["targets"]
                for role in target["roles"]
            }
            if expected != actual or detail["packed_verified_roles"] != len(expected):
                raise ValueError(
                    f"{qid}의 대상별 역할 또는 최종 문맥이 예상과 다릅니다."
                )
            validated["role_cases"] += 1
        if (
            "expected_status" in row
            and detail["interpretation"] != row["expected_status"]
        ):
            raise ValueError(f"{qid}의 해석 실패 상태가 예상과 다릅니다.")
        if (
            "expected_provider_calls" in row
            and detail["provider_calls"] != row["expected_provider_calls"]
        ):
            raise ValueError(f"{qid}의 가짜 해석 호출 수가 예상과 다릅니다.")
        if arm == "lookup-first" and "expect_direct" in row:
            direct = retrievals[key][-1] == "sqlite_direct"
            expected_calls = int(not direct)
            if direct != row["expect_direct"] or any(
                detail[name] != expected_calls
                for name in ("search_calls", "rerank_calls")
            ):
                raise ValueError(
                    f"{qid}의 조회 우선 경로 또는 검색 호출 수가 예상과 다릅니다."
                )
            validated["direct_skips" if direct else "search_fallbacks"] += 1
    return dict(validated)


def evidence_scores(fixtures, retrievals, lookup, arms):
    rows = []
    for fixture in fixtures:
        if "expected_roles" not in fixture:
            continue
        expected = {tuple(pair) for pair in fixture["expected_roles"]}
        metrics = {}
        for arm in arms:
            pool, expanded, context, _, _ = retrievals[fixture["id"], arm]
            metrics[arm] = {
                stage: len(expected & {lookup.page_roles.get(page) for page in pages})
                for stage, pages in (
                    ("candidate_roles", evaluation.sources(pool)),
                    ("expanded_roles", evaluation.sources(expanded)),
                    ("final_roles", evaluation.packed_sources(context)),
                )
            }
        rows.append(
            {"id": fixture["id"], "required_roles": len(expected), "arms": metrics}
        )
    return rows


def compare_routes(args, output, log):
    from experimental.u_10o0o.advanced_experiment import (
        artifact_path,
        experiment_config,
    )

    pages, vectors = load_index()
    matcher = RoutingRuleStore(artifact_path(args.rules_db)).snapshot(args.rule_version)
    lookup = load_target_lookup(pages, rules=matcher)
    storage = load_storage_lookup(pages)
    fixtures = load_fixtures(args.fixtures, lookup)
    search = evaluation.Search(pages, vectors)
    retrievals, traces = {}, {}
    shared_ms = []
    with ExitStack() as stack:
        hg = None
        if args.kind == "lookup-first":
            hg = stack.enter_context(HGStore(artifact_path(args.hg_snapshot), pages))
        search.one(fixtures[0]["question"])
        log(f"{args.kind} 가짜 해석 비교 · {len(fixtures)}문항")
        for row in fixtures:
            if hg is None:
                started = perf_counter()
                prepared = search.one(row["question"])
                shared_ms.append((perf_counter() - started) * 1000)
                baseline = measure(
                    row, search, lookup, storage, interpret=False, prepared=prepared
                )
                variant = measure(
                    row, search, lookup, storage, interpret=True, prepared=prepared
                )
            else:
                variant = measure(row, search, lookup, storage, interpret=True, fast=hg)
                baseline = measure(row, search, lookup, storage, interpret=True)
            for arm, (result, detail) in (("baseline", baseline), (args.kind, variant)):
                key = row["id"], arm
                retrievals[key], traces[key] = result, detail
        evaluation.save_rankings(output, retrievals)
        write_rows(
            output / "traces.jsonl",
            ({"id": qid, "arm": arm, **row} for (qid, arm), row in traces.items()),
        )
    validated = check_fixtures(fixtures, traces, retrievals, args.kind)
    scores = evidence_scores(fixtures, retrievals, lookup, ("baseline", args.kind))
    write_rows(output / "scores.jsonl", scores)
    config = experiment_config()
    config["provider"] = "deterministic_fixture"
    config["router"] = evaluation.stamp(Path(advanced_question_router.__file__))
    if hg is not None:
        config["snapshot"] = evaluation.stamp(args.hg_snapshot / "manifest.json")
    config["rules"] = {"version": matcher.version_id, "sha256": matcher.content_sha256}
    config["fixtures_sha256"] = sha256(
        json.dumps(fixtures, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    by_arm = {
        arm: [row for (_, trace_arm), row in traces.items() if trace_arm == arm]
        for arm in ("baseline", args.kind)
    }
    summary = {
        arm: {
            "statuses": dict(Counter(row["interpretation"] for row in rows)),
            "search_calls": sum(row["search_calls"] for row in rows),
            "rerank_calls": sum(row["rerank_calls"] for row in rows),
            "provider_calls": sum(row["provider_calls"] for row in rows),
            "shared_queries": sum(row["shared_search"] for row in rows),
            "elapsed": evaluation.latency([row["elapsed_ms"] for row in rows]),
        }
        for arm, rows in by_arm.items()
    }
    evaluation.write_json(
        output / "results.json",
        {
            "kind": args.kind,
            "fixture_count": len(fixtures),
            "config": config,
            "summary": summary,
            "validated": validated,
            "fixture_role_coverage": {
                "required": sum(row["required_roles"] for row in scores),
                **{
                    arm: {
                        stage: sum(row["arms"][arm][stage] for row in scores)
                        for stage in (
                            "candidate_roles",
                            "expanded_roles",
                            "final_roles",
                        )
                    }
                    for arm in by_arm
                },
            },
            "shared_search": {
                "search_calls": len(shared_ms),
                "rerank_calls": len(shared_ms),
                "elapsed": evaluation.latency(shared_ms),
            },
        },
    )
    log(f"{args.kind} 가짜 해석 경로 검증 완료")
