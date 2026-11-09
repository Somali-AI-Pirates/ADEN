"""Compare frozen two-event packing on a separate Advanced workload."""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o import advanced_pair_context
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MODELS,
    RECORDS_DIR,
    RERANKED_HEAD,
    rerank_variants,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    RetrievalBenchmark,
    fingerprint,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_workflow_eval import build_dataset, local_output
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

RECORD = RECORDS_DIR / "advanced-pair-holdout-20261109"
SCENARIOS = RECORD / "scenarios.json"
DEVELOPMENT = advanced_pair_context.WORKFLOW / "references.json"
PACKER = Path(advanced_pair_context.__file__)
SEED = "advanced-pair-holdout-20261109-v1"
TYPES = {"explicit": 8, "mixed": 8, "indirect": 8, "single": 4, "clarify": 2}


def fact_hits(ref, pages):
    return tuple(
        bool(fact_coverage({"evidence_requirements": [fact]}, pages)["all_facts"])
        for fact in ref["evidence_requirements"]
    )


def assess(question, ref, baseline, proposed, lookup):
    _, candidates, before, _, _ = baseline
    _, _, after, _, route = proposed
    candidate_hits = fact_hits(ref, [page for page, _ in candidates])
    before_hits, after_hits = fact_hits(ref, before), fact_hits(ref, after)
    expected = set(ref["case_keys"])
    targets = advanced_pair_context.pair_requirements(question["question"], lookup)
    by_event = []
    if len(expected) == 2:
        for side in "AB":
            indices = [
                i
                for i, fact in enumerate(ref["evidence_requirements"])
                if fact["ask"].startswith(f"건 {side}의 ")
            ]
            if not indices:
                raise ValueError("두 사건의 필수 근거가 구분되지 않았습니다.")
            by_event.append(
                {
                    "required": len(indices),
                    "candidate": sum(candidate_hits[i] for i in indices),
                    "baseline": sum(before_hits[i] for i in indices),
                    "packed": sum(after_hits[i] for i in indices),
                }
            )
    return {
        "id": question["id"],
        "type": ref["question_type"],
        "route": route,
        "recognized": bool(targets),
        "recognized_exactly": bool(targets) and targets.keys() == expected,
        "changed": before.context_hash != after.context_hash,
        "all_candidate": all(candidate_hits),
        "all_baseline": all(before_hits),
        "all_packed": all(after_hits),
        "facts_required": len(candidate_hits),
        "facts_candidate": sum(candidate_hits),
        "facts_baseline": sum(before_hits),
        "facts_packed": sum(after_hits),
        "facts_dropped": sum(
            a and not b for a, b in zip(before_hits, after_hits, strict=True)
        ),
        "events": by_event,
    }


def summarize(rows):
    return {
        "questions": len(rows),
        **{
            key: sum(row[key] for row in rows)
            for key in (
                "recognized",
                "recognized_exactly",
                "changed",
                "all_candidate",
                "all_baseline",
                "all_packed",
                "facts_required",
                "facts_candidate",
                "facts_baseline",
                "facts_packed",
                "facts_dropped",
            )
        },
        "gained": sum(row["all_packed"] and not row["all_baseline"] for row in rows),
        "lost": sum(row["all_baseline"] and not row["all_packed"] for row in rows),
        "routes": dict(Counter(row["route"] for row in rows)),
    }


