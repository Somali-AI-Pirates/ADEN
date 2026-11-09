"""Replay the clause-binding A/B on the frozen Advanced scenario suite v2."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_clause_ab import (
    BUDGET,
    context_sources,
    missed_indices,
    run_arms,
    score,
    summarize,
)
from experimental.u_10o0o.advanced_pair_holdout import fact_hits
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import BASE, PEER, pack, start_log
from experimental.u_10o0o.advanced_search import CANDIDATES, FINAL_K
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import CASE_INDEX, reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

DATA = CACHE_PATH.parent / "advanced-scenarios-20261109-v2"
ARCHIVE = CACHE_PATH.parent / "advanced-scenarios-benchmark-20261109-v2"
PREVIOUS = (
    Path(__file__).parent / "records/advanced-clause-binding-20261109/results.json"
)
QPATH = DATA / "retrieval_questions.json"
RPATH = DATA / "retrieval_references.json"
TYPES = {"both_explicit": "explicit", "mixed": "mixed", "both_indirect": "indirect"}
LIMITS = {"candidates": CANDIDATES, "pages": FINAL_K, "context_tokens": BUDGET}


def replay_archive(questions, pages):
    config = json.loads((ARCHIVE / "results.json").read_bytes())
    files = {
        "manifest": DATA / "manifest.json",
        "pages": CACHE_PATH,
        "case_links": CASE_INDEX / "case_sources.jsonl",
    }
    if (
        config["limits"] != LIMITS
        or config["external_api_calls"] != 0
        or config["peer"]["llm_calls"] != 0
        or any(
            fingerprint(path)["sha256"] != config[key]["sha256"]
            for key, path in files.items()
        )
        or fingerprint(QPATH) != config["inputs"][QPATH.name]
    ):
        raise ValueError("보관 실행의 평가 질문, 원문 또는 문맥 한도가 다릅니다.")

    ids = {q["id"] for q in questions}
    rows = [row for row in read_rows(ARCHIVE / "rankings.jsonl") if row["id"] in ids]
    archived = {(row["id"], row["arm"]): row for row in rows}
    if len(rows) != len(archived) or archived.keys() != {
        (qid, arm) for qid in ids for arm in (BASE, PEER)
    }:
        raise ValueError("보관 문항 또는 비교군 순위가 다릅니다.")

    page_map = {location(page): page for page in pages}
    restored = {}
    for (qid, arm), row in archived.items():
        pool, ranked = (
            [
                (page_map[source["book_id"], source["page"]], -float(rank))
                for rank, source in enumerate(row[stage], start=1)
            ]
            for stage in ("pool", "expanded")
        )
        for order in (pool, ranked):
            distinct = len({location(page) for page, _ in order})
            if (
                distinct != len(order)
                or not 0 < distinct <= CANDIDATES
                or (arm == BASE and distinct != CANDIDATES)
            ):
                raise ValueError(f"{qid} · {arm}의 후보 쪽 수가 다릅니다.")
        context = pack(ranked, BUDGET)
        if (
            context.context_hash != row["context_sha256"]
            or context.token_count != row["context_tokens"]
            or context_sources(context)
            != [(item["book_id"], item["page"]) for item in row["packed"]]
        ):
            raise ValueError(f"{qid} · {arm}의 보관 문맥을 재현하지 못했습니다.")
        restored[qid, arm] = pool, ranked, context, (), row["route"]
    return restored, config


def compare_refs(ref, archived, retrievals, predictions, lookup, old_scores):
    qid, scenario = ref["id"], ref["scenario"]
    _, ranked, original, _, _ = archived[qid, BASE]
    row = score(
        ref
        | {"question_type": TYPES[ref["variant"]] if scenario == 4 else "regression"},
        ranked,
        retrievals,
        predictions,
        lookup,
    ) | {"scenario": scenario, "variant": ref["variant"]}

    expected = reference_locations(ref)
    books = {book for book, _ in expected}
    for arm, context in {
        BASE: original,
        "A": retrievals[qid, "A"][2],
        "B": retrievals[qid, "B"][2],
        PEER: archived[qid, PEER][2],
    }.items():
        actual = set(context_sources(context))
        metrics = {
            "page_recall_at_8": len(actual & expected) / len(expected),
            "doc_recall_at_8": len({book for book, _ in actual} & books) / len(books),
        }
        if arm in (BASE, PEER):
            hits = fact_hits(ref, context)
            metrics.update(
                full_evidence=all(hits),
                final_facts_hit=sum(hits),
                final_facts_missed=missed_indices(hits),
            )
            prior = old_scores[arm]
            if (
                metrics["full_evidence"] != prior["final_complete"]
                or metrics["final_facts_hit"] != prior["final_facts"]
                or row["facts_required"] != prior["required_facts"]
                or any(
                    metrics[key] != prior[key]
                    for key in ("page_recall_at_8", "doc_recall_at_8")
                )
            ):
                raise ValueError(f"{qid} · {arm}의 보관 채점을 재현하지 못했습니다.")
        else:
            metrics["final_facts_hit"] = row["facts_required"] - len(
                row["arms"][arm]["final_facts_missed"]
            )
        row["arms"].setdefault(arm, {}).update(metrics)
    return row


def summarize_group(rows):
    summary = summarize(rows)
    for arm in ("A", "B", PEER, BASE):
        stats = [row["arms"][arm] for row in rows]
        summary["arms"].setdefault(arm, {}).update(
            full_evidence=sum(m["full_evidence"] for m in stats),
            final_facts_hit=sum(m["final_facts_hit"] for m in stats),
            page_recall_at_8=fmean(m["page_recall_at_8"] for m in stats),
            doc_recall_at_8=fmean(m["doc_recall_at_8"] for m in stats),
        )
    for metric in ("page_recall_at_8", "doc_recall_at_8"):
        summary["changes"][metric] = {
            "gained": [
                row["id"]
                for row in rows
                if row["arms"]["B"][metric] > row["arms"]["A"][metric]
            ],
            "lost": [
                row["id"]
                for row in rows
                if row["arms"]["B"][metric] < row["arms"]["A"][metric]
            ],
        }
    summary["B_vs_peer"] = dict.fromkeys(("B_only", "peer_only", "both", "neither"), 0)
    for row in rows:
        b, peer = (row["arms"][arm]["full_evidence"] for arm in ("B", PEER))
        outcome = {
            (True, False): "B_only",
            (False, True): "peer_only",
            (True, True): "both",
            (False, False): "neither",
        }
        summary["B_vs_peer"][outcome[b, peer]] += 1
    return summary


def run(output):
    code = json.loads(PREVIOUS.read_bytes())["code"]
    if any(
        fingerprint(PROJECT_ROOT / stamp["path"]) != stamp for stamp in code.values()
    ):
        raise ValueError("직전 A/B의 규칙, 선택기 또는 실행 함수 지문이 바뀌었습니다.")
    started, log = start_log(output)
    questions = load_questions(QPATH)
    pages = json.loads(CACHE_PATH.read_bytes())
    archived, config = replay_archive(questions, pages)
    log(f"개인·동료 보관 문맥 {len(archived)}개 지문·쪽·토큰 수 재현")
    lookup = load_target_lookup(pages)
    frozen = {(q["id"], "baseline"): archived[q["id"], BASE] for q in questions}
    retrievals, predictions = run_arms(questions, frozen, lookup)
    save_rankings(output, retrievals)
    log(f"A/B 문맥 {len(retrievals)}개 저장 · 후보 집합 변경 0건 · 참고 근거 채점 시작")

    if fingerprint(RPATH) != config["inputs"][RPATH.name]:
        raise ValueError("보관 실행의 참고 근거 지문이 다릅니다.")
    references = load_references(RPATH)
    ids = {q["id"] for q in questions}
    old_scores = {
        row["id"]: row["arms"]
        for row in read_rows(ARCHIVE / "scores.jsonl")
        if row["id"] in ids
    }
    if (
        references.keys() != ids
        or old_scores.keys() != ids
        or Counter(row["scenario"] for row in references.values())
        != dict.fromkeys(range(1, 8), 20)
        or not all(
            ref["evidence_required"] and ref["evidence_requirements"]
            for ref in references.values()
        )
    ):
        raise ValueError("상황별 1~7번 각 20문항의 참고 근거가 필요합니다.")
    rows = [
        compare_refs(
            references[qid], archived, retrievals, predictions, lookup, old_scores[qid]
        )
        for qid in sorted(ids)
    ]
    groups = {
        "all": rows,
        "regression": [row for row in rows if row["scenario"] != 4],
        **{
            f"S{n:02}": [row for row in rows if row["scenario"] == n]
            for n in range(1, 8)
        },
    }
    groups.update(
        {
            f"S04/{variant}": [
                row for row in groups["S04"] if row["variant"] == variant
            ]
            for variant in TYPES
        }
    )
    summary = {name: summarize_group(group) for name, group in groups.items()}
    write_rows(output / "questions.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "limits": LIMITS,
            "dataset": {
                name: fingerprint(DATA / name)
                for name in ("manifest.json", QPATH.name, RPATH.name)
            },
            "archive": {
                name: fingerprint(ARCHIVE / name)
                for name in ("results.json", "rankings.jsonl", "scores.jsonl")
            },
            "code": {**code, "scenario_runner": fingerprint(Path(__file__))},
            "candidate_membership_changes": 0,
            "external_api_calls": 0,
            "peer_candidates": {
                "unit": "chunk",
                "max": CANDIDATES,
                "deduplicated_to_pages": True,
            },
            "summary": summary,
            "seconds": round(perf_counter() - started, 3),
        },
    )
    for group in ("S04", "regression", "all"):
        changes = summary[group]["changes"]["full_evidence"]
        log(
            f"{group} · 전체 근거 개선 {len(changes['gained'])} · 하락 {len(changes['lost'])}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(local_output(parser.parse_args().output))


if __name__ == "__main__":
    main()
