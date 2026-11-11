"""Run and score the paired local Advanced experiments."""

import json
from collections import Counter, defaultdict
from functools import cached_property
from hashlib import file_digest
from pathlib import Path
from statistics import fmean, median

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import location
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    KURE,
    MAX_TOKENS,
    MODELS,
    OFFICIAL_SPLITS,
    PEER_SPLITS,
    hybrid_candidates,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_source_lookup import complete_sources
from experimental.u_10o0o.advanced_target_benchmark import save_rankings
from experimental.u_10o0o.embeding import load_model
from experimental.u_10o0o.prepare import PROJECT_ROOT


def stamp(path):
    path = path.resolve()
    display = (
        path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    )
    with path.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    return {"path": str(display), "sha256": digest}


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rss_bytes():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("현재 프로세스의 RSS를 확인할 수 없습니다.")


def latency(samples):
    if not samples:
        return None
    return {
        "count": len(samples),
        "p50_ms": median(samples),
        "p95_ms": float(np.percentile(samples, 95)),
    }


class Search:
    def __init__(self, pages, vectors):
        self.pages = pages
        self.vectors = vectors
        self.searches = 0
        self.reranks = 0

    @cached_property
    def index(self):
        return PatternIndex(self.pages)

    @cached_property
    def embedder(self):
        return load_model()

    @cached_property
    def reranker(self):
        if not torch.cuda.is_available():
            raise RuntimeError("Advanced KURE 비교에는 CUDA가 필요합니다.")
        return CrossEncoder(
            KURE,
            revision=MODELS[KURE],
            device="cuda",
            max_length=MAX_TOKENS,
            local_files_only=True,
            model_kwargs={"dtype": torch.float16},
        )

    def rank(self, questions, vectors, log):
        queries = [row["question"] for row in questions]
        sparse = [self.index.fielded(text, top_k=CANDIDATES) for text in queries]
        embeddings = self.embedder.encode_query(
            queries, normalize_embeddings=True, show_progress_bar=False
        )
        pools = {
            arm: hybrid_candidates(self.pages, array, sparse, embeddings)[1]
            for arm, array in vectors.items()
        }
        self.searches += len(queries)
        heads = rerank_variants(self.reranker, questions, pools, log)
        self.reranks += len(queries)
        return pools, heads

    def one(self, question):
        pools, heads = self.rank(
            [{"question": question}], {"baseline": self.vectors}, lambda _: None
        )
        pool = pools["baseline"][0]
        return pool, retain_reranked_head(pool, heads["baseline"][0])


def batches(questions):
    grouped = defaultdict(list)
    for row in questions:
        split = row["split"]
        name = (
            "official"
            if split in OFFICIAL_SPLITS
            else ("semantic" if split == "semantic_queries" else "categories")
        )
        grouped[name].append(row)
    return grouped.values()


def sources(rows):
    return [location(page) for page, _ in rows]


