import json
from pathlib import Path

root = Path(__file__).resolve().parents[4]
out = root / "artifacts/u_10o0o/advanced-structural-retrieval-20261113"
rec = root / "experimental/u_10o0o/records/advanced-structural-retrieval-20261113"
rows = json.loads((rec / "per-run.json").read_text())
assert len(rows) == 1900 and not any(x["review_pending"] for x in rows)
arms = ["A", "S", "V", "R", "P"]


def metric(items, field):
    valid = [x[field] for x in items if x[field] is not None]
    return (
        f"{sum(bool(v) for v in valid)}/{len(valid)} ({sum(bool(v) for v in valid) / len(valid):.1%})"
        if valid
        else "N/A"
    )


def subset(arm, kind):
    r = [x for x in rows if x["arm"] == arm and x["repeat"] == 1]
    if kind == "official":
        return [x for x in r if x["split"].startswith("official_")]
    if kind == "category":
        return [
            x
            for x in r
            if x["split"]
            in {"scenarios", "semantic_queries", "work_rules", "negative_control"}
        ]
    return [x for x in r if x["split"] == kind]


lines = [
    "# 구조적 검색 개선 비교 결과",
    "",
    "기존 기준선과 제품 설정은 변경하지 않았다. 답변 생성은 제외했다. 모든 검색을 저장한 뒤 동일한 참고 요구로 채점하고, 지정 페이지 외 대체 근거도 대상·역할·효력·조건이 맞으면 인정했다.",
    "",
    "## 최종 필수 근거 전체 충족",
    "",
    "| 조건 | 공식(첫 반복) | 카테고리(첫 반복) | 새 문항(첫 반복) |",
    "|---|---:|---:|---:|",
]
for a in arms:
    lines.append(
        "| "
        + a
        + " | "
        + " | ".join(
            metric(subset(a, k), "semantic_complete")
            for k in ["official", "category", "holdout"]
        )
        + " |"
    )
lines += [
    "",
    "참고 근거 요구가 없는 문항은 위 근거 충족 분모에 넣지 않고 종료 상태를 별도 평가했다. 추가 확인·보류 질문도 지정된 충돌 근거를 찾아야 하는 경우에는 근거 충족을 함께 평가했다. 새 세트는 같은 원문에서 AI가 작성·검수한 새 질문이며 사람 검수·새 문서 평가가 아니다.",
    "",
    "## 후보의 전체 근거 충족",
    "",
    "| 조건 | 공식 | 카테고리 | 새 문항 |",
    "|---|---:|---:|---:|",
]
for a in arms:
    lines.append(
        "| "
        + a
        + " | "
        + " | ".join(
            metric(subset(a, k), "semantic_candidate_complete")
            for k in ["official", "category", "holdout"]
        )
        + " |"
    )
lines += [
    "",
    "최종 문맥이 충분한 경우 같은 문맥을 포함하는 후보는 충분으로 처리했다. 최종 실패 문맥만 후보 전체를 별도로 의미 검수했다.",
    "",
    "## 추가 30문항 반복",
    "",
    "| 조건 | 최종 충족(29문항×3회) | ADD02 | ADD06 | ADD09 |",
    "|---|---:|---:|---:|---:|",
]
for a in arms:
    rr = [x for x in rows if x["arm"] == a and x["split"] == "official_additional"]
    vals = [metric(rr, "semantic_complete")] + [
        metric([x for x in rr if x["question_id"].endswith(q)], "semantic_complete")
        for q in ["AS-ADD02", "AS-ADD06", "AS-ADD09"]
    ]
    lines.append("| " + a + " | " + " | ".join(vals) + " |")
lines += [
    "",
    "반복 3회는 독립 문항 수를 늘린 것이 아니다. A의 이 90회는 직전 동결 런타임의 저장 결과를 재사용했다.",
    "",
    "## 유형별 첫 반복",
    "",
    "| 유형 | A | S | V | R | P |",
    "|---|---:|---:|---:|---:|---:|",
]
labels = json.loads((rec / "dataset-summary.json").read_text())["category_labels"]
categories = sorted({(x["split"], x["category"]) for x in rows if x["repeat"] == 1})
for split, cat in categories:
    vals = [
        metric(
            [
                x
                for x in rows
                if x["arm"] == a
                and x["repeat"] == 1
                and x["split"] == split
                and x["category"] == cat
            ],
            "semantic_complete",
        )
        for a in arms
    ]
    lines.append(
        "| "
        + split
        + "/"
        + labels.get(cat, cat).replace("|", "/")
        + " | "
        + " | ".join(vals)
        + " |"
    )
lines += [
    "",
    "## 충분 판정과 종료 동작",
    "",
    "| 조건 | 필수 근거 부족인데 충분 선언 | 별도 보류 문항의 올바른 종료 |",
    "|---|---:|---:|",
]
for a in arms:
    rr = [x for x in rows if x["arm"] == a and x["repeat"] == 1]
    unanswer = [x for x in rr if x["semantic_complete"] is None]
    lines.append(
        f"| {a} | {sum(x['false_sufficient'] for x in rr)} | {sum(x['status_correct'] for x in unanswer)}/{len(unanswer)} |"
    )
