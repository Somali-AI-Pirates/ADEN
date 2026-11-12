"""Render verified metrics after retrieval and adjudication."""

from pathlib import Path
from experimental.u_10o0o.pattern_retrieval.common import read_json

HERE = Path(__file__).parent


def main():
    summary = read_json(HERE / "reviewed-summary.json")
    timing = read_json(HERE / "timing.json")
    controls = read_json(HERE / "controls.json")
    review = read_json(HERE / "review-manifest.json")
    negative = read_json(HERE / "negative-control.json")
    assert review["all_required_checked"]
    lines = [
        "# 공통 기록 조회를 통합한 TableRAG 비교",
        "",
        "A는 현재 선택된 KURE-v3 B 구성(HG 보강·추가 확인 지침·출처 보존), B는 HG 전용 보강을 제거하고 공통 HG·HA·IS 기록 조회와 분리된 도구를 적용한 구성이다. 두 조건을 같은 문항으로 새로 실행했다. 기존 기본 실행과 운영 API는 변경하지 않았다.",
        "",
        "공식58·카테고리220, KURE-v3 CPU BF16·KURE nano GPU FP32, 5:5 RRF(k=60), 검색마다 후보20개, 최종 최대8쪽·8,000토큰, GPT-6 Luna medium, 최대5회 작업 후 finish, 동시4요청이다. 답변 생성은 제외했다. 여러 변경을 함께 포함하므로 특정 도구 하나의 인과 효과라고 해석할 수 없다.",
        "",
        "## 결과와 판단",
        "",
        "통합 구현과 양쪽 278문항(총556건)의 검색·사후 검수를 완료했다. 공식은 55/58로 같고, 카테고리 근거형은 190/200에서 193/200으로 늘었다. 증가분은 일상 표현 복합 질문 11/20 → 14/20에서 나왔다. 전체 검색 p50은 8.72초 → 9.48초로 늘었고 p95는 23.81초 → 21.93초로 줄었다. 정확도와 속도가 모두 개선됐다는 결론은 아니다.",
        "",
        "HG 전용 런타임 보강을 공통 기록 조회로 대체해도 이번 사건형 140문항의 근거 충족은 유지했다. 다만 공식 지정 페이지 Recall은 88.9% → 83.5%로 내려갔다. 대체 근거를 인정한 점수와 지정 페이지 회수 성능을 구분해야 한다. 통합 구조는 실행 가능한 후보지만 이 한 번의 개발·회귀 비교만으로 기존 기준선보다 전반적으로 우수하다고 확정하지 않는다. 기본 검색 구성은 교체하지 않았다.",
        "",
        "## 근거 충족",
        "",
        "지정 근거를 놓친 모든 문항은 양쪽 최종 원문·유지 SQL을 같은 기준으로 검수했다. 충분한 대체 근거는 인정했고, 다른 사건·시점·역할의 유사 문장은 인정하지 않았다. 이전 모호 문항도 다시 검수했다. AI 검수 후 자체 대조이며 독립 사람 검수는 아니다. 모호한 문항도 분모에 남겼다. 아래 점수는 답변 정확도가 아니라 검색 근거 충족률이다.",
        "",
        "| 묶음 | n | A 지정 | B 지정 | A 검수 반영 | B 검수 반영 | 개선 / 하락 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "official_all": "공식",
        "categories_positive": "카테고리 근거형",
        "work_rules_all": "업무 규정",
        "semantic_single": "일상 표현 단일",
        "semantic_multi": "일상 표현 복합",
    }
    for name in [
        "official_all",
        "categories_positive",
        *[f"S{i:02d}" for i in range(1, 8)],
        "work_rules_all",
        "semantic_single",
        "semantic_multi",
    ]:
        s = summary[name]
        n = s["n"]

        def fmt(a, k):
            v = s[a][k]
            return f"{v}/{n} ({v / n:.1%})"

        lines.append(
            f"| {labels.get(name, name)} | {n} | {fmt('A', 'strict')} | {fmt('B', 'strict')} | {fmt('A', 'reviewed')} | {fmt('B', 'reviewed')} | {s['improved']} / {s['regressed']} |"
        )
    lines += [
        "",
        "카테고리220 중 검색 정답이 없는 범위 밖20문항은 근거형200문항과 분리했다. 공식은 기존 시험에서 고정한58문항을 사용했으며 이번에 점수를 보고 제외한 문항은 없다.",
        "",
        "검수는 어느 한쪽이라도 지정 근거를 충족하지 못한 42문항의 양쪽 출력, 총84건에 대칭으로 적용했다. 양쪽 모두 지정 근거를 충족한 나머지 문항은 별도 의미 검수하지 않았다. 검수 모델의 인용 4건을 실제 반환 문구로 바로잡고, 질문에 없는 추가 요구를 필수로 본 판정 등을 포함해 총16건을 자체 대조·수정했다. AS-SEM-32는 적용 범위가 모호해 양쪽 모두 성공으로 올리지 않고 분모에 남겼다. 원본 인용·해시·검수 전후 판정은 보존했다.",
        "",
        "### 지정 페이지 기준 단계별 비교",
        "",
        "| 묶음 | A 전체 조회 확보 | B 전체 조회 확보 | A 최종 후보20 | B 최종 후보20 | A 평균 페이지 Recall@8 | B 평균 페이지 Recall@8 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in (
        "official_all",
        "categories_positive",
        "semantic_single",
        "semantic_multi",
    ):
        s = summary[name]
        a, b = s["A"], s["B"]
        lines.append(
            f"| {labels[name]} | {a['all_query_candidate_complete']}/{s['n']} | {b['all_query_candidate_complete']}/{s['n']} | {a['candidate']}/{s['n']} | {b['candidate']}/{s['n']} | {a['page_recall']:.1%} | {b['page_recall']:.1%} |"
        )
    lines += [
        "",
        "전체 조회는 여러 검색·기록·표 발견 결과의 합집합이다. 후보20쪽은 최종 문맥 조립 전에 유지한 목록이다. 페이지 Recall은 정확한 book_id+page 지정 근거 기준이며 대체 근거 검수로 수정하지 않았다.",
        "",
        "## 개선·하락 문항",
        "",
        "검수 반영 후 전체 개선4건·하락1건이다. 공식 AS-DEV09와 일상 표현 AS-SEM-37은 초기 후보에 있던 근거를 통합 조건이 최종 문맥에 보존했다. AS-SEM-22와 AS-SEM-31은 후속 문서 검색에서 부족한 절차 근거를 새로 찾았다. AS-DEV02는 반대로 최초 예약 접수 근거가 초기 후보에 있었지만 변경 요청 연락처를 선택하며 하락했다.",
        "",
        "다섯 문항 모두 기록 조회·표 SQL 호출은 없었다. 개선을 DB 코드 연결의 직접 효과라고 설명할 수 없다. 문서 검색·원본 읽기·출처 선택이 함께 바뀐 결과다. [문항별 초기 순위·후속 검색·최종 출처 비교](reviewed-difference-flows.md)에 상세 흐름을 남겼다.",
        "",
        "## 지연과 호출",
        "",
        "| 항목 | A 현재 | B 통합 |",
        "|---|---:|---:|",
    ]
    for name, key in [("전체 검색 p50(초)", "p50"), ("전체 검색 p95(초)", "p95")]:
        lines.append(
            f"| {name} | {timing['A']['end_to_end'][key]:.2f} | {timing['B']['end_to_end'][key]:.2f} |"
        )
    for name, key in [
        ("계획 API 호출", "planner_calls"),
        ("하이브리드·리랭킹 호출", "hybrid_search_calls"),
        ("새 공통 기록 조회(자동 포함)", "record_lookup_calls"),
        ("전체 표 목록 검색", "table_discovery_calls"),
        ("표 계산·필터 SQL 호출", "sql_calls"),
        ("표 계산·필터 SQL 성공", "sql_ok"),
    ]:
        lines.append(f"| {name} | {timing['A'][key]} | {timing['B'][key]} |")
    lines += [
        "",
        "통합의 공통 기록 조회165회는 코드에서 자동으로 실행한109회와 Luna가 선택한56회다. 기존의 0은 HG 조회가 없다는 뜻이 아니라 새 공통 인터페이스를 사용하지 않았다는 뜻이다. 문서 검색은467회 → 362회, 계획 API는475회 → 444회로 줄었다. 반면 입력 토큰은 5,402,410 → 5,443,283으로 약간 늘었고 출력은136,785 → 103,989로 줄었다. 호출 수만으로 총비용·지연이 줄었다고 단정하지 않는다.",
        "",
        "통합 조건은 표 계산·필터 SQL 도구를 등록했지만 이 표본에서 선택하지 않았다. 165회의 공통 기록 조회 자체는 파라미터화된 SQLite SQL을 실행한다. 따라서 ‘DB 사용0회’가 아니라 ‘모델이 임의 표 계산 SQL을 작성한 경우0회’다. 해당 도구는 집중 테스트로 실행·권한·중복 검색 방지를 검증했으나, 이번 전체 평가에서의 모델 선택 효과는 미관측이다.",
        "",
        "통합 조건에서 API 오류1건(AS-S01-05)이 있었다. 오류를 제외하지 않았으며 저장된 최종 원문은 해당 질문의 근거를 충족했다. 이는 실제 답변 제공 성공을 뜻하지 않는다. S06의 추가 확인 종료는 기존10/20, 통합20/20이었다. 근거 충족과 모델의 종료 상태는 별도 지표다.",
        "",
        "모델·색인 로딩과 사후 검수 시간은 제외했다. 동시4요청이 공유하는 CPU/GPU 및 API 대기를 포함한 문항별 지연이며, 단일 사용자 독립 지연이나 순수 SQL 시간과 다르다. 오류·시간 초과 문항도 전체 집계에 포함했다.",
        "",
        f"동일한 초기 하이브리드 후보 단위: {controls['initial_raw_candidates_equal']}/{controls['n']}. 통합 조건의 모든 문서 검색에서 HG 전용 보강 비활성화 확인: {controls['unified_document_searches_native_hg_disabled']}.",
        "",
        "범위 밖20문항 종료 상태: "
        + str({a: negative[a]["finish"] for a in ("A", "B")}),
        "",
        "v3는399건 저장 후 실행 환경 중단을 겪었고, 사용자 재개 요청 뒤 같은94개 실행 파일·입력·색인 지문을 확인해 완료했다. 양쪽 실행을 문항별 교대했지만 재개 전후 API 부하가 같다고 보장할 수 없어 지연은 두 실행 구간의 관측치다. 중단된4개 시도의 시간과 확인된 API 응답2개는 정상 완료 지연·호출 집계에 넣지 않고 따로 보존했다. 개발 중 중단한 v1/v2와 사후 검수84회도 위 호출·토큰 표에 포함하지 않는다.",
        "",
        "## 구현과 검증",
        "",
        "공통 SQLite에는 HG2,730건·HA198건·IS 식별 가능한 표 기록22,523건을 넣었다. 원본 연결36,767개와 표 메타데이터21,126개를 제공한다. HG·HA 역할별 원본 연결14,244개의 기존 저장소 일치, 외래 키·무결성·원문/표 지문을 검사했다. 코드·공유 규격·날짜 종류·대상 충돌·제외·읽기 전용 SQL·동시 실행 분리를 집중 검사했다.",
        "",
        "공통 Ruff와 개인 Ruff, 공통·기존 TableRAG·통합 테스트374개 및 하위 검사15개가 통과했다. 새 CLI의 도움말·실제 색인 빌드·조회·전체 실행을 확인했다. 사용자가 이 대화에서 독립 에이전트를 금지했으므로 코드 리뷰와 최종 검수는 자체 수행했으며 독립 리뷰로 표현하지 않는다. 답변 생성 품질·운영 API 연동·별도 미사용 세트의 일반화는 이번 검증 범위에 없다.",
        "",
        "## 기록과 재현",
        "",
        "- [구현 흐름·CLI·지원 범위](../../table_rag_unified/README.md)",
        "- [과정 및 검증](work-log.md)",
        "- [유형별 집계](reviewed-summary.json) · [문항별 지정/검수 점수](reviewed-per-question.json)",
        "- [문항별 개선·하락](reviewed-differences.json) · [검수 후 차이의 검색 흐름](reviewed-difference-flows.md) · [지정 점수 차이의 검색 흐름](difference-flows.json)",
        "- [호출·지연](timing.json) · [공통 조건 검증](controls.json)",
        "- [색인 검증](index-validation.json) · [검수 지문](review-manifest.json)",
        "",
        "로컬 artifacts/u_10o0o/advanced-table-rag-unified-20261112/v3에 질문·실행 코드·프롬프트·프로토콜·원본 문맥·도구 기록·검수 인용을 보관한다. 공유 기록에는 원문과 참고답을 복사하지 않는다. v1은 페이지 식별 필드 차이, v2는 규격의 표준 코드·한국어 표현 정규화 오류로 중단된 개발 실행이며 최종 집계에서 제외했다.",
        "",
        "재현 명령은 run_benchmark.py의 run, analyze.py의 prepare → 별도 검수와 자체 판정 → report, reviewed_flows.py, render_report.py 순이다. 정확한 실행 명령은 과정 기록에 있다. 완료된 run은 지문이 같은 저장 결과를 재사용한다. 처음부터 다시 유료 호출하려면 실행 경로를 새로 고정해야 한다. 미완료 유료 요청은 자동 재시도하지 않는다. 보고서 작성 후 검색 규칙·프롬프트를 점수에 맞춰 수정하지 않았다.",
        "",
    ]
    (HERE / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
