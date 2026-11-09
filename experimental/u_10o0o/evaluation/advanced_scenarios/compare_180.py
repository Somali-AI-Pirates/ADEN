"""Run personal Advanced retrieval and no-LLM peer retrieval on 180 questions."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import write_rows
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, rank_with_hg_lookup
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import (
    BASE,
    PEER,
    peer_rankings,
    start_log,
)
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    KURE,
    MAX_TOKENS,
    MODELS,
    SELECTED,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_source_lookup import selected_context
from experimental.u_10o0o.advanced_target_benchmark import fingerprint, save_rankings
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.evaluation.advanced_scenarios.compare import assess
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

DATA = Path(__file__).resolve().parent
WORK_RULES = DATA / "work_rules"
BUDGET = DEFAULT_CONTEXT_TOKENS
QUESTION_FILES = {
    "scenario": DATA / "retrieval_questions.json",
    "negative_control": DATA / "negative_control_questions.json",
    "work_rules": WORK_RULES / "questions.json",
}
REFERENCE_FILES = {
    "scenario": DATA / "retrieval_references.json",
    "negative_control": DATA / "negative_control_references.json",
    "work_rules": WORK_RULES / "references.json",
}


def verify_hash(path, expected, description):
    if fingerprint(path)["sha256"] != expected:
        raise ValueError(f"{description} 지문이 다릅니다.")


def load_inputs():
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    work_manifest = json.loads((WORK_RULES / "manifest.json").read_bytes())
    if (
        manifest["total_questions"] != 160
        or manifest["splits"]["retrieval"]["questions"] != 140
        or manifest["splits"]["negative_control"]["questions"] != 20
        or work_manifest["scenario"] != "work_rules"
        or work_manifest["questions"] != 20
        or work_manifest["variants"] != {"procedure": 10, "applicability": 10}
    ):
        raise ValueError("고정된 상황별·업무 규정 데이터 구성이 다릅니다.")

    inputs = {}
    for split, path in QUESTION_FILES.items():
        manifest_key = path.name
        expected = (
            manifest["files_sha256"].get(manifest_key)
            if split != "work_rules"
            else work_manifest["files_sha256"].get(manifest_key)
        )
        if expected is None:
            raise ValueError(f"{split} 질문 파일 지문이 manifest에 없습니다.")
        verify_hash(path, expected, f"{split} 질문 파일")
        inputs[path.name] = fingerprint(path)
    for source in manifest["source_inputs"]:
        verify_hash(PROJECT_ROOT / source["path"], source["sha256"], "상황 평가 원본")
    restored_pages = fingerprint(CACHE_PATH)["sha256"]
    if work_manifest["restored_pages_sha256"] != restored_pages:
        raise ValueError("업무 규정 데이터의 복원 페이지 지문이 다릅니다.")
    return manifest, work_manifest, inputs


def load_query_sets():
    # Preserve the historic order: 140 scenario questions, then 20 negatives;
    # the separately authored Work Rules questions are appended last.
    scenario = load_questions(QUESTION_FILES["scenario"])
    negatives = load_questions(QUESTION_FILES["negative_control"])
    work_rules = load_questions(QUESTION_FILES["work_rules"])
    questions = [
        *({**q, "cohort": "scenario"} for q in scenario),
        *({**q, "cohort": "negative_control"} for q in negatives),
        *({**q, "cohort": "work_rules"} for q in work_rules),
    ]
    ids = [q["id"] for q in questions]
    if len(ids) != 180 or len(set(ids)) != 180:
        raise ValueError("중복 없는 180문항이 필요합니다.")
    if Counter(qid.split("-")[1] for qid in ids[:160]) != {
        f"S{i:02}": 20 for i in range(1, 9)
    }:
        raise ValueError("상황별 140문항과 범위 밖 대조 20문항 구성이 다릅니다.")
    return questions


def personal(questions, pages, lookup, storage, log):
    index = PatternIndex(pages)
    pools = [index.fielded(q["question"], top_k=CANDIDATES) for q in questions]
    log(f"개인 BM25 후보 생성 완료 · {len(questions)}문항")
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    reranked = rerank_variants(reranker, questions, {BASE: pools}, log)[BASE]
    retrievals = {}
    for question, pool, rank in zip(questions, pools, reranked, strict=True):
        post_hg, hg_route = rank_with_hg_lookup(
            question["question"],
            retain_reranked_head(pool, rank),
            lookup.cases,
            lookup.profile_index,
        )
        post_hg = post_hg[:CANDIDATES]
        order, context, cases, source_route, selection_route = selected_context(
            question["question"], post_hg, lookup, storage, BUDGET
        )
        keys = {location(page) for page, _ in order}
        packed = {
            (chunk.chunk.book_id, chunk.chunk.page) for chunk in context.final_chunks
        }
        if (
            len(pool) != CANDIDATES
            or len({location(page) for page, _ in pool}) != CANDIDATES
            or len(post_hg) != CANDIDATES
            or len(order) != CANDIDATES
            or len(keys) != CANDIDATES
            or len(context.final_chunks) > FINAL_K
            or context.token_count > BUDGET
            or not packed <= keys
        ):
            raise ValueError(f"{question['id']}의 후보 또는 문맥 한도가 다릅니다.")
        route = f"{hg_route}/{source_route}/{selection_route}"
        retrievals[question["id"], BASE] = (pool, order, context, cases, route)
    return retrievals


def summarize_group(rows):
    result = {"questions": len(rows), "arms": {}}
    for arm in (BASE, PEER):
        records = [row["arms"][arm] for row in rows]
        scored = [row for row in records if "final_complete" in row]
        metrics = {
            "nonempty_context": sum(row["final_pages"] > 0 for row in records),
            "mean_final_pages": fmean(row["final_pages"] for row in records),
            "max_context_tokens": max(row["context_tokens"] for row in records),
            "budget_excluded_pages": sum(row["budget_excluded"] for row in records),
            "routes": dict(Counter(row["route"] for row in records)),
            "scored": len(scored),
        }
        if scored:
            metrics.update(
                {
                    key: sum(row[key] for row in scored)
                    for key in (
                        "pool_complete",
                        "expanded_complete",
                        "final_complete",
                        "pool_facts",
                        "expanded_facts",
                        "final_facts",
                        "required_facts",
                    )
                }
            )
            for key in ("page_recall_at_8", "doc_recall_at_8"):
                metrics[key] = fmean(row[key] for row in scored)
            case_records = [row for row in scored if "final_has_all_cases" in row]
            if case_records:
                metrics["pool_has_all_cases"] = sum(
                    row["pool_has_all_cases"] for row in case_records
                )
                metrics["final_has_all_cases"] = sum(
                    row["final_has_all_cases"] for row in case_records
                )
        result["arms"][arm] = metrics
    evaluated = [row for row in rows if "final_complete" in row["arms"][BASE]]
    paired = Counter(
        (row["arms"][BASE]["final_complete"], row["arms"][PEER]["final_complete"])
        for row in evaluated
    )
    result["paired"] = {
        "personal_only": paired[True, False],
        "peer_only": paired[False, True],
        "both": paired[True, True],
        "neither": paired[False, False],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = local_output(args.output)
    if not torch.cuda.is_available():
        raise SystemExit("개인 KURE 리랭킹에는 CUDA가 필요합니다.")

    manifest, work_manifest, inputs = load_inputs()
    questions = load_query_sets()
    started, log = start_log(output)
    log(f"180문항 검색 · 140 상황별 + 20 업무 규정 + 20 범위 밖 · {BUDGET:,}토큰")

    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    retrievals = personal(questions, pages, lookup, storage, log)
    peers, peer_settings = peer_rankings(questions, pages, output, BUDGET, log)
    retrievals.update(peers)
    if set(retrievals) != {
        (q["id"], arm) for q in questions for arm in (BASE, PEER)
    } or any(
        context.token_count > BUDGET or len(context.final_chunks) > FINAL_K
        for _, _, context, _, _ in retrievals.values()
    ):
        raise ValueError("평가 범위 또는 최종 문맥 한도가 다릅니다.")

    save_rankings(output, retrievals)
    log("180문항 순위와 문맥 저장 완료 · 이제 평가 참고 근거를 로드합니다.")

    references = {}
    for split, path in REFERENCE_FILES.items():
        stamps = (work_manifest if split == "work_rules" else manifest)["files_sha256"]
        verify_hash(path, stamps[path.name], f"{split} 참고 근거 파일")
        inputs[path.name] = fingerprint(path)
        references.update(load_references(path))
    work_ids = {q["id"] for q in questions if q["cohort"] == "work_rules"}
    for qid in work_ids:
        references[qid].setdefault("case_keys", [])
    ids = {q["id"] for q in questions}
    if references.keys() != ids:
        raise ValueError("저장된 검색 질문과 평가 참고 근거 ID가 다릅니다.")

    main_refs = {
        qid: ref for qid, ref in references.items() if ref["scenario"] != "work_rules"
    }
    work_refs = {
        qid: ref for qid, ref in references.items() if ref["scenario"] == "work_rules"
    }
    if (
        Counter(ref["scenario"] for ref in main_refs.values())
        != dict.fromkeys(range(1, 9), 20)
        or Counter(ref["variant"] for ref in work_refs.values())
        != {"procedure": 10, "applicability": 10}
        or Counter(ref["variant"] for ref in main_refs.values() if ref["scenario"] == 8)
        != {"unrelated": 10, "near_domain": 10}
        or any(
            bool(ref["evidence_required"]) != (ref["scenario"] != 8)
            for ref in references.values()
        )
    ):
        raise ValueError("사후 채점 단계의 상황·업무 규정·대조군 구성이 다릅니다.")

    page_case = {
        location(page): case
        for case, case_pages in lookup.cases.items()
        for page in case_pages
    }
    rows = assess(retrievals, references, page_case)
    cohort = {q["id"]: q["cohort"] for q in questions}
    for row in rows:
        row["cohort"] = cohort[row["id"]]
    write_rows(output / "scores.jsonl", rows)

    scenario_rows = [row for row in rows if row["scenario"] in range(1, 8)]
    positive_rows = [row for row in rows if row["scenario"] != 8]
    work_rows = [row for row in rows if row["scenario"] == "work_rules"]
    negative_rows = [row for row in rows if row["scenario"] == 8]
    groups = {
        "scenario_1_to_7": scenario_rows,
        "positive_160": positive_rows,
        "work_rules": work_rows,
        "work_rules/procedure": [
            row for row in work_rows if row["variant"] == "procedure"
        ],
        "work_rules/applicability": [
            row for row in work_rows if row["variant"] == "applicability"
        ],
        "negative_controls": negative_rows,
        "negative/unrelated": [
            row for row in negative_rows if row["variant"] == "unrelated"
        ],
        "negative/near_domain": [
            row for row in negative_rows if row["variant"] == "near_domain"
        ],
        **{
            f"S{number:02}": [row for row in rows if row["scenario"] == number]
            for number in range(1, 8)
        },
        **{
            f"S04/{variant}": [
                row
                for row in rows
                if row["scenario"] == 4 and row["variant"] == variant
            ]
            for variant in dict.fromkeys(
                row["variant"] for row in rows if row["scenario"] == 4
            )
        },
    }
    summary = {name: summarize_group(group) for name, group in groups.items()}

    write_json(
        output / "results.json",
        {
            "input_manifests": {
                "scenarios": fingerprint(DATA / "manifest.json"),
                "work_rules": fingerprint(WORK_RULES / "manifest.json"),
                "files": inputs,
            },
            "pages": fingerprint(CACHE_PATH),
            "case_links": fingerprint(CASE_INDEX / "case_sources.jsonl"),
            "runner": fingerprint(Path(__file__)),
            "compare_helpers": fingerprint(Path(__file__).with_name("compare.py")),
            "source_lookup": fingerprint(
                PROJECT_ROOT / "experimental/u_10o0o/advanced_source_lookup.py"
            ),
            "limits": {
                "candidates": CANDIDATES,
                "pages": FINAL_K,
                "context_tokens": BUDGET,
            },
            "cohorts": {
                "scenario_1_to_7": len(scenario_rows),
                "work_rules": len(work_rows),
                "positive_160": len(positive_rows),
                "negative_unrelated": sum(
                    row["variant"] == "unrelated" for row in negative_rows
                ),
                "negative_near_domain": sum(
                    row["variant"] == "near_domain" for row in negative_rows
                ),
            },
            "personal": {
                "selected_preset": SELECTED,
                "candidate_source": "fielded_bigram_id_bm25",
                "reranker": KURE,
                "revision": MODELS[KURE],
                "max_tokens": MAX_TOKENS,
                "ordering": "KURE top 3 plus BM25 tail, HG ranking, selected source lookup",
            },
            "peer": peer_settings,
            "external_api_calls": 0,
            "elapsed_seconds": round(perf_counter() - started, 2),
            "summary": summary,
        },
    )
    for name in ("scenario_1_to_7", "work_rules", "positive_160", "negative_controls"):
        if name in summary:
            log(f"{name} · {summary[name]['questions']}문항 · 결과 저장 완료")


if __name__ == "__main__":
    main()
