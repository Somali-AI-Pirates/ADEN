"""Post-run six-question latency and source-coverage report; no model calls."""

from pathlib import Path
import numpy as np

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_is_lookup_ab import BASELINE
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    page_key,
    score_item,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.benchmark import saved_results
from experimental.u_10o0o.table_rag.speed import V5

DEST = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-e2e-six-20261111/v1"


def stages(row):
    answer = row["answers"]["table_rag"]["telemetry"]
    plan = sum(t["api"]["seconds"] for t in row["turns"])
    search = sum(s["seconds"] for s in row["searches"])
    return {
        "total_seconds": row["retrieval_seconds"] + answer["seconds"],
        "retrieval_seconds": row["retrieval_seconds"],
        "planner_seconds": plan,
        "search_seconds": search,
        "other_retrieval_seconds": row["retrieval_seconds"] - plan - search,
        "answer_seconds": answer["seconds"],
        "planner_calls": len(row["turns"]),
        "search_calls": len(row["searches"]),
        "sql_calls": len(row["sql_attempts"]),
        "answer_status": answer["status"],
        "finish_status": row["finish"]["status"],
        "invalid_citations": row["answers"]["table_rag"]["invalid_citations"],
    }


def analyze():
    run = read_json(OUTPUT / "run.json")
    assert run["status"] == "complete" and run["count"] == 6
    for path, sha in run["files"].items():
        assert file_sha(OUTPUT / path) == sha
    assert run["protocol_sha256"] == file_sha(OUTPUT / "protocol.json")
    selection = read_json(OUTPUT / "selection.json")
    old = saved_results(V5)
    fresh = {
        q["id"]: read_json(OUTPUT / "results" / (object_sha(q["id"]) + ".json"))
        for q in selection["items"]
    }
    # References are opened only after every response has been saved and verified.
    baseline_manifest = BASELINE / "manifest.json"
    assert (
        file_sha(baseline_manifest)
        == read_json(V5 / "protocol.json")["baseline_manifest_sha256"]
    )
    for item in read_json(V5 / "score-manifest.json")["references"]:
        assert file_sha(ROOT / item["path"]) == item["sha256"]
    manifest = read_json(baseline_manifest)
    previous_scores = {
        r["id"]: r for r in read_json(V5 / "per-question.json")["table_rag"]
    }
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    meta = {r["id"]: r for r in manifest["items"]}
    source_pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    summaries = []
    for q in selection["items"]:
        rid = q["id"]
        current, previous = fresh[rid], old[rid]
        scores = {}
        for label, row in [("old", previous), ("new", current)]:
            m = meta[rid]
            result = score_item(
                m,
                refs[m["suite"]][m["question_id"]],
                row["traces"]["table_rag"],
                {"pages": row["contexts"]["table_rag"]},
                source_pages,
            )
            if label == "old":
                assert result["stages"] == previous_scores[rid]["stages"], (
                    "Old scores changed"
                )
            scores[label] = {
                "scored": result["scored"],
                "metric": result["complete_metric"],
                "final": result["stages"]["final"],
                "all_evidence": bool(
                    result["stages"]["final"][result["complete_metric"]]
                )
                if result["scored"]
                else None,
            }
        equal_queries = [s["query"] for s in current["searches"]] == [
            s["query"] for s in previous["searches"]
        ]
        summaries.append(
            {
                "id": rid,
                "group": q["group"],
                "old": stages(previous),
                "new": stages(current),
                "actual_new_wall_seconds": current["actual_wall_seconds"],
                "same_query_sequence": equal_queries,
                "same_final_context": current["traces"]["table_rag"]["context_sha256"]
                == previous["traces"]["table_rag"]["context_sha256"],
                "old_final_sources": sorted(
                    page_key(p) for p in previous["contexts"]["table_rag"]
                ),
                "new_final_sources": sorted(
                    page_key(p) for p in current["contexts"]["table_rag"]
                ),
                "evidence": scores,
                "gpu_before": current["gpu_before"],
                "gpu_after": current["gpu_after"],
            }
        )
    totals = [stages(r)["total_seconds"] for r in old.values()]
    calls = [t["api"] for r in fresh.values() for t in r["turns"]]
    calls.extend(r["answers"]["table_rag"]["telemetry"] for r in fresh.values())
    summary = {
        "old_population": {
            "n": len(totals),
            "total_p50": float(np.percentile(totals, 50)),
            "total_p95": float(np.percentile(totals, 95)),
        },
        "sample_rows": summaries,
        "api_calls": run["api_calls"],
        "api_usage": {
            k: sum(c.get(k, 0) for c in calls)
            for k in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cached_input_tokens",
            )
        },
        "answered": sum(
            r["answers"]["table_rag"]["telemetry"]["status"] == "complete"
            for r in fresh.values()
        ),
        "gold_scored_after_all_responses": True,
        "run_sha256": file_sha(OUTPUT / "run.json"),
        "source_run_sha256": file_sha(V5 / "run.json"),
        "reference_files": [
            {**r, "sha256": file_sha(ROOT / r["references"])}
            for r in manifest["question_sources"]
        ],
        "script_sha256": file_sha(Path(__file__)),
    }
    write_json(DEST / "summary.json", summary)
    labels = {"fast": "빠른", "median": "중앙", "slow": "느린"}
    lines = [
        "# TableRAG 최적화 적용 후 실제 응답 6문항",
        "",
        "CPU/GPU 잠금 분리와 GPU 리랭킹 배치 4를 개인 TableRAG의 cpu_gpu_rerank 경로에 적용했다. "
        "CPU 전용/shared_cuda의 기존 전체 잠금과 모델·프롬프트·추론 강도·검색/문맥 예산은 유지했다.",
        "",
        "## 표본과 전체 시간의 정의",
        "",
        "기존 v5의 성공 응답 280개 중 가장 빠른 2개·중앙값과 가까운 2개·가장 느린 2개를 새 실행 전에 고정했다. "
        "과거 및 이번 비교 시간은 `retrieval_seconds + answers.table_rag.telemetry.seconds`다. "
        "검색 단계에는 최초 검색, 계획 API, 후속 검색·SQL·문맥 조립과 모델 잠금 대기가 들어간다. "
        "최종 답변 생성은 뒤에 더한다. 이전 실험의 seconds 필드는 다른 비교군 답변 생성까지 포함하므로 사용하지 않는다.",
        "",
        f"기존 280문항의 전체 p50/p95는 {summary['old_population']['total_p50']:.2f}/{summary['old_population']['total_p95']:.2f}초다. "
        "단계별 p50끼리 더한 값이 아니라 문항마다 전체 시간을 합친 뒤 백분위수를 계산했다.",
        "",
        "GPT-6 Luna medium으로 계획과 답변을 새로 생성했다. 저장된 계획이나 답변 캐시는 사용하지 않았다. "
        "모델·색인을 미리 준비하고 원질문 6개로 로컬 검색을 워밍업했다. 워커는 이전과 같은 4개지만 전체 부하는 6문항이다. "
        "모델 적재와 워커 진입 전 큐 대기는 포함하지 않는다.",
        "",
        "## 실제 결과",
        "",
        "| 구간 | 문항 | 기존 전체 | 적용 후 전체 | 계획 호출 기존→새 | 검색 기존→새 | 필수 근거 기존→새 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in summaries:
        a, b = r["old"], r["new"]
        coverage = {
            arm: "평가 제외"
            if not r["evidence"][arm]["scored"]
            else "충족"
            if r["evidence"][arm]["all_evidence"]
            else "미충족"
            for arm in ("old", "new")
        }
        lines.append(
            f"| {labels[r['group']]} | {r['id'].split(':', 1)[1]} | {a['total_seconds']:.2f}초 | {b['total_seconds']:.2f}초 | {a['planner_calls']}→{b['planner_calls']} | {a['search_calls']}→{b['search_calls']} | {coverage['old']}→{coverage['new']} |"
        )
    lines += [
        "",
        "## 단계별 시간",
        "",
        "검색 시간에는 해당 검색의 CPU/GPU 잠금 대기가 포함된다. 기타는 SQL·문맥 포장·도구 관측 구성 등의 나머지 검색 단계 시간이다.",
        "",
        "| 문항 | 로컬 검색 기존→새 | 계획 API 기존→새 | 답변 API 기존→새 | 기타 기존→새 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in summaries:
        pairs = [
            f"{r['old'][k]:.2f}→{r['new'][k]:.2f}초"
            for k in (
                "search_seconds",
                "planner_seconds",
                "answer_seconds",
                "other_retrieval_seconds",
            )
        ]
        lines.append("| " + r["id"].split(":", 1)[1] + " | " + " | ".join(pairs) + " |")
    lines += [
        "",
        "## 판단",
        "",
        "느린 두 문항의 로컬 검색 합계는 AS-SEM-32가 20.20→6.02초, AS-SEM-30이 25.47→5.96초로 줄었다. "
        "계획과 검색도 각 6회에서 5회로 줄었으므로 감소분 전부를 잠금·배치 변경의 효과로 해석하지 않는다.",
        "AS-DEV20은 같은 최종 문맥에서 로컬 검색이 4.21→1.08초로 줄었지만 계획 API가 3.88→7.85초로 늘어 전체는 12.37→12.47초였다. "
        "AS-S01-19는 계획·검색이 1회에서 2회로 늘고 SQL 조회도 추가됐다. 실행마다 API 시간과 계획 경로가 달라져 빠른 문항도 더 느려질 수 있다.",
        "실제 기본 Retriever의 사전 원질문 검색은 6/6 모두 v5와 같았다. 이후 모델이 고른 하위 질문·최종 출처가 달라져 최종 문맥 해시는 1/6만 같았다. "
        "참고 근거로 채점 가능한 5문항은 기존/신규 모두 3개 충족·2개 미충족이었다. AS-DEV12는 기존 평가 정의상 근거 채점 대상이 아니므로 N/A로 유지했다. "
        "새 답변의 의미 정확도까지 같다고 주장하지 않는다.",
        "현재 구성은 로컬 검색의 동시 처리 개선으로 적용했다. 전체 응답을 더 줄이는 문제는 LLM 계획 호출 지연과 반복 검색 횟수에 남아 있다. "
        "이번 6건으로 새 전체 p50/p95나 운영 SLA를 제시하지 않는다.",
        "",
        "## 해석 범위",
        "",
        "이 6문항은 작은 동작·지연 확인이다. 과거 극단값을 고른 재측정이며 새 계획의 호출 횟수와 API 지연, 280문항/6문항 부하 차이가 함께 변한다. "
        "시간이 줄어든 전부를 잠금·배치 변경의 효과로 돌리거나 이 표본으로 운영 p95를 계산하지 않는다. "
        "동일 계획에서의 로컬 실행 효과는 앞선 280문항 동일성 검사와 15문항 반복 측정을 근거로 삼는다.",
        "",
        "필수 근거 점수는 원본 페이지 기준의 기존 공식/카테고리 채점기를 그대로 사용했다. 생성 답변의 의미 정확도는 새 LLM 채점을 하지 않았다. "
        "답변 호출 완료, 출처 형식 검사와 실제 답변 내용의 정확성은 구분한다.",
        "",
        f"실제 호출 {run['api_calls']}회, 답변 완료 {summary['answered']}/6. 자동 재시도·추가 채점 호출은 없었다. "
        f"최종 문맥 해시가 과거와 같은 문항은 {sum(r['same_final_context'] for r in summaries)}/6이다. "
        "세부 출처와 원문 질문/답변은 로컬 산출물에 보존했다.",
        "",
        "[실행 과정](work-log.md) · [문항별 시간·근거 결과](summary.json) · "
        "[이전 로컬 속도 비교](../advanced-table-rag-speed-20261111/conclusion.md)",
        "",
    ]
    (DEST / "report.md").write_text("\n".join(lines))
    print("Post-run report saved")
    for r in summaries:
        print(
            r["id"],
            r["old"],
            r["new"],
            "same_context",
            r["same_final_context"],
            "evidence",
            r["evidence"]["old"]["all_evidence"],
            r["evidence"]["new"]["all_evidence"],
        )


if __name__ == "__main__":
    analyze()
