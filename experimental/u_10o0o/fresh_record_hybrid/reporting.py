"""Write a shareable report from this run's scored metadata, never source quotes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CATEGORY_NAMES = {
    "exact_code": "완전 식별자",
    "indirect_identifiable_clues": "간접 단서",
    "no_code_everyday_procedure": "코드 없는 일상 절차",
    "one_event_multistage": "한 사건의 여러 단계",
    "two_events": "두 사건 비교",
    "record_plus_rule_exception": "사건과 규정·예외",
    "correction_status_date_type": "정정·상태·날짜",
    "ambiguous_insufficient_conflict": "모호성·근거 부족",
}


def category_name(value):
    return CATEGORY_NAMES.get(value, value)


def split_name(value):
    return {"dev": "개발", "validation": "검증"}.get(value, value)


def number(x):
    return "N/A" if x is None else f"{x:.1%}"


def coverage(value, total):
    return f"{value}/{total}" if total else "N/A"


def render(score_dir: Path, output: Path):
    summary = json.loads((score_dir / "summary.json").read_text())
    category_order = {
        name: index for index, name in enumerate(["all", *CATEGORY_NAMES])
    }
    summary.sort(
        key=lambda row: (
            row["split"],
            category_order.get(row["category"], len(category_order)),
            row["condition"],
        )
    )
    scores = json.loads((score_dir / "scores.json").read_text())
    changes = json.loads((score_dir / "changes.json").read_text())
    manifest = json.loads((score_dir / "score-manifest.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 원문·기록 단위·명시 관계의 새 A/B/C 실험",
        "",
        "이전 실험 결과·정제본·평가 문항·색인을 사용하지 않고 원본 PDF에서 새로 실행했다. 기존 기준선과 운영 API는 변경하지 않았다.",
        "",
        "| 조건 | 구성 |",
        "|---|---|",
        "| A | 원문 페이지 하이브리드 + 한 번의 리랭킹 |",
        "| B | 제목·필드·상태를 보존한 기록 단위 하이브리드 + 같은 리랭킹 |",
        "| C | B + 질문에 명시된 코드→사건→기록 관계 후보 |",
        "",
        "세 조건의 모델·검색 예산은 같다. A→B는 검색 단위 구성을 바꾸는 효과이고, B→C는 제한된 명시 관계를 추가하는 효과다. 질문 계획·재작성·추가 검색·답변 생성은 수행하지 않았다.",
        "",
        "## 전체 결과",
        "",
        "| 세트 | 조건 | 채점 문항 | 후보 전체 근거 | 최종 전체 근거 | 최종 페이지 Recall | MRR | nDCG |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        if r["category"] != "all":
            continue
        n = r["scored"]
        lines.append(
            f"| {split_name(r['split'])} | {r['condition']} | {n}/{r['total']} | {coverage(r['candidate_complete'], n)} | {coverage(r['final_complete'], n)} | {number(r['final_page_recall'])} | {number(r['final_mrr'])} | {number(r['final_ndcg'])} |"
        )
    lines += [
        "",
        "## 유형별 결과",
        "",
        "| 세트 | 유형 | 조건 | 채점 수 | 후보 전체 근거 | 최종 전체 근거 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for r in summary:
        if r["category"] == "all":
            continue
        n = r["scored"]
        lines.append(
            f"| {split_name(r['split'])} | {category_name(r['category'])} | {r['condition']} | {n} | {coverage(r['candidate_complete'], n)} | {coverage(r['final_complete'], n)} |"
        )
    lines += [
        "",
        "모호성/불충분 문항은 일반 근거 검색률 분모에서 제외한다. 채점 수가 0인 유형은 N/A로 표시하며 성공이나 실패로 세지 않는다. 검색 결과를 반환했다는 이유로 보류 또는 추가 확인에 성공했다고 보지 않는다.",
        "",
        "## 문항별 개선과 하락",
        "",
        "| 비교 | 세트 | 최종 개선 | 최종 하락 | 동일 |",
        "|---|---|---:|---:|---:|",
    ]
    delta_lines = [
        "# 문항별 변화",
        "",
        "질문·정답 인용·원문 값은 로컬 artifacts에만 보관한다. 아래 ID로 이번 실행의 상세 추적과 대응한다.",
        "",
    ]
    for pair, items in changes.items():
        for split in sorted({r["split"] for r in items}):
            subset = [r for r in items if r["split"] == split]
            gains = [r for r in subset if not r["final_before"] and r["final_after"]]
            losses = [r for r in subset if r["final_before"] and not r["final_after"]]
            lines.append(
                f"| {pair} | {split_name(split)} | {len(gains)} | {len(losses)} | {len(subset) - len(gains) - len(losses)} |"
            )
        delta_lines += [
            f"## {pair}",
            "",
            "| ID | 세트 | 유형 | 후보 전→후 | 최종 전→후 | 사실 수 전→후 |",
            "|---|---|---|---|---|---|",
        ]
        for r in items:
            if (
                r["candidate_before"],
                r["final_before"],
                r["final_fact_hits_before"],
            ) == (r["candidate_after"], r["final_after"], r["final_fact_hits_after"]):
                continue
            delta_lines.append(
                f"| {r['id']} | {split_name(r['split'])} | {category_name(r['category'])} | {int(r['candidate_before'])}→{int(r['candidate_after'])} | {int(r['final_before'])}→{int(r['final_after'])} | {r['final_fact_hits_before']}→{r['final_fact_hits_after']} |"
            )
        delta_lines += [""]
    lines += [
        "",
        "## 효율과 명시 관계",
        "",
        "| 세트 | 조건 | 검색 p50(ms) | 검색 p95(ms) | 리랭킹 쌍 | 리랭커 입력 토큰 | 절단된 쌍 | 평균 후보 원문 쪽 | 평균 최종 쪽 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        if r["category"] != "all":
            continue
        lines.append(
            f"| {split_name(r['split'])} | {r['condition']} | {r.get('total_ms_p50', 0):.1f} | {r.get('total_ms_p95', 0):.1f} | {r['rerank_pairs']} | {r['rerank_input_tokens']} | {r['rerank_truncated_pairs']} | {r['candidate_pages_mean']:.2f} | {r['final_pages_mean']:.2f} |"
        )
    lines += [
        "",
        "전체 검색 시간은 질문 임베딩·BM25·벡터·관계 조회·RRF·리랭킹·원문 복원을 포함한다. RTX 5060 Laptop GPU float32에서 워밍업 후 측정했다. 모든 관측 구간에 별도 GPU 프로세스가 등록돼 있어 단독 점유 성능이나 CPU 운영 지연으로 해석하지 않는다. 초기 모델·색인 준비는 제외했으며 별도 manifest에 남겼다. 후보 수가 같아도 입력 토큰 수와 원문 쪽 수가 달라 계산 비용이 같지 않다.",
        "",
        "| 세트 | C 대상 연결 문항 | 명시 대상 평가 문항 | 기대 ID 집합과 일치 | 기대 ID와 다른 연결 문항 | 기대 ID 밖 앵커 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        if r["category"] == "all" and r["condition"] == "C":
            lines.append(
                f"| {split_name(r['split'])} | {r['relation_questions_with_proposal']} | {r['relation_eligible']} | {r['relation_exact']} | {r['relation_wrong_questions']} | {r['relation_wrong_events']} |"
            )
    lines += [
        "",
        "A/B는 대상 확정 기능이 없어 대상 확정 정확도는 N/A다. C 표는 기대 ID 집합과의 일치를 검사한다. DEV-126의 ID 차이 1건은 원문 대조에서 같은 예약의 기록 ID와 예약 키 차이로 확인돼 다른 사건 오연결로 해석하지 않는다. 계약 적용 여부나 답변 정확도는 평가하지 않았다. [대상 ID 대조](target-id-audit.md).",
        "",
        "## 해석 범위",
        "",
        "- 후보 전체 근거: 선택한 최대20개 검색 단위에서 복원 가능한 원문 쪽에 각 필수 사실의 허용 인용이 존재하는지 측정했다.",
        "- 최종 전체 근거: 최대8쪽·8000토큰으로 실제 저장한 문맥 안에서 같은 검사를 했다. 잘린 부분은 맞힌 것으로 세지 않는다.",
        "- 페이지 Recall은 각 사실의 기본 출처에 대해 book_id와 page를 모두 맞춰 문항별로 계산한 평균이다. 허용 대체 출처의 사실 충족과 다를 수 있다.",
        "- MRR/nDCG는 허용 출처의 이진 페이지 관련도를 사용한다. nDCG만으로 필수 사실의 다양성이나 조건 적용을 판단하지 않는다.",
        "- 문항은 독립 에이전트가 작성·근거 검수했다. 사람 검수 완료 세트가 아니다. 같은 원본 양식이 개발·검증에 공유되므로 새로운 업무 환경에 대한 일반화를 보장하지 않는다.",
        "- 조건·상태의 인용을 찾았는지와 이를 올바르게 해석했는지는 다르다. 답변 생성·충분성 판단 기능을 넣지 않았으므로 해당 정확도·충분성 오판율은 N/A다.",
        "- 이번 한 번의 고정 조건 실행으로, 결과 확인 뒤 규칙을 바꾸지 않았다. 기준선 자동 교체는 하지 않았다.",
        "",
        "결론: [conclusions.md](conclusions.md) · 평가 기준: [evaluation-method.md](evaluation-method.md) · 누락 단계: [stage-diagnosis.md](stage-diagnosis.md) · 설계: [plan.md](plan.md) · 공개 근거: [references.md](references.md) · 과정: [process.md](process.md) · 문항별 변화: [changes.md](changes.md) · 재현: [reproduce.md](reproduce.md)",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines))
    (output / "changes.md").write_text("\n".join(delta_lines) + "\n")
    for name, value in [
        ("summary.json", summary),
        ("per-question.json", scores),
        ("changes.json", changes),
        ("score-manifest.json", manifest),
    ]:
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--score-dir", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    render(**vars(p.parse_args()))


if __name__ == "__main__":
    main()
