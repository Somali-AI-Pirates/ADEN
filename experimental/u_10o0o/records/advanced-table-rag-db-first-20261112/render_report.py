"""Render measured DB-first comparison after complete symmetric adjudication."""

from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import read_json

HERE = Path(__file__).parent
LABELS = {
    "official_all": "공식",
    "categories_positive": "카테고리 근거형",
    "work_rules_all": "업무 규정",
    "semantic_single": "일상 표현 단일",
    "semantic_multi": "일상 표현 복합",
}


def main():
    summary = read_json(HERE / "reviewed-summary.json")
    timing = read_json(HERE / "timing.json")
    routing = read_json(HERE / "routing-summary.json")
    decisions = read_json(HERE / "reviewed-differences.json")
    per_question = {r["id"]: r for r in read_json(HERE / "routing-per-question.json")}
    audit = read_json(HERE / "review-manifest.json")
    controls = read_json(HERE / "controls.json")
    assert audit["all_required_checked"] and controls["equal_prompt_and_tools"]
    lines = [
        "# TableRAG: 검색 우선과 DB 우선 비교",
        "",
        "A는 직전 통합 TableRAG의 검색 우선, B는 완전 코드로 유일한 대상과 원본을 찾았을 때 DB부터 제공하는 구성이다. 양쪽 모두 새로 실행했다. 변경은 초기 하이브리드 검색 여부와 처음 제공되는 근거이며, Luna 프롬프트·도구·모델·색인·최종 선택기는 같다.",
        "",
        "공식58·카테고리220, 후보20·최종8쪽·8,000토큰, KURE-v3 CPU BF16·KURE nano GPU FP32, GPT-6 Luna medium, 최대5회 작업 후 finish, 동시4요청이다. 답변 생성은 제외한다. 초기 DB에서 시작해도 모델·색인은 미리 준비하고 Luna는 호출한다.",
        "",
        "## 판단",
        "",
        "명시 코드로 DB에서 시작한109문항은 두 조건 모두 지정 근거109/109를 유지했다. 108문항에서 하이브리드·리랭킹을 완전히 생략했고, 나머지1문항은 후속 문서 검색을 실행했다. 해당109문항의 p50은4.77→4.08초, p95는10.07→8.71초였다. 이 범위에서는 근거 손실 없이 불필요한 검색을 줄였다는 근거가 있다.",
        "",
        "전체 p50은5.52→5.47초로 거의 같고 p95는15.57→16.58초로 늘었다. 전체 점수의 순증4문항은 모두 처리 방식이 같은169문항에서 나왔으므로 DB 우선의 정확도 개선으로 해석하지 않는다. 같은 시작 후보169/169를 확인했으며 이후 모델의 도구·출처 선택은 달랐다. 이번 결론은 명시 코드 문항의 검색 비용 절감이고, 전체 질문의 일괄 속도·정확도 개선은 아니다.",
        "",
        "## 실제 검색 생략",
        "",
        f"B의 {routing['unique_code_db_start']['n']}/278문항이 DB에서 시작했다. 그중 {timing['B']['zero_hybrid_questions']}문항은 전체 실행에서 하이브리드·리랭킹0회였고, {timing['B']['db_then_hybrid_questions']}문항은 후속 문서 검색을 실행했다. 코드가 없거나 불명확한 나머지 {routing['same_search_start']['n']}문항은 기존 검색으로 시작했다.",
        "",
        "| 항목 | A 검색 우선 | B DB 우선 |",
        "|---|---:|---:|",
    ]
    for title, key in [("전체 검색 p50(초)", "p50"), ("전체 검색 p95(초)", "p95")]:
        lines.append(
            f"| {title} | {timing['A']['end_to_end'][key]:.2f} | {timing['B']['end_to_end'][key]:.2f} |"
        )
    for title, key in [
        ("계획 API 호출", "planner_calls"),
        ("하이브리드·리랭킹 호출", "hybrid_search_calls"),
        ("공통 기록 조회(자동 포함)", "record_lookup_calls"),
        ("표 SQL 호출", "sql_calls"),
    ]:
        lines.append(f"| {title} | {timing['A'][key]} | {timing['B'][key]} |")
    lines += [
        "",
        "모델·색인 로딩과 사후 검수는 지연에서 제외했다. 동시4요청이 공유하는 CPU/GPU와 API 대기를 포함한 문항별 지연이다. 실패·보류도 제외하지 않았다. 입력 토큰은 A {0:,} / B {1:,}, 출력은 A {2:,} / B {3:,}이다.".format(
            timing["A"]["usage"]["input_tokens"],
            timing["B"]["usage"]["input_tokens"],
            timing["A"]["usage"]["output_tokens"],
            timing["B"]["usage"]["output_tokens"],
        ),
        "",
        "## 실제 반환 근거 충족",
        "",
        "지정 근거를 놓친 모든 문항의 양쪽 최종 원문·유지 SQL을 대칭 검수했다. 같은 대상·역할·적용 범위의 충분한 대체 근거는 인정한다. 질문 밖 부가 설명을 필수로 확대하지 않으며 실제 누락은 실패로 남긴다. 모호한 문항은 분모에 남기고 성공으로 세지 않는다. 검색 근거 점수이며 답변 정확도가 아니다.",
        "",
        "| 묶음 | n | A 지정 | B 지정 | A 검수 반영 | B 검수 반영 | 개선/하락 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
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
        values = [
            f"{s[a][k]}/{n} ({s[a][k] / n:.1%})"
            for k in ["strict", "reviewed"]
            for a in ["A", "B"]
        ]
        lines.append(
            f"| {LABELS.get(name, name)} | {n} | "
            + " | ".join(values)
            + f" | {s['improved']}/{s['regressed']} |"
        )
    lines += [
        "",
        f"범위 밖20문항은 근거형200문항에서 분리했다. 총{audit['reviewed_outputs']}개 출력을 AI 검수 후 자체 대조했다. 질문·참고자료 지문과 출처별 원문 해시가 모두 같은33개 출력은 이전 자체 판정을 재사용했고, 나머지57개는 새로 대조했다. 인용·질문 범위 등12건의 수정과 검수 전후 판정을 보존했다. 양쪽 모두 지정 근거를 충족한 나머지 출력은 별도 의미 검수하지 않았다. 독립 사람 검수는 아니다.",
        "",
        "### DB 시작 가능 여부별 비교",
        "",
        "같은 검색으로 시작하는 문항에도 모델 출력의 실행 편차가 생길 수 있다. 이 묶음의 차이를 DB 우선 처리의 직접 효과로 해석하지 않는다.",
        "",
        "| 묶음 | 근거형 n | A 검수 | B 검수 | A p50/p95 | B p50/p95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in [
        ("unique_code_db_start", "DB 시작 가능"),
        ("same_search_start", "기존 검색 시작"),
    ]:
        s = routing[key]
        a, b = s["A"], s["B"]
        ta, tb = a["timing"]["end_to_end"], b["timing"]["end_to_end"]
        lines.append(
            f"| {label} | {a['positive_n']} | {a['reviewed']} | {b['reviewed']} | {ta['p50']:.2f}/{ta['p95']:.2f}초 | {tb['p50']:.2f}/{tb['p95']:.2f}초 |"
        )
    lines += [
        "",
        "위 묶음의 지연에는 범위 밖20문항도 포함되므로 기존 검색 시작의 지연 n은169, 근거형 점수 n은149다. DB 시작109문항의 하이브리드 호출은110→2회, 계획 API는130→136회, 입력 토큰은1,781,304→1,036,913이었다. 검색량은 크게 줄었지만 LLM 확인 단계는 남는다.",
        "",
        "### 지정 페이지 기준",
        "",
        "| 묶음 | A 후보20 전체 근거 | B 후보20 전체 근거 | A 평균 페이지 Recall@8 | B 평균 페이지 Recall@8 |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ["official_all", "categories_positive", "semantic_multi"]:
        s = summary[key]
        a, b = s["A"], s["B"]
        lines.append(
            f"| {LABELS[key]} | {a['candidate']}/{s['n']} | {b['candidate']}/{s['n']} | {a['page_recall']:.1%} | {b['page_recall']:.1%} |"
        )
    lines += [
        "",
        "페이지 Recall은 기존 book_id+page 기준이며 대체 근거 검수로 수정하지 않았다.",
        "",
        "## 문항별 개선·하락",
        "",
        "| 문항 | DB 시작 | A→B | A/B 검색 수 | 미충족 쪽에서 빠진 근거 |",
        "|---|---|---|---:|---|",
    ]
    for d in decisions:
        p = per_question[d["id"]]
        a, b = p["arms"]["A"], p["arms"]["B"]
        reason = d["reason_current"] if d["unified"] else d["reason_unified"]
        lines.append(
            f"| {d['id']} | {'예' if p['db_start_eligible'] else '아니오'} | {'충족' if d['current'] else '미충족'}→{'충족' if d['unified'] else '미충족'} | {a['hybrid_calls']}/{b['hybrid_calls']} | {reason.replace('|', '/')} |"
        )
    lines += [
        "",
        "하락한 AS-S06-20은 완전 코드가 없는 추가 확인형이다. 양쪽 모두 같은 초기 검색으로 시작했다. A는 화주·규격·실제 반출일만으로 조회해 두 후보의 사건·청구 원본을 확보했다. B는 앞선 검색에서 본 코드2개까지 AND 조건에 넣어 후보를 한 사건으로 좁혔다. 다른 후보의 청구서는 남았지만 실제 반출일의 원본은 후보 조회에서 빠졌다. DB 우선 생략 경로의 손실이 아니라 기존 후속 대상 식별의 실패로 기록한다.",
        "",
        "DB 시작 후 검색으로 전환한 AS-S05-10은 기록 조회 → 추가 기록 조회 → 문서 검색2회 → 원본 읽기 → 종료 순서였다. 최종 종료 상태는 insufficient였지만 지정 근거는 확보했다. 검색 근거 충족과 계획자의 충분 판정이 같은 지표가 아님을 보여준다.",
    ]
    zero = routing["zero_hybrid_in_B"]
    lines += [
        "",
        f"B에서 검색을 완전히 생략한 {zero['n']}문항 중 근거형은 {zero['B']['positive_n']}개다. 같은 문항의 검수 반영 충족은 A {zero['A']['reviewed']}개, B {zero['B']['reviewed']}개다. B가 sufficient로 종료했지만 실제 근거가 불충분한 문항은 {zero['B']['sufficient_but_incomplete']}개다.",
    ]
    negative = read_json(HERE / "negative-control.json")
    lines += [
        "",
        "## 오류·범위 밖과 해석 한계",
        "",
        f"범위 밖20문항의 종료 상태는 A {negative['A']['finish']}, B {negative['B']['finish']}다. 전체 API 상태는 A {timing['A']['api_status']}, B {timing['B']['api_status']}다.",
        "",
        "검색0회는 코드에 연결된 원본을 DB로 읽었다는 뜻이며 LLM0회·원본0쪽이라는 뜻이 아니다. 충분 상태로 종료했는지와 실제 근거 충족을 별도로 확인한다. 저장된 후보·문맥·도구 단계는 routing-per-question.json에 있고 원문 인용은 로컬 artifacts에 보존한다.",
        "",
        "현재 반복 사용한 세트의 단일 실행 비교다. 초기 관측 차이에 따른 계획 변화도 포함하며, 별도 미사용 질문·실제 답변 생성·콜드 스타트의 일반화는 검증하지 않았다. 결과 확인 뒤 같은 실행의 검색 규칙이나 프롬프트를 조정하지 않았다.",
        "",
        "## 기록",
        "",
        "- [구현과 실행 방법](../../table_rag_unified/README.md) · [과정·검증](work-log.md)",
        "- [유형별 점수](reviewed-summary.json) · [라우팅별 비교](routing-summary.json)",
        "- [문항별 호출·출처·점수](routing-per-question.json) · [판정 변경](reviewed-differences.json)",
        "- [호출·지연](timing.json) · [고정 조건](protocol.json) · [검수 완전성](review-manifest.json)",
        "",
    ]
    (HERE / "report.md").write_text("\n".join(lines).replace("범위 밖", "범위 밖"))


if __name__ == "__main__":
    main()
