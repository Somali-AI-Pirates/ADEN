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
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    load_cases,
    load_profiles,
    rank_with_hg_lookup,
)
from experimental.u_10o0o.bm25 import B, K1, BM25Index, load_tokenizer
from experimental.u_10o0o.context_evidence import score_context
from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.evaluate import RECORDS_DIR, fingerprint, score_question
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET
from experimental.u_10o0o.rerank import KST, MAX_TOKENS, MODELS, WEIGHTS
from experimental.u_10o0o.search import load_index, search

HOLDOUT = Path(__file__).with_name("evaluation") / "advanced_holdout"
KURE = "nlpai-lab/KURE-Reranker-nano"
PREDICT_BATCH_SIZE = 32
CONTEXT_TOKENS = 4000
RERANKED_HEAD = 3
FINAL_K = 8
BIGRAM_KURE = "bigram_id_kure"
BODY_SELECTED = "bigram_id_kure_top3_bm25"
TITLE_ONLY = "bigram_id_fielded_kure_top3"
SELECTED = "bigram_id_fielded_kure_top3_hg_lookup"


def evaluation_files():
    return {
        "holdout": (
            HOLDOUT / "advanced_holdout_questions.json",
            HOLDOUT / "advanced_holdout.json",
        ),
        **{
            name: (
                DATA_DIR / "evaluation" / f"{name}_questions.json",
                DATA_DIR / "evaluation" / f"{name}.json",
            )
            for name in ("development", "additional")
        },
    }


def evaluation_questions(files):
    return [
        {**q, "split": split}
        for split, (questions, _) in files.items()
        for q in load_questions(questions)
    ]


