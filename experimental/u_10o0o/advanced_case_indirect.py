"""Measure source-verified indirect HG lookup against explicit-ID lookup."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import fmean, median
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_case_lookup import (
    ARCHIVE,
    LATENCY_SAMPLE,
    METRICS,
    rank_fielded,
    score,
)
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_pattern_full import BATCH_SIZE, DATA, input_files
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    PROFILE_ROLES,
    ProfileIndex,
    augment,
    linked_pages,
    load_cases,
    load_profiles,
)
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    RECORDS_DIR,
    fingerprint,
)
from experimental.u_10o0o.prepare import CACHE_PATH

EXPLICIT = RECORDS_DIR / "advanced-curation/hg-explicit-lookup-20261109"
ARMS = ("explicit_lookup", "indirect_lookup")


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        split = "overlap" if row["blind_related"] else "disjoint"
        groups["all"].append(row)
        groups[split].append(row)
        groups[f"{split}:identifier:{row['identifier_mode']}"].append(row)
        groups[f"{split}:type:{row['question_type']}"].append(row)
        if row["route"] == "triggered":
            groups[f"{split}:triggered"].append(row)
    return {
        name: {
            "questions": len(items),
            "routes": dict(Counter(row["route"] for row in items)),
            "wrong_cases": sum(row["wrong_case"] for row in items),
            "all_linked_pages_in_context": sum(
                row["all_linked_pages_in_context"] for row in items
            ),
            "archived_control_differences": sum(
                row["archived_control_differs"] for row in items
            ),
            "gained_all_facts": sum(
                row["arms"][ARMS[1]]["all_facts"] > row["arms"][ARMS[0]]["all_facts"]
                for row in items
            ),
            "lost_all_facts": sum(
                row["arms"][ARMS[1]]["all_facts"] < row["arms"][ARMS[0]]["all_facts"]
                for row in items
            ),
            "arms": {
                arm: {
                    key: fmean(row["arms"][arm][key] for row in items)
                    for key in METRICS
                }
                for arm in ARMS
            },
        }
        for name, items in groups.items()
    }


def latency(index, reranker, questions, profiles, cases):
    sample = sorted(questions, key=lambda q: sha256(q["id"].encode()).digest())[
        :LATENCY_SAMPLE
    ]
    if not sample:
        return {}

    def request(q, arm):
        start = perf_counter()
        ranked = rank_fielded(index, reranker, [q], lambda _message: None)[0]
        if arm == ARMS[1]:
            case, _ = profiles.candidate(q["question"])
            if case is not None:
                ranked = augment(ranked, linked_pages(ranked, cases[case]))
        packed_context(ranked)
        return (perf_counter() - start) * 1000

    timings = {arm: [] for arm in ARMS}
    for arm in ARMS:
        request(sample[0], arm)
    for i, q in enumerate(sample):
        for arm in ARMS if i % 2 == 0 else ARMS[::-1]:
            timings[arm].append(request(q, arm))
    return {
        "question_ids": [q["id"] for q in sample],
        "scope": "warm single-query search, KURE, lookup and context packing on implicit queries",
        "arms": {
            arm: {
                "p50_ms": median(values),
                "p95_ms": sorted(values)[ceil(0.95 * len(values)) - 1],
            }
            for arm, values in timings.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 CUDA FP16 환경이 필요합니다.")

    started = perf_counter()
    questions, inputs = input_files()
    archive = json.loads((ARCHIVE / "config.json").read_bytes())
    explicit = json.loads((EXPLICIT / "config.json").read_bytes())
    case_summary = json.loads((args.case_index / "summary.json").read_bytes())
    curated_sha = sha256((args.curated / "manifest.json").read_bytes()).hexdigest()
    if (
        inputs != explicit["inputs"]
        or fingerprint(CACHE_PATH) != explicit["pages"]
        or explicit["kure"] != archive["kure"]
        or explicit["kure"]["gpu"] != torch.cuda.get_device_name()
        or case_summary != explicit["case_index"]
        or curated_sha != case_summary["curation_manifest_sha256"]
    ):
        raise ValueError("명시 조회 벤치와 입력 또는 실행 조건이 다릅니다.")

    pages = json.loads(CACHE_PATH.read_bytes())
    cases = load_cases(args.case_index, pages, curated_sha)
    attributes = load_profiles(args.curated, cases)
    profiles = ProfileIndex(attributes)
    decisions = {q["id"]: profiles.candidate(q["question"]) for q in questions}
    active = [q for q in questions if decisions[q["id"]][0] is not None]
    args.output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    log(
        f"간접 식별 시작 · 출처 확인 {len(attributes):,}사건 · 후보 {len(active):,}/{len(questions):,}문항"
    )
    index = PatternIndex(pages)
    if json.loads(json.dumps(index.config)) != archive["retrieval"]:
        raise ValueError("보관된 제목 보너스 검색 설정과 다릅니다.")
    index.base_index = FastBM25(index.base_index)
    if index.heading_index is not None:
        index.heading_index = FastBM25(index.heading_index)
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    write_json(
        args.output / "config.json",
        {
            **{
                key: explicit[key]
                for key in (
                    "inputs",
                    "pages",
                    "candidate_count",
                    "final_k",
                    "context_tokens",
                    "kure",
                )
            },
            "explicit_archive": {
                name: fingerprint(EXPLICIT / name)
                for name in ("config.json", "questions.jsonl")
            },
            "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "case_index": case_summary,
            "profiles": len(attributes),
            "profile_roles": PROFILE_ROLES,
            "unique_profiles": sum(len(ids) == 1 for ids in profiles.cases.values()),
            "cue_fields": ["shipper", "cargo", "equipment_spec"],
            "pre_search_routes": dict(
                Counter(reason for _, reason in decisions.values())
            ),
            "generation_calls": 0,
        },
    )

    packed = {}
    for start in range(0, len(active), BATCH_SIZE):
        batch = active[start : start + BATCH_SIZE]
        rankings = rank_fielded(index, reranker, batch, log)
        for q, order in zip(batch, rankings, strict=True):
            qid = q["id"]
            case, _ = decisions[qid]
            linked = linked_pages(order, cases[case])
            if linked is None:
                decisions[qid] = None, "not_in_candidates"
                continue
            expanded = augment(order, linked)
            packed[qid] = (
                order,
                packed_context(order),
                expanded,
                packed_context(expanded),
            )
            decisions[qid] = case, "triggered"
        log(
            f"검색 확인 · {min(start + BATCH_SIZE, len(active)):,}/{len(active):,}문항 · 보강 {len(packed):,}문항"
        )

    references = load_references(DATA / "references.json")
    previous = {row["id"]: row for row in read_rows(EXPLICIT / "questions.jsonl")}
    if set(decisions) != set(previous) or set(decisions) != set(references):
        raise ValueError("문항·정답·명시 조회 결과의 ID가 다릅니다.")
    rows = []
    for q in questions:
        qid = q["id"]
        archived = previous[qid]
        ref = references[qid]
        case, route = decisions[qid]
        base = changed = archived["arms"]["case_lookup"]
        wrong = complete = differs = False
        if case is not None:
            order, context, expanded, augmented = packed[qid]
            base = score(q, order, context, ref)
            changed = score(q, expanded, augmented, ref)
            differs = base != archived["arms"]["case_lookup"]
            wrong = case != ref["case_id"]
            cited = {
                (item.book_id, item.page) for item in augmented.source_map.values()
            }
            complete = {location(page) for page in cases[case]} <= cited
        rows.append(
            {
                "id": qid,
                "blind_related": archived["blind_related"],
                "question_type": ref["question_type"],
                "identifier_mode": ref["identifier_mode"],
                "route": route,
                "wrong_case": wrong,
                "all_linked_pages_in_context": complete,
                "archived_control_differs": differs,
                "arms": dict(zip(ARMS, (base, changed), strict=True)),
            }
        )
    write_rows(args.output / "questions.jsonl", rows)
    write_json(
        args.output / "latency.json", latency(index, reranker, active, profiles, cases)
    )
    write_json(args.output / "summary.json", summarize(rows))
    log(f"6,500문항 평가 완료 · 경과 {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
