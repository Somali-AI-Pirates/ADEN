"""Ablate only BM25 complete-code spelling normalization in the current baseline."""

import argparse
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from app.evaluation.dataset import load_references
from experimental.u_10o0o import advanced_field_roles
from experimental.u_10o0o.bm25 import (
    load_tokenizer,
    tokenize_bigram_id,
)
from experimental.u_10o0o.bm25 import (
    tokenize_bigram_id_normalized as tokenize_normalized_code,
)
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    logger,
    read_json,
    read_jsonl,
    text_sha,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    load_inputs,
    loaded_runtime,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    page_key,
    score_item,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import LegacyBaseline

ARMS = ("baseline", "normalized_code")
TOKENIZER_CONFIG = load_tokenizer("bigram-id-normalized")[1]


def _index_with_tokenizer(pages, name):
    tokenizer, config = load_tokenizer(name)
    with patch.object(
        advanced_field_roles, "load_tokenizer", return_value=(tokenizer, config)
    ):
        return advanced_field_roles.PatternIndex(pages)


def previous_index(pages):
    """Keep the pre-adoption condition explicit when the default changes."""
    return _index_with_tokenizer(pages, "bigram-id")


def normalized_index(pages):
    return _index_with_tokenizer(pages, "bigram-id-normalized")


def token_audit(questions, indexes, pages):
    def differs(text):
        return Counter(tokenize_bigram_id(text)) != Counter(
            tokenize_normalized_code(text)
        )

    return {
        "total_queries": len(questions),
        "changed_query_ids": [
            row["id"] for row in questions if differs(row["question"])
        ],
        "total_body_pages": len(pages),
        "changed_body_pages": sum(differs(p["text"]) for p in pages),
        "total_heading_pages": len(indexes["baseline"].heading_pages),
        "changed_heading_pages": sum(
            differs(p["text"]) for p in indexes["baseline"].heading_pages
        ),
    }


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    log = logger(output)
    started = perf_counter()
    questions, items, sources, counts = load_inputs()
    write_json(output / "questions.json", questions)
    engine = LegacyBaseline()
    engine._ensure_corpus()
    indexes = {
        "baseline": previous_index(engine.pages),
        "normalized_code": normalized_index(engine.pages),
    }
    if [page_key(p) for p in indexes["baseline"].heading_pages] != [
        page_key(p) for p in indexes["normalized_code"].heading_pages
    ]:
        raise ValueError(
            "Heading selection changed; the ablation must only change tokens."
        )
    engine._ensure_models()
    original = engine.metadata()
    configs = {
        "baseline": original["config"],
        "normalized_code": deepcopy(original["config"]),
    }
    configs["baseline"]["fielded"]["tokenizer"] = load_tokenizer("bigram-id")[1]
    configs["normalized_code"]["fielded"]["tokenizer"] = TOKENIZER_CONFIG
    manifest = {
        "status": "running",
        "arms": list(ARMS),
        "items": items,
        "suite_counts": counts,
        "question_sources": sources,
        "questions_sha256": file_sha(output / "questions.json"),
        "configs": configs,
        "inputs": original["inputs"],
        "model_revisions": original["models"],
        "versions": original["versions"],
        "runtime_files": loaded_runtime(),
        "external_api_calls": 0,
        "token_audit": token_audit(questions, indexes, engine.pages),
        "results": {},
    }
    write_json(output / "manifest.json", manifest)
    log(
        f"Token audit: {manifest['token_audit']['changed_body_pages']} changed body pages; {len(manifest['token_audit']['changed_query_ids'])} changed questions."
    )
    for arm in ARMS:
        arm_started = perf_counter()
        engine.index = indexes[arm]
        destination = output / arm
        destination.mkdir()
        completed = 0
        for batch in batch_questions(questions, items):
            results = engine.retrieve_batch(batch, log)
            for expected, (trace, context) in zip(batch, results, strict=True):
                if trace["id"] != expected["id"] or context["id"] != expected["id"]:
                    raise ValueError("Question/result alignment changed.")
                trace["arm"] = context["arm"] = arm
                append_json(destination / "traces.jsonl", trace)
                append_json(destination / "contexts.jsonl", context)
            completed += len(batch)
            log(f"{arm}: saved {completed}/280")
        manifest["results"][arm] = {
            "count": completed,
            "traces_sha256": file_sha(destination / "traces.jsonl"),
            "contexts_sha256": file_sha(destination / "contexts.jsonl"),
            "batch_wall_seconds": perf_counter() - arm_started,
        }
        write_json(output / "manifest.json", manifest)
    check_runtime(manifest["runtime_files"])
    check_runtime(manifest["inputs"] + manifest["question_sources"])
    manifest.update(
        status="complete",
        wall_seconds=perf_counter() - started,
        references_opened=False,
    )
    write_json(output / "manifest.json", manifest)
    log("Both 280-question retrievals saved; references were not opened.")


