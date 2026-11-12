"""Render aggregate HG ablation results without private source quotations."""

from pathlib import Path
from experimental.u_10o0o.pattern_retrieval.common import read_json

D = Path(__file__).parent
LABELS = {
    "official_all": "공식",
    "categories_positive": "카테고리 근거형 전체",
    "S01": "1 직접 조회",
    "S02": "2 번호 없이 사건 식별",
    "S03": "3 한 사건의 여러 기록",
    "S04": "4 두 사건 비교",
    "S05": "5 적용 기록·조건",
    "S06": "6 모호한 두 후보",
    "S07": "7 확정 근거 부족",
    "work_rules_all": "업무 절차·규정",
    "semantic_single": "일상 표현·한 문서",
    "semantic_multi": "일상 표현·여러 문서",
}


def main():
    summary = read_json(D / "reviewed-summary.json")
    usage = read_json(D / "timing.json")
    controls = read_json(D / "controls.json")
    decisions = read_json(D / "review-manifest.json")
    differences = read_json(D / "reviewed-differences.json")
    negative = read_json(D / "negative-control.json")
    lines = [
        "# TableRAG HG 원본 보강 ON/OFF",
        "",
        "A는 기존 HG 원본 보강을 유지하고 B는 모든 검색에서 그 단계만 생략했다. BM25·KURE-v3 벡터·5:5 RRF·KURE nano·Luna medium·SQLite·최종 병합은 같다. 양쪽에서 Luna가 새로 후속 검색·SQL을 결정했다. 기존 기본 실행은 변경하지 않았다. OFF는 complete_sources_with_dates 전체를 우회하므로 명시 HG·간접/날짜·모호한 두 후보·두 사건 원본 보강이 함께 빠진다.",
        "",
        "공식60 중 이전부터 참조 근거가 없는 AS-DEV12·AS-ADD12를 제외한58개와 카테고리220개를 실행했다. 카테고리는 근거형200개와 범위 밖20개다. 범위 밖은 별도 집계한다. 답변 생성은 하지 않았으므로 수치는 검색 근거 충족률이다.",
        "",
        "## 최종 근거 충족",
        "",
        "| 유형 | n | 지정 HG ON | 지정 HG OFF | 검수 ON | 검수 OFF | OFF 개선/하락 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in LABELS:
        s = summary[key]
        n, a, b = s["n"], s["A"], s["B"]
        cells = [
            f"{v}/{n} ({v / n:.1%})"
            for v in (a["strict"], b["strict"], a["reviewed"], b["reviewed"])
        ]
        lines.append(
            f"| {LABELS[key]} | {n} | "
            + " | ".join(cells)
            + f" | +{s['improved']}/−{s['regressed']} |"
        )
    lines += [
        "",
        f"指定 근거 실패가 하나라도 있는 문항의 양쪽 최종 출력과 기존 모호 판정, 총{decisions['reviewed_outputs']}개 출력을 재검수했다.".replace(
            "指定", "지정"
        ),
        "실제로 남은 원문/유지 SQL이 질문의 대상·시점·값·조건을 모두 지원하는 경우만 대체 근거로 인정했다. 단순 주제 유사성은 인정하지 않았다. AI 검수·출처 인용 대조·자체 검토이며 독립 사람 검수가 아니다. 나머지 지정 통과 문항까지 모두 의미 검수한 것은 아니다. 페이지 Recall은 보정하지 않았다.",
        "",
        "## 후보 확보와 페이지 Recall",
        "",
        "| 유형 | 후보20 지정 ON | 후보20 지정 OFF | 최종 페이지 Recall ON | 최종 페이지 Recall OFF |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in LABELS:
        s = summary[key]
        a, b = s["A"], s["B"]
        lines.append(
            f"| {LABELS[key]} | {a['candidate']}/{s['n']} | {b['candidate']}/{s['n']} | {a['page_recall']:.1%} | {b['page_recall']:.1%} |"
        )
    lines += [
        "",
        "## 지연과 호출",
        "",
        "| 묶음 | HG ON p50/p95초 | HG OFF p50/p95초 | 계획 호출 ON/OFF | 검색 호출 ON/OFF |",
        "|---|---:|---:|---:|---:|",
    ]
    timing_groups = {
        k: {a: summary[k][a]["timing"] for a in ("A", "B")}
        for k in ("official_all", "categories_positive")
    }
    timing_groups["all"] = usage
    for name, pair in timing_groups.items():
        a, b = pair["A"], pair["B"]
        label = LABELS.get(name, "전체278")
        lines.append(
            f"| {label} | {a['end_to_end']['p50']:.2f}/{a['end_to_end']['p95']:.2f} | {b['end_to_end']['p50']:.2f}/{b['end_to_end']['p95']:.2f} | {a['planner_calls']}/{b['planner_calls']} | {a['search_calls']}/{b['search_calls']} |"
        )
    a, b = usage["A"], usage["B"]
    lines += [
        "",
        "CPU KURE-v3 질의 임베딩, GPU KURE nano 리랭킹, 동시4요청이다. 모델 적재·웜업·답변 생성·사후 검수는 제외하고 임베딩·검색·Luna 계획·SQLite·최종 문맥 및 요청 간 대기를 포함했다. 각 문항은 조건별1회 실행했고 A/B 순서를 교대했다. API 응답 및 공유 장치 부하의 변동이 있으므로 지연의 작은 차이를 안정적인 속도 개선으로 단정하지 않는다.",
        "",
        f"HG 보강 자체는 검색1회당 p50 {a['hg_per_search']['p50'] * 1000:.2f}ms, p95 {a['hg_per_search']['p95'] * 1000:.2f}ms였다. SQL 시도 ON/OFF는 {a['sql_calls']}/{b['sql_calls']}회이고 성공은 {a['sql_ok']}/{b['sql_ok']}회, 최종 유지 결과는 {a['sql_kept']}/{b['sql_kept']}개다.",
        "",
        f"API 상태 ON {a['api_status']}, OFF {b['api_status']}. 사후 검수 API는 이 검색 지연·호출 수에 포함하지 않았다.",
        "",
        "## 통제 확인",
        "",
        f"- 원질문 HG 전 후보가 같은 문항 {controls['initial_raw_candidates_equal']}/{controls['n']}, HG 전 순위가 같은 문항 {controls['initial_pre_hg_rank_equal']}/{controls['n']}.",
        f"- OFF의 HG 원본 보강 실행은 {controls['off_hg_lookup_calls']}회다. ON에서 첫 검색의 순위/페이지가 변한 문항은 {controls['initial_hg_changed_questions']}개다.",
        "- HG OFF는 원문이나 SQLite 행을 제거하지 않는다. 완전 코드 BM25와 뒤의 SQLite 도구도 유지한다.",
        "- 동일 프롬프트라도 Luna가 새로 계획하므로 결과 차이에는 계획의 변동성이 포함된다. HG와 무관한 문항의 차이는 흐름을 별도로 확인한다.",
        "",
        "## 검수 후 문항별 개선·하락",
        "",
        "| 문항 ID | HG ON | HG OFF | 첫 HG 보강이 순위를 바꿈 |",
        "|---|---:|---:|---:|",
    ]
    for r in differences:
        lines.append(
            f"| {r['id']} | {int(r['hg_on'])} | {int(r['hg_off'])} | {r['initial_hg_changes_order']} |"
        )
    lines += ["", "## 범위 밖20문항", ""]
    for arm, label in (("A", "HG ON"), ("B", "HG OFF")):
        lines.append(
            f"- {label}의 계획 종료 상태 {negative[arm]['finish']}. 답변을 생성하지 않았으므로 거절 답변 정확도로 해석하지 않는다."
        )
    lines += [
        "",
        "## 기록과 재현",
        "",
        "[최종 판단](conclusion.md) · [설계·작업 기록](work-log.md) · [실행기](run_benchmark.py) · [지정 점수](per-question.json) · [검수 점수](reviewed-per-question.json) · [문항별 변화](reviewed-differences.json) · [지연](timing.json) · [검수 확인](review-manifest.json) · [검사](verification.md)",
        "",
        "민감 원문·질문·참고답·인용은 artifacts/u_10o0o/advanced-table-rag-hg-ablation-20261111/v1에만 보관했다. 기존 코드와 결과는 덮어쓰지 않았다.",
        "",
        chr(96) * 3 + "bash",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-hg-ablation-20261111.run_benchmark selfcheck",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-hg-ablation-20261111.analyze report",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-hg-ablation-20261111.render_report",
        chr(96) * 3,
        "",
        "실제 새 실행은 별도의 빈 출력 경로에서 freeze/run을 수행한다. 완료된 경로의 결과를 덮어쓰지 않는다.",
        "",
    ]
    (D / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
