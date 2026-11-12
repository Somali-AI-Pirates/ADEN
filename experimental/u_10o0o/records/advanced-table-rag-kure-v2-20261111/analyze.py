"""Score only after 280 new retrievals are saved; preserve the original references."""

from collections import Counter
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
    score_item,
    aggregates,
    page_key,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.benchmark import saved_results
from experimental.u_10o0o.table_rag.kure_benchmark import OUTPUT, RECORDS, SIX
from experimental.u_10o0o.table_rag.speed import V5

GROUPS = {
    "official_all": "공식 전체",
    "official_complex_groups": "공식 복합",
    "categories_positive": "카테고리 전체",
    **{f"S{i:02d}": f"상황 {i}" for i in range(1, 8)},
    "semantic_all": "일상 표현",
    "work_rules_all": "업무 규정",
}


def timing(row):
    result = {
        "retrieval_seconds": row["retrieval_seconds"],
        "search_seconds": sum(s["seconds"] for s in row["searches"]),
        "planner_seconds": sum(t["api"]["seconds"] for t in row["turns"]),
        "planner_calls": len(row["turns"]),
        "search_calls": len(row["searches"]),
        "sql_calls": len(row["sql_attempts"]),
    }
    answer = row["answers"].get("table_rag")
    if answer:
        result.update(
            answer_seconds=answer["telemetry"]["seconds"],
            total_seconds=row["retrieval_seconds"] + answer["telemetry"]["seconds"],
            answer_status=answer["telemetry"]["status"],
            invalid_citations=answer["invalid_citations"],
        )
    return result


def telemetry(rows):
    searches = [s for r in rows.values() for s in r["searches"]]
    apis = [t["api"] for r in rows.values() for t in r["turns"]]
    measured_queries = [s["query_input"] for s in searches if "query_input" in s]
    return {
        "planner_calls": len(apis),
        "search_calls": len(searches),
        "sql_calls": sum(len(r["sql_attempts"]) for r in rows.values()),
        "sql_status": dict(
            Counter(s["status"] for r in rows.values() for s in r["sql_attempts"])
        ),
        "planner_status": dict(Counter(a["status"] for a in apis)),
        "finish_status": dict(Counter(r["finish"]["status"] for r in rows.values())),
        "query_truncated": sum(q["truncated"] for q in measured_queries)
        if measured_queries
        else None,
        "query_max_raw_tokens": max(
            (q["raw_tokens"] for q in measured_queries), default=None
        ),
        "tool_status": dict(
            Counter(t.get("status") for r in rows.values() for t in r["turns"])
        ),
        "rerank_pairs": sum(s["work"]["rerank_pairs"] for s in searches),
        "rerank_truncated_pairs": sum(s["work"]["truncated_pairs"] for s in searches),
        "retrieval_p50": float(
            np.percentile([r["retrieval_seconds"] for r in rows.values()], 50)
        ),
        "retrieval_p95": float(
            np.percentile([r["retrieval_seconds"] for r in rows.values()], 95)
        ),
        "planner_usage": {
            k: sum(a.get(k, 0) for a in apis)
            for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        },
    }