def score_rankings(questions, rankings, files):
    references = {split: load_references(path) for split, (_, path) in files.items()}
    records = []
    groups = defaultdict(list)
    for q, ranked in zip(questions, rankings, strict=True):
        qid, split = q["id"], q["split"]
        reference = references[split][qid]
        evidence = reference["evidence_groups"]
        sources = [
            {"book_id": Path(item["file_name"]).stem, "page": item["page"]}
            for item, _ in ranked
        ]
        record = {"id": qid, "split": split, "ranked": sources, "metrics": None}
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
                context = score_context(
                    ranked[:k], evidence, token_budget=CONTEXT_TOKENS, top_k=k
                )
                metrics[f"quote_recall@{k}"] = (
                    context["found_quotes"] / context["expected_quotes"]
                )
                metrics[f"context_tokens@{k}"] = context["context_tokens"]
        record["metrics"] = metrics
        groups[split].append(metrics)
        if split == "holdout":
            groups[f"holdout_{reference['identifier_mode']}"].append(metrics)
        else:
            groups["official"].append(metrics)
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advanced 제목 보너스·명시 및 간접 HG 사건 조회 평가"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="제목 보너스 단독, 이전 본문 기준선 및 기존 검색 대조군도 비교",
    )
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--output-dir", type=Path, help="결과를 저장할 새 디렉터리")
    args = parser.parse_args()
    if DATASET != "advanced" or not torch.cuda.is_available():
        raise SystemExit("Advanced 페이지 캐시와 CUDA가 필요합니다.")

    files = evaluation_files()
    questions = evaluation_questions(files)
    pages = load_index()[0] if args.compare else json.loads(CACHE_PATH.read_bytes())
    try:
        manifest = (args.curated / "manifest.json").read_bytes()
        if json.loads(manifest)["identity"]["dataset"] != DATASET:
            raise ValueError("Advanced 정제 스냅샷이 필요합니다.")
        manifest_sha = sha256(manifest).hexdigest()
        cases = load_cases(args.case_index, pages, manifest_sha)
        profiles = ProfileIndex(load_profiles(args.curated, cases))
    except (FileNotFoundError, ValueError) as error:
        parser.error(f"HG 조회 자료를 확인하세요. {error}")

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
        f"Advanced 검색 비교 시작 · {len(questions)}문항 · {torch.cuda.get_device_name()}"
    )
    log(f"HG 조회 준비 완료 · 검증된 {len(cases):,}사건")
    index = PatternIndex(pages)
    fielded = [index.fielded(q["question"], top_k=CANDIDATES) for q in questions]
    log(f"제목 보너스 후보 고정 · {len(pages):,}쪽 · {perf_counter() - started:.1f}초")
    candidates = {"fielded": fielded}
    if args.compare:
        sparse = [index.base(q["question"], top_k=CANDIDATES) for q in questions]
        kiwi_tokenize, kiwi_config = load_tokenizer("kiwi")
        kiwi_index = BM25Index(pages, tokenizer=kiwi_tokenize)
        candidates[BIGRAM_KURE] = sparse
        candidates.update(kiwi_rrf_kure=[], bigram_id_rrf_kure=[])
        for q, base in zip(questions, sparse, strict=True):
            question = q["question"]
            dense = search(question, top_k=CANDIDATES)
            kiwi = kiwi_index.search(question, top_k=CANDIDATES)
            candidates["kiwi_rrf_kure"].append(
                weighted_rrf(kiwi, dense, WEIGHTS, top_k=CANDIDATES)
            )
            candidates["bigram_id_rrf_kure"].append(
                weighted_rrf(base, dense, WEIGHTS, top_k=CANDIDATES)
            )
        log(f"RRF 비교 후보 고정 · {perf_counter() - started:.1f}초")

    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    reranked = rerank_variants(reranker, questions, candidates, log)
    fielded_reranked = reranked.pop("fielded")
    title_rankings = [
        retain_reranked_head(base, head)
        for base, head in zip(fielded, fielded_reranked, strict=True)
    ]
    lookup_rankings = []
    routes = Counter()
    for question, order in zip(questions, title_rankings, strict=True):
        ranked, route = rank_with_hg_lookup(
            question["question"], order, cases, profiles
        )
        lookup_rankings.append(ranked[:CANDIDATES])
        routes[route] += 1
    log(
        f"HG 조회 적용 · 명시 {routes['explicit_hg']}문항 · 간접 {routes['indirect_hg']}문항"
    )
    rankings = {SELECTED: lookup_rankings}
    if args.compare:
        rankings[TITLE_ONLY] = title_rankings
        rankings[BODY_SELECTED] = [
            retain_reranked_head(base, head)
            for base, head in zip(sparse, reranked[BIGRAM_KURE], strict=True)
        ]
        rankings.update(reranked)
        rankings["bigram_id_bm25"] = sparse
    results = {
        name: score_rankings(questions, ranks, files)
        for name, ranks in rankings.items()
    }
    config = {
        "run_at": now.isoformat(),
        "inputs": {
            split: {"questions": fingerprint(q), "references": fingerprint(r)}
            for split, (q, r) in files.items()
        },
        "pages": fingerprint(CACHE_PATH),
        "bm25": {**index.tokenizer_config, "k1": K1, "b": B},
        "fielded": index.config["fielded"],
        "hg_lookup": {
            "curated": str(args.curated),
            "curation_manifest_sha256": manifest_sha,
            "case_index": str(args.case_index),
            "case_sources_sha256": sha256(
                (args.case_index / "case_sources.jsonl").read_bytes()
            ).hexdigest(),
            "indexed_cases": len(cases),
            "context": "restored_full_page",
            "routes": dict(routes),
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
        "context_tokens": CONTEXT_TOKENS,
        "reranked_head": RERANKED_HEAD,
        "selected_top_k": FINAL_K,
    }
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
        row = result["summary"]["holdout"]
        log(
            f"{name} · 추가 {row['scored']}문항 · 문서 Recall@{FINAL_K} {row[f'doc_recall@{FINAL_K}']:.2%}"
        )
    log(f"평가 완료 · {perf_counter() - started:.1f}초 · {output}")


if __name__ == "__main__":
    main()
