"""Compare Advanced context ordering on vocabulary, official, and peer questions."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from shutil import copytree, ignore_patterns
from statistics import fmean, median
from time import perf_counter

from app.evaluation.dataset import load_questions, load_references
from app.generation.context import build_context
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import ProfileIndex, rank_with_hg_lookup
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    OFFICIAL_SPLITS,
    PEER_AMBIGUOUS,
    RERANKED_HEAD,
    evaluation_files,
    evaluation_questions,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    USAGE,
    RetrievalBenchmark,
    fingerprint,
    ranked_sources,
    save_rankings,
    verify_quote_audit,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_target_metrics import score_run
from experimental.u_10o0o.advanced_usage_blind import reference_locations
from experimental.u_10o0o.advanced_vocabulary import (
    ARMS,
    RRF_WEIGHTS,
    VARIANTS,
    regression_questions,
    verify_manifest,
)
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.advanced_workflow_peer import INDEX_DIR, prepare_index
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.hybrid import RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index, rank_pages

BASE = "bm25_kure_top3"
PEER = "peer_v2_no_llm"


def pack(order, budget):
    return build_context(
        _context_candidates(order[:FINAL_K]), FINAL_K, budget, _count_tokens
    )


def load_archive(archive, questions, page_map):
    saved = list(read_rows(archive / "rankings.jsonl"))
    by_key = {(row["id"], row["arm"]): row for row in saved}
    expected = {(q["id"], arm) for q in questions for arm in ARMS}
    if by_key.keys() != expected or len(saved) != len(expected):
        raise ValueError("보관 순위의 질문 또는 비교군이 원래 실행과 다릅니다.")
    pools = {
        arm: [
            [
                (page_map[(source["book_id"], source["page"])], -float(rank))
                for rank, source in enumerate(by_key[q["id"], arm]["pool"], start=1)
            ]
            for q in questions
        ]
        for arm in ARMS
    }
    if any(
        len({location(page) for page, _ in pool}) != CANDIDATES
        or len(pool) != CANDIDATES
        for rows in pools.values()
        for pool in rows
    ):
        raise ValueError("보관 후보는 비교군마다 서로 다른 20쪽이어야 합니다.")
    return by_key, pools


def search_pools(questions, benchmark, log):
    queries = [q["question"] for q in questions]
    bm25 = [benchmark.index.fielded(q, top_k=CANDIDATES) for q in queries]
    log(f"BM25 후보 생성 완료 · {len(bm25)}문항")
    embeddings = load_model().encode_query(
        queries, normalize_embeddings=True, show_progress_bar=False
    )
    dense = [
        rank_pages(vector, benchmark.vectors, benchmark.pages, top_k=CANDIDATES)
        for vector in embeddings
    ]
    log(f"Snowflake 후보 생성 완료 · {len(dense)}문항")
    return {
        "bm25": bm25,
        "rrf_55": [
            weighted_rrf(sparse, vector, RRF_WEIGHTS, top_k=CANDIDATES)
            for sparse, vector in zip(bm25, dense, strict=True)
        ],
    }


def personal_rankings(questions, pages, vectors, archive, budget, log):
    lookup = load_target_lookup(pages)
    benchmark = RetrievalBenchmark(pages, vectors, lookup)
    if archive is None:
        saved, pools = {}, search_pools(questions, benchmark, log)
    else:
        saved, pools = load_archive(archive, questions, {location(p): p for p in pages})
    ranks = rerank_variants(benchmark.reranker, questions, pools, log)
    profiles = ProfileIndex(lookup.profiles)
    for i, question in enumerate(questions):
        qid, query = question["id"], question["question"]
        bm25, rrf = pools["bm25"][i], pools["rrf_55"][i]
        kure = ranks["rrf_55"][i]
        bm25_position = {location(page): j for j, (page, _) in enumerate(bm25)}
        # Stable sort leaves RRF-only pages in their original RRF order.
        bm25_tail = sorted(
            rrf, key=lambda item: bm25_position.get(location(item[0]), CANDIDATES)
        )
        variants = {
            BASE: (bm25, retain_reranked_head(bm25, ranks["bm25"][i])),
            "rrf_kure_top3_rrf_tail": (rrf, retain_reranked_head(rrf, kure)),
            "rrf_kure_top3_bm25_tail": (rrf, retain_reranked_head(bm25_tail, kure)),
            "rrf_kure_full": (rrf, kure),
            "rrf_only": (rrf, rrf),
        }
        for arm, (pool, order) in variants.items():
            if {location(page) for page, _ in pool} != {
                location(page) for page, _ in order
            }:
                raise ValueError(f"{qid} {arm} 후보 집합이 달라졌습니다.")
            expanded, route = rank_with_hg_lookup(query, order, lookup.cases, profiles)
            expanded = expanded[:CANDIDATES]
            archived_arm = {BASE: "bm25", "rrf_kure_top3_rrf_tail": "rrf_55"}.get(arm)
            if saved and archived_arm is not None:
                previous = saved[qid, archived_arm]
                if (
                    ranked_sources(expanded) != previous["expanded"]
                    or packed_context(expanded).context_hash
                    != previous["context_sha256"]
                    or route != previous["route"]
                ):
                    raise ValueError(
                        f"{qid} {arm}의 기존 4,000토큰 문맥을 재현하지 못했습니다."
                    )
            yield (qid, arm), (pool, expanded, pack(expanded, budget), (), route)


def peer_rankings(questions, pages, output, budget, log):
    index_dir = output / "peer-index"
    copytree(
        INDEX_DIR / "adv", index_dir / "adv", ignore=ignore_patterns("rerank_cache.pkl")
    )
    peer = prepare_index(index_dir, log)
    cfg, index, ix, run, reranker = (
        peer[key] for key in ("cfg", "index", "ix", "run", "rr")
    )
    if cfg["qr"] or cfg["mq"] or cfg["llmsel"] or cfg["pool"] != CANDIDATES:
        raise ValueError("동료 비교는 API 없는 20청크 team_graph 설정이어야 합니다.")
    page_map = {location(page): page for page in pages}
    counts = Counter()
    results = {}
    for start in range(0, len(questions), 32):
        batch = questions[start : start + 32]
        queries = [q["question"] for q in batch]
        vectors = index.embed_queries(queries, cfg["emb"])
        for q, vector in zip(batch, vectors, strict=True):
            chunks = run.retrieve(
                ix, reranker, cfg, q["question"], vector, out_n=cfg["pool"]
            )
            keys = [(ix.rows[i]["book_id"], int(ix.rows[i]["page"])) for i in chunks]
            counts["chunks"] += len(keys)
            unique = list(dict.fromkeys(keys))
            counts["unique_pages"] += len(unique)
            counts["missing_pages"] += sum(key not in page_map for key in unique)
            order = [
                (page_map[key], -float(i))
                for i, key in enumerate(unique, start=1)
                if key in page_map
            ]
            results[q["id"], PEER] = (
                order,
                order,
                pack(order, budget),
                (),
                "team_graph",
            )
        log(
            f"동료 검색 완료 · {min(start + len(batch), len(questions))}/{len(questions)}문항"
        )
    return results, {
        **peer["manifest"],
        "returned": dict(counts),
        "reranker_pairs": reranker.dirty,
    }


def score(retrievals, refs):
    rows = []
    for (qid, arm), (pool, _, context, _, _) in retrievals.items():
        ref = refs[qid]
        expected = reference_locations(ref)
        actual = {
            (chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks
        }
        docs = {book for book, _ in expected}
        facts = fact_coverage(ref, context)
        rows.append(
            {
                "id": qid,
                "arm": arm,
                "group": ref.get("variant", "identifier"),
                "required_pages": len(expected),
                "pool_pages_hit": len(expected & {location(page) for page, _ in pool}),
                "context_pages_hit": len(expected & actual),
                "doc_recall_at_8": len(docs & {book for book, _ in actual}) / len(docs),
                "required_facts": facts["required_facts"],
                "facts_hit": facts["required_facts_hit"],
                "complete": bool(facts["all_facts"]),
                "context_pages": len(context.final_chunks),
                "context_tokens": context.token_count,
                "budget_excluded_pages": len(context.excluded_chunk_ids),
            }
        )
    return rows


def summarize(rows):
    totals = (
        "required_pages",
        "pool_pages_hit",
        "context_pages_hit",
        "required_facts",
        "facts_hit",
        "complete",
        "budget_excluded_pages",
    )
    baseline = {row["id"]: row["complete"] for row in rows if row["arm"] == BASE}
    summary = {}
    for group in (*VARIANTS, "identifier", "all"):
        summary[group] = {}
        for arm in dict.fromkeys(row["arm"] for row in rows):
            selected = [
                row
                for row in rows
                if row["arm"] == arm and (group == "all" or row["group"] == group)
            ]
            summary[group][arm] = {
                "questions": len(selected),
                **{key: sum(row[key] for row in selected) for key in totals},
                "doc_recall_at_8": fmean(row["doc_recall_at_8"] for row in selected),
                "median_tokens": median(row["context_tokens"] for row in selected),
                "max_tokens": max(row["context_tokens"] for row in selected),
                "gain": sum(
                    row["complete"] and not baseline[row["id"]] for row in selected
                ),
                "loss": sum(
                    not row["complete"] and baseline[row["id"]] for row in selected
                ),
            }
    return summary


def load_reference_suites(quote_audit):
    files = evaluation_files("all")
    files.pop("holdout")
    inputs = {
        split: {"questions": fingerprint(q), "references": fingerprint(r)}
        for split, (q, r) in files.items()
    }
    verify_quote_audit(quote_audit, inputs)
    questions = evaluation_questions(files)
    counts = Counter(q["split"] for q in questions)
    if (
        sum(counts[split] for split in OFFICIAL_SPLITS) != 60
        or sum(count for split, count in counts.items() if split.startswith("peer_"))
        != 304
        or len({q["id"] for q in questions}) != len(questions)
    ):
        raise ValueError("공식 60문항과 동료 304문항의 ID 및 개수를 확인하세요.")
    return files, questions, inputs


def score_reference_suites(questions, retrievals, files, quote_audit):
    refs = {
        qid: ref
        for _, path in files.values()
        for qid, ref in load_references(path).items()
    }
    ambiguous = frozenset(json.loads(PEER_AMBIGUOUS.read_bytes()))
    scored = score_run(
        questions,
        retrievals,
        refs,
        page_cases={},
        peer_ambiguous=ambiguous,
        quote_audit=json.loads(quote_audit.read_bytes()),
    )
    groups = defaultdict(list)
    for row in scored["records"]:
        split = row["split"]
        groups[split].append(row)
        if split in OFFICIAL_SPLITS:
            groups["official"].append(row)
        else:
            groups["peer"].append(row)
            groups[f"peer_{row['ambiguity']}"].append(row)

    summary = {}
    for group, rows in groups.items():
        baseline = [
            r["arms"][BASE]["context"]["all_scoreable_evidence_groups"] for r in rows
        ]
        summary[group] = {}
        for arm in scored["arm_names"]:
            contexts = [r["arms"][arm]["context"] for r in rows]
            complete = [c["all_scoreable_evidence_groups"] for c in contexts]
            doc_recall = [
                c["document_recall"]
                for c in contexts
                if c["document_recall"] is not None
            ]
            packed = [retrievals[r["id"], arm][2] for r in rows]
            paired = [
                (a, b)
                for a, b in zip(baseline, complete, strict=True)
                if a is not None and b is not None
            ]
            summary[group][arm] = {
                "questions": len(rows),
                "scored": sum(v is not None for v in complete),
                "complete": sum(v or 0 for v in complete),
                "strict_complete": sum(
                    c["all_strict_quote_groups"] or 0 for c in contexts
                ),
                "doc_recall_at_8": fmean(doc_recall) if doc_recall else None,
                "doc_scored": len(doc_recall),
                "required_pages": sum(c["gold_pages"] for c in contexts),
                "context_pages_hit": sum(c["gold_pages_found"] for c in contexts),
                "evidence_groups_expected": sum(
                    c["evidence_groups_expected"] for c in contexts
                ),
                "evidence_groups_unscorable": sum(
                    c["evidence_groups_unscorable"] for c in contexts
                ),
                "evidence_groups_found": sum(
                    c["evidence_groups_found"] for c in contexts
                ),
                "median_tokens": median(c.token_count for c in packed),
                "max_tokens": max(c.token_count for c in packed),
                "budget_excluded_pages": sum(len(c.excluded_chunk_ids) for c in packed),
                "gain": sum(b > a for a, b in paired),
                "loss": sum(b < a for a, b in paired),
            }
    return scored["records"], summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--quote-audit",
        type=Path,
        help="공식 60·동료 304문항 실행에 사용할 인용 감사 파일",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context-tokens", default=8000, type=int)
    args = parser.parse_args()
    if args.context_tokens <= 0:
        parser.error("--context-tokens에는 양의 정수를 입력하세요.")
    if args.quote_audit is not None:
        if args.data is not None or args.archive is not None:
            parser.error("--quote-audit는 --data, --archive와 별도로 사용하세요.")
        run_reference_suites(
            local_output(args.quote_audit),
            local_output(args.output),
            args.context_tokens,
        )
    elif args.data is None or args.archive is None:
        parser.error("--data와 --archive 또는 --quote-audit가 필요합니다.")
    else:
        replay_vocabulary(
            local_output(args.data),
            local_output(args.archive),
            local_output(args.output),
            args.context_tokens,
        )


def run_reference_suites(audit, output, budget):
    files, questions, inputs = load_reference_suites(audit)
    started, log = start_log(output)
    log(f"공식 60문항·동료 304문항 · 최대 {FINAL_K}쪽·{budget:,}토큰")
    pages, vectors = load_index()
    retrievals = dict(personal_rankings(questions, pages, vectors, None, budget, log))
    peer, peer_config = peer_rankings(questions, pages, output, budget, log)
    retrievals.update(peer)
    if any(
        c.token_count > budget or len(c.final_chunks) > FINAL_K
        for _, _, c, _, _ in retrievals.values()
    ):
        raise ValueError("최종 문맥 한도를 초과했습니다.")
    save_rankings(output, retrievals)
    log("문맥 저장 완료 · 채점용 참고 근거 로드")
    rows, summary = score_reference_suites(questions, retrievals, files, audit)
    write_rows(output / "scores.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "inputs": inputs,
            "quote_audit": fingerprint(audit),
            "peer_ambiguous": fingerprint(PEER_AMBIGUOUS),
            "pages": fingerprint(CACHE_PATH),
            "vectors": fingerprint(VECTORS_PATH),
            "code": fingerprint(Path(__file__)),
            "limits": {
                "candidates": CANDIDATES,
                "head": RERANKED_HEAD,
                "pages": FINAL_K,
                "tokens": budget,
            },
            "rrf": {"weights": RRF_WEIGHTS, "k": RRF_K},
            "personal_models": {
                "embedding": {
                    "name": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "device": "cpu",
                },
                "reranker": {
                    "name": KURE,
                    "revision": MODELS[KURE],
                    "device": peer_config["device"],
                    "max_length": MAX_TOKENS,
                },
            },
            "peer": peer_config,
            "external_api_calls": 0,
            "summary": summary,
        },
    )
    log(f"채점 완료 · 외부 API 0회 · {perf_counter() - started:.1f}초")


def start_log(output):
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    return started, log


def replay_vocabulary(data, archive, output, budget):
    manifest = verify_manifest(data)
    regression, stamps = regression_questions()
    questions = load_questions(data / "questions.json") + regression
    previous = json.loads((archive / "results.json").read_bytes())
    replay_inputs = (
        "questions",
        "references",
        "scenarios",
        "pages",
        "role_facts",
        "case_links",
    )
    if previous["regression_inputs"] != stamps or any(
        previous["dataset"][key]["sha256"] != manifest[key]["sha256"]
        for key in replay_inputs
    ):
        raise ValueError("보관 순위와 현재 질문·색인의 지문이 다릅니다.")
    started, log = start_log(output)
    log(f"검색 순위 비교 · {len(questions)}문항 · 최대 {FINAL_K}쪽·{budget:,}토큰")
    pages, vectors = load_index()
    retrievals = dict(
        personal_rankings(questions, pages, vectors, archive, budget, log)
    )
    log(f"기존 4,000토큰 순위와 문맥 지문 {2 * len(questions)}개 일치")
    peer, peer_config = peer_rankings(questions, pages, output, budget, log)
    retrievals.update(peer)
    save_rankings(output, retrievals)
    log("문맥 저장 완료 · 채점용 참고 근거 로드")
    refs = load_references(data / "references.json") | load_references(
        USAGE / "blind_references.json"
    )
    rows = score(retrievals, refs)
    if any(
        row["context_pages"] > FINAL_K or row["context_tokens"] > budget for row in rows
    ):
        raise ValueError("최종 문맥 한도를 초과했습니다.")
    write_rows(output / "scores.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "dataset": manifest,
            "regression_inputs": stamps,
            "archive": fingerprint(archive / "rankings.jsonl"),
            "code": fingerprint(Path(__file__)),
            "limits": {
                "candidates": CANDIDATES,
                "head": RERANKED_HEAD,
                "pages": FINAL_K,
                "tokens": budget,
            },
            "rrf": previous["rrf"],
            "personal_models": previous["models"],
            "peer": peer_config,
            "external_api_calls": 0,
            "summary": summarize(rows),
        },
    )
    log(f"채점 완료 · 외부 API 0회 · {perf_counter() - started:.1f}초")


if __name__ == "__main__":
    main()
