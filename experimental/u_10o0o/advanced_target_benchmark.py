"""Compare target lookup and bounded dense retrieval on frozen Advanced questions."""

import argparse
import json
from collections import Counter
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    augment,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.advanced_pattern_full import DATA as FULL_DATA
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    PEER_AMBIGUOUS,
    RECORDS_DIR,
    RERANKED_HEAD,
    evaluation_files,
    evaluation_questions,
    fingerprint as project_fingerprint,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index

ARMS = ("baseline", "field_lookup", "field_lookup_dense")
USAGE = Path(__file__).with_name("evaluation") / "advanced_usage_blind" / "local"
FULL_ARCHIVE = RECORDS_DIR / "advanced-curation/hg-role-context-20261109"
PEER_BASELINE = CACHE_PATH.parent / "advanced-peer-suite-20261109/results.json"
DENSE_SLOTS = 3


def fingerprint(path):
    return project_fingerprint(path.resolve())


def dense_candidates(requests, embedder, pages, vectors, page_indices):
    if not requests:
        return []
    encoded = embedder.encode_query(
        [request.text for request in requests],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    candidates = []
    for request, query_vector in zip(requests, encoded, strict=True):
        indices = [page_indices[location(page)] for page in request.pages]
        if not indices:
            continue
        scores = vectors[indices] @ query_vector
        order = np.argsort(-scores, kind="stable")[:DENSE_SLOTS]
        candidates.append([(pages[indices[i]], float(scores[i])) for i in order])
    return candidates


def reserve_dense(order, field_pages, candidate_lists):
    """Reserve a few of the same 20 slots for distinct scoped dense additions."""
    seen = {location(page) for page, _ in order}
    additions = []
    for group in zip_longest(*candidate_lists):
        for candidate in group:
            if candidate is None:
                continue
            page, score = candidate
            source = location(page)
            if source not in seen:
                seen.add(source)
                additions.append((page, score))
                if len(additions) == DENSE_SLOTS:
                    break
        if len(additions) == DENSE_SLOTS:
            break
    if not additions:
        return order
    kept = max(len(field_pages), CANDIDATES - len(additions))
    return (order[:kept] + additions)[:CANDIDATES]


def target_pages(plan):
    return [
        page
        for group in zip_longest(*(target.pages for target in plan.targets))
        for page in group
        if page is not None
    ]


class RetrievalBenchmark:
    def __init__(self, pages, vectors, lookup):
        self.pages = pages
        self.vectors = vectors
        self.lookup = lookup
        self.baseline_profiles = ProfileIndex(lookup.profiles)
        self.page_indices = {location(page): i for i, page in enumerate(pages)}
        self.index = PatternIndex(pages)
        self.reranker = CrossEncoder(
            KURE,
            revision=MODELS[KURE],
            device="cuda",
            max_length=MAX_TOKENS,
            local_files_only=True,
            model_kwargs={"dtype": torch.float16},
        )
        self.embedder = None

    def pools(self, text, plan, arms):
        baseline = self.index.fielded(text, top_k=CANDIDATES)
        pools = {ARMS[0]: baseline}
        if plan is None:
            return pools

        exact = target_pages(plan)
        field = augment(baseline, exact)[:CANDIDATES]
        pools[ARMS[1]] = field
        if ARMS[2] not in arms:
            return pools

        dense = []
        if plan.candidate_queries:
            if self.embedder is None:
                self.embedder = load_model()
            dense = dense_candidates(
                plan.candidate_queries,
                self.embedder,
                self.pages,
                self.vectors,
                self.page_indices,
            )
        pools[ARMS[2]] = reserve_dense(field, exact, dense)
        return pools

    def complete(self, question, pool, reranked, plan):
        order = retain_reranked_head(pool, reranked)
        expanded, baseline_route = rank_with_hg_lookup(
            question, order, self.lookup.cases, self.baseline_profiles
        )
        added_cases = ()
        route = baseline_route
        if plan is not None:
            pages = target_pages(plan)
            if pages:
                expanded = augment(expanded, pages)
                added_cases = tuple(target.case_id for target in plan.targets)
            route = plan.status
        expanded = expanded[:CANDIDATES]
        return pool, expanded, packed_context(expanded), added_cases, route

    def batch(self, questions, plans, log):
        pools = {arm: [] for arm in ARMS}
        for index, question in enumerate(questions, start=1):
            for arm, pool in self.pools(
                question["question"], plans[question["id"]], ARMS
            ).items():
                pools[arm].append(pool)
            if index % 150 == 0 or index == len(questions):
                log(f"후보 확보 {index}/{len(questions)}")
        ranks = rerank_variants(self.reranker, questions, pools, log)
        log("고정 예산으로 문맥 조립")
        return {
            (q["id"], arm): self.complete(
                q["question"],
                pools[arm][i],
                ranks[arm][i],
                plans[q["id"]] if arm != ARMS[0] else None,
            )
            for i, q in enumerate(questions)
            for arm in ARMS
        }

    def latency(self, questions, plans, count):
        if count == 0:
            return {}
        sample = sorted(questions, key=lambda q: q["id"])
        dense = [q for q in sample if plans[q["id"]].candidate_queries]
        exact = [q for q in sample if plans[q["id"]].targets]
        dense_ids = {q["id"] for q in dense}
        ordinary = [
            q for q in sample if q["id"] not in dense_ids and not plans[q["id"]].targets
        ]
        selected = []
        seen = set()
        groups = [iter(dense), iter(exact), iter(ordinary)]
        while len(selected) < count and groups:
            for group in groups[:]:
                q = next(group, None)
                if q is None:
                    groups.remove(group)
                    continue
                if q["id"] not in seen:
                    selected.append(q)
                    seen.add(q["id"])
                if len(selected) == count:
                    break

        def one(q, arm):
            text = q["question"]
            started = perf_counter()
            plan = self.lookup.plan(text) if arm != ARMS[0] else None
            pool = self.pools(text, plan, (arm,))[arm]
            ranked = rerank_variants(self.reranker, [q], {arm: [pool]}, lambda _: None)[
                arm
            ][0]
            self.complete(text, pool, ranked, plan)
            return (perf_counter() - started) * 1000

        for arm in ARMS:
            one(selected[0], arm)
        times = {arm: [] for arm in ARMS}
        for index, q in enumerate(selected):
            for arm in ARMS[index % len(ARMS) :] + ARMS[: index % len(ARMS)]:
                times[arm].append(one(q, arm))
        return {
            "queries": len(selected),
            "scope": "local_single_query_bm25_target_dense_rerank_pack_no_startup",
            "sampling": "cycle_dense_exact_ordinary_sorted_by_question_id",
            "dense_eligible": sum(q["id"] in dense_ids for q in selected),
            "exact_eligible": sum(bool(plans[q["id"]].targets) for q in selected),
            "arms": {
                arm: {
                    "p50_ms": median(values),
                    "p95_ms": float(np.percentile(values, 95)),
                }
                for arm, values in times.items()
            },
        }


def ranked_sources(rows):
    return [{"book_id": location(page)[0], "page": page["page"]} for page, _ in rows]


def save_rankings(output, retrievals):
    write_rows(
        output / "rankings.jsonl",
        (
            {
                "id": qid,
                "arm": arm,
                "pool": ranked_sources(pool),
                "expanded": ranked_sources(expanded),
                "packed": [
                    {"book_id": chunk.chunk.book_id, "page": chunk.chunk.page}
                    for chunk in context.final_chunks
                ],
                "context_sha256": context.context_hash,
                "context_tokens": context.token_count,
                "route": route,
                "linked_cases_count": len(added_cases),
            }
            for (qid, arm), (
                pool,
                expanded,
                context,
                added_cases,
                route,
            ) in retrievals.items()
        ),
    )


def load_full_archive(inputs, full_questions):
    config = json.loads((FULL_ARCHIVE / "config.json").read_bytes())
    expected = {
        name: config["inputs"][name]["sha256"] for name in ("questions", "references")
    }
    actual = {name: inputs["full"][name]["sha256"] for name in expected}
    if (
        actual != expected
        or config["pages"]["sha256"] != fingerprint(CACHE_PATH)["sha256"]
    ):
        raise ValueError("6,500문항 기준선 보관값의 질문·정답·페이지 지문이 다릅니다.")
    rows = {row["id"]: row for row in read_rows(FULL_ARCHIVE / "questions.jsonl")}
    if len(rows) != 6500 or rows.keys() != {q["id"] for q in full_questions}:
        raise ValueError("6,500문항 기준선 보관값의 ID 집합이 다릅니다.")
    return rows


def verify_quote_audit(path, inputs):
    if not path.is_file():
        raise FileNotFoundError(path)
    provenance = json.loads(path.with_name("audit_summary.json").read_bytes())[
        "input_sha256"
    ]
    previous = json.loads(PEER_BASELINE.read_bytes())["config"]
    if (
        provenance["restored_pages"] != fingerprint(CACHE_PATH)["sha256"]
        or provenance["peer_run"] != fingerprint(PEER_BASELINE)["sha256"]
        or any(
            inputs[split][kind]["sha256"] != expected[kind]["sha256"]
            for split, expected in previous["inputs"].items()
            for kind in ("questions", "references")
        )
    ):
        raise ValueError(
            "인용 감사와 벤치의 원본 페이지 또는 평가 입력 지문이 다릅니다."
        )


def verify_usage_inputs(inputs):
    manifest = json.loads((USAGE.parent / "manifest.json").read_bytes())
    for split in ("blind", "pilot"):
        for kind in ("questions", "references"):
            expected = manifest[split]["sha256"][f"{split}_{kind}.json"]
            if inputs[f"usage_{split}"][kind]["sha256"] != expected:
                raise ValueError("기존 블라인드·파일럿 평가 파일의 지문이 다릅니다.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quote-audit", required=True, type=Path)
    parser.add_argument("--latency-queries", type=int, default=36)
    args = parser.parse_args()
    if args.latency_queries < 0:
        parser.error("--latency-queries must be nonnegative")
    args.output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    started = perf_counter()
    log("Advanced 대상별 검색 비교 시작 · 검색 전용")
    files = evaluation_files("peer")
    files["usage_blind"] = (
        USAGE / "blind_questions.json",
        USAGE / "blind_references.json",
    )
    files["usage_pilot"] = (
        USAGE / "pilot_questions.json",
        USAGE / "pilot_references.json",
    )
    files["full"] = FULL_DATA / "questions.json", FULL_DATA / "references.json"
    inputs = {
        split: {"questions": fingerprint(q), "references": fingerprint(r)}
        for split, (q, r) in files.items()
    }
    verify_usage_inputs(inputs)
    verify_quote_audit(args.quote_audit, inputs)
    questions = evaluation_questions(files)
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("평가 세트에 중복 문항 ID가 있습니다.")
    full_questions = [q for q in questions if q["split"] == "full"]
    archive = load_full_archive(inputs, full_questions)
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    plans = {q["id"]: lookup.plan(q["question"]) for q in questions}
    full_active = {
        q["id"]
        for q in full_questions
        if (plan := plans[q["id"]]).targets or plan.candidate_queries
    }
    measured = [q for q in questions if q["split"] != "full" or q["id"] in full_active]
    log(
        f"계획 완료 · 실측 {len(measured)}문항 · 6,500문항 중 변경 경로 {len(full_active)}문항"
    )
    bench = RetrievalBenchmark(pages, vectors, lookup)
    retrievals = bench.batch(measured, plans, log)
    latency = bench.latency(
        [q for q in measured if q["split"] != "full"], plans, args.latency_queries
    )
    save_rankings(args.output, retrievals)
    log("검색 종료 · 참고 자료로 채점 시작")

    references = {
        qid: ref
        for _, path in files.values()
        for qid, ref in load_references(path).items()
    }
    from experimental.u_10o0o.advanced_target_metrics import score_run

    audit = json.loads(args.quote_audit.read_bytes())
    page_cases = {
        location(page): case
        for case, case_pages in lookup.cases.items()
        for page in case_pages
    }
    scored = score_run(
        measured,
        retrievals,
        {q["id"]: references[q["id"]] for q in measured},
        page_cases=page_cases,
        peer_ambiguous=frozenset(json.loads(PEER_AMBIGUOUS.read_bytes())),
        quote_audit=audit,
    )
    write_rows(args.output / "questions.jsonl", scored["records"])

    active_records = [row for row in scored["records"] if row["id"] in full_active]
    archived_control_mismatches = sum(
        row["arms"][ARMS[0]]["context"]["personal_all_facts"]
        != archive[row["id"]]["arms"]["full_page"]["all_facts"]
        for row in active_records
    )
    previous_complete = sum(
        row["arms"]["full_page"]["all_facts"] for row in archive.values()
    )
    archived_active_complete = sum(
        archive[qid]["arms"]["full_page"]["all_facts"] for qid in full_active
    )
    full_regression = {
        "questions": len(full_questions),
        "reused_archived_no_new_candidates": len(full_questions) - len(full_active),
        "freshly_measured": len(full_active),
        "archived_complete": previous_complete,
        "archived_active_complete": archived_active_complete,
        "archived_control_mismatches": archived_control_mismatches,
        "complete_by_arm": None
        if archived_control_mismatches
        else {
            arm: previous_complete
            - archived_active_complete
            + sum(
                row["arms"][arm]["context"]["personal_all_facts"]
                for row in active_records
            )
            for arm in ARMS
        },
    }
    write_json(
        args.output / "summary.json",
        {
            "config": {
                "inputs": inputs,
                "pages": fingerprint(CACHE_PATH),
                "vectors": fingerprint(VECTORS_PATH),
                "case_links": fingerprint(CASE_INDEX / "case_sources.jsonl"),
                "curation_manifest": fingerprint(CURATED / "manifest.json"),
                "quote_audit": fingerprint(args.quote_audit),
                "full_baseline": fingerprint(FULL_ARCHIVE / "questions.jsonl"),
                "target_lookup_code": fingerprint(
                    Path(__file__).with_name("advanced_target_lookup.py")
                ),
                "scoring_code": fingerprint(
                    Path(__file__).with_name("advanced_target_metrics.py")
                ),
                "benchmark_code": fingerprint(Path(__file__)),
                "candidate_count": CANDIDATES,
                "reranked_head": RERANKED_HEAD,
                "final_k": FINAL_K,
                "context_tokens": CONTEXT_TOKENS,
                "dense_slots": DENSE_SLOTS,
                "reranker": {
                    "model": KURE,
                    "revision": MODELS[KURE],
                    "gpu": torch.cuda.get_device_name(),
                },
                "embedding": {
                    "model": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "device": "cpu",
                },
                "generation_calls": 0,
            },
            "plan_routes": {
                split: dict(
                    Counter(
                        plans[q["id"]].status for q in questions if q["split"] == split
                    )
                )
                for split in files
            },
            "summary": scored["summary"],
            "metric_notes": scored["metric_notes"],
            "full_regression": full_regression,
            "latency": latency,
            "elapsed_seconds": perf_counter() - started,
        },
    )
    log(f"평가 완료 · {perf_counter() - started:.1f}초")


if __name__ == "__main__":
    main()
