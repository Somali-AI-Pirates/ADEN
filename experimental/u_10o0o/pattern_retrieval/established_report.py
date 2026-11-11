"""Render the established-set A/current-baseline comparison from saved scores."""

import argparse
from collections import Counter
from pathlib import Path

from .common import ROOT, read_json

LABELS = {
    "official_all": "공식 전체",
    "official_development": "공식 Development",
    "official_additional": "공식 Additional",
    "official_complex_groups": "공식 복합 (근거 그룹 2개 이상)",
    "official_multi_page": "공식 다중 페이지 (정답 쪽 2개 이상)",
    "categories_positive": "카테고리 유효 전체",
    "S01": "1. 번호로 단일 기록 조회",
    "S02": "2. 번호 없는 단일 사건",
    "S03": "3. 한 사건의 여러 기록",
    "S04": "4. 두 사건 비교",
    "S05": "5. 날짜·승인·금액 구분",
    "S06": "6. 모호한 두 후보의 근거",
    "S07": "7. 확인 자료가 부족한 사건",
    "work_rules_all": "업무 규정 전체",
    "work_rules/procedure": "업무 절차",
    "work_rules/applicability": "적용 조건·예외",
    "semantic_all": "일상 표현 전체",
    "semantic_queries/procedure_paraphrase": "일상 표현: 절차",
    "semantic_queries/rule_paraphrase": "일상 표현: 규정",
    "semantic_queries/cross_document_workflow": "일상 표현: 여러 문서의 업무 흐름",
    "semantic_queries/cross_document_rule": "일상 표현: 여러 문서의 규정",
}


def pct(value):
    return f"{value:.1%}"


def completion(row, stage="final"):
    value = row["stages"][stage]
    return f"{value['complete']}/{row['scored']} ({pct(value['complete_rate'])})"


