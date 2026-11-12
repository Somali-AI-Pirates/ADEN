"""Post-evaluation export: no changes to retrieval or its frozen runtime."""

import argparse
from collections import Counter
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
)
from experimental.u_10o0o.table_rag.benchmark import saved_results

DEST = Path(__file__).resolve().parent


def union_diagnosis(directory, records):
    from app.evaluation.dataset import load_references
    from experimental.u_10o0o.advanced_is_lookup_ab import BASELINE
    from experimental.u_10o0o.pattern_retrieval.common import ROOT
    from experimental.u_10o0o.pattern_retrieval.established_score import (
        validate_saved,
        page_key,
        official_metrics,
    )
    from experimental.u_10o0o.advanced_full_metrics import fact_coverage
    from experimental.u_10o0o.prepare import CACHE_PATH

    manifest, _, _ = validate_saved(BASELINE)
    refs = {
        x["suite"]: load_references(ROOT / x["references"])
        for x in manifest["question_sources"]
    }
    source_pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    scored = {
        r["id"]: r for r in read_json(directory / "per-question.json")["table_rag"]
    }
    rows = []
    for meta in manifest["items"]:
        score = scored[meta["id"]]
        if not score["scored"]:
            continue
        record = records[meta["id"]]
        keys = {page_key(p) for search in record["searches"] for p in search["ranked"]}
        keys.update(
            page_key(p)
            for sql in record["sql_attempts"]
            if sql["status"] == "ok"
            for p in sql["sources"]
        )
        pages = [source_pages[k] for k in sorted(keys)]
        ref = refs[meta["suite"]][meta["question_id"]]
        all_found = (
            bool(official_metrics(ref, pages, len(pages))["all_groups"])
            if meta["suite"].startswith("official_")
            else bool(fact_coverage(ref, pages)["all_facts"])
        )
        metric = score["complete_metric"]
        available = bool(score["stages"]["available"][metric])
        final = bool(score["stages"]["final"][metric])
        stage = (
            "success"
            if final
            else "final_selection"
            if available
            else "candidate_merge"
            if all_found
            else "search_requests"
        )
        rows.append(
            {
                **meta,
                "all_search_union_pages": len(keys),
                "all_searches_complete": all_found,
                "merged20_complete": available,
                "final8_complete": final,
                "stage": stage,
            }
        )
    write_json(DEST / "union-diagnosis.json", rows)
    return rows


