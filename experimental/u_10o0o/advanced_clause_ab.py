"""Replay the same Advanced selector with legacy and clause-bound requirements."""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter
from unittest.mock import patch

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o import advanced_pair_context
from experimental.u_10o0o.advanced_clause_binding import clause_requirements
from experimental.u_10o0o.advanced_pair_holdout import fact_hits
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_search import CANDIDATES, FINAL_K, KST
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_diagnosis import (
    BUDGET,
    PAIR_TYPES,
    replay,
    required_slots,
    validate_manifest,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH

ARMS = {"A": advanced_pair_context.pair_requirements, "B": clause_requirements}
INPUTS = {
    "replay": (
        "advanced-pair-holdout-20261109-data",
        "advanced-target-diagnosis-replay-final-20261109",
        24,
        6,
    ),
    "fresh": (
        "advanced-target-diagnosis-fresh-final-20261109-data",
        "advanced-target-diagnosis-fresh-final-20261109",
        15,
        5,
    ),
}


def archived_candidates(data, archive, questions, pages):
    manifest = json.loads((data / "manifest.json").read_bytes())
    if (
        fingerprint(data / "questions.json")["sha256"]
        != manifest["questions"]["sha256"]
        or fingerprint(CACHE_PATH)["sha256"] != manifest["pages"]["sha256"]
    ):
        raise ValueError("고정 질문 또는 원문 지문이 다릅니다.")
    config = json.loads((archive / "results.json").read_bytes())
    if config["limits"] != {
        "candidates": CANDIDATES,
        "final_pages": FINAL_K,
        "tokens": BUDGET,
    }:
        raise ValueError("보관 후보·문맥 한도가 다릅니다.")
    saved = list(read_rows(archive / "rankings.jsonl"))
    by_id = {row["id"]: row for row in saved if row["arm"] == "baseline"}
    if len(saved) != len(by_id) or len(by_id) != len(questions):
        raise ValueError("보관 문항과 기준선 순위 수가 다릅니다.")
    restored = replay(questions, pages, archive, manifest)
    for (qid, _), (pool, ranked, baseline, _, _) in restored.items():
        if any(
            len(order) != CANDIDATES or len(page_keys(order)) != CANDIDATES
            for order in (pool, ranked)
        ):
            raise ValueError(f"{qid}의 보관 순위가 서로 다른 20쪽이 아닙니다.")
        if (
            context_sources(baseline)
            != [(row["book_id"], row["page"]) for row in by_id[qid]["packed"]]
            or baseline.token_count != by_id[qid]["context_tokens"]
        ):
            raise ValueError(f"{qid}의 보관 문맥 구성 또는 토큰 수가 다릅니다.")
    return restored, manifest


def page_keys(order):
    return {location(page) for page, _ in order}


def context_sources(context):
    return [(item.chunk.book_id, item.chunk.page) for item in context.final_chunks]


def selection_eligible(prediction):
    return len(prediction) == 2 and all(prediction.values())


def select(question, ranked, lookup, baseline, prediction):
    # The selector source is fingerprinted by the earlier holdout. Inject only
    # its requirements and shared budget for this sequential offline replay.
    eligible = prediction if selection_eligible(prediction) else {}
    with patch.multiple(
        advanced_pair_context,
        pair_requirements=lambda _question, _lookup: eligible,
        CONTEXT_TOKENS=BUDGET,
    ):
        return advanced_pair_context.pack_pair_context(
            question, ranked, lookup, baseline
        )


def run_arms(questions, candidates, lookup):
    retrievals, predictions = {}, {}
    for q in questions:
        qid, question = q["id"], q["question"]
        pool, ranked, baseline, _, _ = candidates[qid, "baseline"]
        source_order = [location(page) for page, _ in ranked]
        for arm, rule in ARMS.items():
            prediction = rule(question, lookup)
            ordered, context, route = select(
                question, ranked, lookup, baseline, prediction
            )
            sources = context_sources(context)
            if (
                [location(page) for page, _ in ranked] != source_order
                or len(ordered) != CANDIDATES
                or page_keys(ordered) != set(source_order)
                or len(sources) > FINAL_K
                or len(sources) != len(set(sources))
                or not set(sources) <= set(source_order)
                or context.token_count > BUDGET
            ):
                raise ValueError(
                    f"{qid} · {arm}의 후보 또는 최종 문맥 한도가 다릅니다."
                )
            retrievals[qid, arm] = pool, ordered, context, (), route
            predictions[qid, arm] = prediction
    return retrievals, predictions


def score(ref, ranked, retrievals, predictions, lookup):
    qid, kind = ref["id"], ref["question_type"]
    is_pair = kind in PAIR_TYPES
    cases = ref["case_keys"]
    if is_pair and len(set(cases)) != 2:
        raise ValueError(f"{qid}에 두 개의 서로 다른 사건이 필요합니다.")
    needed = required_slots(ref, lookup.page_roles) if is_pair else set()
    candidate_slots = needed & {lookup.page_roles.get(key) for key in page_keys(ranked)}
    candidate_hits = (
        fact_hits(ref, [page for page, _ in ranked]) if kind != "clarify" else None
    )
    row = {
        "id": qid,
        "type": kind,
        "candidate_complete": all(candidate_hits) if candidate_hits else None,
        "facts_required": len(candidate_hits) if candidate_hits else None,
        "candidate_facts_missed": missed_indices(candidate_hits),
        "required_slots": len(needed) if is_pair else None,
        "candidate_required_slots": len(candidate_slots) if is_pair else None,
        "arms": {},
    }
    for arm in ARMS:
        prediction = predictions[qid, arm]
        _, _, context, _, route = retrievals[qid, arm]
        final_hits = fact_hits(ref, context) if candidate_hits is not None else None
        if final_hits is not None and any(
            final and not candidate
            for final, candidate in zip(final_hits, candidate_hits, strict=True)
        ):
            raise ValueError(f"{qid} · {arm}의 최종 근거가 보관 후보에 없습니다.")
        slots = {(case, role) for case, roles in prediction.items() for role in roles}
        targets_exact = set(prediction) == set(cases)
        row["arms"][arm] = {
            "targets_detected": len(prediction),
            "paired_targets_detected": len(prediction) == 2,
            "selection_eligible": selection_eligible(prediction),
            "target_hits": len(set(prediction).intersection(cases))
            if is_pair
            else None,
            "targets_exact": targets_exact if is_pair else None,
            "all_required_roles": targets_exact and needed <= slots
            if is_pair
            else None,
            "roles_exact": needed == slots if is_pair else None,
            "slot_hits": len(needed & slots) if is_pair else None,
            "slots_predicted": len(slots) if is_pair else None,
            "targets": [
                {
                    "target": side,
                    "recognized": case in prediction,
                    "required_roles": sorted(
                        role for key, role in needed if key == case
                    ),
                    "candidate_required_roles": sorted(
                        role for key, role in candidate_slots if key == case
                    ),
                    "predicted_roles": list(prediction.get(case, ())),
                }
                for side, case in zip("AB", cases, strict=True)
            ]
            if is_pair
            else [],
            "full_evidence": all(final_hits) if final_hits else None,
            "final_facts_missed": missed_indices(final_hits),
            "final_pages": len(context.final_chunks),
            "context_tokens": context.token_count,
            "budget_excluded_pages": len(context.excluded_chunk_ids),
            "route": route,
        }
    before, after = (row["arms"][arm] for arm in ARMS)
    row["change"] = {
        metric: None
        if before[metric] is None
        else int(after[metric]) - int(before[metric])
        for metric in (
            "targets_exact",
            "all_required_roles",
            "roles_exact",
            "full_evidence",
        )
    }
    row["change"]["context_changed"] = (
        len({retrievals[qid, arm][2].context_hash for arm in ARMS}) > 1
    )
    if candidate_hits is not None:
        before_missed = set(before["final_facts_missed"])
        after_missed = set(after["final_facts_missed"])
        row["change"]["fact_indices_gained"] = sorted(before_missed - after_missed)
        row["change"]["fact_indices_lost"] = sorted(after_missed - before_missed)
    else:
        row["change"]["fact_indices_gained"] = None
        row["change"]["fact_indices_lost"] = None
    return row


def missed_indices(hits):
    return (
        [i for i, hit in enumerate(hits, start=1) if not hit]
        if hits is not None
        else None
    )


def summarize(rows):
    summary = {"questions": len(rows), "arms": {}, "changes": {}}
    summary["candidate_complete"] = sum(
        row["candidate_complete"] is True for row in rows
    )
    for arm in ARMS:
        results = [row["arms"][arm] for row in rows]
        summary["arms"][arm] = {
            key: sum(row[key] is True for row in results)
            for key in (
                "paired_targets_detected",
                "selection_eligible",
                "targets_exact",
                "all_required_roles",
                "roles_exact",
                "full_evidence",
            )
        } | {
            "target_hits": sum(row["target_hits"] or 0 for row in results),
            "any_target_detected": sum(row["targets_detected"] > 0 for row in results),
            "slot_hits": sum(row["slot_hits"] or 0 for row in results),
            "slots_predicted": sum(row["slots_predicted"] or 0 for row in results),
            "final_facts_hit": sum(
                row["facts_required"] - len(result["final_facts_missed"])
                for row, result in zip(rows, results, strict=True)
                if row["facts_required"] is not None
            ),
            "routes": dict(Counter(row["route"] for row in results)),
            "mean_final_pages": fmean(row["final_pages"] for row in results),
            "mean_context_tokens": fmean(row["context_tokens"] for row in results),
        }
    summary["targets_required"] = 2 * sum(row["type"] in PAIR_TYPES for row in rows)
    summary["slots_required"] = sum(row["required_slots"] or 0 for row in rows)
    summary["candidate_required_slots"] = sum(
        row["candidate_required_slots"] or 0 for row in rows
    )
    summary["facts_required"] = sum(row["facts_required"] or 0 for row in rows)
    summary["context_changed"] = [
        row["id"] for row in rows if row["change"]["context_changed"]
    ]
    for metric in (
        "targets_exact",
        "all_required_roles",
        "roles_exact",
        "full_evidence",
    ):
        summary["changes"][metric] = {
            "gained": [row["id"] for row in rows if row["change"][metric] == 1],
            "lost": [row["id"] for row in rows if row["change"][metric] == -1],
        }
    summary["changes"]["any_fact"] = {
        change: [row["id"] for row in rows if row["change"][f"fact_indices_{change}"]]
        for change in ("gained", "lost")
    }
    return summary


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    archives = {}
    contexts_saved = 0
    for split, (dataset, archive_name, _, _) in INPUTS.items():
        data, archive = (CACHE_PATH.parent / name for name in (dataset, archive_name))
        questions = load_questions(data / "questions.json")
        frozen, manifest = archived_candidates(data, archive, questions, pages)
        log(f"{split} · 보관 후보 순서와 8,000토큰 기준 문맥 {len(questions)}개 재현")
        retrievals, predictions = run_arms(questions, frozen, lookup)
        out = output / split
        out.mkdir()
        save_rankings(out, retrievals)
        contexts_saved += len(retrievals)
        archives[split] = data, archive, frozen, manifest, retrievals, predictions

    log(
        f"두 비교군 문맥 {contexts_saved}개 저장 · 후보 집합 변경 0건 · 참고 근거 채점 시작"
    )
    datasets, all_rows = {}, []
    for split, (
        data,
        archive,
        frozen,
        manifest,
        retrievals,
        predictions,
    ) in archives.items():
        if validate_manifest(data) != manifest:
            raise ValueError("평가 입력이 재생 도중 바뀌었습니다.")
        refs = load_references(data / "references.json")
        if refs.keys() != {qid for qid, _ in frozen}:
            raise ValueError("질문과 참고 자료의 ID가 다릅니다.")
        rows = [
            score(refs[qid], ranked, retrievals, predictions, lookup) | {"split": split}
            for (qid, _), (_, ranked, _, _, _) in frozen.items()
        ]
        pairs = [row for row in rows if row["type"] in PAIR_TYPES]
        controls = [row for row in rows if row["type"] not in PAIR_TYPES]
        if (len(pairs), len(controls)) != INPUTS[split][2:]:
            raise ValueError(f"{split}의 실험 문항 수가 다릅니다.")
        groups = {
            "pair": pairs,
            "pair_candidate_complete": [
                row for row in pairs if row["candidate_complete"]
            ],
            **{
                kind: [row for row in rows if row["type"] == kind]
                for kind in (*sorted(PAIR_TYPES), "single", "clarify")
            },
        }
        datasets[split] = {
            "manifest": manifest,
            "archive": {
                name: fingerprint(archive / name)
                for name in ("results.json", "rankings.jsonl")
            },
            "summary": {key: summarize(group) for key, group in groups.items()},
        }
        all_rows.extend(rows)
        changes = datasets[split]["summary"]["pair"]["changes"]["full_evidence"]
        log(
            f"{split} · 전체 근거 개선 {len(changes['gained'])} · 하락 {len(changes['lost'])}"
        )

    write_rows(output / "questions.jsonl", all_rows)
    write_json(
        output / "results.json",
        {
            "limits": {
                "candidates": CANDIDATES,
                "final_pages": FINAL_K,
                "tokens": BUDGET,
            },
            "candidate_membership_changes": 0,
            "external_api_calls": 0,
            "code": {
                name: fingerprint(Path(path))
                for name, path in {
                    "runner": __file__,
                    "selector": advanced_pair_context.__file__,
                    "clause_binding": clause_requirements.__code__.co_filename,
                }.items()
            },
            "datasets": datasets,
            "seconds": round(perf_counter() - started, 3),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(local_output(parser.parse_args().output))


if __name__ == "__main__":
    main()