def make_report(directory):
    directory = Path(directory)
    summary = read_json(directory / "summary.json")
    diff = read_json(directory / "differences.json")
    manifest = read_json(directory / "manifest.json")
    a, baseline = summary["A"], summary["baseline"]
    lines = [
        "# 새 A와 기존 Advanced 기준선: 공식 60·카테고리 220 비교",
        "",
        "2026-11-11. 기존 질문 280개를 두 구성에서 각각 새로 검색했다. 답변 생성과 외부 API 호출은 하지 않았다. 기본 검색 구성은 유지한다.",
        "",
        f"이번 기존 세트에서는 현재 기준선이 앞섰다. 공식 근거 그룹 전체 충족은 기존 {completion(baseline['official_all'])}, A {completion(a['official_all'])}이고, 카테고리 필수 근거 전체 충족은 기존 {completion(baseline['categories_positive'])}, A {completion(a['categories_positive'])}다. A로 기본 구성을 교체할 근거는 없다.",
        "",
        f"일상 표현 40문항은 두 구성 모두 {a['semantic_all']['stages']['final']['complete']}/40으로 같았다. 새로운 단순 페이지 검색만으로 표현 차이와 여러 문서의 근거 확보 문제가 해결되지는 않았다.",
        "",
        "## 비교한 구성",
        "",
        "| 항목 | 기존 기준선 | 새 A |",
        "|---|---|---|",
        "| 검색 입력 | 복원 원본 페이지 | 원본 PDF를 Poppler layout으로 새 추출한 페이지 |",
        "| BM25 | bigram + 완전 ID, 제목 보너스 | bigram + 완전 코드, 제목 보너스 없음 |",
        "| 후보 | BM25·벡터 각각 20 → 5:5 RRF(60) → 20 | 각각 50 → 5:5 RRF(60) → 20 |",
        "| 리랭커 | KURE nano, 최대 512토큰·FP16 | 같은 모델·리비전, 최대 2,048토큰·FP32 |",
        "| 최종 순서 | KURE 상위 3 + RRF 나머지 | KURE 전체 순위 |",
        "| 원문 추가 조회 | HG·간접 대상·모호 후보·두 사건 조회 | 없음 |",
        "| 최종 문맥 | 최대 8쪽·8,000 o200k 토큰 | 최대 8쪽·8,000 cl100k 토큰 |",
        "",
        "임베딩은 두 구성 모두 고정 리비전의 Snowflake-ko다. 기존은 원래 CPU 질문 임베딩·GPU FP16 리랭커를, A는 GPU FP32를 유지했다. 파서·입력 길이·검색 폭·순위 정책·조회·토큰 계산기가 함께 다르므로 특정 한 요소의 효과로 해석할 수 없다. 명목 토큰 상한은 같지만 실제 허용 분량은 동일하지 않다.",
        "",
        "## 채점 기준",
        "",
        "- 공식 60개 중 참고 근거가 있는 58개를 채점했다. 페이지 Recall은 `book_id+page`가 모두 일치해야 한다. Recall·MRR·nDCG는 문항별 점수의 평균이다. 공식 문서 Recall도 함께 기록한다.",
        "- 공식의 ‘전체 근거 그룹 충족’은 각 그룹에서 최소 한 쪽을 찾고 모든 그룹을 충족하는 기준이다. 한 그룹에 열거된 모든 쪽을 요구하는 더 엄격한 지표는 별도로 남긴다.",
        "- 카테고리 220개 중 범위 밖 20개를 분리하고 200개를 채점했다. 기존 `fact_coverage`로 필수 사실마다 주 근거 묶음 또는 허용 대체 묶음의 모든 인용이 실제 문맥 안에 있는지 확인한다.",
        "- 후보는 순수 하이브리드 최초 20개를 뜻한다. 기존의 HG·원문 조회는 그 이후에도 쪽을 추가할 수 있다. 조회 후 선택 가능한 근거도 별도로 기록했다.",
        "- 실제 저장한 최종 문맥을 채점했다. 채점 때 순위를 바꾸거나 문맥을 다시 포장하지 않았다. 두 구성 560개 검색 결과를 모두 저장한 후 참고 근거를 읽었다.",
        "",
        "## 공식 평가",
        "",
        "| 묶음 | 기존 초기 후보 충족 | A 초기 후보 충족 | 기존 최종 그룹 충족 | A 최종 그룹 충족 | 기존 페이지 Recall@8 | A 페이지 Recall@8 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in (
        "official_all",
        "official_development",
        "official_additional",
        "official_complex_groups",
        "official_multi_page",
    ):
        if group not in a:
            continue
        b, n = baseline[group], a[group]
        lines.append(
            f"| {LABELS[group]} | {completion(b, 'initial')} | {completion(n, 'initial')} | {completion(b)} | {completion(n)} | {pct(b['stages']['final']['page_recall'])} | {pct(n['stages']['final']['page_recall'])} |"
        )
    lines += ["", "| 공식 전체 58문항 보조 지표 | 기존 | A |", "|---|---:|---:|"]
    for label, stage, key in (
        ("페이지 Recall@20 (초기 후보)", "initial", "page_recall"),
        ("문서 Recall@8", "final", "document_recall"),
        ("MRR@8 (정확 페이지)", "final", "mrr"),
        ("nDCG@8 (정확 페이지)", "final", "ndcg"),
    ):
        lines.append(
            f"| {label} | {pct(baseline['official_all']['stages'][stage][key])} | {pct(a['official_all']['stages'][stage][key])} |"
        )
    lines.append(
        f"| 열거된 모든 정답 쪽 충족 | {baseline['official_all']['stages']['final']['strict_all_pages']}/58 | {a['official_all']['stages']['final']['strict_all_pages']}/58 |"
    )
    lines += [
        "",
        "## 카테고리별 필수 근거 전체 충족",
        "",
        "| 유형 | 기존 초기 후보 | A 초기 후보 | 기존 최종 문맥 | A 최종 문맥 | 변화 (A−기존) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for group in (
        "categories_positive",
        *[f"S{i:02}" for i in range(1, 8)],
        "work_rules_all",
        "work_rules/procedure",
        "work_rules/applicability",
        "semantic_all",
        "semantic_queries/procedure_paraphrase",
        "semantic_queries/rule_paraphrase",
        "semantic_queries/cross_document_workflow",
        "semantic_queries/cross_document_rule",
    ):
        b, n = baseline[group], a[group]
        delta = n["stages"]["final"]["complete"] - b["stages"]["final"]["complete"]
        lines.append(
            f"| {LABELS[group]} | {completion(b, 'initial')} | {completion(n, 'initial')} | {completion(b)} | {completion(n)} | {delta:+}문항 |"
        )
    lines += [
        "",
        "6·7번은 추가 확인을 실제로 했는지 채점한 것이 아니다. 그 판단에 필요한 기록을 검색했는지만 본다. 범위 밖 20문항에도 두 검색기가 문서를 반환하며, 답변을 생성하지 않았으므로 거절·환각 방지 성능은 평가하지 않았다.",
        "",
        "## 문항별 변화와 누락 단계",
        "",
        "| 묶음 | 완전 충족으로 개선 | 완전 충족에서 하락 | 변화 없음 |",
        "|---|---:|---:|---:|",
    ]
    for label, rows in (
        (
            "공식 유효 58",
            [r for r in diff if r["suite"].startswith("official_") and r["scored"]],
        ),
        (
            "카테고리 유효 200",
            [r for r in diff if not r["suite"].startswith("official_") and r["scored"]],
        ),
    ):
        counts = Counter(r["status"] for r in rows)
        lines.append(
            f"| {label} | {counts['improved']} | {counts['regressed']} | {counts['unchanged']} |"
        )
    lines += [
        "",
        "최초 후보 누락과 최종 선택 누락을 구분한 전 문항 기록은 [per-question.json](per-question.json), 개선·하락 목록은 [문항별 변화 표](differences.md)와 [differences.json](differences.json)에 있다. `initial`은 하이브리드 후보, `after_hg`는 HG 조회 직후, `available`은 모든 원문 추가 조회와 선택 규칙 적용 후의 작업 목록, `final`은 실제 문맥이다. 기존 기준선은 조회로 초기 후보 밖의 근거를 회복할 수 있다.",
        "",
        "누락 단계의 구체적인 집계와 해석은 [진단 기록](diagnosis.md)에 있다.",
        "",
        "## 데이터와 해석상의 한계",
        "",
        "- 독립 데이터 점검에서 200개 카테고리 문항의 필수 인용은 양쪽 원문에 모두 존재했다. 공식 58개의 정답 페이지도 모두 존재했다. 공식 2문항은 인용 문자열 차이가 있으나 페이지 채점에는 영향이 없다. [데이터 점검](dataset-audit.json)",
        "- 기존 기준선은 이 데이터셋을 보며 발전시킨 구성이고, 새 A는 단순 대조군이다. 이번 수치는 기존 세트에서의 비교·회귀 결과이며 미사용 데이터에 대한 일반화 성능을 입증하지 않는다.",
        "- 결과 차이는 두 구성 전체의 차이다. 제목 보너스, 원문 조회, 입력 길이 중 하나가 원인이라고 단정하지 않는다.",
        "- 실행 시간에는 초기화·질문 배치·저장이 섞여 있고 GPU·CPU 사용 방식도 다르다. 온라인 지연이나 비용의 우열로 사용하지 않는다.",
        "",
        "## 재현",
        "",
        "```bash",
    ]
    base = "artifacts/u_10o0o/advanced-a-baseline-20261111"
    lines += [
        f".venv/bin/python -m experimental.u_10o0o.pattern_retrieval.established_retrieve --arm A --output-dir {base}/A-new",
        f".venv/bin/python -m experimental.u_10o0o.pattern_retrieval.established_retrieve --arm baseline --output-dir {base}/baseline-new",
        ".venv/bin/python -m experimental.u_10o0o.pattern_retrieval.established_score \\",
        f"  --a-dir {base}/A-new --baseline-dir {base}/baseline-new \\",
        "  --audit experimental/u_10o0o/records/advanced-a-baseline-20261111/dataset-audit.json \\",
        f"  --output-dir {base}/scores-new",
        "```",
        "",
        "기록은 원문·질문·정답 인용을 복사하지 않는다. 상세 검색 결과와 문맥은 로컬 `artifacts/`에 보관하고, 공유 기록에는 문항 ID·집계·지문만 남긴다. [입력·설정 지문](manifest.json) · [과정](process.md) · [전체 집계](summary.json)",
        "",
    ]
    # Manifest is read to reject an unfinished or wrongly located score export.
    if manifest["status"] != "complete":
        raise ValueError("Scores are incomplete.")
    changes = [
        "# 기존 기준선 대비 A의 문항별 개선·하락",
        "",
        "전체 근거 충족 여부가 바뀐 문항만 표시한다. 질문·정답 인용은 복사하지 않는다. 공식은 그룹 전체 충족, 카테고리는 필수 사실 전체 충족 기준이다.",
        "",
        "| ID | 유형 | 기존 최종 → A 최종 | A 초기 후보 충족 | A 누락 단계 |",
        "|---|---|---:|---:|---|",
    ]
    stage_labels = {
        "success": "충족",
        "selection_or_budget": "후보 확보 후 최종 선택·예산",
        "initial_retrieval_or_lookup": "후보·추가 조회에서 미확보",
    }
    for row in diff:
        if row["status"] in ("improved", "regressed"):
            changes.append(
                f"| {row['question_id']} | {LABELS.get(row['category'], row['category'])} | {row['baseline_complete']} → {row['A_complete']} | {row['A_initial']} | {stage_labels[row['A_failure_stage']]} |"
            )
    (directory / "differences.md").write_text(
        "\n".join(changes) + "\n", encoding="utf-8"
    )
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores-dir", type=Path, required=True)
    args = parser.parse_args()
    make_report(args.scores_dir)
    print(
        f"Report saved under {args.scores_dir.relative_to(ROOT) if args.scores_dir.is_absolute() else args.scores_dir}"
    )


if __name__ == "__main__":
    main()