def validate_run(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if (
        manifest.get("status") != "complete"
        or manifest.get("references_opened") is not False
    ):
        raise ValueError("Both retrievals must complete before references are opened.")
    check_runtime(manifest["runtime_files"])
    check_runtime(manifest["inputs"] + manifest["question_sources"])
    if file_sha(directory / "questions.json") != manifest["questions_sha256"]:
        raise ValueError("Questions changed.")
    questions = read_json(directory / "questions.json")
    ids = [r["id"] for r in manifest["items"]]
    if len(ids) != 280 or len(set(ids)) != 280:
        raise ValueError("Expected 280 unique IDs.")
    saved = {}
    for arm in ARMS:
        result = manifest["results"][arm]
        if result["count"] != 280:
            raise ValueError("Incomplete arm.")
        traces, contexts = [], []
        for name, target in (("traces", traces), ("contexts", contexts)):
            path = directory / arm / f"{name}.jsonl"
            if file_sha(path) != result[f"{name}_sha256"]:
                raise ValueError("Result fingerprint changed.")
            target.extend(read_jsonl(path))
            if [r["id"] for r in target] != ids:
                raise ValueError("Result identities changed.")
        for question, trace, context in zip(questions, traces, contexts, strict=True):
            if (
                trace["arm"] != arm
                or context["arm"] != arm
                or trace["question_sha256"] != text_sha(question["question"])
            ):
                raise ValueError("Wrong arm or question text.")
            if (
                len(trace["pool"]) > 20
                or len(context["pages"]) > 8
                or trace["context_tokens"] > 8000
            ):
                raise ValueError("Budget exceeded.")
            if [page_key(r) for r in trace["final_pages"]] != [
                page_key(r) for r in context["pages"]
            ]:
                raise ValueError("Final source list and saved context disagree.")
        saved[arm] = traces, contexts
    return manifest, saved


def score(directory, output):
    manifest, saved = validate_run(directory)
    # Reference contents are only loaded after both complete result sets validate.
    references = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    cache = next(
        r["path"]
        for r in manifest["inputs"]
        if r["path"].endswith("advanced_restored_pages.json")
    )
    pages = {page_key(p): p for p in read_json(ROOT / cache)}
    by_arm = {}
    for arm, (traces, contexts) in saved.items():
        by_arm[arm] = [
            score_item(
                meta,
                references[meta["suite"]][meta["question_id"]],
                trace,
                context,
                pages,
            )
            for meta, trace, context in zip(
                manifest["items"], traces, contexts, strict=True
            )
        ]
    differences = []
    for before, after, bt, nt in zip(
        by_arm["baseline"],
        by_arm["normalized_code"],
        saved["baseline"][0],
        saved["normalized_code"][0],
        strict=True,
    ):
        row = {
            k: before[k] for k in ("id", "question_id", "suite", "category", "scored")
        }
        row.update(
            initial_order_changed=[page_key(r) for r in bt["pool"]]
            != [page_key(r) for r in nt["pool"]],
            final_context_changed=bt["context_sha256"] != nt["context_sha256"],
            status="not_scored",
        )
        if row["scored"]:
            key = before["complete_metric"]
            b, n = before["stages"]["final"], after["stages"]["final"]
            delta = n[key] - b[key]
            row.update(
                status="improved"
                if delta > 0
                else "regressed"
                if delta < 0
                else "unchanged",
                baseline_complete=b[key],
                normalized_complete=n[key],
                initial_complete_delta=after["stages"]["initial"][key]
                - before["stages"]["initial"][key],
                page_recall_delta=n["page_recall"] - b["page_recall"],
                normalized_failure_stage=after["failure_stage"],
            )
        differences.append(row)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "summary.json", {arm: aggregates(rows) for arm, rows in by_arm.items()}
    )
    write_json(output / "per-question.json", by_arm)
    write_json(output / "differences.json", differences)
    write_json(
        output / "manifest.json",
        {
            **manifest,
            "run_directory": str(Path(directory).resolve().relative_to(ROOT)),
            "references_read_after_both_retrievals": True,
            "reference_files": [
                {"path": r["references"], "sha256": file_sha(ROOT / r["references"])}
                for r in manifest["question_sources"]
            ],
        },
    )


