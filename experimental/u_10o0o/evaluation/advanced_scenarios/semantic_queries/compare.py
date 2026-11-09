"""Compare lexical and hybrid candidates on the Advanced everyday wording suite."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, rank_with_hg_lookup
from experimental.u_10o0o.advanced_pair_holdout import fact_hits
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import PEER, peer_rankings, start_log
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    FINAL_K,
    KURE,
    MAX_TOKENS,
    MODELS,
    SELECTED,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_source_lookup import selected_context
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.hybrid import RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index, rank_pages

DATA = Path(__file__).resolve().parent
BASE = "bm25"
HYBRID = "rrf_55"
ARMS = (BASE, HYBRID, PEER)
BUDGET = 8000
WEIGHTS = (0.5, 0.5)


def personal(questions, pages, vectors, lookup, storage, log):
    index = PatternIndex(pages)
    queries = [q["question"] for q in questions]
    sparse = [index.fielded(query, top_k=CANDIDATES) for query in queries]
    embeddings = load_model().encode_query(
        queries, normalize_embeddings=True, show_progress_bar=False
    )
    hybrid = [
        weighted_rrf(
            pool,
            rank_pages(vector, vectors, pages, top_k=CANDIDATES),
            WEIGHTS,
            top_k=CANDIDATES,
        )
        for pool, vector in zip(sparse, embeddings, strict=True)
    ]
    log(f"BM25·Snowflake-ko 후보 준비 · {len(questions)}문항")
    pools = {BASE: sparse, HYBRID: hybrid}
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    reranked = rerank_variants(reranker, questions, pools, log)
    for arm, candidate_rows in pools.items():
        for q, candidates, rank in zip(
            questions, candidate_rows, reranked[arm], strict=True
        ):
            query = q["question"]
            linked, hg_route = rank_with_hg_lookup(
                query,
                retain_reranked_head(candidates, rank),
                lookup.cases,
                lookup.profile_index,
            )
            order, context, cases, source_route, selection_route = selected_context(
                query, linked[:CANDIDATES], lookup, storage, BUDGET
            )
            yield (
                (q["id"], arm),
                (
                    candidates,
                    order,
                    context,
                    cases,
                    f"{hg_route}/{source_route}/{selection_route}",
                ),
            )


def score(ref, retrievals):
    qid = ref["id"]
    gold = reference_locations(ref)
    row = {
        "id": qid,
        "variant": ref["variant"],
        "required_documents": ref["required_document_count"],
        "required_facts": len(ref["evidence_requirements"]),
        "arms": {},
    }
    if not gold or not row["required_facts"]:
        raise ValueError(f"{qid}에 필수 근거가 없습니다.")
    for arm in ARMS:
        pool, expanded, context, _, route = retrievals[qid, arm]
        pages = {(item.chunk.book_id, item.chunk.page) for item in context.final_chunks}
        metrics = {
            "route": route,
            "context_tokens": context.token_count,
            "page_recall_at_8": len(gold & pages) / len(gold),
        }
        for stage, material in (
            ("candidate", [page for page, _ in pool]),
            ("expanded", [page for page, _ in expanded]),
            ("final", context),
        ):
            hits = fact_hits(ref, material)
            metrics[f"{stage}_complete"] = all(hits)
            metrics[f"{stage}_hits"] = sum(hits)
            metrics[f"{stage}_missed"] = [i for i, hit in enumerate(hits, 1) if not hit]
        row["arms"][arm] = metrics
    before, after = (row["arms"][arm] for arm in (BASE, HYBRID))
    row["hybrid_vs_bm25"] = {}
    for stage in ("candidate", "final"):
        missed_before, missed_after = (
            set(arm[f"{stage}_missed"]) for arm in (before, after)
        )
        row["hybrid_vs_bm25"][stage] = {
            "gained_facts": sorted(missed_before - missed_after),
            "lost_facts": sorted(missed_after - missed_before),
            "complete_delta": int(after[f"{stage}_complete"])
            - int(before[f"{stage}_complete"]),
        }
    return row


def summarize(rows):
    result = {
        "questions": len(rows),
        "required_facts": sum(q["required_facts"] for q in rows),
        "arms": {},
    }
    for arm in ARMS:
        values = [row["arms"][arm] for row in rows]
        result["arms"][arm] = {
            **{
                f"{stage}_{metric}": sum(row[f"{stage}_{metric}"] for row in values)
                for stage in ("candidate", "expanded", "final")
                for metric in ("complete", "hits")
            },
            "page_recall_at_8": fmean(row["page_recall_at_8"] for row in values),
            "routes": dict(Counter(row["route"] for row in values)),
        }
    result["hybrid_vs_bm25"] = {}
    for stage in ("candidate", "final"):
        changes = [row["hybrid_vs_bm25"][stage] for row in rows]
        result["hybrid_vs_bm25"][stage] = {
            "complete_gained": [
                q["id"]
                for q, change in zip(rows, changes, strict=True)
                if change["complete_delta"] == 1
            ],
            "complete_lost": [
                q["id"]
                for q, change in zip(rows, changes, strict=True)
                if change["complete_delta"] == -1
            ],
            "facts_gained": sum(len(change["gained_facts"]) for change in changes),
            "facts_lost": sum(len(change["lost_facts"]) for change in changes),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = local_output(parser.parse_args().output)
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    qpath, rpath = (DATA / f"{name}.json" for name in ("questions", "references"))
    stamps = manifest["files_sha256"]
    if (
        fingerprint(qpath)["sha256"] != stamps[qpath.name]
        or fingerprint(CACHE_PATH)["sha256"] != manifest["restored_pages_sha256"]
    ):
        raise ValueError("평가 질문 또는 복원 원문 지문이 다릅니다.")
    questions = load_questions(qpath)
    if len(questions) != manifest["questions"] or len(questions) != 40:
        raise ValueError("일상 표현 40문항이 필요합니다.")
    started, log = start_log(output)
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    retrievals = dict(personal(questions, pages, vectors, lookup, storage, log))
    peer, peer_config = peer_rankings(questions, pages, output, BUDGET, log)
    retrievals.update(peer)
    if (
        retrievals.keys() != {(q["id"], arm) for q in questions for arm in ARMS}
        or peer_config["returned"]["missing_pages"]
    ):
        raise ValueError("평가 문항 또는 비교 설정의 복원 페이지가 다릅니다.")
    for (_, arm), (pool, expanded, context, _, _) in retrievals.items():
        if (
            any(
                len(order) != len({location(page) for page, _ in order})
                or not 0 < len(order) <= CANDIDATES
                or (arm != PEER and len(order) != CANDIDATES)
                for order in (pool, expanded)
            )
            or len(context.final_chunks) > FINAL_K
            or context.token_count > BUDGET
        ):
            raise ValueError("후보 또는 최종 문맥 한도가 다릅니다.")
    save_rankings(output, retrievals)
    log(f"검색 문맥 {len(retrievals)}개 저장 · 참고 근거 채점 시작")
    if fingerprint(rpath)["sha256"] != stamps[rpath.name]:
        raise ValueError("참고 근거 지문이 다릅니다.")
    refs = load_references(rpath)
    if (
        refs.keys() != {q["id"] for q in questions}
        or Counter(r["variant"] for r in refs.values()) != manifest["variants"]
        or Counter(str(r["required_document_count"]) for r in refs.values())
        != manifest["required_document_count_distribution"]
    ):
        raise ValueError("문항 유형·요구 문서 수가 고정 구성과 다릅니다.")
    rows = [score(refs[q["id"]], retrievals) for q in questions]
    groups = {
        "all": rows,
        "single_document": [q for q in rows if q["required_documents"] == 1],
        "multi_document": [q for q in rows if q["required_documents"] > 1],
        **{
            name: [q for q in rows if q["variant"] == name]
            for name in manifest["variants"]
        },
    }
    summary = {name: summarize(group) for name, group in groups.items()}
    write_rows(output / "scores.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "inputs": {
                path.name: fingerprint(path)
                for path in (DATA / "manifest.json", qpath, rpath)
            },
            "source": {
                "pages": fingerprint(CACHE_PATH),
                "case_links": fingerprint(CASE_INDEX / "case_sources.jsonl"),
            },
            "code": {
                "runner": fingerprint(Path(__file__)),
                "source_lookup": fingerprint(
                    Path(__file__).parents[3] / "advanced_source_lookup.py"
                ),
            },
            "limits": {
                "candidates": CANDIDATES,
                "pages": FINAL_K,
                "context_tokens": BUDGET,
            },
            "personal": {
                "baseline": SELECTED,
                "reranker": KURE,
                "revision": MODELS[KURE],
            },
            "hybrid": {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "vectors": fingerprint(VECTORS_PATH),
                "weights": WEIGHTS,
                "rrf_k": RRF_K,
            },
            "peer": peer_config,
            "external_api_calls": 0,
            "summary": summary,
        },
    )
    log(
        "최종 전체 근거 · "
        + " · ".join(
            f"{arm} {summary['all']['arms'][arm]['final_complete']}/40" for arm in ARMS
        )
    )


if __name__ == "__main__":
    main()
