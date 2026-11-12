"""Render all-question evidence results without private quotations."""

from pathlib import Path
from experimental.u_10o0o.pattern_retrieval.common import read_json

HERE = Path(__file__).parent


def main():
    summary = read_json(HERE / "reviewed-summary.json")["official_all"]
    per = read_json(HERE / "reviewed-per-question.json")
    flows = read_json(HERE / "routing-per-question.json")
    timing = read_json(HERE / "timing.json")
    audit = read_json(HERE / "review-manifest.json")
    assert audit["all_required_checked"] and audit["reviewed_outputs"] == 116

    def count(arm, field, complex_only=False):
        rows = [r for r in per[arm] if not complex_only or r["complex"]]
        return f"{sum(bool(r[field]) for r in rows)}/{len(rows)}"

    lines = [
        "# 공식58: 요청별 근거 확인 비교",
        "",
        "> 실험 종료 후 최종 선택은 [통합 DB 우선·인용 검사 OFF](../advanced-table-rag-closeout-20261112/decision.md)다. 아래 A/B·점수는 이 실행의 기록이며 다른 실행의 기준선 점수와 합치지 않는다.",
        "",
        "A는 기존 DB 우선 통합 TableRAG, B는 같은 경로에 요청별 근거 인용과 읽지 않은 후보 미리보기·최종 보존 검증을 추가했다. 양쪽을 새로 실행했다.",
        "모델·색인·하이브리드5:5·후보20·최종8쪽·8,000토큰·Luna medium·최대5작업+종료를 고정했다. 답변 생성은 없다.",
        "",
        "## 문항 전체 근거 충족",
        "",
        "지정 근거 실패뿐 아니라 지정 통과도 포함해 양쪽116개 실제 반환 문맥을 검수했다. 질문의 필수 요구를 모두 지원해야 충분으로 인정한다. 같은 대상·범위의 대체 출처는 인정하고, 참고답의 질문 밖 부가사항을 필수로 확대하지 않는다. 모호·미완료 판정은 성공으로 세지 않는다.",
        "",
        "| 기준 | A 기존 DB 우선 | B 요청별 확인 |",
        "|---|---:|---:|",
        f"| 실제 반환 문맥 전체 근거 충족 | {count('A', 'reviewed_complete')} | {count('B', 'reviewed_complete')} |",
        f"| 지정 근거 전체 적중 | {count('A', 'strict_complete')} | {count('B', 'strict_complete')} |",
        f"| 복합질문 실제 전체 근거 충족 | {count('A', 'reviewed_complete', True)} | {count('B', 'reviewed_complete', True)} |",
        f"| 복합질문 지정 근거 전체 적중 | {count('A', 'strict_complete', True)} | {count('B', 'strict_complete', True)} |",
        f"| 후보20 지정 근거 전체 확보 | {summary['A']['candidate']}/58 | {summary['B']['candidate']}/58 |",
        f"| 지정 페이지 평균 Recall@8 | {summary['A']['page_recall']:.1%} | {summary['B']['page_recall']:.1%} |",
        "",
        f"실제 근거 기준 개선 {summary['improved']}문항, 하락 {summary['regressed']}문항. 지정 페이지 Recall은 대체 근거 검수로 수정하지 않았다.",
        "AI 검수와 자체 대조이며 독립 사람 검수가 아니다. 검색에 참고답을 제공하지 않았고, 모든 검색 저장 후 별도 단계에서 읽었다.",
        "",
        "## 호출과 지연",
        "",
        "| 항목 | A | B |",
        "|---|---:|---:|",
    ]
    for title, key in [("p50(초)", "p50"), ("p95(초)", "p95")]:
        lines.append(
            f"| {title} | {timing['A']['end_to_end'][key]:.2f} | {timing['B']['end_to_end'][key]:.2f} |"
        )
    for title, key in [
        ("계획 API 호출", "planner_calls"),
        ("하이브리드·리랭킹", "hybrid_search_calls"),
        ("공통 DB 조회", "record_lookup_calls"),
        ("표 계산 SQL", "sql_calls"),
    ]:
        lines.append(f"| {title} | {timing['A'][key]} | {timing['B'][key]} |")
    lines.append(
        f"| 원문 읽기 | {timing['A']['tools'].get('read_sources', 0)} | {timing['B']['tools'].get('read_sources', 0)} |"
    )
    lines += [
        "",
        "동시4요청의 실제 처리 시간을 측정했다. 모델·색인 로딩과 사후 검수 지연은 제외했다. API 변동과 공유 CPU/GPU 경합이 포함된 단일 실행 비교다.",
        "",
        "## 해석과 선택",
        "",
        "실제 전체 근거 충족은 한 문항 순증했지만 지정 근거 전체 적중은 세 문항 줄었고 p50은 약3.9초 늘었다. 이 결과만으로 기존 경로를 전면 교체할 근거는 부족하다. 새 기능은 비교 옵션으로 유지한다.",
        "새 경로는 검색을 줄이고 기존 후보의 원문을 더 읽었다. 읽지 않은 본문 인용과 선택 누락을 거절한 종료 요청이29회였고, 두 문항은 마지막 인용 검증에서 미충족으로 종료했으나 실제 문맥 검수에서는 충분했다. 계획자의 종료 상태와 실제 반환 근거 점수는 구분했다.",
        "",
        "| 실제 근거 변화 | 문항 | 확인한 흐름 |",
        "|---|---|---|",
        "| 개선 | AS-DEV06 | 기존에는 SI 정정 담당 정보만 남았다. 새 경로는 후보의 SI 최초 접수 원문을 읽고 최종 문맥에 보존했다. 지정1쪽 대신 최초 접수5쪽을 사용했다. |",
        "| 개선 | AS-ADD06 | 기존에도20GP VGM 적용 규정이 후속 후보20·11·12위에 있었지만 선택하지 않았다. 새 경로는 후속10위 원문을 읽고 최종 문맥에 남겼다. |",
        "| 하락 | AS-ADD09 | 기존은 추가 검색으로 봉인 변경의 운송사 통지 규정을3위로 찾았다. 새 경로는 기존 후보 읽기 후 종료했고, 그 규정은 어느 검색 후보에도 없었다. |",
        "",
        "AS-ADD09에서 새 요구표는 SI·반입팀과 VGM을 확인했지만 운송사 통지라는 추가 의무를 놓쳤다. 인용 존재 검사는 잘못된 인용을 잡아도 질문에 필요한 모든 업무 의무를 스스로 발견하는 보장은 아니다. 없는 근거를 기존 후보에서 계속 읽는 것으로 해결할 수 없다.",
        "AS-DEV09는 이번 새 A/B에서 둘 다 필요한 정정 메일 원문을 확보했다. 과거 실행 대비 회복을 이번 변경의 단독 효과로 세지 않았다.",
        "",
        "## 검수 교정",
        "",
        "116개 AI 검수 원본을 보존하고15건의 인용·범위 교정을 기록했다. 실제 판정 변경은8개 출력이다. SI 최초 접수 표의 나뉜 주소는 원문 행 전체로 확인했고, 취소·기존 포털 접수·무료일 질문에 질문 밖 요구를 추가한 판정은 바로잡았다. 날짜별 신규 경로의 근거가 없는 사례와 운송사 통지 누락은 실패로 남겼다. 연속 인용의 형식 오류와 의미상 근거 부족을 구분했다.",
        "[교정 사유](assistant-review-changes.json)에 양쪽 판정 변경을 기록했다. 원문 인용과 질문은 로컬 artifacts에만 보존한다.",
        "",
        "## 문항별 결과",
        "",
        "| 문항 | A 지정/검수 | B 지정/검수 | B 원문읽기 | 실제 근거 변화 |",
        "|---|---|---|---:|---|",
    ]
    for f in sorted(
        flows,
        key=lambda row: (not row["id"].startswith("official_development:"), row["id"]),
    ):
        a, b = f["arms"]["A"], f["arms"]["B"]

        def mark(x):
            return "충족" if x else "미충족"

        delta = (
            "유지"
            if a["reviewed_complete"] == b["reviewed_complete"]
            else "개선"
            if b["reviewed_complete"]
            else "하락"
        )
        lines.append(
            f"| {f['id'].split(':')[1]} | {mark(a['strict_complete'])}/{mark(a['reviewed_complete'])} | {mark(b['strict_complete'])}/{mark(b['reviewed_complete'])} | {b['tools'].count('read_sources')} | {delta} |"
        )
    lines += [
        "",
        "## 재현과 한계",
        "",
        "실행: python -m experimental.u_10o0o.table_rag_unified query 질문 --db-first --verify-evidence --output artifacts/u_10o0o/새-결과.json",
        "실험은 run_benchmark.py freeze/run, analyze.py score, review.py, analyze.py drafts/report, render_report.py 순서다. 기존 고정 경로는 덮어쓰지 않는다. drafts와 report 사이에는 실제 반환 문맥·인용·질문 범위의 자체 검수가 필요하다.",
        "검증기는 질문 구절·관측 인용·선택 출처·최종 문맥의 존재를 확인한다. 모델이 질문의 요구를 빠뜨리거나 근거를 잘못 해석할 가능성은 남는다. 이번58문항은 반복 사용한 개발·회귀 세트이며 카테고리220과 미사용 검증은 이번에 실행하지 않았다.",
        "기본 실행이나 운영 API의 자동 교체는 하지 않았다. [문항별 단계](routing-per-question.json), [검수 결과](reviewed-per-question.json), [과정](work-log.md), [구현 검증](implementation-review.json)에 근거를 보존한다.",
    ]
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