def packed_sources(context):
    return [(chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks]


def finish(question, pool, order, lookup, storage):
    linked, selection, hg_route = complete_sources(
        question, order, lookup, storage, DEFAULT_CONTEXT_TOKENS
    )
    selected, context, cases, source_route, selection_route = selection
    return (
        (
            pool,
            selected,
            context,
            cases,
            f"{hg_route}/{source_route}/{selection_route}",
        ),
        linked,
    )


def trace(retrieval, linked, lookup, order):
    pool, selected, context, cases, route = retrieval
    return {
        "route": route,
        "cases": list(cases),
        "roles": [
            lookup.page_roles[key]
            for key in sources(linked) + sources(selected)
            if key in lookup.page_roles
        ],
        "pool": sources(pool),
        "reranked": sources(order),
        "linked": sources(linked),
        "expanded": sources(selected),
        "packed": packed_sources(context),
        "context_sha256": context.context_hash,
    }


def query_plan(question, lookup):
    plan = lookup.plan(question)
    return {
        "status": plan.status,
        "targets": [
            {
                "case": target.case_id,
                "roles": [
                    lookup.page_roles[location(page)][1] for page in target.pages
                ],
                "fields": target.matched_fields,
            }
            for target in plan.targets
        ],
        "candidate_cases": [list(query.case_ids) for query in plan.candidate_queries],
    }


def score_one(reference, retrieval, *, peer=False):
    pool, expanded, context, _, _ = retrieval
    groups = reference["evidence_groups"]
    gold = {(e["book_id"], e["page"]) for group in groups for e in group}
    if not gold:
        return {"returned_pages": len(context.final_chunks)}
    evidence = (
        reference
        if "evidence_requirements" in reference
        else {
            "evidence_requirements": [
                {"primary": group[:1], "alternatives": [[cite] for cite in group[1:]]}
                if peer
                else {"primary": group}
                for group in groups
                if group
            ],
        }
    )
    row = {
        "page_recall_at_20": len(gold.intersection(sources(pool))) / len(gold),
        "page_recall_at_8": len(gold.intersection(packed_sources(context))) / len(gold),
    }
    if peer:
        books = {book for book, _ in gold}
        packed_books = {book for book, _ in packed_sources(context)}
        row["doc_recall_at_8"] = len(books & packed_books) / len(books)
        row["doc_complete_at_8"] = int(books <= packed_books)
    for stage, material in (
        ("candidate", [page for page, _ in pool]),
        ("expanded", [page for page, _ in expanded]),
        ("final", context),
    ):
        coverage = fact_coverage(evidence, material)
        row[f"{stage}_complete"] = coverage["all_facts"]
        row[f"{stage}_facts"] = coverage["required_facts_hit"]
    row["required_facts"] = coverage["required_facts"]
    return row


def category(question, ref):
    split = question["split"]
    if split in OFFICIAL_SPLITS:
        return "official"
    if split in PEER_SPLITS:
        return split
    if split == "scenarios":
        return f"S{ref['scenario']:02}"
    return f"{split}/{ref['variant']}"


def score(output, questions, files, retrievals, traces, arms, *, ranking_rows=None):
    if ranking_rows is None:
        save_rankings(output, retrievals)
    else:
        write_rows(output / "rankings.jsonl", ranking_rows)
    write_rows(
        output / "traces.jsonl",
        ({**row, "id": qid, "arm": arm} for (qid, arm), row in traces.items()),
    )
    references = {split: load_references(path) for split, (_, path) in files.items()}
    rows = []
    categories = defaultdict(list)
    for q in questions:
        qid = q["id"]
        reference = references[q["split"]][qid]
        label = category(q, reference)
        metrics = {
            arm: score_one(
                reference, retrievals[qid, arm], peer=q["split"] in PEER_SPLITS
            )
            for arm in arms
        }
        row = {"id": qid, "category": label, "arms": metrics}
        if "final_complete" in metrics["baseline"]:
            row["final_delta"] = {
                arm: metrics[arm]["final_complete"]
                - metrics["baseline"]["final_complete"]
                for arm in arms
                if arm != "baseline"
            }
        rows.append(row)
        categories[label].append(row)
        if "final_delta" in row and label != "official":
            categories["categories_positive"].append(row)
    write_rows(output / "scores.jsonl", rows)
    return (
        {
            group: {
                arm: summarize([row["arms"][arm] for row in members]) for arm in arms
            }
            for group, members in categories.items()
        },
        rows,
        references,
    )


def summarize(rows):
    counts = Counter()
    for row in rows:
        counts.update(row)
    return {
        "questions": len(rows),
        "scored": sum("page_recall_at_8" in row for row in rows),
        **{
            key: fmean(row[key] for row in rows if key in row)
            if key.startswith(("page_recall", "doc_recall"))
            else value
            for key, value in counts.items()
        },
    }
