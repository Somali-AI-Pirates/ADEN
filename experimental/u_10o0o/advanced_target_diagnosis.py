"""Locate target, candidate, and packed-evidence failures without changing retrieval."""

import argparse
import json
from collections import Counter
from datetime import datetime
from itertools import product
from pathlib import Path
from time import perf_counter

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_pair_context import pair_requirements
from experimental.u_10o0o.advanced_pair_holdout import fact_hits
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import pack
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    PREDICT_BATCH_SIZE,
    RERANKED_HEAD,
    rerank_variants,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    RetrievalBenchmark,
    fingerprint,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_usage_blind import CASE_INDEX, FACTS
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

BUDGET = 8000
PAIR_TYPES = {"explicit", "mixed", "indirect"}


def source_key(source):
    return source["book_id"], source["page"]


def validate_manifest(data):
    manifest = json.loads((data / "manifest.json").read_bytes())
    inputs = {
        "questions": data / "questions.json",
        "references": data / "references.json",
        "pages": CACHE_PATH,
        "case_links": CASE_INDEX / "case_sources.jsonl",
        "role_facts": FACTS / "role_facts.jsonl",
        "scenarios": PROJECT_ROOT / manifest["scenarios"]["path"],
    }
    if any(
        fingerprint(path)["sha256"] != manifest[k]["sha256"]
        for k, path in inputs.items()
    ):
        raise ValueError("고정 평가 입력이나 원본 색인의 지문이 다릅니다.")
    scenarios = json.loads(inputs["scenarios"].read_bytes())
    for scenario in scenarios:
        expected = 1 if scenario["type"] == "single" else 2
        if (
            len(scenario["requests"]) != expected
            or len(scenario["identify"]) != expected
        ):
            raise ValueError(f"{scenario['id']}의 질문 유형과 대상 수가 다릅니다.")
    return manifest


def replay(questions, pages, archive, manifest):
    config = json.loads((archive / "results.json").read_bytes())
    if config["dataset"] != manifest or config["limits"]["final_pages"] != FINAL_K:
        raise ValueError("보관 순위의 질문 또는 문맥 설정이 다릅니다.")
    saved = {
        r["id"]: r
        for r in read_rows(archive / "rankings.jsonl")
        if r["arm"] == "baseline"
    }
    if saved.keys() != {q["id"] for q in questions}:
        raise ValueError("보관 순위의 문항 ID가 일치하지 않습니다.")
    page_map = {location(page): page for page in pages}
    rows = {}
    for q in questions:
        qid = q["id"]
        row = saved[qid]
        pool, ordered = (
            [
                (page_map[source_key(x)], -float(i))
                for i, x in enumerate(row[stage], start=1)
            ]
            for stage in ("pool", "expanded")
        )
        previous = pack(ordered, config["limits"]["tokens"])
        if previous.context_hash != row["context_sha256"]:
            raise ValueError(f"{qid}의 보관 문맥을 재현하지 못했습니다.")
        rows[qid, "baseline"] = (pool, ordered, pack(ordered, BUDGET), (), row["route"])
    return rows


def search(questions, pages, lookup, log):
    benchmark = RetrievalBenchmark(pages, None, lookup)
    pools = [
        benchmark.pools(q["question"], None, ("baseline",))["baseline"]
        for q in questions
    ]
    ranks = rerank_variants(benchmark.reranker, questions, {"baseline": pools}, log)[
        "baseline"
    ]
    rows = {}
    for q, pool, rank in zip(questions, pools, ranks, strict=True):
        _, ordered, _, cases, route = benchmark.complete(
            q["question"], pool, rank, None
        )
        rows[q["id"], "baseline"] = (pool, ordered, pack(ordered, BUDGET), cases, route)
    return rows


def required_slots(ref, page_roles):
    slots = set()
    for fact in ref["evidence_requirements"]:
        bundle = {page_roles.get(source_key(source)) for source in fact["primary"]}
        if not bundle or None in bundle:
            raise ValueError(
                f"{ref['id']}의 필수 근거를 사건과 기록 역할에 연결할 수 없습니다."
            )
        slots.update(bundle)
    if {case for case, _ in slots} != set(ref["case_keys"]):
        raise ValueError(f"{ref['id']}의 사건과 근거 페이지가 다릅니다.")
    return slots


