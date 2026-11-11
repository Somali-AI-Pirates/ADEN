"""Compare Advanced retrieval with HG cases routed out of general search."""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from time import perf_counter

from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_hg_route import decide
from experimental.u_10o0o.advanced_search import (
    OFFICIAL_SPLITS,
    PEER_AMBIGUOUS,
    PEER_SPLITS,
    evaluation_files,
    evaluation_questions,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.search import load_index

WORKERS = 8
FATAL_HTTP = {400, 401, 403, 404, 429}
ARMS = ("baseline", "hg_partition", "hg_full_fallback")


def full_fallback_result(
    question, path, original, order, partition, lookup, source_pages, roles
):
    """Apply the new policy using the unchanged full-corpus baseline retrieval."""
    if path == "lookup":
        return partition, "lookup"
    if source_pages and path == "mixed":
        from experimental.u_10o0o.advanced_hg_partition import attach_verified_sources

        pool = original[0]
        expanded, context, packed_route = attach_verified_sources(
            question,
            pool,
            order,
            lookup,
            source_pages,
            requested_roles=roles,
            mixed=True,
            allow_deferred_search_pages=True,
        )
        return (
            pool,
            expanded,
            context,
            tuple(source_pages),
            f"mixed/full/{packed_route}",
        ), "mixed"
    return original, "full_fallback"


def full_fallback_trace(previous, result, path):
    return {
        **previous,
        "legacy_route": previous["route"],
        "route": path,
        "search_calls": int(path != "lookup"),
        "pool": evaluation.sources(result[0]),
        "packed": evaluation.packed_sources(result[2]),
    }


def run_tool(questions, provider, pilot, check_pilot, output, log):
    """Run one paid attempt per question and reuse the real pilot response."""
    results = {}
    with (output / "api-traces.jsonl").open("w", encoding="utf-8") as stream:

        def record(row, result):
            results[row["id"]] = result
            stream.write(
                json.dumps(
                    {
                        "id": row["id"],
                        "status": result.status,
                        "tool_requested": result.tool_requested,
                        "telemetry": result.telemetry.as_dict(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()

        result = provider.invoke(pilot["question"])
        record(pilot, result)
        status = result.telemetry.as_dict().get("api_status_code")
        if status in FATAL_HTTP:
            raise RuntimeError(f"Luna 호출 중단 (HTTP {status}).")
        if check_pilot(pilot, result) is False:
            log("첫 응답에서 DB 원본 조회 미확정 · 응답을 그대로 본 평가에 포함")
        else:
            log("실제 Luna 함수 호출과 SQLite 원본 조회 확인 · 본 평가에 응답 재사용")
        remaining = iter(row for row in questions if row["id"] != pilot["id"])
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            pending = {
                pool.submit(provider.invoke, row["question"]): row
                for row in islice(remaining, WORKERS)
            }
            while pending:
                future = next(as_completed(pending))
                row = pending.pop(future)
                result = future.result()
                record(row, result)
                status = result.telemetry.as_dict().get("api_status_code")
                if status in FATAL_HTTP:
                    for task, queued in pending.items():
                        if not task.cancel():
                            record(queued, task.result())
                    raise RuntimeError(f"Luna 호출 중단 (HTTP {status}).")
                if queued := next(remaining, None):
                    pending[pool.submit(provider.invoke, queued["question"])] = queued
                if len(results) % 10 == 0:
                    log(f"Luna 해석 · {len(results)}/{len(questions)}문항")
    return results


def _positive_gold(reference):
    return {
        (cite["book_id"], cite["page"])
        for group in reference["evidence_groups"]
        for cite in group
    }


def _cohorts(rows, questions, references, decisions, *, peer_ambiguous=(), arms=ARMS):
    splits = {row["id"]: row["split"] for row in questions}
    peer = all(split in PEER_SPLITS for split in splits.values())
    groups = {
        "peer" if peer else "official": rows,
        "hg_partition_sources": [],
        "other_sources": [],
    }
    for row in rows:
        qid = row["id"]
        split = splits[qid]
        reference = references[split][qid]
        gold = _positive_gold(reference)
        if peer:
            groups.setdefault(split, []).append(row)
            action = reference.get("expected_action")
            if action in {"answer", "clarify", "abstain"}:
                groups.setdefault(
                    "peer_expected_answer"
                    if action == "answer"
                    else "peer_expected_clarify_or_abstain",
                    [],
                ).append(row)
            if split == "peer_two_doc":
                label = (
                    "peer_two_doc_ambiguous"
                    if qid in peer_ambiguous
                    else "peer_two_doc_other"
                )
                groups.setdefault(label, []).append(row)
            four_doc = (
                split == "peer_multi_doc" and len({book for book, _ in gold}) == 4
            )
            groups.setdefault(
                "peer_four_doc" if four_doc else "peer_except_four_doc", []
            ).append(row)
        if gold:
            key = (
                "hg_partition_sources"
                if any("AS057" <= book <= "AS120" for book, _ in gold)
                else "other_sources"
            )
            groups[key].append(row)
    return {
        group: {
            "metrics": {
                arm: evaluation.summarize([row["arms"][arm] for row in members])
                for arm in arms
            },
            "routes": dict(Counter(decisions[row["id"]].path for row in members)),
            "gains": [
                row["id"]
                for row in members
                if row.get("final_delta", {}).get("hg_partition") == 1
            ],
            "losses": [
                row["id"]
                for row in members
                if row.get("final_delta", {}).get("hg_partition") == -1
            ],
        }
        for group, members in groups.items()
    }


def paired_full_fallback(rows):
    comparisons = {}
    for arm in ARMS[:-1]:
        gains, losses, unchanged = [], [], 0
        for row in rows:
            before = row["arms"][arm].get("final_complete")
            after = row["arms"][ARMS[-1]].get("final_complete")
            if before is None or after is None:
                continue
            if after > before:
                gains.append(row["id"])
            elif after < before:
                losses.append(row["id"])
            else:
                unchanged += 1
        comparisons[arm] = {"gains": gains, "losses": losses, "unchanged": unchanged}
    return comparisons


def compare_hg_partition(args, output, log):
    from experimental.u_10o0o.advanced_case_sources import ROLES
    from experimental.u_10o0o.advanced_experiment import (
        artifact_path,
        experiment_config,
    )
    from experimental.u_10o0o.advanced_hg_luna import (
        HGLuna,
        PROMPT_SHA256,
        SCHEMA_SHA256,
    )
    from experimental.u_10o0o.advanced_hg_partition import (
        PartitionSearch,
        attach_verified_sources,
    )
    from experimental.u_10o0o.advanced_hg_relations import HGRelations

    if args.suite == "peer":
        files = evaluation_files("peer")
        expected = 304
    else:
        available = evaluation_files()
        files = {split: available[split] for split in OFFICIAL_SPLITS}
        expected = 60
    questions = evaluation_questions(files)
    if len(questions) != expected or len({row["id"] for row in questions}) != expected:
        raise ValueError(f"{args.suite} 평가에 중복 없는 {expected}문항이 필요합니다.")
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    snapshot = artifact_path(args.hg_relations_snapshot)
    search = PartitionSearch(pages, vectors, hg_page_ids=set(lookup.page_roles))
    config = experiment_config()
    config.update(
        suite=args.suite,
        question_inputs={
            split: evaluation.stamp(path) for split, (path, _) in files.items()
        },
        relation_snapshot=evaluation.stamp(snapshot / "manifest.json"),
        excluded_general_books=["AS057", "AS120"],
        corpus_pages={
            "baseline": len(search.baseline.pages),
            "general": len(search.general.pages),
            "hg": len(search.hg.pages),
        },
        luna={
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
            "timeout_seconds": 10,
            "max_attempts": expected if args.live_luna else 0,
            "retries": 0,
            "workers": WORKERS,
            "prompt_sha256": PROMPT_SHA256,
            "schema_sha256": SCHEMA_SHA256,
        },
    )
    evaluation.write_json(output / "config.json", config)
    started = perf_counter()
    with HGRelations(
        snapshot,
        pages=pages,
        structured_snapshot=artifact_path(args.structured_snapshot),
    ) as relations:
        from experimental.u_10o0o.advanced_hg_route import pilot_for

        calls = {}

        def verify_call(question, call):
            return decide(
                question,
                lookup,
                relations,
                call.proposal if call else None,
                tool_requested=bool(call and call.tool_requested),
            )

        def check(row, call):
            decision = verify_call(row["question"], call)
            if not decision.cases:
                if args.suite == "peer":
                    return False
                raise RuntimeError(
                    f"Luna·SQLite 연결 검증 실패 ({call.status}, {decision.reason})."
                )
            if not all(relations.pages_for(case.case_id) for case in decision.cases):
                raise RuntimeError("Luna가 확인한 HG 사건의 원본 연결이 없습니다.")

        log(f"HG 분리 {args.suite} 비교 시작 · {expected}문항 · 답변 생성 0회")
        if args.live_luna:
            pilot = pilot_for(
                sorted(questions, key=lambda row: row["split"] == "peer_false_premise"),
                lookup,
                relations,
            )
            calls = run_tool(questions, HGLuna(), pilot, check, output, log)

        decisions = {
            row["id"]: verify_call(row["question"], calls.get(row["id"]))
            for row in questions
        }
        baseline = search.rank(questions, "baseline", log)
        general = search.rank(
            [
                row
                for row in questions
                if decisions[row["id"]].path in {"general", "mixed"}
            ],
            "general",
            log,
        )
        hg = search.rank(
            [row for row in questions if decisions[row["id"]].path == "hg_search"],
            "hg",
            log,
        )
        retrievals, traces = {}, {}
        for row in questions:
            qid, question = row["id"], row["question"]
            pool, full_order = baseline[qid]
            original, linked = evaluation.finish(
                question, pool, full_order, lookup, storage
            )
            retrievals[qid, "baseline"] = original
            base_trace = evaluation.trace(original, linked, lookup, full_order)
            traces[qid, "baseline"] = {
                key: value
                for key, value in base_trace.items()
                if key not in {"cases", "roles"}
            }
            decision = decisions[qid]
            source_pages = {
                case.case_id: dict(
                    zip(ROLES, relations.pages_for(case.case_id), strict=True)
                )
                for case in decision.cases
            }
            roles = {
                case.case_id: ROLES
                if len(decision.cases) == 1
                else (case.roles or ROLES)
                for case in decision.cases
            }
            pool, order = (
                hg[qid] if decision.path == "hg_search" else general.get(qid, ([], []))
            )
            if decision.path == "hg_search":
                result, _ = evaluation.finish(question, pool, order, lookup, storage)
            else:
                expanded, context, route = attach_verified_sources(
                    question,
                    pool,
                    order,
                    lookup,
                    source_pages,
                    requested_roles=roles,
                    mixed=decision.path == "mixed",
                )
                result = (
                    pool,
                    expanded,
                    context,
                    tuple(source_pages),
                    f"{decision.path}/{route}",
                )
            retrievals[qid, "hg_partition"] = result
            call = calls.get(qid)
            traces[qid, "hg_partition"] = {
                "route": decision.path,
                "reason": decision.reason,
                "cue_types": decision.cue_types,
                "candidate_case_count": decision.candidate_count,
                "resolved_case_count": len(decision.cases),
                "database_sources": sorted(
                    {
                        (
                            page["book_id"]
                            if "book_id" in page
                            else page["file_name"].removesuffix(".pdf"),
                            page["page"],
                        )
                        for role_pages in source_pages.values()
                        for page in role_pages.values()
                    }
                ),
                "search_calls": int(decision.path in {"hg_search", "general", "mixed"}),
                "pool": evaluation.sources(pool),
                "packed": evaluation.packed_sources(result[2]),
                "tool_requested": bool(call and call.tool_requested),
                "provider_status": call.status if call else None,
            }
            revised, path = full_fallback_result(
                question,
                decision.path,
                original,
                full_order,
                result,
                lookup,
                source_pages,
                roles,
            )
            retrievals[qid, ARMS[-1]] = revised
            traces[qid, ARMS[-1]] = full_fallback_trace(
                traces[qid, "hg_partition"], revised, path
            )
    log("후보·원본 출처·최종 문맥 저장 후 참고 근거 채점")
    _, score_rows, references = evaluation.score(
        output, questions, files, retrievals, traces, ARMS
    )
    config["references"] = {
        split: evaluation.stamp(path) for split, (_, path) in files.items()
    }
    config["external_api_calls"] = len(calls)
    peer_ambiguous = set()
    if args.suite == "peer":
        peer_ambiguous = set(json.loads(PEER_AMBIGUOUS.read_bytes()))
        config["peer_ambiguous"] = evaluation.stamp(PEER_AMBIGUOUS)
        config["evidence_group_policy"] = "any_citation_per_group"
    evaluation.write_json(output / "config.json", config)
    telemetry = [call.telemetry.as_dict() for call in calls.values()]
    case_validation = Counter()
    for question in questions:
        qid = question["id"]
        actual = {request.case_id for request in decisions[qid].cases}
        expected = {
            lookup.page_roles[page][0]
            for page in _positive_gold(references[question["split"]][qid])
            if page in lookup.page_roles
        }
        if actual and expected:
            if args.suite == "peer":
                case_validation[
                    "shares_reference_case"
                    if actual & expected
                    else "no_shared_reference_case"
                ] += 1
            else:
                case_validation["matched" if actual == expected else "mismatched"] += 1
        elif actual:
            case_validation["without_hg_reference"] += 1
    evaluation.write_json(
        output / "results.json",
        {
            "config": config,
            "cohorts": _cohorts(
                score_rows,
                questions,
                references,
                decisions,
                peer_ambiguous=peer_ambiguous,
            ),
            "routes": dict(Counter(d.path for d in decisions.values())),
            "full_fallback_routes": dict(
                Counter(
                    trace["route"]
                    for (_, arm), trace in traces.items()
                    if arm == ARMS[-1]
                )
            ),
            "paired_full_fallback": paired_full_fallback(score_rows),
            "reasons": dict(Counter(d.reason for d in decisions.values())),
            "verified_cases_against_references": dict(case_validation),
            "provider": {
                "calls": len(calls),
                "tool_requests": sum(call.tool_requested for call in calls.values()),
                "verified_db_requests": sum(bool(d.cases) for d in decisions.values()),
                "statuses": dict(Counter(call.status for call in calls.values())),
                "latency": evaluation.latency(
                    [
                        x["latency_ms"]
                        for x in telemetry
                        if x.get("latency_ms") is not None
                    ]
                ),
                "input_tokens": sum(x.get("input_tokens") or 0 for x in telemetry),
                "output_tokens": sum(x.get("output_tokens") or 0 for x in telemetry),
            },
            "elapsed_seconds": perf_counter() - started,
        },
    )
    log(f"HG 분리 {args.suite} 비교 완료 · Luna {len(calls)}회 · 답변 생성 0회")