def analyze(directory):
    directory = Path(directory)
    records = saved_results(directory)
    differences = read_json(directory / "differences.json")
    stages = union_diagnosis(directory, records)
    usage = read_json(directory / "telemetry.json")
    original_usage_sha = file_sha(directory / "telemetry.json")
    # The SDK helper stores input/output separately, not total_tokens.
    # Derive totals instead of treating an absent SDK field as measured zero.
    usage["api_usage"]["total_tokens"] = (
        usage["api_usage"]["input_tokens"] + usage["api_usage"]["output_tokens"]
    )
    for arm in usage["answer_api_usage"].values():
        arm["total_tokens"] = arm["input_tokens"] + arm["output_tokens"]
    usage["total_token_provenance"] = {
        "calculation": "recorded input_tokens + output_tokens; reasoning is included in output, cached tokens in input",
        "raw_telemetry_sha256": original_usage_sha,
        "postprocessor_sha256": file_sha(Path(__file__)),
    }
    write_json(DEST / "telemetry.json", usage)
    if (directory / "timing.json").exists():
        write_json(DEST / "timing.json", read_json(directory / "timing.json"))
    flows = []
    for delta in differences:
        r = records[delta["id"]]
        available = {p["source_id"] for p in r["traces"]["table_rag"]["ranked"]}
        final = {f"{p['book_id']}:p{p['page']:04d}" for p in r["contexts"]["table_rag"]}
        requested = set(r["finish"]["selected_sources"])
        flows.append(
            {
                **delta,
                "search_calls": len(r["searches"]),
                "planner_calls": len(r["turns"]),
                "finish_status": r["finish"]["status"],
                "model_selected_outside_final_pool": sorted(requested - available),
                "model_selected_not_in_final_context": sorted(requested - final),
                "sql_calls": len(r["sql_attempts"]),
                "sql_status": dict(Counter(s["status"] for s in r["sql_attempts"])),
                "sql_results_kept": len(r["sql_results"]),
                "original_first_pool_pages": [
                    p["source_id"] for p in r["traces"]["table_rag"]["pool"]
                ],
                "final_pool_pages": [
                    p["source_id"] for p in r["traces"]["table_rag"]["ranked"]
                ],
                "final_pages": sorted(final),
            }
        )
    write_json(DEST / "flow-audit.json", flows)
    write_json(
        DEST / "analysis-manifest.json",
        {
            "script_sha256": file_sha(Path(__file__)),
            "report_generator_sha256": file_sha(
                DEST.parent.parent / "table_rag/report.py"
            ),
            "run_sha256": file_sha(directory / "run.json"),
            "source_outputs": {
                name: file_sha(directory / name)
                for name in (
                    "summary.json",
                    "differences.json",
                    "telemetry.json",
                    "answer-summary.json",
                )
            },
            "source_oracle_sha256": file_sha(directory / "source-oracle.json"),
            "retrieval_or_answers_modified": False,
        },
    )
    proof = read_json(directory / "reuse-proof.json")
    write_json(DEST / "reuse-proof.json", proof)
    parity = read_json(directory.parent / "hg-parity.json")
    write_json(DEST / "hg-parity.json", parity)
    timing = read_json(directory / "timing.json")
    summary = dict(Counter(r["stage"] for r in stages))
    extra = [
        "\n## 실행 출처와 추가 분석\n",
        f"최종 v5 결과 280건 중 {proof['rerun']}건은 선택한 HG 날짜 조회 보완을 포함해 재실행했다. "
        f"{proof['reused']}건은 원질문과 모든 저장된 후속 검색에서 새 분기가 발동하지 않는지 검사한 뒤 결과 지문을 대조해 재사용했다. "
        "기존 기준선 순위·문맥은 같은 조회 함수로 280/280 재현했다. 수정 전 v4 자체 점수를 최종 결과에 섞지 않았다.",
        "",
        f"258개 근거 채점 문항 중 최종 충족은 {summary.get('success', 0)}개다. "
        f"전체 조회에도 근거가 없던 문항은 {summary.get('search_requests', 0)}개, "
        f"조회에는 있었지만 후보 20쪽 통합에서 빠진 문항은 {summary.get('candidate_merge', 0)}개, "
        f"후보에는 있었지만 최종 8쪽 선택에서 실패한 문항은 {summary.get('final_selection', 0)}개다.",
        "유일한 하락 문항 AS-SEM-37도 조회 합집합에는 필수 근거가 있었지만 통합 후보 20쪽에서 빠졌다.",
        "search_requests는 전체 조회 결과에도 필수 근거가 없는 경우, candidate_merge는 조회에는 있었지만 합친 후보 20쪽에서 빠진 경우, "
        "final_selection은 후보 20쪽에는 있지만 최종 8쪽에서 빠진 경우다. 단계별 판정은 [union-diagnosis.json](union-diagnosis.json)에 있다.",
        "",
        "API를 제외한 20개 고정 표본의 순차 재생 결과다.",
        "",
        "| 설정 | 로컬 연산 p50 | 로컬 연산 p95 | 저장된 최종 문맥과 일치 |",
        "|---|---:|---:|---:|",
        *[
            f"| {label} | {timing['summary'][arm]['p50']:.2f}초 | "
            f"{timing['summary'][arm]['p95']:.2f}초 | {timing['summary'][arm]['same_final']}/20 |"
            for arm, label in (("baseline", "기준선"), ("table_rag", "TableRAG"))
        ],
        "",
        "이 값은 저장된 하위 질문을 재생한 로컬 연산 시간이며 모델의 계획 결정을 다시 호출한 전체 지연이 아니다. "
        "원질문부터 새로 계획한 전체 지연은 telemetry의 관측값을 사용한다.",
        "",
        "기준선의 새 실행은 4문항에서 저장 문맥과 달랐다. 따라서 이 로컬 지연 표를 결과까지 완전히 같은 "
        "두 실행의 엄밀한 속도 비교로 해석하지 않는다. 본 정확도 비교의 기준선 문맥 280건은 저장본과 동일하다.",
        "",
        "사용량 합계는 실제 기록된 input_tokens + output_tokens로 계산했다. 실패/중단되어 사용량을 돌려받지 못한 호출의 과금과 "
        "v3/v4 초도 실행 비용은 본 평가 사용량에 포함하지 않는다.",
        "",
        "설계 대응은 [paper-coverage.md](paper-coverage.md), 실행 과정은 [work-log.md](work-log.md), "
        "재사용 근거는 [reuse-proof.json](reuse-proof.json), 문항별 흐름은 [flow-audit.json](flow-audit.json)에서 확인할 수 있다.",
        "",
    ]
    report = DEST / "report.md"
    text = report.read_text()
    marker = "\n## 실행 출처와 추가 분석\n"
    if marker in text:
        text = text.split(marker)[0]
    report.write_text(text + "\n".join(extra), encoding="utf-8")
    print("Flow audit and token totals exported")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    analyze(parser.parse_args().directory)