lines += [
    "",
    "### 종료 상태의 별도 검증",
    "",
    "근거 충족과 답변 확정 가능 여부를 분리한다. 아래 오보류는 근거가 충분하고 데이터가 답변을 허용하는데 실제 종료 상태가 허용 상태 목록 밖인 경우다. 올바른 추가 확인·보류는 오류로 세지 않는다.",
    "",
    "| 조건 | 근거 충분·답변 가능 문항의 오보류 | 전체 종료 상태 정합 |",
    "|---|---:|---:|",
]
for a in arms:
    rr = [x for x in rows if x["arm"] == a and x["repeat"] == 1]
    lines.append(
        f"| {a} | {sum(x['false_insufficient_for_answerable_evidence'] for x in rr)} | {sum(x['status_correct'] for x in rr)}/320 |"
    )

lines += [
    "",
    "## 실행량과 지연",
    "",
    "아래 지연은 추가30문항의 역사적 A 실행을 제외한 새 실행290문항이다. GPU·CPU를 공유한 실험 환경의 관측값이며 서비스 SLA가 아니다. S/V는 A 실행 시간에 선택 호출 시간을 더한 후처리 합계다.",
    "",
    "| 조건 | p50(초) | p95(초) | 계획 호출 | 추가 선택 호출 | 검색 호출 | KURE 쌍 |",
    "|---|---:|---:|---:|---:|---:|---:|",
]


def pct(v, p):
    v = sorted(v)
    k = (len(v) - 1) * p
    i = int(k)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (k - i)


for a in arms:
    rr = [x for x in rows if x["arm"] == a and x["split"] != "official_additional"]
    lines.append(
        f"| {a} | {pct([x['latency_seconds'] for x in rr], 0.5):.2f} | {pct([x['latency_seconds'] for x in rr], 0.95):.2f} | {sum(x['planner_calls'] for x in rr)} | {sum(x['selector_calls'] for x in rr)} | {sum(x['search_calls'] for x in rr)} | {sum(x['rerank_pairs'] for x in rr)} |"
    )
lines += [
    "",
    "## 지정 페이지 위치 진단",
    "",
    "아래는 원래 지정 book_id+page 목록에 대한 위치 지표다. 다른 원문에서 같은 사실을 찾은 경우도 낮게 나오므로 검색 정답률로 사용하지 않는다. 대체 출처 목록의 합집합을 분모로 하는 페이지 Recall은 특히 모든 대체 페이지 회수를 요구한다는 한계가 있다.",
    "",
    "| 세트 | 조건 | 모든 지정 근거 그룹 회수 | 지정 페이지 Recall 평균 |",
    "|---|---|---:|---:|",
]
for kind in ["official", "category", "holdout"]:
    for a in arms:
        rr = [x for x in subset(a, kind) if x["semantic_complete"] is not None]
        vals = [
            x["exact_final"]["page_recall"]
            for x in rr
            if x["exact_final"]["page_recall"] is not None
        ]
        lines.append(
            f"| {kind} | {a} | {sum(x['exact_final']['all_groups'] for x in rr)}/{len(rr)} | {sum(vals) / len(vals):.1%} |"
        )

lines += [
    "",
    "A=동결 기준선, S=같은 관측에서 요청별 최종 선택, V=후보별 관련 원문 발췌+동일 선택, R=RRF 이후 축소 지연, P=원문 IS 문단 보조 색인.",
    "",
    "## 해석 경계",
    "",
    "- V는 관측 범위와 발췌 위치를 함께 바꿨다. 순수한 발췌 위치 효과로 단정하지 않는다.",
    "- P는 원문 문단 단위를 추가하면서 BM25 통계도 달라진다. 규칙 자동 병합이나 GraphRAG 실험이 아니다.",
    "- S/V는 기존 검색을 고정한 후처리다. 누락된 근거를 추가 검색하는 제품 흐름을 구현한 것은 아니다.",
    "- R/P의 후속 도구 선택에는 모델 변동이 포함된다. 실제 알고리즘 경로와 같은 질문의 반복을 함께 봐야 한다.",
    "- AI 의미 채점·대체 근거 검수는 사람 판정과 같지 않다. 원시 판정과 수정 판정은 모두 보존했다.",
    "- 이번 결과로 기본 구성이나 운영 API를 자동 변경하지 않았다.",
    "",
    "[설계](plan.md) · [실행 과정](process.md) · [입력·모델·코드 지문](protocol.json) · [문항별 비교](paired-changes.json) · [실행별 지표](per-run.json)",
]
(rec / "metrics.md").write_text("\n".join(lines) + "\n")
print("Generated metrics.md")
