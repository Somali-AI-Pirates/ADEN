"""Run the two local Advanced retrieval settings on the frozen scenario set."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import (
    BASE,
    PEER,
    pack,
    peer_rankings,
    start_log,
)
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    FINAL_K,
    KURE,
    MAX_TOKENS,
    MODELS,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH

DATA = Path(__file__).resolve().parent
BUDGET = 8000
ACTIVE = ("retrieval", "negative_control")


def personal(questions, pages, lookup, log):
    index = PatternIndex(pages)
    pools = [index.fielded(q["question"], top_k=CANDIDATES) for q in questions]
    log(f"개인 BM25 후보 생성 완료 · {len(questions)}문항")
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    rankings = rerank_variants(reranker, questions, {BASE: pools}, log)[BASE]
    profiles = ProfileIndex(lookup.profiles)
    for question, pool, reranked in zip(questions, pools, rankings, strict=True):
        order = retain_reranked_head(pool, reranked)
        linked, route = rank_with_hg_lookup(
            question["question"], order, lookup.cases, profiles
        )
        linked = linked[:CANDIDATES]
        yield (question["id"], BASE), (pool, linked, pack(linked, BUDGET), (), route)


def assess(retrievals, references, page_case):
    rows = []
    for qid, ref in references.items():
        gold_pages = reference_locations(ref)
        gold_books = {book for book, _ in gold_pages}
        target_cases = set(ref["case_keys"])
        arms = {}
        for arm in (BASE, PEER):
            pool, expanded, context, _, route = retrievals[qid, arm]
            packed = {
                (row.chunk.book_id, row.chunk.page) for row in context.final_chunks
            }
            metrics = {
                "route": route,
                "pool_pages": len(pool),
                "final_pages": len(packed),
                "context_tokens": context.token_count,
                "budget_excluded": len(context.excluded_chunk_ids),
            }
            if ref["evidence_required"]:
                for stage, items in (
                    ("pool", [page for page, _ in pool]),
                    ("expanded", [page for page, _ in expanded]),
                    ("final", context),
                ):
                    result = fact_coverage(ref, items)
                    metrics[f"{stage}_complete"] = bool(result["all_facts"])
                    metrics[f"{stage}_facts"] = result["required_facts_hit"]
                metrics.update(
                    {
                        "page_recall_at_8": len(packed & gold_pages) / len(gold_pages),
                        "doc_recall_at_8": len(
                            gold_books & {book for book, _ in packed}
                        )
                        / len(gold_books),
                        "required_facts": result["required_facts"],
                    }
                )
                if ref["scenario"] in (2, 4, 6):
                    metrics["pool_has_all_cases"] = target_cases <= {
                        page_case.get(location(p)) for p, _ in pool
                    }
                    metrics["final_has_all_cases"] = target_cases <= {
                        page_case.get(key) for key in packed
                    }
            arms[arm] = metrics
        rows.append(
            {
                "id": qid,
                "scenario": ref["scenario"],
                "variant": ref["variant"],
                "arms": arms,
            }
        )
    return rows


def summarize(rows):
    groups = {
        f"S{group:02}": [row for row in rows if row["scenario"] == group]
        for group in range(1, 9)
    }
    groups["retrieval"] = [row for row in rows if row["scenario"] != 8]
    for scenario in (3, 4, 5, 7, 8):
        source = groups[f"S{scenario:02}"]
        for variant in dict.fromkeys(row["variant"] for row in source):
            groups[f"S{scenario:02}/{variant}"] = [
                row for row in source if row["variant"] == variant
            ]
    summary = {}
    for group, selected in groups.items():
        result = {"questions": len(selected), "arms": {}}
        for arm in (BASE, PEER):
            records = [row["arms"][arm] for row in selected]
            result["arms"][arm] = metrics = {
                "nonempty_context": sum(r["final_pages"] > 0 for r in records),
                "mean_final_pages": fmean(r["final_pages"] for r in records),
                "max_context_tokens": max(r["context_tokens"] for r in records),
                "budget_excluded_pages": sum(r["budget_excluded"] for r in records),
                "routes": dict(Counter(r["route"] for r in records)),
            }
            if "final_complete" in records[0]:
                metrics.update(
                    {
                        **{
                            key: sum(r[key] for r in records)
                            for key in (
                                "pool_complete",
                                "expanded_complete",
                                "final_complete",
                                "final_facts",
                                "required_facts",
                            )
                        },
                        **{
                            key: fmean(r[key] for r in records)
                            for key in ("page_recall_at_8", "doc_recall_at_8")
                        },
                    }
                )
                if "final_has_all_cases" in records[0]:
                    metrics["pool_has_all_cases"] = sum(
                        r["pool_has_all_cases"] for r in records
                    )
                    metrics["final_has_all_cases"] = sum(
                        r["final_has_all_cases"] for r in records
                    )
        if "final_complete" in result["arms"][BASE]:
            paired = Counter(
                (
                    row["arms"][BASE]["final_complete"],
                    row["arms"][PEER]["final_complete"],
                )
                for row in selected
            )
            result["paired"] = {
                "personal_only": paired[True, False],
                "peer_only": paired[False, True],
                "both": paired[True, True],
                "neither": paired[False, False],
            }
        summary[group] = result
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = local_output(parser.parse_args().output)
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    inputs = {
        name: fingerprint(DATA / name)
        for name in manifest["files_sha256"]
        if name.startswith(ACTIVE)
    }
    if any(
        file["sha256"] != manifest["files_sha256"][name]
        for name, file in inputs.items()
    ):
        raise ValueError("상황별 평가 입력 지문이 다릅니다.")
    questions = [
        q for split in ACTIVE for q in load_questions(DATA / f"{split}_questions.json")
    ]
    ids = {q["id"] for q in questions}
    if len(questions) != len(ids) or Counter(qid.split("-")[1] for qid in ids) != {
        f"S{i:02}": 20 for i in range(1, 9)
    }:
        raise ValueError("검색 140문항과 범위 밖 20문항이 필요합니다.")
    started, log = start_log(output)
    log(
        f"두 설정 비교 · 검색 140문항·별도 대조 20문항 · 최대 {FINAL_K}쪽·{BUDGET:,}토큰"
    )
    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    retrievals = dict(personal(questions, pages, lookup, log))
    peers, peer_settings = peer_rankings(questions, pages, output, BUDGET, log)
    retrievals.update(peers)
    if set(retrievals) != {(qid, arm) for qid in ids for arm in (BASE, PEER)} or any(
        c.token_count > BUDGET or len(c.final_chunks) > FINAL_K
        for _, _, c, _, _ in retrievals.values()
    ):
        raise ValueError("평가 범위 또는 문맥 한도가 다릅니다.")
    save_rankings(output, retrievals)
    log("문맥 저장 완료 · 채점용 참고 근거 로드")
    references = {
        qid: ref
        for split in ACTIVE
        for qid, ref in load_references(DATA / f"{split}_references.json").items()
    }
    if references.keys() != ids or peer_settings["returned"]["missing_pages"]:
        raise ValueError("정답 ID 또는 동료 복원 페이지를 확인하세요.")
    page_case = {
        location(page): case
        for case, case_pages in lookup.cases.items()
        for page in case_pages
    }
    rows = assess(retrievals, references, page_case)
    write_rows(output / "scores.jsonl", rows)
    elapsed = perf_counter() - started
    write_json(
        output / "results.json",
        {
            "inputs": inputs,
            "manifest": fingerprint(DATA / "manifest.json"),
            "pages": fingerprint(CACHE_PATH),
            "case_links": fingerprint(CASE_INDEX / "case_sources.jsonl"),
            "runner": fingerprint(Path(__file__)),
            "limits": {
                "candidates": CANDIDATES,
                "pages": FINAL_K,
                "context_tokens": BUDGET,
            },
            "personal": {
                "candidate_source": "fielded_bigram_id_bm25",
                "model": KURE,
                "revision": MODELS[KURE],
                "model_tokens": MAX_TOKENS,
                "ordering": "KURE top 3 plus BM25 tail and explicit/indirect HG lookup",
            },
            "peer": peer_settings,
            "external_api_calls": 0,
            "elapsed_seconds": round(elapsed, 2),
            "summary": summarize(rows),
        },
    )
    log(f"검색 근거 채점 완료 · 외부 API 0회 · {elapsed:.1f}초")


if __name__ == "__main__":
    main()
