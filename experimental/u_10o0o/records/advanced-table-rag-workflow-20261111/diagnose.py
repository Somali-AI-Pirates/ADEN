"""Post-run stage diagnosis using fixed references; never changes retrieval."""

import argparse
from collections import Counter
from pathlib import Path

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    page_key,
    official_metrics,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.workflow_experiment import (
    saved_results,
    baseline_manifest,
)

DEST = Path(__file__).resolve().parent


def diagnose(directory):
    saved = saved_results(directory)
    manifest = baseline_manifest()
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    source_pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    scored = read_json(directory / "per-question.json")
    metrics = {a: {r["id"]: r for r in rows} for a, rows in scored.items()}
    output = []

    def complete(ref, pages, official):
        if official:
            m = official_metrics(ref, pages, max(8, len(pages)))
            return bool(m and m["all_groups"])
        return bool(fact_coverage(ref, pages)["all_facts"])

    for meta in manifest["items"]:
        rid = meta["id"]
        measured = metrics["workflow"][rid]
        if not measured["scored"]:
            continue
        row = saved[rid]
        ref = refs[meta["suite"]][meta["question_id"]]
        official = meta["suite"].startswith("official_")
        search_keys = {page_key(p) for s in row["searches"] for p in s["ranked"]}
        sql_keys = {
            page_key(p)
            for s in row["steps"]
            if s["sql"] and s["sql"]["status"] == "ok"
            for p in s["sql"]["sources"]
        }
        searched = complete(
            ref, [source_pages[k] for k in sorted(search_keys)], official
        )
        source_union = complete(
            ref, [source_pages[k] for k in sorted(search_keys | sql_keys)], official
        )
        key = measured["complete_metric"]
        raw_final = bool(measured["stages"]["final"][key])
        quoted_final = bool(metrics["workflow_quotes"][rid]["stages"]["final"][key])
        merged = bool(measured["stages"]["available"][key])
        previous = bool(metrics["previous"][rid]["stages"]["final"][key])
        old_baseline = bool(metrics["baseline"][rid]["stages"]["final"][key])
        initial = bool(measured["stages"]["initial"][key])
        stage = (
            "success"
            if raw_final
            else "final_selection_or_budget"
            if merged
            else "merge_or_evidence_priority"
            if source_union
            else "search_or_table_discovery"
        )

        def change(before, after):
            return (
                "improved"
                if after and not before
                else "regressed"
                if before and not after
                else "unchanged"
            )

        output.append(
            {
                **meta,
                "category": measured["category"],
                "baseline_complete": old_baseline,
                "previous_v5_complete": previous,
                "initial_complete": initial,
                "search_union_complete": searched,
                "raw_source_upper_bound_complete": source_union,
                "merged20_complete": merged,
                "raw8_complete": raw_final,
                "retained_quotes_complete": quoted_final,
                "failure_stage": stage,
                "versus_v5": change(previous, raw_final),
                "versus_baseline": change(old_baseline, raw_final),
                "initial_success_lost": initial and not raw_final,
                "runtime_complete": row["complete"],
                "finish": row["finish"]["status"],
                "steps": len(row["steps"]),
                "api_calls": len(row["calls"]),
                "api_failures": [
                    c["stage"] for c in row["calls"] if c["status"] != "complete"
                ],
                "search_union_pages": len(search_keys),
                "raw_source_upper_bound_pages": len(search_keys | sql_keys),
                "raw_pages": len(row["evidence"]["pages"]),
                "raw_truncated_pages": sum(
                    p.get("truncated", False) for p in row["evidence"]["pages"]
                ),
                "tokens": row["trace"]["context_tokens"],
                "intermediate_claims": len(row["evidence"]["intermediate_answers"]),
                "dropped_claims": row["dropped_claims"],
            }
        )
    groups = {}
    for name, selected in {
        "official": [r for r in output if r["suite"].startswith("official_")],
        "categories_positive": [
            r for r in output if not r["suite"].startswith("official_")
        ],
        "v5_regressions": [r for r in output if r["versus_v5"] == "regressed"],
    }.items():
        groups[name] = {
            "n": len(selected),
            "stages": {
                k: sum(r[k] for r in selected)
                for k in (
                    "initial_complete",
                    "search_union_complete",
                    "raw_source_upper_bound_complete",
                    "merged20_complete",
                    "raw8_complete",
                    "retained_quotes_complete",
                )
            },
            "failures": dict(
                Counter(r["failure_stage"] for r in selected if not r["raw8_complete"])
            ),
            "initial_success_lost": sum(r["initial_success_lost"] for r in selected),
            "questions_with_api_failure": sum(
                bool(r["api_failures"]) for r in selected
            ),
        }
    write_json(
        DEST / "stage-diagnosis.json",
        {
            "groups": groups,
            "questions": output,
            "method": "Search union is full original candidate pages. Raw source upper bound additionally includes full pages touched by SQL; this is an upper bound, not actual SQL-result evidence.",
            "run_sha256": file_sha(directory / "run.json"),
            "script_sha256": file_sha(Path(__file__)),
        },
    )
    lines = [
        "# 검색 단계별 누락 분석",
        "",
        "결과와 답변을 모두 저장한 뒤 기존 참고 근거로 계산했다. 원문 검색 합집합은 각 검색에서 확보한 후보 원문의 합집합이다. "
        "SQL 출처까지 포함한 값은 해당 원문 전체가 있었다고 가정한 상한이며, 실제 SQL 결과가 그 근거를 반환했다는 뜻은 아니다.",
        "",
        "| 세트 | 문항 | 최초 후보 | 검색 후보 합집합 | SQL 출처 포함 상한 | 병합 20쪽 | 최종 원문 8쪽 | 보존 인용 포함 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, g in groups.items():
        lines.append(
            "| "
            + " | ".join([name, str(g["n"]), *map(str, g["stages"].values())])
            + " |"
        )
    lines += [
        "",
        "## 이전 v5보다 하락한 문항",
        "",
        "| 문항 ID | 누락 단계 | 최초 후보 충족 | 원문 쪽수 | 절단 쪽수 | 토큰 | 호출 실패 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    labels = {
        "search_or_table_discovery": "검색/표 발견",
        "merge_or_evidence_priority": "병합·중간 근거 우선",
        "final_selection_or_budget": "최종 선택/예산",
    }
    for r in output:
        if r["versus_v5"] != "regressed":
            continue
        lines.append(
            f"| {r['id']} | {labels[r['failure_stage']]} | {int(r['initial_complete'])} | "
            f"{r['raw_pages']} | {r['raw_truncated_pages']} | {r['tokens']} | {','.join(r['api_failures']) or '없음'} |"
        )
    lines += [
        "",
        "공식 지표는 정답 페이지 그룹의 출처 적중, 카테고리는 필수 사실/허용 대체 근거의 기존 채점 규칙이다. "
        "공식 출처 적중만으로 실제 답변의 의미적 완전성이 보장되지는 않는다. 문항별 값은 stage-diagnosis.json에 있다.",
        "",
    ]
    (DEST / "stage-diagnosis.md").write_text("\n".join(lines), encoding="utf-8")
    print(groups)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    diagnose(p.parse_args().directory.resolve())
