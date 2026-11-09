"""Compare query wording, lexical expansion, and dense candidate supply."""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

import torch

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, normalized
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_search import (
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    RECORDS_DIR,
    RERANKED_HEAD,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    DENSE_SLOTS,
    USAGE,
    RetrievalBenchmark,
    fingerprint,
    reserve_dense,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import (
    EXCLUSION,
    SPEC_ALIASES,
    load_target_lookup,
)
from experimental.u_10o0o.advanced_usage_blind import HG_FIELDS, reference_locations
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.bm25 import B, K1
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index, rank_pages

RECORD = RECORDS_DIR / "advanced-vocabulary-20261109"
ARMS = ("bm25", "alias_bm25", "snowflake", "rrf_55", "dense_slots")
VARIANTS = ("source", "plain", "contrast")
REGRESSION = USAGE.parent / "blind-retrieval.json"
RRF_WEIGHTS = (0.5, 0.5)

# Directional matches are limited to phrases describing the same source field.
RECORD_TERMS = {
    "free": "보관료 무료기간",
    "gate": "실제 적입 장비 반출",
    "discharge": "실제 양하",
    "scheduled": "반납 예약 예정",
    "returned": "실제 지정 장치장 반납",
    "paid": "보관료 유료일",
    "charged": "보관료 확정 금액",
    "received": "수납",
}
ALIAS_TERMS = (
    SPEC_ALIASES
    | {HG_FIELDS[field][2]: term for field, term in RECORD_TERMS.items()}
    | {
        "20피트짜리 일반 컨테이너": "20gp",
        "40피트짜리 일반 컨테이너": "40gp",
        "배에서 화물을 실제로 내린 날": "실제 양하",
        "화물을 실은 장비를 실제로 내보낸 날": "실제 적입 장비 반출",
        "빈 장비를 돌려주기로 한 날": "반납 예약 예정",
        "지정 장소에 빈 장비를 실제로 돌려준 날": "실제 지정 장치장 반납",
        "보관료를 매긴 일수": "보관료 유료일",
    }
)
ALIAS_PATTERNS = {
    alias: re.compile(
        r"(?<!\w)" + r"\s*".join(map(re.escape, alias.split())) + r"(?![a-z0-9])"
    )
    for alias in ALIAS_TERMS
}


def expanded_query(text):
    if EXCLUSION.search(text):
        return text, ()
    original = normalized(text)
    terms = dict.fromkeys(
        ALIAS_TERMS[alias]
        for alias, pattern in ALIAS_PATTERNS.items()
        if pattern.search(original)
        and not re.search(rf"(?<!\w){re.escape(ALIAS_TERMS[alias])}(?!\w)", original)
    )
    return " ".join((text, *terms)), tuple(terms)


def verify_manifest(data):
    manifest = json.loads((data / "manifest.json").read_bytes())
    paths = {
        "questions": data / "questions.json",
        "references": data / "references.json",
        "scenarios": RECORD / "scenarios.json",
        "pages": CACHE_PATH,
    }
    if any(
        not isinstance(manifest.get(key), dict)
        or not {"path", "sha256"} <= manifest[key].keys()
        for key in paths
    ):
        raise ValueError("고정 질문과 원문 지문이 필요합니다.")
    for key, stamp in manifest.items():
        if isinstance(stamp, dict) and {"path", "sha256"} <= stamp.keys():
            path = paths.get(key, PROJECT_ROOT / stamp["path"])
            if fingerprint(path)["sha256"] != stamp["sha256"]:
                raise ValueError(f"고정 입력 지문이 바뀌었습니다. {key}")
    return manifest


def regression_questions():
    config = json.loads(REGRESSION.read_bytes())["config"]["input_sha256"]
    paths = (
        USAGE / "blind_questions.json",
        USAGE / "blind_references.json",
        CACHE_PATH,
        VECTORS_PATH,
        CASE_INDEX / "case_sources.jsonl",
    )
    stamps = {path.name: fingerprint(path) for path in paths}
    if any(config[name] != stamp["sha256"] for name, stamp in stamps.items()):
        raise ValueError("보관된 블라인드 비교 입력 지문이 다릅니다.")
    rows = [q for q in load_questions(paths[0]) if identifiers(q["question"])]
    if len(rows) != 160:
        raise ValueError("번호가 있는 기존 문항 160개가 필요합니다.")
    return rows, stamps


def retrieve(questions, log):
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    benchmark = RetrievalBenchmark(pages, vectors, lookup)
    text = [q["question"] for q in questions]
    expansions = [expanded_query(q) for q in text]
    lexical = [benchmark.index.fielded(q, top_k=CANDIDATES) for q in text]
    alias = [
        benchmark.index.fielded(expanded, top_k=CANDIDATES) if terms else pool
        for (expanded, terms), pool in zip(expansions, lexical, strict=True)
    ]
    log(
        f"BM25 후보 생성 완료 · 별칭 확장 {sum(bool(terms) for _, terms in expansions)}문항"
    )
    dense_vectors = load_model().encode_query(
        text, normalize_embeddings=True, show_progress_bar=False
    )
    dense = [rank_pages(v, vectors, pages, top_k=CANDIDATES) for v in dense_vectors]
    log(f"Snowflake 후보 생성 완료 · {len(dense)}문항")
    pools = dict(
        zip(
            ARMS,
            (
                lexical,
                alias,
                dense,
                [
                    weighted_rrf(a, b, RRF_WEIGHTS, top_k=CANDIDATES)
                    for a, b in zip(lexical, dense, strict=True)
                ],
                [
                    reserve_dense(a, (), [b])
                    for a, b in zip(lexical, dense, strict=True)
                ],
            ),
            strict=True,
        )
    )
    for q, base, vector, reserve in zip(
        questions, lexical, dense, pools[ARMS[-1]], strict=True
    ):
        base_keys = {location(p) for p, _ in base}
        dense_only = {location(p) for p, _ in vector} - base_keys
        reserved = {location(p) for p, _ in reserve} - base_keys
        if (
            len(reserved) != min(DENSE_SLOTS, len(dense_only))
            or not reserved <= dense_only
        ):
            raise ValueError(f"{q['id']}의 벡터 후보 배정 조건이 다릅니다.")
    ranks = rerank_variants(benchmark.reranker, questions, pools, log)
    retrievals, head_pages = {}, {}
    for i, q in enumerate(questions):
        for arm in ARMS:
            key = q["id"], arm
            pool, rank = pools[arm][i], ranks[arm][i]
            head_pages[key] = [p for p, _ in retain_reranked_head(pool, rank)[:FINAL_K]]
            row = benchmark.complete(q["question"], pool, rank, None)
            candidate_keys = {location(p) for p, _ in pool}
            if (
                len(pool) != len(candidate_keys)
                or len(pool) != CANDIDATES
                or len(row[2].final_chunks) > FINAL_K
                or row[2].token_count > CONTEXT_TOKENS
            ):
                raise ValueError(f"{q['id']}의 후보 또는 최종 문맥 한도가 다릅니다.")
            retrievals[key] = row
    return (
        retrievals,
        head_pages,
        dict(
            zip(
                (q["id"] for q in questions),
                (terms for _, terms in expansions),
                strict=True,
            )
        ),
        {
            **benchmark.index.config["fielded"],
            **benchmark.index.tokenizer_config,
            "k1": K1,
            "b": B,
        },
    )


def assess(qid, ref, retrievals, head_pages, alias, other_requirements):
    expected = reference_locations(ref)
    arms, candidates = {}, {}
    for arm in ARMS:
        key = qid, arm
        pool, linked, context, _, route = retrievals[key]
        candidates[arm] = {location(p) for p, _ in pool}
        hits = fact_coverage(ref, context)
        alternate = (
            fact_coverage({"evidence_requirements": other_requirements}, context)
            if other_requirements and not hits["all_facts"]
            else None
        )
        arms[arm] = {
            "candidate_pages": len(candidates[arm] & expected),
            "candidate_facts": fact_coverage(ref, [p for p, _ in pool])[
                "required_facts_hit"
            ],
            "reranked_head_facts": fact_coverage(ref, head_pages[key])[
                "required_facts_hit"
            ],
            "hg_head_facts": fact_coverage(ref, [p for p, _ in linked[:FINAL_K]])[
                "required_facts_hit"
            ],
            "facts": hits["required_facts_hit"],
            "complete": hits["all_facts"],
            "other_variant_fact_without_complete": bool(
                alternate and alternate["required_facts_hit"]
            ),
            "route": route,
        }
    unique_gold = (candidates["snowflake"] - candidates["bm25"]) & expected
    lexical_gold = (candidates["bm25"] - candidates["snowflake"]) & expected
    return {
        "id": qid,
        "pair_id": ref.get("pair_id"),
        "variant": ref.get("variant", "identifier"),
        "scenario_type": ref.get("scenario_type", "identifier"),
        "alias_matched": bool(alias),
        "alias_terms": alias,
        "required_pages": len(expected),
        "required_facts": len(ref["evidence_requirements"]),
        "dense_unique_gold": len(unique_gold),
        "dense_unique_gold_dropped_by_rrf": len(unique_gold - candidates["rrf_55"]),
        "lexical_unique_gold": len(lexical_gold),
        "lexical_unique_gold_dropped_by_rrf": len(lexical_gold - candidates["rrf_55"]),
        "union_gold_pages": len(
            (candidates["snowflake"] | candidates["bm25"]) & expected
        ),
        "arms": arms,
    }


def totals(rows):
    keys = (
        "candidate_pages",
        "candidate_facts",
        "reranked_head_facts",
        "hg_head_facts",
        "facts",
        "complete",
        "other_variant_fact_without_complete",
    )
    return {
        "questions": len(rows),
        **{
            key: sum(row[key] for row in rows)
            for key in (
                "required_pages",
                "required_facts",
                "alias_matched",
                "dense_unique_gold",
                "dense_unique_gold_dropped_by_rrf",
                "lexical_unique_gold",
                "lexical_unique_gold_dropped_by_rrf",
                "union_gold_pages",
            )
        },
        "arms": {
            arm: {
                **{key: sum(row["arms"][arm][key] for row in rows) for key in keys},
                "candidate_complete": sum(
                    row["arms"][arm]["candidate_facts"] == row["required_facts"]
                    for row in rows
                ),
                "gain": sum(
                    row["arms"][arm]["complete"] > row["arms"]["bm25"]["complete"]
                    for row in rows
                ),
                "loss": sum(
                    row["arms"][arm]["complete"] < row["arms"]["bm25"]["complete"]
                    for row in rows
                ),
            }
            for arm in ARMS
        },
    }


def compare_variants(rows):
    pairs = defaultdict(dict)
    for row in rows:
        pairs[row["pair_id"]][row["variant"]] = row
    if len(pairs) != 36 or any(pair.keys() != set(VARIANTS) for pair in pairs.values()):
        raise ValueError("사건별 원문식·일상식·대조 문항이 필요합니다.")
    states = {
        "both": (1, 1),
        "source_only": (1, 0),
        "plain_only": (0, 1),
        "neither": (0, 0),
    }
    return {
        arm: {
            name: sum(
                tuple(
                    pair[variant]["arms"][arm]["complete"]
                    for variant in ("source", "plain")
                )
                == state
                for pair in pairs.values()
            )
            for name, state in states.items()
        }
        for arm in ARMS
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data, output = local_output(args.data), local_output(args.output)
    manifest = verify_manifest(data)
    regression, regression_stamps = regression_questions()
    questions = load_questions(data / "questions.json")
    if len(questions) != 108 or {q["id"] for q in questions} & {
        q["id"] for q in regression
    }:
        raise ValueError("새 문항 108개와 기존 회귀 문항의 ID를 구분해야 합니다.")
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    log(
        f"고정 입력 지문 확인 · 새 문항 {len(questions)}개 · 기존 번호 문항 {len(regression)}개"
    )
    all_questions = questions + regression
    retrievals, heads, aliases, lexical_config = retrieve(all_questions, log)
    save_rankings(output, retrievals)
    log("검색과 문맥 조립 완료 · 참고 정답을 읽어 근거 채점 시작")
    refs = load_references(data / "references.json")
    old_refs = load_references(USAGE / "blind_references.json")
    if refs.keys() != {q["id"] for q in questions} or any(
        old_refs[q["id"]]["expected_action"] != "answer" for q in regression
    ):
        raise ValueError("질문과 정답의 평가 범위가 다릅니다.")
    rows = []
    for q in all_questions:
        qid = q["id"]
        if qid in refs:
            ref = refs[qid]
            other_variant = "source" if ref["variant"] == "contrast" else "contrast"
            other = refs[f"{ref['pair_id']}-{other_variant}"]
            other_requirements = [
                fact
                for field, fact in zip(
                    other["fact_keys"], other["evidence_requirements"], strict=True
                )
                if field not in ref["fact_keys"]
            ]
        else:
            ref, other_requirements = old_refs[qid], []
        rows.append(
            assess(qid, ref, retrievals, heads, aliases[qid], other_requirements)
        )
    new = [r for r in rows if r["pair_id"]]
    groups = {
        variant: [r for r in rows if r["variant"] == variant]
        for variant in (*VARIANTS, "identifier")
    }
    groups |= {
        "plain_alias_matched": [r for r in groups["plain"] if r["alias_matched"]],
        "plain_alias_unmatched": [r for r in groups["plain"] if not r["alias_matched"]],
        **{
            f"plain_{kind}": [r for r in groups["plain"] if r["scenario_type"] == kind]
            for kind in ("spec", "record", "combined")
        },
    }
    summary = {name: totals(group) for name, group in groups.items()}
    write_rows(output / "questions.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "dataset": manifest,
            "regression_inputs": regression_stamps,
            "code": fingerprint(Path(__file__)),
            "search_code": [
                fingerprint(Path(__file__).with_name(name))
                for name in (
                    "advanced_field_roles.py",
                    "advanced_full_metrics.py",
                    "advanced_hg_lookup.py",
                    "advanced_patterns.py",
                    "advanced_search.py",
                    "advanced_target_benchmark.py",
                    "advanced_target_lookup.py",
                    "bm25.py",
                    "hybrid.py",
                    "search.py",
                )
            ]
            + [fingerprint(PROJECT_ROOT / "app/generation/context.py")],
            "limits": {
                "candidates": CANDIDATES,
                "reranked_head": RERANKED_HEAD,
                "pages": FINAL_K,
                "tokens": CONTEXT_TOKENS,
            },
            "models": {
                "embedding": {
                    "name": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "device": "cpu",
                },
                "reranker": {
                    "name": KURE,
                    "revision": MODELS[KURE],
                    "device": torch.cuda.get_device_name(),
                    "max_length": MAX_TOKENS,
                },
            },
            "rrf": {"weights": RRF_WEIGHTS, "k": RRF_K},
            "bm25": lexical_config,
            "dense_slots": DENSE_SLOTS,
            "aliases": ALIAS_TERMS,
            "external_api_calls": 0,
            "paired_wording": compare_variants(new),
            "summary": summary,
        },
    )
    log(
        f"채점 완료 · 일상식 {len(groups['plain'])}문항 · {perf_counter() - started:.1f}초"
    )


if __name__ == "__main__":
    main()