def run(data, output):
    manifest = json.loads((data / "manifest.json").read_bytes())
    paths = {
        "questions": data / "questions.json",
        "references": data / "references.json",
        "scenarios": SCENARIOS,
        "pages": CACHE_PATH,
        "packer": PACKER,
    }
    for key, stamp in manifest.items():
        if not isinstance(stamp, dict) or "sha256" not in stamp:
            continue
        path = paths.get(key, PROJECT_ROOT / stamp["path"])
        if fingerprint(path)["sha256"] != stamp["sha256"]:
            raise ValueError("고정한 평가 입력 지문이 변경됐습니다.")
    if (
        not paths.keys() <= manifest.keys()
        or manifest["seed"] != SEED
        or manifest["questions_by_type"] != TYPES
        or manifest["excluded_references"] != [fingerprint(DEVELOPMENT)]
    ):
        raise ValueError("사전에 정한 평가 유형과 문항 수가 다릅니다.")

    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    questions = load_questions(data / "questions.json")
    log(f"입력 지문 검증 · {len(questions)}문항 · 개인 검색 후보 한 번 생성")
    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    benchmark = RetrievalBenchmark(pages, None, lookup)
    pools = [
        benchmark.pools(q["question"], None, ("baseline",))["baseline"]
        for q in questions
    ]
    ranks = rerank_variants(benchmark.reranker, questions, {"baseline": pools}, log)
    retrievals = {}
    for q, pool, rank in zip(questions, pools, ranks["baseline"], strict=True):
        qid, text = q["id"], q["question"]
        base = benchmark.complete(text, pool, rank, None)
        _, candidates, context, added, _ = base
        ordered, new_context, route = advanced_pair_context.pack_pair_context(
            text, candidates, lookup, context
        )
        keys = {location(page) for page, _ in candidates}
        if (
            len({location(page) for page, _ in pool}) != CANDIDATES
            or len(keys) != len(ordered)
            or len(keys) != len(candidates)
            or len(keys) != CANDIDATES
            or keys != {location(page) for page, _ in ordered}
            or any(
                len(bundle.final_chunks) > FINAL_K
                or bundle.token_count > CONTEXT_TOKENS
                or not {
                    (item.chunk.book_id, item.chunk.page)
                    for item in bundle.final_chunks
                }
                <= keys
                for bundle in (context, new_context)
            )
        ):
            raise ValueError(f"{qid}의 후보 또는 최종 문맥 한도가 다릅니다.")
        retrievals[qid, "baseline"] = base
        retrievals[qid, "pair_context"] = pool, ordered, new_context, added, route

    save_rankings(output, retrievals)
    log("두 문맥 저장 완료 · 후보 집합 변경 0건 · 원문 근거 채점 시작")
    references = load_references(data / "references.json")
    rows = [
        assess(
            q,
            references[q["id"]],
            retrievals[q["id"], "baseline"],
            retrievals[q["id"], "pair_context"],
            lookup,
        )
        for q in questions
    ]
    pairs = [row for row in rows if row["type"] not in {"single", "clarify"}]
    groups = {
        "pair": pairs,
        "pair_candidate_complete": [row for row in pairs if row["all_candidate"]],
        "pair_candidate_incomplete": [row for row in pairs if not row["all_candidate"]],
        "controls": [row for row in rows if row["type"] in {"single", "clarify"}],
        **{kind: [row for row in rows if row["type"] == kind] for kind in TYPES},
    }
    summary = {kind: summarize(group) for kind, group in groups.items()}
    write_rows(output / "questions.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "dataset": manifest,
            "code": fingerprint(Path(__file__)),
            "limits": {
                "candidates": CANDIDATES,
                "final_pages": FINAL_K,
                "tokens": CONTEXT_TOKENS,
            },
            "candidate_membership_changes": 0,
            "baseline_queries": len(questions),
            "reranker": {"name": KURE, "revision": MODELS[KURE], "head": RERANKED_HEAD},
            "reranked_pairs": sum(map(len, pools)),
            "external_api_calls": 0,
            "summary": summary,
        },
    )
    log(
        f"채점 완료 · 두 사건 개선 {summary['pair']['gained']} · 하락 {summary['pair']['lost']}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data", type=Path, help="재사용할 고정 데이터 경로")
    args = parser.parse_args()
    output = local_output(args.output)
    data = (
        local_output(args.data)
        if args.data
        else output.with_name(f"{output.name}-data")
    )
    if args.data is None:
        build_dataset(
            data, scenarios_path=SCENARIOS, seed=SEED, excluded=(DEVELOPMENT,)
        )
        path = data / "manifest.json"
        manifest = json.loads(path.read_bytes())
        write_json(path, manifest | {"packer": fingerprint(PACKER)})
    run(data, output)


if __name__ == "__main__":
    main()