def possible_context(ref, ordered):
    """Find one full-evidence subset of the saved candidates for diagnostic scoring."""
    by_key = {location(page): page for page, _ in ordered}
    choices = []
    for fact in ref["evidence_requirements"]:
        allowed = set()
        for bundle in (fact["primary"], *fact.get("alternatives", ())):
            keys = frozenset(source_key(source) for source in bundle)
            if (
                keys <= by_key.keys()
                and fact_coverage(
                    {"evidence_requirements": [{"primary": bundle}]},
                    [by_key[key] for key in keys],
                )["all_facts"]
            ):
                allowed.add(keys)
        if not allowed:
            return None
        choices.append(allowed)
    covers = {frozenset().union(*option) for option in product(*choices)}
    for keys in sorted(covers, key=lambda s: (len(s), sorted(s))):
        if len(keys) > FINAL_K:
            break
        context = pack([row for row in ordered if location(row[0]) in keys], BUDGET)
        if fact_coverage(ref, context)["all_facts"]:
            return {"pages": len(context.final_chunks), "tokens": context.token_count}
    return None


def diagnose(ref, retrieved, prediction, lookup):
    qid, kind = ref["id"], ref["question_type"]
    pool, ordered, context, _, route = retrieved
    initial = fact_hits(ref, [p for p, _ in pool])
    ready = fact_hits(ref, [p for p, _ in ordered])
    final = fact_hits(ref, context)
    possible = possible_context(ref, ordered)
    if (possible is not None and not all(ready)) or (all(final) and possible is None):
        raise ValueError(f"{qid}의 후보 진단 상한이 최종 문맥과 모순됩니다.")
    is_pair = kind in PAIR_TYPES
    if is_pair and len(set(ref["case_keys"])) != 2:
        raise ValueError(f"{qid}에 두 개의 서로 다른 사건이 필요합니다.")
    gold = required_slots(ref, lookup.page_roles)
    predicted = {(case, role) for case, roles in prediction.items() for role in roles}
    exact_target = set(prediction) == set(ref["case_keys"]) if is_pair else None
    failure = "covered"
    if not is_pair:
        failure = "control"
    elif not all(ready):
        failure = "candidate_missing"
    elif not possible:
        failure = "context_limit"
    elif not all(final):
        if not exact_target:
            failure = "target_unrecognized" if not prediction else "target_mismatch"
        elif not gold <= predicted:
            failure = "required_role_missing"
        else:
            failure = "selection"

    sides = []
    for side, case in zip("AB", ref["case_keys"]):
        needed = {role for key, role in gold if key == case}
        candidate_roles = {
            lookup.page_roles[location(page)][1]
            for page, _ in ordered
            if lookup.page_roles.get(location(page), (None,))[0] == case
        }
        sides.append(
            {
                "target": side,
                "recognized": case in prediction,
                "required_roles": sorted(needed),
                "predicted_roles": sorted(prediction.get(case, ())),
                "candidate_roles": sorted(needed & candidate_roles),
            }
        )
    return {
        "id": qid,
        "type": kind,
        "diagnosis": failure,
        "pair_triggered": bool(prediction),
        "target_exact": exact_target,
        "required_slots_covered_by_prediction": gold <= predicted if is_pair else None,
        "slots_exact": gold == predicted if is_pair else None,
        "target_roles": sides,
        "initial_all": all(initial),
        "candidate_all": all(ready),
        "final_all": all(final),
        "candidate_oracle": possible,
        "facts_required": len(ready),
        "facts_candidate": sum(ready),
        "facts_final": sum(final),
        "context_tokens": context.token_count,
        "budget_excluded_pages": len(context.excluded_chunk_ids),
        "baseline_route": route,
    }


