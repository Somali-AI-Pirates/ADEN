"""Compare two frozen retrieval paths on Advanced work questions."""

import argparse
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from statistics import fmean

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    load_cases,
    load_profiles,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    CONTEXT_TOKENS,
    FINAL_K,
    KURE,
    MAX_TOKENS,
    MODELS,
    RERANKED_HEAD,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_usage_blind import reference_locations
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index, rank_pages

ARMS = ("bm25", "hybrid_55")


def retrieve_questions(questions):
    pages, vectors = load_index()
    manifest = sha256((CURATED / "manifest.json").read_bytes()).hexdigest()
    cases = load_cases(CASE_INDEX, pages, manifest)
    profiles = ProfileIndex(load_profiles(CURATED, cases))
    index = PatternIndex(pages)
    lexical = [index.fielded(q["question"], top_k=CANDIDATES) for q in questions]
    query_vectors = load_model().encode_query(
        [q["question"] for q in questions],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    dense = [rank_pages(v, vectors, pages, top_k=CANDIDATES) for v in query_vectors]
    pools = {
        ARMS[0]: lexical,
        ARMS[1]: [
            weighted_rrf(b, d, (0.5, 0.5), top_k=CANDIDATES)
            for b, d in zip(lexical, dense, strict=True)
        ],
    }
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    ranked = rerank_variants(reranker, questions, pools, print)
    contexts = {}
    for arm, candidates in pools.items():
        for q, pool, reranked in zip(questions, candidates, ranked[arm], strict=True):
            order = retain_reranked_head(pool, reranked)
            expanded, route = rank_with_hg_lookup(q["question"], order, cases, profiles)
            contexts[q["id"], arm] = (
                {location(p) for p, _ in pool},
                packed_context(expanded),
                route,
            )
    return contexts


def score_questions(questions, contexts, references):
    if {q["id"] for q in questions} != set(references):
        raise ValueError("질문과 참고 파일의 ID가 다릅니다.")
    rows = []
    for q in questions:
        qid = q["id"]
        ref = references[qid]
        expected = reference_locations(ref)
        metrics = {}
        for arm in ARMS:
            pool, context, route = contexts[qid, arm]
            facts = fact_coverage(ref, context)
            metrics[arm] = {
                "lookup_route": route,
                "candidate_page_hits": len(pool & expected),
                "required_pages": len(expected),
                "context_tokens": context.token_count,
                **facts,
            }
        rows.append(
            {
                "id": qid,
                "action": ref["expected_action"],
                "family": ref["source_family"],
                "style": ref["identifier_mode"],
                "arms": metrics,
            }
        )
    return rows


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups[f"action:{row['action']}"].append(row)
        groups[f"family:{row['family']}"].append(row)
        groups[f"style:{row['style']}"].append(row)
        if row["action"] == "answer":
            count = row["arms"][ARMS[0]]["required_facts"]
            groups[f"answer_facts:{count}"].append(row)
    return {
        name: {
            "questions": len(items),
            "hybrid_only_complete": sum(
                r["arms"][ARMS[1]]["all_facts"] > r["arms"][ARMS[0]]["all_facts"]
                for r in items
            ),
            "bm25_only_complete": sum(
                r["arms"][ARMS[0]]["all_facts"] > r["arms"][ARMS[1]]["all_facts"]
                for r in items
            ),
            "arms": {
                arm: {
                    "complete": sum(r["arms"][arm]["all_facts"] for r in items),
                    "facts_found": sum(
                        r["arms"][arm]["required_facts_hit"] for r in items
                    ),
                    "facts_expected": sum(
                        r["arms"][arm]["required_facts"] for r in items
                    ),
                    "candidate_page_hits": sum(
                        r["arms"][arm]["candidate_page_hits"] for r in items
                    ),
                    "candidate_pages_expected": sum(
                        r["arms"][arm]["required_pages"] for r in items
                    ),
                    "mean_context_tokens": fmean(
                        r["arms"][arm]["context_tokens"] for r in items
                    ),
                }
                for arm in ARMS
            },
        }
        for name, items in groups.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", choices=("pilot", "blind"), default="pilot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("새 출력 경로가 필요합니다.")
    questions_path = args.data / f"{args.split}_questions.json"
    refs_path = args.data / f"{args.split}_references.json"
    questions = load_questions(questions_path)
    contexts = retrieve_questions(questions)
    rows = score_questions(questions, contexts, load_references(refs_path))
    args.output.mkdir(parents=True)
    write_rows(args.output / "questions.jsonl", rows)
    write_json(args.output / "summary.json", summarize(rows))
    write_json(
        args.output / "config.json",
        {
            "split": args.split,
            "input_sha256": {
                path.name: sha256(path.read_bytes()).hexdigest()
                for path in (questions_path, refs_path, CACHE_PATH, VECTORS_PATH)
            },
            "candidates": CANDIDATES,
            "reranked_head": RERANKED_HEAD,
            "context_pages": FINAL_K,
            "context_tokens": CONTEXT_TOKENS,
            "reranker": {"model": KURE, "revision": MODELS[KURE]},
            "hybrid": {
                "embedding_model": MODEL_ID,
                "revision": MODEL_REVISION,
                "rrf_k": RRF_K,
                "weights": [0.5, 0.5],
            },
            "generation_calls": 0,
        },
    )
    print(json.dumps(summarize(rows)["all"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
