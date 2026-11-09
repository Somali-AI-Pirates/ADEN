"""Compare paired source-page lookup with the frozen source-lookup baseline."""

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
from experimental.u_10o0o.advanced_pair_source_lookup import pair_source_lookup
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import BASE, PEER, start_log
from experimental.u_10o0o.advanced_scenarios_clause_ab import (
    BUDGET,
    QPATH,
    RPATH,
    replay_archive,
)
from experimental.u_10o0o.advanced_scenarios_clause_ab import (
    LIMITS as ARCHIVE_LIMITS,
)
from experimental.u_10o0o.advanced_search import CANDIDATES, FINAL_K
from experimental.u_10o0o.advanced_source_lookup import (
    apply_sources,
    baseline_context,
)
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

DATA = CACHE_PATH.parent / "advanced-scenarios-20261109-v2"
ARCHIVE = CACHE_PATH.parent / "advanced-scenarios-benchmark-20261109-v2"
PRIOR_RUN = CACHE_PATH.parent / "advanced-source-lookup-20261109-verified"
PRIOR_RECORD = Path(__file__).parent / "records/advanced-source-lookup-20261109"
PRIOR_QPATH = PRIOR_RUN / "rankings.jsonl"
LIMITS = {"candidates": CANDIDATES, "pages": FINAL_K, "context_tokens": BUDGET}
ARMS = ("A", "B", PEER)


def page_keys(ranked):
    return {location(page) for page, _ in ranked}


def source_keys(context):
    return set(context_sources(context))