def summarize(rows):
    kinds = ("pair", "explicit", "mixed", "indirect", "single", "clarify")
    groups = {}
    for kind in kinds:
        group = [
            row
            for row in rows
            if row["type"] == kind or (kind == "pair" and row["type"] in PAIR_TYPES)
        ]
        groups[kind] = {
            "questions": len(group),
            **{
                k: sum(bool(row[k]) for row in group)
                for k in (
                    "pair_triggered",
                    "target_exact",
                    "required_slots_covered_by_prediction",
                    "slots_exact",
                    "initial_all",
                    "candidate_all",
                    "candidate_oracle",
                    "final_all",
                )
            },
            "diagnoses": dict(Counter(row["diagnosis"] for row in group)),
        }
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--archive", type=Path, help="이전 후보와 문맥을 재사용할 실행 경로"
    )
    args = parser.parse_args()
    data, output = map(local_output, (args.data, args.output))
    archive = local_output(args.archive) if args.archive else None
    manifest = validate_manifest(data)
    questions = load_questions(data / "questions.json")
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    predicted = {q["id"]: pair_requirements(q["question"], lookup) for q in questions}
    log(
        f"입력 지문 확인 · {len(questions)}문항 · 후보 {CANDIDATES}쪽 · 최종 {FINAL_K}쪽·{BUDGET:,}토큰"
    )
    if archive:
        retrievals = replay(questions, pages, archive, manifest)
        log(
            f"보관 기준선 문맥 지문 {len(questions)}개 일치 · 같은 순위를 8,000토큰으로 조립"
        )
    else:
        retrievals = search(questions, pages, lookup, log)
    if any(
        len({location(p) for p, _ in pool}) != CANDIDATES
        or len({location(p) for p, _ in order}) != CANDIDATES
        or len(ctx.final_chunks) > FINAL_K
        or ctx.token_count > BUDGET
        for pool, order, ctx, _, _ in retrievals.values()
    ):
        raise ValueError("후보 수 또는 최종 문맥 한도가 다릅니다.")
    save_rankings(output, retrievals)
    log("순위 저장 완료 · 참고 근거를 읽어 대상·기록·후보·문맥 진단")
    refs = load_references(data / "references.json")
    if refs.keys() != predicted.keys():
        raise ValueError("참고 근거와 질문 ID가 다릅니다.")
    rows = [
        diagnose(
            refs[q["id"]], retrievals[q["id"], "baseline"], predicted[q["id"]], lookup
        )
        for q in questions
    ]
    if Counter(row["type"] for row in rows) != manifest["questions_by_type"]:
        raise ValueError("문항 유형의 개수가 지문 기록과 다릅니다.")
    write_rows(output / "diagnosis.jsonl", rows)
    summary = summarize(rows)
    write_json(
        output / "results.json",
        {
            "dataset": manifest,
            "code": fingerprint(Path(__file__)),
            "search_code": [
                fingerprint(Path(__file__).with_name(name))
                for name in (
                    "advanced_search.py",
                    "advanced_field_roles.py",
                    "advanced_hg_lookup.py",
                    "advanced_target_lookup.py",
                    "advanced_pair_context.py",
                    "advanced_target_benchmark.py",
                    "advanced_rank_policy.py",
                )
            ],
            "archive": fingerprint(archive / "rankings.jsonl") if archive else None,
            "limits": {
                "candidates": CANDIDATES,
                "final_pages": FINAL_K,
                "tokens": BUDGET,
            },
            "external_api_calls": 0,
            "reranker": {
                "name": KURE,
                "revision": MODELS[KURE],
                "head": RERANKED_HEAD,
                "max_length": MAX_TOKENS,
                "batch_size": PREDICT_BATCH_SIZE,
                "dtype": "float16",
                "device": "cuda",
            },
            "new_retrieval_questions": len(questions) if archive is None else 0,
            "summary": summary,
        },
    )
    pair = summary["pair"]
    log(
        f"두 사건 {pair['questions']}문항 · 후보 완전 {pair['candidate_all']} · 진단 상한 {pair['candidate_oracle']} · 최종 완전 {pair['final_all']} · 외부 API 0회 · {perf_counter() - started:.1f}초"
    )


if __name__ == "__main__":
    main()