def analyze():
    run = read_json(OUTPUT / "run.json")
    assert (
        run["status"] == "complete"
        and run["count"] == 280
        and not run["references_opened"]
    )
    assert file_sha(OUTPUT / "protocol.json") == run["protocol_sha256"]
    for path, sha in run["files"].items():
        assert file_sha(OUTPUT / path) == sha
    new = {r["id"]: r for r in (read_json(OUTPUT / p) for p in run["files"])}
    old = saved_results(V5)
    protocol = read_json(OUTPUT / "protocol.json")
    assert protocol["source_run_sha256"] == file_sha(V5 / "run.json")
    assert (
        file_sha(BASELINE / "manifest.json")
        == read_json(V5 / "protocol.json")["baseline_manifest_sha256"]
    )
    reference_manifest = read_json(V5 / "score-manifest.json")
    for item in reference_manifest["references"]:
        assert file_sha(ROOT / item["path"]) == item["sha256"]
    # Only now open labels; the older baseline runtime validator is not needed for scoring outputs.
    manifest = read_json(BASELINE / "manifest.json")
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    previous_score = {
        r["id"]: r for r in read_json(V5 / "per-question.json")["table_rag"]
    }
    rows = {}
    for arm, saved in [("A", old), ("B", new)]:
        rows[arm] = []
        for m in manifest["items"]:
            r = saved[m["id"]]
            score = score_item(
                m,
                refs[m["suite"]][m["question_id"]],
                r["traces"]["table_rag"],
                {"pages": r["contexts"]["table_rag"]},
                pages,
            )
            if arm == "A":
                assert score["stages"] == previous_score[m["id"]]["stages"]
            rows[arm].append(score)
    summary = {a: aggregates(rs) for a, rs in rows.items()}
    changes = []
    for a, b in zip(rows["A"], rows["B"], strict=True):
        if not a["scored"]:
            continue
        metric = a["complete_metric"]
        af = bool(a["stages"]["final"][metric])
        bf = bool(b["stages"]["final"][metric])
        if af != bf:
            rid = a["id"]
            changes.append(
                {
                    "id": rid,
                    "suite": a["suite"],
                    "category": a["category"],
                    "old_complete": af,
                    "new_complete": bf,
                    "old_stages": a["stages"],
                    "new_stages": b["stages"],
                    "old_calls": timing(old[rid]),
                    "new_calls": timing(new[rid]),
                    "old_sources": sorted(
                        page_key(p) for p in old[rid]["contexts"]["table_rag"]
                    ),
                    "new_sources": sorted(
                        page_key(p) for p in new[rid]["contexts"]["table_rag"]
                    ),
                }
            )
    previous_six_run = read_json(SIX / "run.json")
    assert file_sha(SIX / "run.json") == protocol["previous_six_run_sha256"]
    for path, sha in previous_six_run["files"].items():
        assert file_sha(SIX / path) == sha
    six = []
    score_by_id = {a: {r["id"]: r for r in rs} for a, rs in rows.items()}
    for rid in protocol["six_answer_ids"]:
        before = read_json(SIX / "results" / (object_sha(rid) + ".json"))
        after = new[rid]
        m = score_by_id["B"][rid]
        metric = m["complete_metric"]
        six.append(
            {
                "id": rid,
                "old": timing(before),
                "new": timing(after),
                "new_final_complete": bool(m["stages"]["final"][metric])
                if m["scored"]
                else None,
                "same_final_context": before["traces"]["table_rag"]["context_sha256"]
                == after["traces"]["table_rag"]["context_sha256"],
            }
        )
    usage = {a: telemetry(rs) for a, rs in [("A", old), ("B", new)]}
    result = {
        "quality": summary,
        "telemetry": usage,
        "six_responses": six,
        "improved": sum(c["new_complete"] for c in changes),
        "regressed": sum(c["old_complete"] for c in changes),
        "api_calls": run["api_calls"],
        "cuda_peak_allocated_bytes": run["cuda_peak_allocated_bytes"],
        "run_sha256": file_sha(OUTPUT / "run.json"),
        "script_sha256": file_sha(Path(__file__)),
        "reference_files": reference_manifest["references"],
        "references_read_after_all_outputs": True,
    }
    write_json(RECORDS / "summary.json", result)
    write_json(RECORDS / "differences.json", changes)
    write_json(RECORDS / "per-question.json", rows)
    lines = [
        "# TableRAG KURE-v2·128/1024 비교",
        "",
        "A는 저장된 TableRAG v5의 KURE-v3·질문/리랭커2048 구성이다. B는 KURE-v2 MaxSim·질문128·리랭커1024다. "
        "BM25·5:5 RRF·각 검색의 후보20쪽·HG 조회·최종8쪽·8000토큰·Luna medium·프롬프트는 유지했다. "
        "별도 실험의 범위 필터나 Top3+RRF는 가져오지 않았다. 현재 기본 설정은 A다.",
        "",
        "## 원질문 검색과 최종 근거",
        "",
        "원질문 1회 검색은 LLM이 후속 검색을 결정하기 전의 결과다. 최종 점수는 새 LLM 계획과 후속 조회·선택까지 포함한다. "
        "모델·입력 상한·GPU 배치 환경이 함께 바뀐 구성 비교이므로 입력 한도 확대 하나의 효과라고 해석하지 않는다.",
        "",
        "| 유형 | 대상 | 원질문 A | 원질문 B | 최종 A | 최종 B |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in GROUPS.items():
        a, b = summary["A"][key], summary["B"][key]
        n = a["scored"]
        values = [
            f"{s['stages'][stage]['complete']}/{n} ({100 * s['stages'][stage]['complete_rate']:.1f}%)"
            for stage in ("initial", "final")
            for s in (a, b)
        ]
        lines.append(f"| {label} | {n} | " + " | ".join(values) + " |")
    lines += [
        "",
        "公式/카테고리의 원래 근거 정의를 유지했다. 공식2개와 음성 대조20개는 일반 근거 점수에서 제외한다. "
        "다른 실험에서 사후 검수한 대체 출처나 SEM-32 제외는 적용하지 않았다. book_id와 page를 함께 채점했다.".replace(
            "公式", "공식"
        ),
        "",
        f"최종 전체 근거 충족 기준 개선 {result['improved']}개, 하락 {result['regressed']}개다. "
        "문항별 단계·출처와 호출 횟수는 differences.json, 모든 문항의 Recall·MRR·nDCG는 per-question.json에 남겼다.",
        "",
        "## 기존 6문항 실제 응답",
        "",
        "A의 시간은 직전 잠금 분리·배치4 적용 후 실제 6문항 실행값이다. B도 같은6개를 먼저4워커로 실제 계획+답변까지 호출했다. "
        "과거280문항의 극단값과 비교하는 표가 아니며, API 지연과 새로운 계획 경로의 변동은 남는다. 전체는 검색/계획 완료 시간+답변 생성시간이다.",
        "",
        "| 문항 | A 전체 | B 전체 | 계획 호출 A→B | 검색 A→B | B 필수 근거 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in six:
        a, b = r["old"], r["new"]
        covered = {True: "충족", False: "미충족", None: "N/A"}[r["new_final_complete"]]
        lines.append(
            f"| {r['id'].split(':', 1)[1]} | {a['total_seconds']:.2f}초 | {b['total_seconds']:.2f}초 | {a['planner_calls']}→{b['planner_calls']} | {a['search_calls']}→{b['search_calls']} | {covered} |"
        )
    lines += ["", "## 호출·입력 절단·메모리", "", "| 항목 | A | B |", "|---|---:|---:|"]
    for key, label in [
        ("planner_calls", "계획 API"),
        ("search_calls", "검색"),
        ("sql_calls", "SQL"),
        ("rerank_pairs", "리랭킹 쌍"),
        ("rerank_truncated_pairs", "리랭커 절단 쌍"),
    ]:
        lines.append(f"| {label} | {usage['A'][key]} | {usage['B'][key]} |")
    lines += [
        "",
        f"B의 질문128토큰 절단은 {usage['B']['query_truncated']}/{usage['B']['search_calls']}회, 최대 원래 질문 길이는 {usage['B']['query_max_raw_tokens']}토큰이다. "
        f"B의 계획 호출 상태는 {usage['B']['planner_status']}, SQL 상태는 {usage['B']['sql_status']}다.",
        f"새 외부 호출은 총 {run['api_calls']}회(계획+답변6회)다. 답변 자동 채점은 하지 않았다. "
        f"GPU 최대 PyTorch 할당은 {run['cuda_peak_allocated_bytes'] / 1024**3:.2f}GiB다.",
        "A는 CPU bfloat16 질문 임베딩+GPU float32 리랭커다. B는 KURE-v2 질문 임베딩·MaxSim도 GPU로 옮겼고 토큰 색인은 float16이다. "
        "원본 토큰 색인 약1.8GB와 추가 표 색인을 상주시켜야 해 프로세스 GPU 비율을0.12에서0.65로 늘렸다. "
        "두 모델과 MaxSim은 같은 GPU 잠금으로 보호한다. B의 속도를 동일 메모리 비용에서의 순수 모델 성능으로 표현하지 않는다.",
        "",
        "## 이번 적용 판단",
        "",
        "최종 필수 근거는 A 233/258(90.3%), B 232/258(89.9%)로 개선되지 않았다. 원질문1회 검색도223→221개였다. "
        "일상 표현 최종 점수는25→26개지만 공식은50→48개였다. 현재 KURE-v3 기준선을 교체할 근거는 부족하다.",
        "하락7건 중 DEV14·ADD14는 새 최종 후보20쪽에 필수 근거가 있었지만 최종8쪽에서 빠졌다. "
        "나머지5건은 새 최종 후보20쪽부터 필수 근거가 부족했다. 그5건 모두 최초 검색 실패라는 뜻은 아니며 후속 질의·후보 통합도 포함한다.",
        "128토큰 질의 절단은 SEM-37의 마지막 후속 검색150토큰에서 발생했고 이 문항은 최종 점수가 개선됐다. "
        "리랭커1024 절단1쌍은 하락한 SEM-09의 최초 검색에서 발생했다. 절단 하나가 하락 원인인지 분리한 통제 비교는 하지 않았으므로 원인으로 확정하지 않는다.",
        "6문항은5개가 빨라지고1개가 느려졌다. 느려진 SEM-30은 계획/검색이5회에서6회로 늘었다. "
        "단건 속도 변화에는 GPU에서의 질의 인코딩·MaxSim, 새 LLM 계획과 API 지연이 함께 포함된다. "
        "일상 표현 근거를 놓치는 문제는 이번 교체만으로 해결되지 않았다.",
        "",
        "## 판단의 범위",
        "",
        "전체 품질 대조군 A는 검증된 과거 v5 출력이며 B는 새 LLM 실행이다. 원질문 검색 비교는 LLM 계획 변동 이전의 신호이고, "
        "최종 변화에는 모델의 후속 질의와 출처 선택도 포함된다. 이번 개발 세트를 여러 실험에 사용했으므로 독립 검증 결과가 아니다. "
        "6개 답변의 API 완료와 실제 답변 정확도는 구분하며, 모델 기반 답변 채점이나 사람 검수는 추가하지 않았다.",
        "기본 모델 자동 교체·운영 API 변경·커밋·푸시는 수행하지 않았다. 독립 에이전트가 금지된 부대 대화여서 자체 검토했다.",
        "",
        "[과정과 재현](work-log.md) · [집계](summary.json) · [문항별 개선·하락](differences.json) · [전체 문항 점수](per-question.json)",
        "",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines))
    print(
        "Scoring complete",
        {
            "improved": result["improved"],
            "regressed": result["regressed"],
            "api_calls": run["api_calls"],
        },
    )
    for key in (
        "official_all",
        "categories_positive",
        "semantic_all",
        "work_rules_all",
    ):
        print(
            key, {a: summary[a][key]["stages"]["final"]["complete"] for a in ("A", "B")}
        )


if __name__ == "__main__":
    analyze()
