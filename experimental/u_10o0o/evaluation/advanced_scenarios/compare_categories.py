"""Complete the hybrid comparison and group all Advanced scenario questions."""

import argparse
import json
from pathlib import Path
from statistics import fmean

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_clause_ab import context_sources
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import BASE as ARCHIVED_BASE
from experimental.u_10o0o.advanced_rank_policy import pack, start_log
from experimental.u_10o0o.advanced_search import CANDIDATES, FINAL_K, SELECTED
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.embeding import VECTORS_PATH
from experimental.u_10o0o.evaluation.advanced_scenarios.compare_180 import (
    REFERENCE_FILES,
    load_inputs,
    load_query_sets,
)
from experimental.u_10o0o.evaluation.advanced_scenarios.semantic_queries.compare import (
    ARMS,
    BASE,
    BUDGET,
    PEER,
    personal,
    score,
    summarize,
)
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index

EARLIER = CACHE_PATH.parent / "advanced-scenarios-180-20261109"
SEMANTIC = CACHE_PATH.parent / "advanced-semantic-queries-20261109"
LIMITS = {"candidates": CANDIDATES, "pages": FINAL_K, "context_tokens": BUDGET}


def replay_peer(saved, page_map):
    order = [
        (page_map[item["book_id"], item["page"]], -float(i))
        for i, item in enumerate(saved["expanded"], 1)
    ]
    context = pack(order, BUDGET)
    if (
        context.context_hash != saved["context_sha256"]
        or context.token_count != saved["context_tokens"]
    ):
        raise ValueError(f"{saved['id']}의 보관 동료 문맥을 재현하지 못했습니다.")
    return order, order, context, (), saved["route"]


def negative_summary(rows):
    return {
        "questions": len(rows),
        "arms": {
            arm: {
                "nonempty_context": sum(
                    row["arms"][arm]["final_pages"] > 0 for row in rows
                ),
                "mean_final_pages": fmean(
                    row["arms"][arm]["final_pages"] for row in rows
                ),
            }
            for arm in ARMS
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = local_output(parser.parse_args().output)
    earlier = json.loads((EARLIER / "results.json").read_bytes())
    semantic = json.loads((SEMANTIC / "results.json").read_bytes())
    _, _, inputs = load_inputs()
    expected = earlier["input_manifests"]["files"]
    if (
        earlier["limits"] != LIMITS
        or semantic["limits"] != LIMITS
        or earlier["personal"]["selected_preset"] != SELECTED
        or semantic["personal"]["baseline"] != SELECTED
        or fingerprint(CACHE_PATH) != earlier["pages"]
        or fingerprint(CACHE_PATH) != semantic["source"]["pages"]
        or fingerprint(VECTORS_PATH) != semantic["hybrid"]["vectors"]
        or fingerprint(PROJECT_ROOT / earlier["source_lookup"]["path"])
        != earlier["source_lookup"]
        or any(stamp != expected[name] for name, stamp in inputs.items())
    ):
        raise ValueError("보관 기준선·데이터·벡터 또는 비교 한도가 다릅니다.")
    questions = load_query_sets()
    ids = {q["id"] for q in questions}
    source_rows = list(read_rows(EARLIER / "rankings.jsonl"))
    archived = {(row["id"], row["arm"]): row for row in source_rows}
    if len(source_rows) != len(archived) or archived.keys() != {
        (qid, arm) for qid in ids for arm in (ARCHIVED_BASE, PEER)
    }:
        raise ValueError("보관된 180문항의 검색 결과가 다릅니다.")

    _, log = start_log(output)
    log("기존 180문항의 5:5 하이브리드 결과 추가 · 보관 동료 순위 재사용")
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    retrievals = dict(personal(questions, pages, vectors, lookup, storage, log))
    page_map = {location(page): page for page in pages}
    for qid in ids:
        pool, expanded, context, _, _ = retrievals[qid, BASE]
        saved = archived[qid, ARCHIVED_BASE]
        if context.context_hash != saved["context_sha256"] or any(
            keys != [(item["book_id"], item["page"]) for item in saved[stage]]
            for stage, keys in (
                ("pool", [location(page) for page, _ in pool]),
                ("expanded", [location(page) for page, _ in expanded]),
                ("packed", context_sources(context)),
            )
        ):
            raise ValueError(
                f"{qid}의 보관 기준선 순위 또는 문맥을 재현하지 못했습니다."
            )
        retrievals[qid, PEER] = replay_peer(archived[qid, PEER], page_map)
    save_rankings(output, retrievals)
    log("180문항의 기준선 문맥 재현 · 검색 결과 저장 · 참고 근거 채점 시작")

    refs = {}
    for path in REFERENCE_FILES.values():
        if fingerprint(path) != expected[path.name]:
            raise ValueError("보관 참고 근거 지문이 다릅니다.")
        refs.update(load_references(path))
    if refs.keys() != ids:
        raise ValueError("180문항의 참고 근거 ID가 다릅니다.")
    rows, negative = [], []
    for q in questions:
        qid = q["id"]
        ref = refs[qid]
        scenario = ref["scenario"]
        if scenario == 8:
            negative.append(
                {
                    "id": qid,
                    "category": f"negative/{ref['variant']}",
                    "arms": {
                        arm: {"final_pages": len(retrievals[qid, arm][2].final_chunks)}
                        for arm in ARMS
                    },
                }
            )
        else:
            category = (
                f"work_rules/{ref['variant']}"
                if scenario == "work_rules"
                else f"S{scenario:02}"
            )
            rows.append(score(ref, retrievals) | {"category": category})

    for stamp in semantic["inputs"].values():
        if fingerprint(PROJECT_ROOT / stamp["path"]) != stamp:
            raise ValueError("보관 일상 표현 세트 지문이 다릅니다.")
    semantic_rows = list(read_rows(SEMANTIC / "scores.jsonl"))
    if (
        len(semantic_rows) != 40
        or len({row["id"] for row in semantic_rows}) != 40
        or ids & {row["id"] for row in semantic_rows}
    ):
        raise ValueError("보관 일상 표현 문항 구성이 다릅니다.")
    rows.extend(
        row | {"category": f"semantic/{row['variant']}"} for row in semantic_rows
    )
    categories = dict.fromkeys(row["category"] for row in rows)
    groups = {
        key: summarize([row for row in rows if row["category"] == key])
        for key in categories
    }
    groups["all_positive"] = summarize(rows)
    negative_groups = {
        key: negative_summary([row for row in negative if row["category"] == key])
        for key in dict.fromkeys(row["category"] for row in negative)
    }
    write_rows(output / "scores.jsonl", [*rows, *negative])
    write_json(
        output / "results.json",
        {
            "limits": LIMITS,
            "hybrid": semantic["hybrid"],
            "archives": {
                key: {
                    name: fingerprint(directory / name)
                    for name in ("results.json", "rankings.jsonl", "scores.jsonl")
                }
                for key, directory in (("earlier", EARLIER), ("semantic", SEMANTIC))
            },
            "code": fingerprint(Path(__file__)),
            "external_api_calls": 0,
            "summary": groups,
            "negative_controls": negative_groups,
        },
    )
    log(f"근거형 {len(rows)}문항·범위 밖 {len(negative)}문항 · 카테고리 집계 완료")


if __name__ == "__main__":
    main()
