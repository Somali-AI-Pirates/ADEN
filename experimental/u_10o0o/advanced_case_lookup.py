"""Measure explicit HG case lookup against the Advanced heading bonus."""

import argparse
import json
from collections import defaultdict
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
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    augment,
    load_cases,
    matched_case,
)
from experimental.u_10o0o.advanced_pattern_full import BATCH_SIZE, DATA, input_files
from experimental.u_10o0o.advanced_patterns import (
    location,
    packed_context,
    score_variant,
    write_json,
)
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    PREDICT_BATCH_SIZE,
    RECORDS_DIR,
    fingerprint,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.prepare import CACHE_PATH

ARCHIVE = RECORDS_DIR / "advanced-industry-patterns-20261108/full-coverage"
METRICS = (
    "page_recall@8",
    "doc_recall@8",
    "quote_recall",
    "fact_recall",
    "all_facts",
    "context_tokens",
    "context_chunks",
)
ARMS = ("fielded", "case_lookup")
LATENCY_SAMPLE = 16


def rank_fielded(index, reranker, questions, log):
    sparse = [index.fielded(q["question"], top_k=CANDIDATES) for q in questions]
    reranked = rerank_variants(reranker, questions, {"fielded": sparse}, log)["fielded"]
    return [
        retain_reranked_head(pool, rank)
        for pool, rank in zip(sparse, reranked, strict=True)
    ]


