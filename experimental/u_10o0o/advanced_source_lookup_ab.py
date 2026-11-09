"""Compare source lookups against the frozen Advanced scenario baseline."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_clause_ab import context_sources
from experimental.u_10o0o.advanced_pair_holdout import fact_hits
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import BASE, PEER, start_log
from experimental.u_10o0o.advanced_scenarios_clause_ab import (
    BUDGET,
    LIMITS,
    QPATH,
    RPATH,
    replay_archive,
)
from experimental.u_10o0o.advanced_search import CANDIDATES, FINAL_K
from experimental.u_10o0o.advanced_source_lookup import (
    ambiguous_lookup,
    apply_sources,
    choose_sources,
    select_context,
    unique_lookup,
)
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import FACTS, reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

ARMS = {
    "A": (),
    "unique": ("unique",),
    "ambiguous": ("ambiguous",),
    "both": ("unique", "ambiguous"),
}
PRIOR_RUN = CACHE_PATH.parent / "advanced-scenarios-clause-ab-20261109"
PRIOR_RECORD = Path(__file__).parent / "records/advanced-scenarios-clause-ab-20261109"


def page_keys(ranked):
    return {location(page) for page, _ in ranked}


def retrieve(questions, archived, lookup, storage):
    retrievals, traces = {}, {}
    for item in questions:
        qid, question = item["id"], item["question"]
        pool, ranked, _, _, _ = archived[qid, BASE]
        base = select_context(question, ranked, lookup, BUDGET)
        baseline_candidates = page_keys(ranked)
        matches = {
            "unique": unique_lookup(question, lookup),
            "ambiguous": ambiguous_lookup(question, storage),
        }
        for arm, enabled in ARMS.items():
            lookup_route, cases, linked = choose_sources(
                {name: matches[name] for name in enabled}
            )
            sources = {location(page) for page in linked}
            order, context, route = apply_sources(
                question, ranked, lookup, BUDGET, linked, base
            )
            keys = page_keys(order)
            added = keys - baseline_candidates
            if (
                len(order) != CANDIDATES
                or len(keys) != CANDIDATES
                or not added <= sources
                or not sources <= keys
                or len(context.final_chunks) > FINAL_K
                or context.token_count > BUDGET
                or not set(context_sources(context)) <= keys
            ):
                raise ValueError(f"{qid} · {arm}의 후보 또는 문맥 한도가 다릅니다.")
            retrievals[qid, arm] = pool, order, context, cases, route
            traces[qid, arm] = {
                "lookup_route": lookup_route,
                "lookup_pages": len(sources),
                "new_candidate_pages": len(added),
                "lookup_pages_in_context": len(sources & set(context_sources(context))),
            }
    return retrievals, traces


def verify_baseline(questions, retrievals):
    archived = [
        row for row in read_rows(PRIOR_RUN / "rankings.jsonl") if row["arm"] == "A"
    ]
    by_id = {row["id"]: row for row in archived}
    if len(archived) != len(by_id) or by_id.keys() != {q["id"] for q in questions}:
        raise ValueError("이전 A의 보관 문항이 다릅니다.")
    for qid, row in by_id.items():
        pool, ranked, context, _, route = retrievals[qid, "A"]
        if (
            context.context_hash != row["context_sha256"]
            or context.token_count != row["context_tokens"]
            or route != row["route"]
            or any(
                actual != [(item["book_id"], item["page"]) for item in row[stage]]
                for stage, actual in (
                    ("pool", [location(page) for page, _ in pool]),
                    ("expanded", [location(page) for page, _ in ranked]),
                    ("packed", context_sources(context)),
                )
            )
        ):
            raise ValueError(f"{qid}의 이전 A를 재현하지 못했습니다.")


def score_question(ref, retrievals, traces, prior):
    qid = ref["id"]
    required_pages = reference_locations(ref)
    row = {
        "id": qid,
        "scenario": ref["scenario"],
        "variant": ref["variant"],
        "facts_required": len(ref["evidence_requirements"]),
        "arms": {},
    }
    base_context = retrievals[qid, "A"][2]
    for arm in (*ARMS, PEER):
        _, ranked, context, cases, route = retrievals[qid, arm]
        candidate = fact_hits(ref, [page for page, _ in ranked])
        final = fact_hits(ref, context)
        if any(hit and not found for found, hit in zip(candidate, final, strict=True)):
            raise ValueError(f"{qid} · {arm}의 최종 근거가 후보에 없습니다.")
        row["arms"][arm] = {
            **traces.get((qid, arm), {}),
            "lookup_cases": len(cases),
            "lookup_cases_exact": set(cases) == set(ref["case_keys"])
            if cases
            else None,
            "candidate_complete": all(candidate),
            "candidate_facts_hit": sum(candidate),
            "full_evidence": all(final),
            "facts_hit": sum(final),
            "missed_facts": [i for i, hit in enumerate(final, 1) if not hit],
            "page_recall_at_8": len(required_pages & set(context_sources(context)))
            / len(required_pages),
            "context_changed": context.context_hash != base_context.context_hash,
            "context_tokens": context.token_count,
            "selection_route": route,
        }
        if arm in ("A", PEER):
            previous = prior["arms"][arm]
            actual = row["arms"][arm]
            if any(
                actual[key] != previous[old]
                for key, old in (
                    ("full_evidence", "full_evidence"),
                    ("facts_hit", "final_facts_hit"),
                    ("page_recall_at_8", "page_recall_at_8"),
                )
            ):
                raise ValueError(f"{qid} · {arm}의 종전 채점을 재현하지 못했습니다.")

    baseline = set(row["arms"]["A"]["missed_facts"])
    for arm in (*ARMS, PEER):
        after = row["arms"][arm]
        missed = set(after["missed_facts"])
        after["gained_facts"] = sorted(baseline - missed)
        after["lost_facts"] = sorted(missed - baseline)
    return row


def summarize(rows):
    result = {
        "questions": len(rows),
        "facts_required": sum(row["facts_required"] for row in rows),
        "arms": {},
    }
    for arm in (*ARMS, PEER):
        values = [row["arms"][arm] for row in rows]
        result["arms"][arm] = {
            **{
                key: sum(row[key] for row in values)
                for key in (
                    "candidate_complete",
                    "candidate_facts_hit",
                    "full_evidence",
                    "facts_hit",
                    "context_changed",
                )
            },
            "page_recall_at_8": fmean(row["page_recall_at_8"] for row in values),
            "lookup_routes": dict(
                Counter(row.get("lookup_route", "peer") for row in values)
            ),
            "lookup_exact": sum(row["lookup_cases_exact"] is True for row in values),
            "candidate_changed": sum(
                bool(row.get("new_candidate_pages")) for row in values
            ),
            "gained": [
                row["id"]
                for row in rows
                if row["arms"][arm]["full_evidence"]
                and not row["arms"]["A"]["full_evidence"]
            ],
            "lost": [
                row["id"]
                for row in rows
                if not row["arms"][arm]["full_evidence"]
                and row["arms"]["A"]["full_evidence"]
            ],
            "any_fact_gained": [
                row["id"] for row in rows if row["arms"][arm]["gained_facts"]
            ],
            "any_fact_lost": [
                row["id"] for row in rows if row["arms"][arm]["lost_facts"]
            ],
        }
    return result


def run(output):
    started, log = start_log(output)
    previous = json.loads((PRIOR_RECORD / "results.json").read_bytes())
    if any(
        fingerprint(PROJECT_ROOT / stamp["path"]) != stamp
        for name in ("code", "archive")
        for stamp in previous[name].values()
    ):
        raise ValueError("이전 A/B의 기준 코드 또는 보관 검색 지문이 바뀌었습니다.")
    questions = load_questions(QPATH)
    pages = json.loads(CACHE_PATH.read_bytes())
    archived, config = replay_archive(questions, pages)
    log(f"보관 개인·동료 문맥 {len(archived)}개 재현 · 원문 조회 네 설정 실행")
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    retrievals, traces = retrieve(questions, archived, lookup, storage)
    verify_baseline(questions, retrievals)
    save_rankings(output, retrievals)
    log(
        f"개인 문맥 {len(retrievals)}개 저장 · 이전 A {len(questions)}문항 재현 · 참고 근거 채점 시작"
    )

    if fingerprint(RPATH) != config["inputs"][RPATH.name]:
        raise ValueError("보관 참고 근거의 지문이 다릅니다.")
    refs = load_references(RPATH)
    prior = {row["id"]: row for row in read_rows(PRIOR_RECORD / "questions.jsonl")}
    if (
        refs.keys() != prior.keys()
        or refs.keys() != {q["id"] for q in questions}
        or Counter(ref["scenario"] for ref in refs.values())
        != dict.fromkeys(range(1, 8), 20)
        or not all(
            ref["evidence_required"] and ref["evidence_requirements"]
            for ref in refs.values()
        )
    ):
        raise ValueError("상황별 평가 1~7번 각 20문항이 필요합니다.")
    retrievals.update({key: value for key, value in archived.items() if key[1] == PEER})
    rows = [
        score_question(ref, retrievals, traces, prior[qid])
        for qid, ref in sorted(refs.items())
    ]
    groups = {
        "all": rows,
        "regression": [row for row in rows if row["scenario"] not in (2, 6)],
    }
    groups.update(
        {
            f"S{scenario:02}": [row for row in rows if row["scenario"] == scenario]
            for scenario in range(1, 8)
        }
    )
    groups.update(
        {
            f"S02/{variant}": [
                row for row in groups["S02"] if row["variant"] == variant
            ]
            for variant in sorted({row["variant"] for row in groups["S02"]})
        }
    )
    summary = {key: summarize(group) for key, group in groups.items()}
    write_rows(output / "questions.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "limits": LIMITS,
            "arms": ARMS,
            "inputs": config["inputs"],
            "archived_personal_and_peer": previous["archive"],
            "prior_A": fingerprint(PRIOR_RUN / "rankings.jsonl"),
            "source_index": {
                "pages": config["pages"],
                "case_links": config["case_links"],
                "role_facts": fingerprint(FACTS / "role_facts.jsonl"),
            },
            "code": {
                **previous["code"],
                "source_lookup_runner": fingerprint(Path(__file__)),
                **{
                    name: fingerprint(Path(__file__).with_name(f"{name}.py"))
                    for name in (
                        "advanced_target_lookup",
                        "advanced_clarify_lookup",
                        "advanced_hg_lookup",
                        "advanced_source_lookup",
                    )
                },
            },
            "external_api_calls": 0,
            "summary": summary,
            "seconds": round(perf_counter() - started, 3),
        },
    )
    for name in ("S02", "S06", "regression"):
        scores = " · ".join(
            f"{arm} {summary[name]['arms'][arm]['full_evidence']}/{summary[name]['questions']}"
            for arm in ARMS
        )
        log(f"{name} · {scores}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(local_output(parser.parse_args().output))


if __name__ == "__main__":
    main()