def verify_stamp(stamp):
    path = Path(stamp["path"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if fingerprint(path)["sha256"] != stamp["sha256"]:
        raise ValueError(f"보관 입력 지문이 달라졌습니다: {stamp['path']}")


def verify_previous_source_run():
    previous = json.loads((PRIOR_RUN / "results.json").read_bytes())
    if previous["limits"] != LIMITS or previous["external_api_calls"] != 0:
        raise ValueError("기존 source lookup 실행의 한도 또는 API 호출 수가 다릅니다.")
    if not {"A", "unique", "ambiguous", "both"} <= previous["arms"].keys():
        raise ValueError("기존 source lookup 비교군 구성이 다릅니다.")

    for section in (
        "inputs",
        "archived_personal_and_peer",
        "source_index",
    ):
        value = previous[section]
        stamps = value.values() if isinstance(value, dict) else (value,)
        for stamp in stamps:
            verify_stamp(stamp)
    verify_stamp(previous["prior_A"])

    if (
        fingerprint(PRIOR_RECORD / "results.json")["sha256"]
        != fingerprint(PRIOR_RUN / "results.json")["sha256"]
    ):
        raise ValueError("저장소의 이전 source lookup 기록과 원 실행이 다릅니다.")
    if fingerprint(QPATH)["sha256"] != previous["inputs"][QPATH.name]["sha256"]:
        raise ValueError("고정 평가 질문이 이전 source lookup 실행과 다릅니다.")
    if fingerprint(RPATH)["sha256"] != previous["inputs"][RPATH.name]["sha256"]:
        raise ValueError("고정 참고 근거가 이전 source lookup 실행과 다릅니다.")
    return previous


def verify_baseline(questions, retrievals):
    saved = [row for row in read_rows(PRIOR_QPATH) if row["arm"] == "both"]
    by_id = {row["id"]: row for row in saved}
    if len(saved) != len(by_id) or by_id.keys() != {q["id"] for q in questions}:
        raise ValueError("기존 both 비교군의 보관 질문 집합이 다릅니다.")

    for qid, row in by_id.items():
        pool, ranked, context, cases, selection_route = retrievals[qid, "A"]
        if (
            [location(page) for page, _ in pool]
            != [(item["book_id"], item["page"]) for item in row["pool"]]
            or [location(page) for page, _ in ranked]
            != [(item["book_id"], item["page"]) for item in row["expanded"]]
            or context_sources(context)
            != [(item["book_id"], item["page"]) for item in row["packed"]]
            or context.context_hash != row["context_sha256"]
            or context.token_count != row["context_tokens"]
            or selection_route != row["route"]
            or len(cases) != row["linked_cases_count"]
        ):
            raise ValueError(f"{qid}의 재구성한 기존 both 기준선이 보관값과 다릅니다.")


def retrieve(questions, archived, lookup, storage):
    retrievals, traces = {}, {}
    for item in questions:
        qid, question = item["id"], item["question"]
        pool, ranked, _, _, _ = archived[qid, BASE]
        ordered, context, cases, source_route, selection_route = baseline_context(
            question, ranked, lookup, storage, BUDGET
        )
        baseline = (ordered, context, selection_route)
        pair_cases, linked = pair_source_lookup(question, lookup)
        pair_order, pair_context, pair_selection_route = apply_sources(
            question, ordered, lookup, BUDGET, linked, baseline
        )

        baseline_keys = page_keys(ordered)
        pair_keys = page_keys(pair_order)
        linked_keys = {location(page) for page in linked}
        packed = source_keys(pair_context)
        if (
            bool(pair_cases) != bool(linked)
            or (pair_cases and len(set(pair_cases)) != 2)
            or len(linked) != len(linked_keys)
            or len(ordered) != CANDIDATES
            or len(baseline_keys) != CANDIDATES
            or len(pair_order) != CANDIDATES
            or len(pair_keys) != CANDIDATES
            or not linked_keys <= pair_keys
            or not pair_keys - baseline_keys <= linked_keys
            or not packed <= pair_keys
            or len(pair_context.final_chunks) > FINAL_K
            or pair_context.token_count > BUDGET
        ):
            raise ValueError(f"{qid}의 조회 후보 또는 문맥 한도가 다릅니다.")

        if not linked or linked_keys <= source_keys(context):
            if (
                [location(page) for page, _ in pair_order]
                != [location(page) for page, _ in ordered]
                or pair_context.context_hash != context.context_hash
                or pair_selection_route != selection_route
            ):
                raise ValueError(
                    f"{qid}의 조회 불필요 경로가 기준 문맥을 보존하지 못했습니다."
                )

        retrievals[qid, "A"] = pool, ordered, context, cases, selection_route
        retrievals[qid, "B"] = (
            pool,
            pair_order,
            pair_context,
            pair_cases or cases,
            pair_selection_route,
        )
        traces[qid, "A"] = {
            "source_route": source_route,
            "selection_route": selection_route,
            "source_cases": len(cases),
        }
        traces[qid, "B"] = {
            "pair_lookup_cases": len(pair_cases),
            "pair_lookup_pages": len(linked_keys),
            "pair_lookup_route": "matched" if pair_cases else "fallback",
            "new_candidate_pages": len(pair_keys - baseline_keys),
            "dropped_candidate_pages": len(baseline_keys - pair_keys),
            "pair_pages_in_context": len(linked_keys & packed),
            "selection_route": pair_selection_route,
        }
    return retrievals, traces


def score_question(ref, retrievals, traces):
    qid = ref["id"]
    required_pages = set(reference_locations(ref))
    if not required_pages or not ref["evidence_requirements"]:
        raise ValueError(f"{qid}에 필수 근거 또는 페이지가 없습니다.")

    row = {
        "id": qid,
        "scenario": ref["scenario"],
        "variant": ref["variant"],
        "facts_required": len(ref["evidence_requirements"]),
        "required_pages": len(required_pages),
        "arms": {},
    }
    for arm in ARMS:
        _, ranked, context, cases, route = retrievals[qid, arm]
        candidate_hits = fact_hits(ref, [page for page, _ in ranked])
        final_hits = fact_hits(ref, context)
        if any(
            final and not candidate
            for final, candidate in zip(final_hits, candidate_hits, strict=True)
        ):
            raise ValueError(f"{qid} · {arm} 최종 근거가 후보에 없습니다.")
        selected = source_keys(context)
        candidate_keys = page_keys(ranked)
        arm_trace = traces.get((qid, arm), {})
        row["arms"][arm] = {
            **arm_trace,
            "lookup_cases": len(cases),
            "lookup_cases_exact": set(cases) == set(ref["case_keys"])
            if cases
            else None,
            "candidate_complete": all(candidate_hits),
            "candidate_facts_hit": sum(candidate_hits),
            "candidate_facts_required": len(candidate_hits),
            "final_complete": all(final_hits),
            "final_facts_hit": sum(final_hits),
            "final_facts_required": len(final_hits),
            "candidate_missed_facts": [
                index for index, hit in enumerate(candidate_hits, start=1) if not hit
            ],
            "final_missed_facts": [
                index for index, hit in enumerate(final_hits, start=1) if not hit
            ],
            "candidate_page_recall_at_20": len(candidate_keys & required_pages)
            / len(required_pages),
            "final_page_recall_at_8": len(selected & required_pages)
            / len(required_pages),
            "context_tokens": context.token_count,
            "context_changed_from_A": context.context_hash
            != retrievals[qid, "A"][2].context_hash,
            "selection_route": route,
        }

    base = row["arms"]["A"]
    proposed = row["arms"]["B"]
    base_missed = set(base["final_missed_facts"])
    proposed_missed = set(proposed["final_missed_facts"])
    proposed["gained_final_facts"] = sorted(base_missed - proposed_missed)
    proposed["lost_final_facts"] = sorted(proposed_missed - base_missed)
    proposed["candidate_complete_gained"] = (
        proposed["candidate_complete"] and not base["candidate_complete"]
    )
    proposed["candidate_complete_lost"] = (
        base["candidate_complete"] and not proposed["candidate_complete"]
    )
    proposed["final_complete_gained"] = (
        proposed["final_complete"] and not base["final_complete"]
    )
    proposed["final_complete_lost"] = (
        base["final_complete"] and not proposed["final_complete"]
    )
    proposed["candidate_page_recall_delta"] = (
        proposed["candidate_page_recall_at_20"] - base["candidate_page_recall_at_20"]
    )
    proposed["final_page_recall_delta"] = (
        proposed["final_page_recall_at_8"] - base["final_page_recall_at_8"]
    )
    return row


def summarize(rows):
    summary = {
        "questions": len(rows),
        "facts_required": sum(row["facts_required"] for row in rows),
        "arms": {},
    }
    for arm in ARMS:
        values = [row["arms"][arm] for row in rows]
        summary["arms"][arm] = {
            "candidate_complete": sum(row["candidate_complete"] for row in values),
            "candidate_facts_hit": sum(row["candidate_facts_hit"] for row in values),
            "candidate_facts_required": sum(
                row["candidate_facts_required"] for row in values
            ),
            "final_complete": sum(row["final_complete"] for row in values),
            "final_facts_hit": sum(row["final_facts_hit"] for row in values),
            "final_facts_required": sum(row["final_facts_required"] for row in values),
            "context_changed_from_A": sum(
                row["context_changed_from_A"] for row in values
            ),
            "candidate_page_recall_at_20": fmean(
                row["candidate_page_recall_at_20"] for row in values
            ),
            "final_page_recall_at_8": fmean(
                row["final_page_recall_at_8"] for row in values
            ),
        }
    if "B" in summary["arms"] and "A" in summary["arms"]:
        values = [row["arms"]["B"] for row in rows]
        summary["B_vs_A"] = {
            "candidate_complete_gained": sum(
                row["candidate_complete_gained"] for row in values
            ),
            "candidate_complete_lost": sum(
                row["candidate_complete_lost"] for row in values
            ),
            "final_complete_gained": sum(
                row["final_complete_gained"] for row in values
            ),
            "final_complete_lost": sum(row["final_complete_lost"] for row in values),
            "questions_with_final_facts_gained": sum(
                bool(row["gained_final_facts"]) for row in values
            ),
            "questions_with_final_facts_lost": sum(
                bool(row["lost_final_facts"]) for row in values
            ),
            "final_facts_gained": sum(len(row["gained_final_facts"]) for row in values),
            "final_facts_lost": sum(len(row["lost_final_facts"]) for row in values),
            "candidate_page_recall_delta": fmean(
                row["candidate_page_recall_delta"] for row in values
            ),
            "final_page_recall_delta": fmean(
                row["final_page_recall_delta"] for row in values
            ),
            "pair_lookup_questions": sum(
                row["pair_lookup_cases"] > 0 for row in values
            ),
            "pair_lookup_cases_exact": sum(
                row["pair_lookup_cases"] > 0 and row["lookup_cases_exact"] is True
                for row in values
            ),
            "candidate_membership_changed": sum(
                row["new_candidate_pages"] > 0 or row["dropped_candidate_pages"] > 0
                for row in values
            ),
            "routes": dict(Counter(row["pair_lookup_route"] for row in values)),
            "gained_final_question_ids": [
                row["id"] for row in rows if row["arms"]["B"]["final_complete_gained"]
            ],
            "lost_final_question_ids": [
                row["id"] for row in rows if row["arms"]["B"]["final_complete_lost"]
            ],
        }
    return summary


def run(output):
    started, log = start_log(output)
    previous = verify_previous_source_run()
    questions = load_questions(QPATH)
    pages = json.loads(CACHE_PATH.read_bytes())
    archived, archive_config = replay_archive(questions, pages)
    if archive_config["limits"] != ARCHIVE_LIMITS or len(questions) != 140:
        raise ValueError("140문항 보관 후보와 기존 비교 실행의 조건이 다릅니다.")
    log(f"고정 입력 지문 확인 · {len(questions)}문항 · 후보 20쪽·문맥 8쪽·8,000토큰")

    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    retrievals, traces = retrieve(questions, archived, lookup, storage)
    verify_baseline(questions, retrievals)
    retrievals.update({key: value for key, value in archived.items() if key[1] == PEER})
    save_rankings(output, retrievals)
    log("새 기준 문맥이 기존 both 보관 순위·쪽·토큰 지문과 일치 · 순위 저장 완료")

    if fingerprint(RPATH)["sha256"] != archive_config["inputs"][RPATH.name]["sha256"]:
        raise ValueError("보관 참고 근거 지문이 달라졌습니다.")
    references = load_references(RPATH)
    if (
        references.keys() != {q["id"] for q in questions}
        or Counter(ref["scenario"] for ref in references.values())
        != dict.fromkeys(range(1, 8), 20)
        or not all(
            ref["evidence_required"] and ref["evidence_requirements"]
            for ref in references.values()
        )
    ):
        raise ValueError("상황별 1~7번 각 20문항의 평가 근거가 필요합니다.")
    rows = [
        score_question(references[qid], retrievals, traces)
        for qid in sorted(references)
    ]
    groups = {
        "all": rows,
        "S04": [row for row in rows if row["scenario"] == 4],
        "other120_regression": [row for row in rows if row["scenario"] != 4],
        **{
            f"S{scenario:02}": [row for row in rows if row["scenario"] == scenario]
            for scenario in range(1, 8)
        },
    }
    groups.update(
        {
            f"S04/{variant}": [
                row for row in groups["S04"] if row["variant"] == variant
            ]
            for variant in sorted(
                {row["variant"] for row in groups["S04"]},
                key=lambda value: value or "",
            )
        }
    )
    summary = {name: summarize(group) for name, group in groups.items()}
    write_rows(output / "questions.jsonl", rows)

    code_paths = {
        "runner": Path(__file__),
        "source_lookup": Path(__file__).with_name("advanced_source_lookup.py"),
        "pair_lookup": Path(__file__).with_name("advanced_pair_source_lookup.py"),
        "selector": Path(__file__).with_name("advanced_pair_context.py"),
    }
    write_json(
        output / "results.json",
        {
            "limits": LIMITS,
            "arms": list(ARMS),
            "dataset": {
                "manifest": fingerprint(DATA / "manifest.json"),
                "questions": fingerprint(QPATH),
                "references": fingerprint(RPATH),
                "pages": fingerprint(CACHE_PATH),
            },
            "archive": {
                "results": fingerprint(ARCHIVE / "results.json"),
                "rankings": fingerprint(ARCHIVE / "rankings.jsonl"),
                "source_lookup_results": fingerprint(PRIOR_RUN / "results.json"),
                "source_lookup_rankings": fingerprint(PRIOR_QPATH),
            },
            "source_index": previous["source_index"],
            "code": {name: fingerprint(path) for name, path in code_paths.items()},
            "external_api_calls": 0,
            "summary": summary,
            "seconds": round(perf_counter() - started, 3),
        },
    )
    for name in ("S04", "other120_regression", "all"):
        values = summary[name]["B_vs_A"]
        log(
            f"{name} · 후보 전체 {summary[name]['arms']['B']['candidate_complete']}"
            f"/{summary[name]['questions']} · 최종 전체 {summary[name]['arms']['B']['final_complete']}"
            f"/{summary[name]['questions']} · 기준 대비 최종 완전근거 증가/감소 "
            f"{values['final_complete_gained']}/{values['final_complete_lost']}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(local_output(args.output))


if __name__ == "__main__":
    main()