def score(question, ranked, context, reference):
    scored = score_variant(question, ranked, context, reference)
    values = scored["metrics"] | fact_coverage(reference, context)
    values.update(
        context_tokens=context.token_count, context_chunks=len(context.final_chunks)
    )
    return {key: values[key] for key in METRICS}


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        split = "overlap" if row["blind_related"] else "disjoint"
        groups[split].append(row)
        gate = "triggered" if row["triggered"] else "fallback"
        groups[gate].append(row)
        groups[f"{split}:{gate}"].append(row)
        groups[f"{split}:type:{row['question_type']}"].append(row)
        groups[f"{split}:identifier:{row['identifier_mode']}"].append(row)

    return {
        name: {
            "questions": len(items),
            "triggered": sum(row["triggered"] for row in items),
            "changed_contexts": sum(row["context_changed"] for row in items),
            "wrong_cases": sum(row["wrong_case"] for row in items),
            "all_linked_pages_in_context": sum(
                row["all_linked_pages_in_context"] for row in items
            ),
            "archived_control_differences": sum(
                row["archived_control_differs"] for row in items
            ),
            "gained_all_facts": sum(
                row["arms"]["case_lookup"]["all_facts"]
                > row["arms"]["fielded"]["all_facts"]
                for row in items
            ),
            "lost_all_facts": sum(
                row["arms"]["case_lookup"]["all_facts"]
                < row["arms"]["fielded"]["all_facts"]
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


def latency(index, reranker, questions, cases):
    sample = sorted(questions, key=lambda q: sha256(q["id"].encode()).digest())[
        :LATENCY_SAMPLE
    ]
    if not sample:
        return {}

    def request(q, arm):
        started = perf_counter()
        ranked = rank_fielded(index, reranker, [q], lambda _message: None)[0]
        if arm == "case_lookup":
            ranked = augment(ranked, cases.get(matched_case(q["question"], cases)))
        packed_context(ranked)
        return (perf_counter() - started) * 1000

    timings = {arm: [] for arm in ARMS}
    for arm in ARMS:
        request(sample[0], arm)
    for i, q in enumerate(sample):
        for arm in ARMS if i % 2 == 0 else ARMS[::-1]:
            timings[arm].append(request(q, arm))
    return {
        "question_ids": [q["id"] for q in sample],
        "scope": "warm single-query search, KURE, lookup and context packing",
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
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 KURE FP16 CUDA 실행이 필요합니다.")
    started = perf_counter()
    questions, inputs = input_files()
    archive = json.loads((ARCHIVE / "config.json").read_bytes())
    page_input = fingerprint(CACHE_PATH)
    kure = {
        "model": KURE,
        "revision": MODELS[KURE],
        "max_tokens": MAX_TOKENS,
        "batch_size": PREDICT_BATCH_SIZE,
        "dtype": "float16",
        "gpu": torch.cuda.get_device_name(),
    }
    fixed = {
        "inputs": inputs,
        "pages": page_input,
        "candidate_count": CANDIDATES,
        "final_k": FINAL_K,
        "context_tokens": CONTEXT_TOKENS,
        "kure": kure,
    }
    if any(archive[key] != value for key, value in fixed.items()):
        raise ValueError("보관된 제목 보너스 기준선과 현재 입력 또는 조건이 다릅니다.")
    pages = json.loads(CACHE_PATH.read_bytes())
    cases = load_cases(args.case_index, pages, archive["curation_manifest_sha256"])
    matched = {q["id"]: matched_case(q["question"], cases) for q in questions}
    active = [q for q in questions if matched[q["id"]] is not None]
    if not active:
        raise ValueError("명시 HG 사건이 있는 문항을 찾지 못했습니다.")

    args.output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    log(f"명시 HG 조회 시작 · 발동 {len(active):,}/{len(questions):,}문항")
    index = PatternIndex(pages)
    if index.config != archive["retrieval"]:
        # JSON stores tuples as arrays.
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
            **fixed,
            "case_index": json.loads((args.case_index / "summary.json").read_bytes()),
            "archived_control": {
                name: fingerprint(ARCHIVE / name)
                for name in ("config.json", "questions.jsonl")
            },
            "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "active_questions": len(active),
            "control_replay": "same-run paired scores on triggered queries; archived scores on identical fallback",
            "question_batch_size": BATCH_SIZE,
            "generation_calls": 0,
        },
    )

    packed = {}
    for start in range(0, len(active), BATCH_SIZE):
        batch = active[start : start + BATCH_SIZE]
        rankings = rank_fielded(index, reranker, batch, log)
        for q, order in zip(batch, rankings, strict=True):
            case = matched[q["id"]]
            linked = cases[case]
            expanded = augment(order, linked)
            packed[q["id"]] = (
                order,
                packed_context(order),
                expanded,
                packed_context(expanded),
            )
        log(f"두 조건 문맥 준비 · {len(packed):,}/{len(active):,}문항")

    references = load_references(DATA / "references.json")
    archived_rows = {row["id"]: row for row in read_rows(ARCHIVE / "questions.jsonl")}
    if set(matched) != set(references) or set(matched) != set(archived_rows):
        raise ValueError("질문·정답·기준선의 문항 ID가 다릅니다.")
    rows = []
    for q in questions:
        qid = q["id"]
        reference = references[qid]
        previous = archived_rows[qid]
        archived = {key: previous["arms"]["fielded"][key] for key in METRICS}
        base = lookup = archived
        changed = wrong = complete = False
        case = matched[qid]
        if case is not None:
            order, context, expanded, lookup_context = packed[qid]
            base = score(q, order, context, reference)
            lookup = score(q, expanded, lookup_context, reference)
            changed = context.context_hash != lookup_context.context_hash
            wrong = case != reference["case_id"]
            cited = {
                (item.book_id, item.page) for item in lookup_context.source_map.values()
            }
            complete = {location(page) for page in cases[case]} <= cited
        rows.append(
            {
                "id": qid,
                "blind_related": previous["blind_related"],
                "question_type": reference["question_type"],
                "identifier_mode": reference["identifier_mode"],
                "triggered": case is not None,
                "context_changed": changed,
                "wrong_case": wrong,
                "all_linked_pages_in_context": complete,
                "archived_control_differs": base != archived,
                "arms": {"fielded": base, "case_lookup": lookup},
            }
        )
    write_rows(args.output / "questions.jsonl", rows)
    write_json(args.output / "latency.json", latency(index, reranker, active, cases))
    write_json(args.output / "summary.json", summarize(rows))
    log(f"6,500문항 평가 완료 · 경과 {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