def report(scores, output):
    scores, output = Path(scores), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = read_json(scores / "manifest.json")
    summary = read_json(scores / "summary.json")
    diff = read_json(scores / "differences.json")
    names = {
        "official_all": "공식 전체",
        "official_complex_groups": "공식 복합",
        "categories_positive": "카테고리 유효 전체",
        "S01": "1. 번호로 단일 기록 조회",
        "S02": "2. 번호 없는 단일 사건",
        "S03": "3. 한 사건의 여러 기록",
        "S04": "4. 두 사건 비교",
        "S05": "5. 날짜·승인·금액 구분",
        "S06": "6. 모호한 두 후보",
        "S07": "7. 확인 자료가 부족한 사건",
        "work_rules_all": "업무 규정 전체",
        "semantic_all": "일상 표현 전체",
        "work_rules/procedure": "업무 절차",
        "work_rules/applicability": "적용 조건·예외",
        "semantic_queries/procedure_paraphrase": "일상 표현: 절차",
        "semantic_queries/rule_paraphrase": "일상 표현: 규정",
        "semantic_queries/cross_document_workflow": "일상 표현: 복합 업무 흐름",
        "semantic_queries/cross_document_rule": "일상 표현: 복합 규정",
    }
    lines = [
        "# BM25 완전 코드 표기 정규화: 기존 기준선과 비교",
        "",
        "2026-11-11. 공식 60문항과 카테고리 220문항을 두 조건에서 각각 새로 실행했다. 기존 기준선의 대문자 완전 코드 보존을 소문자·하이픈 주변 공백·유사 하이픈 표기까지 확장했다. 코드 문법의 길이·분절 수 제한은 기존과 같고 일반 bigram 토큰은 바꾸지 않았다.",
        "",
        "본문·제목 BM25와 BM25 질의의 코드 토큰만 변경했다. 벡터·제목 보너스·5:5 RRF·후보 20개·KURE 512토큰 FP16·Top3+RRF·원문 추가 조회·최종 8쪽/o200k 8,000토큰은 동일하다. 질문 원문은 임베딩·리랭커·HG 조회에 그대로 전달했다.",
        "",
        "## 최종 근거 전체 충족",
        "",
        "| 유형 | 기존 | 코드 표기 정규화 | 차이 |",
        "|---|---:|---:|---:|",
    ]
    for group, label in names.items():
        b, n = summary["baseline"][group], summary["normalized_code"][group]
        bv, nv = b["stages"]["final"], n["stages"]["final"]
        lines.append(
            f"| {label} | {bv['complete']}/{b['scored']} ({bv['complete_rate']:.1%}) | {nv['complete']}/{n['scored']} ({nv['complete_rate']:.1%}) | {nv['complete'] - bv['complete']:+} |"
        )
    lines += [
        "",
        "공식은 근거 있는 58개에서 문서 ID·쪽 번호를 정확히 대조하고, 각 근거 그룹에서 한 출처 이상/모든 그룹 충족으로 채점했다. 카테고리는 범위 밖 20개를 분리한 200개에서 기존 fact_coverage로 필수 사실의 주 근거 또는 대체 근거 묶음을 확인했다. 실제 저장된 최종 문맥을 채점했고 답변 생성은 하지 않았다.",
        "",
        "## 공식 페이지 지표",
        "",
        "| 지표 | 기존 | 정규화 |",
        "|---|---:|---:|",
    ]
    for label, stage, key in (
        ("초기 후보 페이지 Recall@20", "initial", "page_recall"),
        ("최종 페이지 Recall@8", "final", "page_recall"),
        ("최종 문서 Recall@8", "final", "document_recall"),
        ("MRR@8", "final", "mrr"),
        ("nDCG@8", "final", "ndcg"),
    ):
        b, n = (summary[arm]["official_all"]["stages"][stage][key] for arm in ARMS)
        lines.append(f"| {label} | {b:.2%} | {n:.2%} |")
    audit = manifest["token_audit"]
    lines += [
        "",
        "## 토큰과 검색 결과 변화",
        "",
        f"- 토큰이 달라진 질문: {len(audit['changed_query_ids'])}/{audit['total_queries']}. 본문: {audit['changed_body_pages']}/{audit['total_body_pages']}쪽. 제목 입력: {audit['changed_heading_pages']}/{audit['total_heading_pages']}개.",
        f"- 초기 후보 순서 변경: {sum(r['initial_order_changed'] for r in diff)}/280. 최종 문맥 변경: {sum(r['final_context_changed'] for r in diff)}/280.",
        f"- 최종 전체 근거 충족 기준 개선: {sum(r['status'] == 'improved' for r in diff)}개, 하락: {sum(r['status'] == 'regressed' for r in diff)}개.",
        "",
        "수치가 같으면 이 세트에서 정규화로 회복할 기회가 적었는지와 최종 검색·조회 단계가 변화를 흡수했는지를 구분해야 한다. 기존 문항을 수정하거나 표기 변형 문항을 추가하지 않았다. 이 실험만으로 모든 코드 표기에서의 견고함을 평가했다고 주장하지 않는다.",
        "",
        "[문항별 점수](per-question.json) · [문항별 변화](differences.json) · [설정·입력 지문](manifest.json) · [과정](process.md)",
        "",
        "## 재현",
        "",
        "```bash",
        ".venv/bin/python -m experimental.u_10o0o.bm25_code_normalization run --output-dir artifacts/u_10o0o/bm25-code-normalization-NEW",
        ".venv/bin/python -m experimental.u_10o0o.bm25_code_normalization score --run-dir artifacts/u_10o0o/bm25-code-normalization-NEW --output-dir artifacts/u_10o0o/bm25-code-normalization-NEW-scores",
        ".venv/bin/python -m experimental.u_10o0o.bm25_code_normalization report --scores-dir artifacts/u_10o0o/bm25-code-normalization-NEW-scores --output-dir experimental/u_10o0o/records/advanced-bm25-code-normalization-NEW",
        "```",
        "",
    ]
    for name in (
        "manifest.json",
        "summary.json",
        "per-question.json",
        "differences.json",
    ):
        write_json(output / name, read_json(scores / name))
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run").add_argument("--output-dir", type=Path, required=True)
    scorer = sub.add_parser("score")
    scorer.add_argument("--run-dir", type=Path, required=True)
    scorer.add_argument("--output-dir", type=Path, required=True)
    reporter = sub.add_parser("report")
    reporter.add_argument("--scores-dir", type=Path, required=True)
    reporter.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.command == "run":
        run(args.output_dir)
    elif args.command == "score":
        score(args.run_dir, args.output_dir)
    else:
        report(args.scores_dir, args.output_dir)


if __name__ == "__main__":
    main()
