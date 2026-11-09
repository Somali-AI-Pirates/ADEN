"""Run the selected local Advanced retrieval preset and its optional controls."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from statistics import fmean
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from app.evaluation.metrics import retrieval_metrics
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.bm25 import K1, B, BM25Index, load_tokenizer
from experimental.u_10o0o.context_evidence import score_context, score_packed_context
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.evaluate import RECORDS_DIR, fingerprint, score_question
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET, PROJECT_ROOT
from experimental.u_10o0o.rerank import KST, MAX_TOKENS, MODELS, WEIGHTS
from experimental.u_10o0o.search import load_index, rank_pages

HOLDOUT = Path(__file__).with_name("evaluation") / "advanced_holdout"
SCENARIOS = HOLDOUT.with_name("advanced_scenarios")
PEER_DATA = PROJECT_ROOT / "experimental/ysl727727/overnight/data"
PEER_SPLITS = {
    "peer_false_premise": "false_premise_adv",
    "peer_two_doc": "synth_adv",
    "peer_multi_doc": "synth_multi_adv",
}
PEER_AMBIGUOUS = PEER_DATA / "synth_adv_ambiguous.json"
OFFICIAL_SPLITS = ("development", "additional")
KURE = "nlpai-lab/KURE-Reranker-nano"
PREDICT_BATCH_SIZE = 32
# Fixed budget for the experiment runners that reproduce the 4,000-token results.
CONTEXT_TOKENS = 4000
DEFAULT_CONTEXT_TOKENS = 8000
RERANKED_HEAD = 3
FINAL_K = 8
BIGRAM_KURE = "bigram_id_kure"
BODY_SELECTED = "bigram_id_kure_top3_bm25"
TITLE_ONLY = "bigram_id_fielded_kure_top3"
HG_ONLY = "bigram_id_fielded_kure_top3_hg_lookup"
# Keep the preset ID used by the archived BM25 comparison runners.
SELECTED = "bigram_id_fielded_kure_top3_paired_source_lookup"
HYBRID_SELECTED = "bigram_id_fielded_snowflake_ko_rrf_55_kure_top3_paired_source_lookup"
HYBRID_WEIGHTS = (0.5, 0.5)


def evaluation_files(suite="standard"):
    files = {}
    if suite in ("scenarios", "categories"):
        files["scenarios"] = (
            SCENARIOS / "retrieval_questions.json",
            SCENARIOS / "retrieval_references.json",
        )
    if suite == "categories":
        files.update(
            negative_control=(
                SCENARIOS / "negative_control_questions.json",
                SCENARIOS / "negative_control_references.json",
            ),
            work_rules=(
                SCENARIOS / "work_rules/questions.json",
                SCENARIOS / "work_rules/references.json",
            ),
            semantic_queries=(
                SCENARIOS / "semantic_queries/questions.json",
                SCENARIOS / "semantic_queries/references.json",
            ),
        )
    if suite in ("standard", "all"):
        files["holdout"] = (
            HOLDOUT / "advanced_holdout_questions.json",
            HOLDOUT / "advanced_holdout.json",
        )
        files.update(
            {
                split: (
                    DATA_DIR / "evaluation" / f"{split}_questions.json",
                    DATA_DIR / "evaluation" / f"{split}.json",
                )
                for split in OFFICIAL_SPLITS
            }
        )
    if suite in ("peer", "all"):
        files.update(
            {
                split: (
                    PEER_DATA / f"{name}_questions.json",
                    PEER_DATA / f"{name}.json",
                )
                for split, name in PEER_SPLITS.items()
            }
        )
    return files


def evaluation_questions(files):
    return [
        {**q, "split": split}
        for split, (questions, _) in files.items()
        for q in load_questions(questions)
    ]


def score_rankings(
    questions,
    rankings,
    files,
    *,
    context_tokens,
    peer_ambiguous=frozenset(),
    final_contexts=None,
):
    references = {split: load_references(path) for split, (_, path) in files.items()}
    records = []
    groups = defaultdict(list)
    for index, (q, ranked) in enumerate(zip(questions, rankings, strict=True)):
        qid, split = q["id"], q["split"]
        reference = references[split][qid]
        evidence = reference["evidence_groups"]
        sources = [
            {"book_id": Path(item["file_name"]).stem, "page": item["page"]}
            for item, _ in ranked
        ]
        record = {"id": qid, "split": split, "ranked": sources, "metrics": None}
        if final_contexts is not None:
            context = final_contexts[index]
            record["packed"] = [
                {"book_id": item.chunk.book_id, "page": item.chunk.page}
                for item in context.final_chunks
            ]
            record["context_sha256"] = context.context_hash
        records.append(record)
        if not any(evidence):
            continue
        metrics = {}
        for k in (RERANKED_HEAD, FINAL_K, CANDIDATES):
            page = score_question(qid, ranked, evidence, top_k=k)["metrics"]
            doc = retrieval_metrics(reference, sources, k)
            metrics.update({f"page_{key}@{k}": value for key, value in page.items()})
            metrics[f"doc_hit@{k}"] = doc["hit_at_k"]["value"]
            metrics[f"doc_recall@{k}"] = doc["recall_at_k"]["value"]
            if k != CANDIDATES:
                if k == FINAL_K and final_contexts is not None:
                    context = score_packed_context(final_contexts[index], evidence)
                else:
                    context = score_context(
                        ranked[:k], evidence, token_budget=context_tokens, top_k=k
                    )
                metrics[f"quote_recall@{k}"] = (
                    context["found_quotes"] / context["expected_quotes"]
                )
                metrics[f"context_tokens@{k}"] = context["context_tokens"]
        record["metrics"] = metrics
        groups[split].append(metrics)
        if split == "holdout":
            groups[f"holdout_{reference['identifier_mode']}"].append(metrics)
        elif split in OFFICIAL_SPLITS:
            groups["official"].append(metrics)
        elif split in PEER_SPLITS:
            groups["peer"].append(metrics)
            if split == "peer_two_doc":
                group = (
                    "peer_two_doc_ambiguous"
                    if qid in peer_ambiguous
                    else "peer_two_doc_other"
                )
                groups[group].append(metrics)
    summary = {
        name: {
            "scored": len(rows),
            **{key: fmean(row[key] for row in rows) for key in rows[0]},
        }
        for name, rows in groups.items()
    }
    return {"summary": summary, "records": records}


def rerank_variants(reranker, questions, candidates, log):
    scores = {}
    rankings = {}
    # Score the primary variant first so its batches stay identical in comparison runs.
    for name, rows in candidates.items():
        pairs, identities = [], []
        for index, (q, ranked) in enumerate(zip(questions, rows, strict=True)):
            for page, _ in ranked:
                key = (index, id(page))
                if key not in scores:
                    pairs.append((q["question"], page["text"]))
                    identities.append(key)
        for start in range(0, len(pairs), 1600):
            end = start + 1600
            values = reranker.predict(
                pairs[start:end], batch_size=PREDICT_BATCH_SIZE, show_progress_bar=False
            )
            scores.update(zip(identities[start:end], map(float, values), strict=True))
            log(f"{name} 리랭킹 {min(end, len(pairs))}/{len(pairs)}쌍")
        rankings[name] = [
            sorted(
                ((page, scores[index, id(page)]) for page, _ in ranked),
                key=lambda row: -row[1],
            )
            for index, ranked in enumerate(rows)
        ]
    return rankings


def retain_reranked_head(sparse, reranked):
    head = reranked[:RERANKED_HEAD]
    selected = {id(page) for page, _ in head}
    return head + [(page, score) for page, score in sparse if id(page) not in selected]


def score_categories(questions, files, retrievals, preset):
    references = {split: load_references(path) for split, (_, path) in files.items()}
    categories = defaultdict(Counter)
    controls = defaultdict(Counter)
    for question in questions:
        qid, split = question["id"], question["split"]
        ref = references[split][qid]
        pool, _, context, _, _ = retrievals[qid, preset]
        if split == "negative_control":
            row = controls[f"negative/{ref['variant']}"]
            row["count"] += 1
            row["returned_pages"] += len(context.final_chunks)
            continue
        if split == "scenarios":
            category = f"S{ref['scenario']:02}"
        else:
            prefix = "semantic" if split == "semantic_queries" else split
            category = f"{prefix}/{ref['variant']}"
        candidate = fact_coverage(ref, [page for page, _ in pool])
        final = fact_coverage(ref, context)
        row = Counter(
            count=1,
            candidate_complete=candidate["all_facts"],
            final_complete=final["all_facts"],
            final_facts=final["required_facts_hit"],
            required_facts=final["required_facts"],
        )
        categories[category].update(row)
        categories["all_positive"].update(row)
    return {"positive": dict(categories), "negative_control": dict(controls)}


def main() -> None:
    from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
    from experimental.u_10o0o.advanced_role_context import FACTS
    from experimental.u_10o0o.advanced_source_lookup import selected_context
    from experimental.u_10o0o.advanced_target_benchmark import save_rankings
    from experimental.u_10o0o.advanced_target_lookup import load_target_lookup

    parser = argparse.ArgumentParser(
        description="Advanced 5:5 하이브리드 검색 및 원문 사건 조회 평가"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="이전 BM25 기준선, 제목 보너스 단독 및 기존 검색 대조군도 비교",
    )
    parser.add_argument(
        "--suite",
        choices=("standard", "scenarios", "categories", "peer", "all"),
        default="standard",
        help="공식·holdout(standard), 상황별 140문항(scenarios), 카테고리별 220문항(categories), 동료(peer), 공식·holdout과 동료(all)",
    )
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--role-facts", type=Path, default=FACTS)
    parser.add_argument("--output-dir", type=Path, help="결과를 저장할 새 디렉터리")
    parser.add_argument(
        "--context-tokens",
        type=int,
        default=DEFAULT_CONTEXT_TOKENS,
        help="최종 문맥 토큰 한도 (기본 8,000)",
    )
    args = parser.parse_args()
    if args.context_tokens <= 0:
        parser.error("--context-tokens에는 양의 정수를 입력하세요.")
    if DATASET != "advanced" or not torch.cuda.is_available():
        raise SystemExit("Advanced 페이지 캐시와 CUDA가 필요합니다.")

    files = evaluation_files(args.suite)
    questions = evaluation_questions(files)
    try:
        pages, vectors = load_index()
        manifest = (args.curated / "manifest.json").read_bytes()
        if json.loads(manifest)["identity"]["dataset"] != DATASET:
            raise ValueError("Advanced 정제 스냅샷이 필요합니다.")
        manifest_sha = sha256(manifest).hexdigest()
        sources = {
            "curated": args.curated,
            "case_index": args.case_index,
            "facts": args.role_facts,
        }
        lookup = load_target_lookup(pages, **sources)
        storage = load_storage_lookup(pages, **sources)
    except (FileNotFoundError, ValueError) as error:
        parser.error(f"Advanced 페이지·벡터와 원문 조회 자료를 확인하세요. {error}")

    now = datetime.now(KST)
    output = (
        args.output_dir or RECORDS_DIR / f"advanced-selected-{now:%Y%m%d-%H%M%S-%f}"
    )
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    started = perf_counter()
    log(
        f"Advanced 검색 비교 시작 · {len(questions)}문항 · "
        f"최대 {FINAL_K}쪽 · {args.context_tokens:,}토큰 · {torch.cuda.get_device_name()}"
    )
    log(f"HG 조회 준비 완료 · 검증된 {len(lookup.cases):,}사건")
    index = PatternIndex(pages)
    embedder = load_model()
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    if args.compare:
        kiwi_tokenize, kiwi_config = load_tokenizer("kiwi")
        kiwi_index = BM25Index(pages, tokenizer=kiwi_tokenize)

    batches = [questions]
    if args.suite == "categories":
        # The 180-question run and the later 40-question run had separate batches.
        split = next(
            i for i, q in enumerate(questions) if q["split"] == "semantic_queries"
        )
        batches = [questions[:split], questions[split:]]

    presets = [HYBRID_SELECTED, SELECTED] if args.compare else [HYBRID_SELECTED]
    retrievals = {}
    rankings = defaultdict(list)
    final_contexts = defaultdict(list)
    routes, source_routes, selection_routes = Counter(), Counter(), Counter()
    for batch in batches:
        queries = [q["question"] for q in batch]
        fielded = [index.fielded(query, top_k=CANDIDATES) for query in queries]
        embeddings = embedder.encode_query(
            queries, normalize_embeddings=True, show_progress_bar=False
        )
        dense = [
            rank_pages(vector, vectors, pages, top_k=CANDIDATES)
            for vector in embeddings
        ]
        hybrid = [
            weighted_rrf(sparse, vector, HYBRID_WEIGHTS, top_k=CANDIDATES)
            for sparse, vector in zip(fielded, dense, strict=True)
        ]
        log(f"BM25·Snowflake-ko 후보 준비 · {len(batch)}문항 · {len(pages):,}쪽")
        candidates = {HYBRID_SELECTED: hybrid}
        if args.compare:
            candidates = {SELECTED: fielded, **candidates}
            sparse = [index.base(query, top_k=CANDIDATES) for query in queries]
            candidates[BIGRAM_KURE] = sparse
            candidates["kiwi_rrf_kure"] = [
                weighted_rrf(
                    kiwi_index.search(query, top_k=CANDIDATES),
                    vector,
                    WEIGHTS,
                    top_k=CANDIDATES,
                )
                for query, vector in zip(queries, dense, strict=True)
            ]
            candidates["bigram_id_rrf_kure"] = [
                weighted_rrf(base, vector, WEIGHTS, top_k=CANDIDATES)
                for base, vector in zip(sparse, dense, strict=True)
            ]
        reranked = rerank_variants(reranker, batch, candidates, log)
        for preset in presets:
            for question, pool, head in zip(
                batch, candidates[preset], reranked[preset], strict=True
            ):
                query = question["question"]
                order = retain_reranked_head(pool, head)
                linked, hg_route = rank_with_hg_lookup(
                    query, order, lookup.cases, lookup.profile_index
                )
                linked = linked[:CANDIDATES]
                selected, context, cases, source_route, selection_route = (
                    selected_context(
                        query, linked, lookup, storage, args.context_tokens
                    )
                )
                rankings[preset].append(selected)
                final_contexts[preset].append(context)
                if args.suite == "categories":
                    retrievals[question["id"], preset] = (
                        pool,
                        selected,
                        context,
                        cases,
                        f"{hg_route}/{source_route}/{selection_route}",
                    )
                if preset == HYBRID_SELECTED:
                    routes[hg_route] += 1
                    source_routes[source_route] += 1
                    selection_routes[selection_route] += 1
                else:
                    rankings[HG_ONLY].append(linked)
                    rankings[TITLE_ONLY].append(order)
        if args.compare:
            rankings[BODY_SELECTED].extend(
                retain_reranked_head(base, head)
                for base, head in zip(sparse, reranked[BIGRAM_KURE], strict=True)
            )
            for name in (BIGRAM_KURE, "kiwi_rrf_kure", "bigram_id_rrf_kure"):
                rankings[name].extend(reranked[name])
            rankings["bigram_id_bm25"].extend(sparse)
    log(
        f"HG 조회 적용 · 명시 {routes['explicit_hg']}문항 · 간접 {routes['indirect_hg']}문항"
    )
    log(
        f"원문 조회 · 단일 {source_routes['unique']}문항 · 모호 {source_routes['ambiguous']}문항"
        f" · 두 사건 {source_routes['pair']}문항"
    )
    if args.suite == "categories":
        save_rankings(output, retrievals)
        log("후보 순위·최종 문맥 지문 저장 · 참고 근거 채점 시작")
    peer_ambiguous = (
        frozenset(json.loads(PEER_AMBIGUOUS.read_bytes()))
        if "peer_two_doc" in files
        else frozenset()
    )
    results = {
        name: score_rankings(
            questions,
            ranks,
            files,
            context_tokens=args.context_tokens,
            peer_ambiguous=peer_ambiguous,
            final_contexts=final_contexts.get(name),
        )
        for name, ranks in rankings.items()
    }
    if args.suite == "categories":
        for preset in presets:
            results[preset]["categories"] = score_categories(
                questions, files, retrievals, preset
            )
    config = {
        "run_at": now.isoformat(),
        "suite": args.suite,
        "selected_preset": HYBRID_SELECTED,
        "inputs": {
            split: {"questions": fingerprint(q), "references": fingerprint(r)}
            for split, (q, r) in files.items()
        },
        "pages": fingerprint(CACHE_PATH),
        "bm25": {**index.tokenizer_config, "k1": K1, "b": B},
        "fielded": index.config["fielded"],
        "hybrid": {
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "k": RRF_K,
            "weights": HYBRID_WEIGHTS,
            "vectors": fingerprint(VECTORS_PATH),
        },
        "hg_lookup": {
            "curated": str(args.curated),
            "curation_manifest_sha256": manifest_sha,
            "case_index": str(args.case_index),
            "case_sources_sha256": sha256(
                (args.case_index / "case_sources.jsonl").read_bytes()
            ).hexdigest(),
            "indexed_cases": len(lookup.cases),
            "context": "restored_full_page",
            "routes": dict(routes),
        },
        "source_lookup": {
            "role_facts": {
                "path": str(args.role_facts / "role_facts.jsonl"),
                "sha256": sha256(
                    (args.role_facts / "role_facts.jsonl").read_bytes()
                ).hexdigest(),
            },
            "routes": dict(source_routes),
            "selection_routes": dict(selection_routes),
        },
        "candidate_count": CANDIDATES,
        "reranker": {
            "model": KURE,
            "revision": MODELS[KURE],
            "dtype": "float16",
            "max_tokens": MAX_TOKENS,
            "batch_size": PREDICT_BATCH_SIZE,
        },
        "gpu": torch.cuda.get_device_name(),
        "versions": {
            name: version(name)
            for name in ("torch", "transformers", "sentence-transformers", "numpy")
        },
        "context_tokens": args.context_tokens,
        "reranked_head": RERANKED_HEAD,
        "selected_top_k": FINAL_K,
    }
    if "peer_two_doc" in files:
        config["peer_ambiguous"] = fingerprint(PEER_AMBIGUOUS)
    if args.compare:
        config["rrf_controls"] = {
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "k": RRF_K,
            "weights": WEIGHTS,
            "vectors": fingerprint(VECTORS_PATH),
            "kiwi": kiwi_config,
        }
    (output / "results.json").write_text(
        json.dumps({"config": config, "results": results}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    for name, result in results.items():
        for split, label in (("holdout", "추가"), ("peer", "동료")):
            if row := result["summary"].get(split):
                log(
                    f"{name} · {label} {row['scored']}문항 · 문서 Recall@{FINAL_K} {row[f'doc_recall@{FINAL_K}']:.2%}"
                )
    log(f"평가 완료 · {perf_counter() - started:.1f}초 · {output}")


if __name__ == "__main__":
    main()
