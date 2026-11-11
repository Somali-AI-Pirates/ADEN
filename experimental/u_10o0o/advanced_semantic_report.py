"""Create sanitized reports for the semantic first-miss A/B experiment."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

METRICS = {
    "all_facts",
    "all_groups",
    "complete",
    "complete_rate",
    "page_recall",
    "document_recall",
    "hit",
    "mrr",
    "ndcg",
    "matched_pages",
    "required_pages",
    "strict_all_pages",
    "required_facts",
    "matched_facts",
    "required_facts_hit",
    "fact_recall",
    "primary_hits",
    "alternative_hits",
    "alternative_only_hits",
}
STAGES = ("initial", "after_hg", "available", "final")


def read(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def metrics(value):
    return (
        {
            k: v
            for k, v in value.items()
            if k in METRICS and isinstance(v, (bool, int, float))
        }
        if isinstance(value, dict)
        else {}
    )


def sanitize_stage(data):
    summary = data["summary"]
    records = []
    for row in data["records"]:
        item = {
            k: row[k]
            for k in (
                "id",
                "variant",
                "plan_status",
                "executed_tasks",
                "queries",
                "candidate_failure",
                "first_miss",
            )
            if k in row
        }
        item["stages"] = {k: metrics(v) for k, v in row.get("stages", {}).items()}
        item["requirements"] = [
            {k: r[k] for k in ("requirement", "covered", "first_miss") if k in r}
            for r in row.get("requirements", [])
        ]
        records.append(item)
    return {
        "summary": {
            k: summary[k]
            for k in (
                "questions",
                "candidate_failures",
                "failure_stages",
                "stage_complete",
                "plan_states",
                "external_api_calls",
                "new_searches",
            )
            if k in summary
        },
        "records": records,
    }


def sanitize_retriever(data):
    specs = (
        ("bm25_required_depth_within80", 80),
        ("dense_required_depth_within1000", 1000),
    )
    totals = {
        name: {"cutoff": limit, "within_cutoff": 0, "not_observed_within_cutoff": 0}
        for name, limit in specs
    }
    records = []
    for row in data.get("records", []):
        reqs, observations = row.get("requirements", []), {}
        for name, limit in specs:
            values = [r.get(name) for r in reqs]
            seen = sum(v is not None for v in values)
            observations[name] = {
                "cutoff": limit,
                "within_cutoff": seen,
                "not_observed_within_cutoff": len(values) - seen,
            }
            totals[name]["within_cutoff"] += seen
            totals[name]["not_observed_within_cutoff"] += len(values) - seen
        records.append(
            {
                "id": row.get("id"),
                "first_miss": row.get("first_miss"),
                "all_query_orders_match": row.get("all_query_orders_match"),
                "replayed_union_fact_coverage_equal": row.get(
                    "replayed_union_fact_coverage_equal"
                ),
                "requirements_count": len(reqs),
                "requirements": [
                    {
                        "requirement": item.get("requirement"),
                        "bm25_required_depth_within80": item.get(
                            "bm25_required_depth_within80"
                        ),
                        "dense_required_depth_within1000": item.get(
                            "dense_required_depth_within1000"
                        ),
                        "archived_stages": item.get("archived_stages", {}),
                    }
                    for item in reqs
                ],
                "retriever_depth_observations": observations,
            }
        )
    return {
        "scope": data.get("scope"),
        "candidate_failure_queries": data.get("candidate_failure_queries"),
        "all_archived_fact_coverages_reproduced": data.get(
            "all_archived_fact_coverages_reproduced"
        ),
        "rank_depth_observation_totals": totals,
        "records": records,
    }


def sanitize_summary(data):
    out = {}
    for experiment, arms in data.items():
        out[experiment] = {}
        for arm, groups in arms.items():
            out[experiment][arm] = {}
            for group, row in groups.items():
                item = {
                    k: row[k]
                    for k in (
                        "total",
                        "scored",
                        "excluded",
                        "mean_returned_pages",
                        "mean_context_tokens",
                    )
                    if k in row and isinstance(row[k], (int, float))
                }
                item["stages"] = {
                    k: metrics(v) for k, v in row.get("stages", {}).items()
                }
                out[experiment][arm][group] = item
    return out


def sanitize_per_question(data):
    fields = (
        "id",
        "suite",
        "question_id",
        "category",
        "arm",
        "returned_pages",
        "context_tokens",
        "lookup_added_final_pages",
        "complex",
        "multi_page",
        "complete_metric",
        "scored",
        "failure_stage",
    )
    return {
        exp: {
            arm: [
                {
                    **{k: row[k] for k in fields if k in row},
                    "stages": {s: metrics(v) for s, v in row.get("stages", {}).items()},
                }
                for row in rows
            ]
            for arm, rows in arms.items()
        }
        for exp, arms in data.items()
    }


def sanitize_manifest(data):
    fields = (
        "status",
        "arms",
        "questions",
        "results",
        "external_api_calls",
        "frozen_bm25_tokenizer",
        "baseline_scope",
        "single_change",
        "fixed",
        "timing_scope",
        "wall_seconds",
        "references_read_after_retrieval",
        "traces_sha256",
        "contexts_sha256",
        "reference_fingerprints",
    )
    out = {k: data[k] for k in fields if k in data}
    out["input_fingerprints"] = [
        {k: row[k] for k in ("path", "sha256") if k in row}
        for row in data.get("inputs", [])
    ]
    return out


def sanitize_rank_manifest(data):
    fields = (
        "status",
        "outcome",
        "questions_expected",
        "questions_completed",
        "query_records_completed",
        "all_40_complete",
        "archive_mismatches",
        "replay_ok",
        "inputs_unchanged",
        "snapshots_unchanged",
    )
    boundary, tokenizer = (
        data.get("execution_boundary", {}),
        data.get("frozen_tokenizer", {}),
    )
    return {
        **{k: data[k] for k in fields if k in data},
        "frozen_tokenizer": {"selected": tokenizer.get("selected")},
        "external_api_calls": boundary.get("external_api_calls"),
        "input_hashes_unchanged": data.get("input_hashes")
        == data.get("input_hashes_after"),
    }


def complete(row, stage):
    value = row.get("stages", {}).get(stage, {})
    return f"{value.get('complete', '—')}/{row.get('scored', '—')}"


def stage_rows(summary, experiment, group):
    lines = [
        "| 조건 | 채점/전체 | 초기 후보 | 원문 조회 후 | 최종 문맥 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm, label in (("control", "기존 검색"), ("is_windows", "IS 창 벡터")):
        row = summary[experiment][arm][group]
        lines.append(
            f"| {label} | {row.get('scored', 0)}/{row.get('total', 0)} | "
            + " | ".join(
                complete(row, stage) for stage in ("initial", "available", "final")
            )
            + " |"
        )
    return lines


def percent(value):
    return "—" if value is None else f"{value:.1%}"


def regression_table(summary):
    groups = [
        "official_all",
        "official_complex_groups",
        "categories_all",
        *(f"S{number:02}" for number in range(1, 8)),
        "work_rules_all",
        "semantic_all",
    ]
    labels = {
        "official_all": "공식 전체",
        "official_complex_groups": "공식 복합 근거",
        "categories_all": "카테고리 전체",
        "work_rules_all": "업무 규정",
        "semantic_all": "의미 질의",
    }
    lines = [
        "| 범주 | 채점/전체 | 기존 조회 후20 | IS 조회 후20 | 기존 최종8 | IS 최종8 | 기존 최종 페이지 Recall | IS 최종 페이지 Recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group in groups:
        left = summary["regression"]["control"][group]
        right = summary["regression"]["is_windows"][group]
        label = labels.get(group, f"시나리오 {group[1:]}")
        available_left = left.get("stages", {}).get("available", {})
        available_right = right.get("stages", {}).get("available", {})
        final_left = left.get("stages", {}).get("final", {})
        final_right = right.get("stages", {}).get("final", {})
        lines.append(
            f"| {label} | {left.get('scored', 0)}/{left.get('total', 0)} | "
            f"{available_left.get('complete', '—')}/{left.get('scored', '—')} | "
            f"{available_right.get('complete', '—')}/{right.get('scored', '—')} | "
            f"{final_left.get('complete', '—')}/{left.get('scored', '—')} | "
            f"{final_right.get('complete', '—')}/{right.get('scored', '—')} | "
            f"{percent(final_left.get('page_recall'))} | {percent(final_right.get('page_recall'))} |"
        )
    return lines


def primary_stage_table(data):
    if not data:
        return []
    labels = {
        "retriever_union": "검색기 합집합",
        "rrf20_union": "별도 RRF20 경로",
        "request_nomination_union": "요청 지명 합집합",
        "merged20": "병합 후보20",
        "expanded20": "원문 확장20",
        "final8": "최종 문맥8",
    }
    counts = {
        arm: {
            stage: sum(
                bool(row.get("stages", {}).get(stage))
                for row in data.get("records", [])
                if row.get("arm") == arm
            )
            for stage in labels
        }
        for arm in ("control", "is_windows")
    }
    lines = [
        "| 단계 | 기존 검색 | IS 창 벡터 |",
        "| --- | ---: | ---: |",
    ]
    for key, label in labels.items():
        lines.append(
            f"| {label} | {counts['control'][key]}/40 | {counts['is_windows'][key]}/40 |"
        )
    return lines


def changed_ids(differences, experiment, stage, direction):
    result = []
    for row in differences:
        pair = row.get(stage, {})
        if row.get("experiment") != experiment or not isinstance(pair, dict):
            continue
        before, after = pair.get("control"), pair.get("is_windows")
        if before is None or after is None:
            continue
        if (direction == "gain" and after > before) or (
            direction == "loss" and after < before
        ):
            result.append(row)
    return result


def changes_markdown(summary, diffs):
    base, changed = (
        summary["regression"]["control"],
        summary["regression"]["is_windows"],
    )
    lines = [
        "# 카테고리별 회귀와 문항 변화",
        "",
        "## 회귀 집계의 모든 범주",
        "",
        "| 범주 | 채점/전체 | 기존 초기·가용·최종 | IS 초기·가용·최종 | 최종 차이 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for group in sorted(set(base) | set(changed)):
        left, right = base.get(group, {}), changed.get(group, {})
        lfinal = left.get("stages", {}).get("final", {}).get("complete", 0)
        rfinal = right.get("stages", {}).get("final", {}).get("complete", 0)
        left_stages = " / ".join(
            complete(left, s) for s in ("initial", "available", "final")
        )
        right_stages = " / ".join(
            complete(right, s) for s in ("initial", "available", "final")
        )
        lines.append(
            f"| {group} | {left.get('scored', 0)}/{left.get('total', 0)} | {left_stages} | {right_stages} | {rfinal - lfinal:+d} |"
        )
    counts = Counter((r.get("experiment"), r.get("status")) for r in diffs)
    for exp, title in (
        ("semantic_planned", "계획이 있는 의미 질의 40개"),
        ("regression", "원 질문 회귀 280개"),
    ):
        rows = [
            r
            for r in diffs
            if r.get("experiment") == exp
            and r.get("status") in {"improved", "regressed"}
        ]
        lines.extend(
            [
                "",
                f"## {title}: 개선 {counts[exp, 'improved']}개, 회귀 {counts[exp, 'regressed']}개",
                "",
                "| ID | 범주 | 변화 | 초기 | 가용 | 최종 |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        if not rows:
            lines.append("| 변화 없음 | — | — | — | — | — |")
        for row in rows:
            label = "개선" if row["status"] == "improved" else "회귀"
            pairs = [row.get(s, {}) for s in ("initial", "available", "final")]
            values = [
                f"{p.get('control', '—')} → {p.get('is_windows', '—')}" for p in pairs
            ]
            lines.append(
                f"| {row.get('id', '—')} | {row.get('category', '—')} | {label} | "
                + " | ".join(values)
                + " |"
            )
    candidates = [
        row
        for row in diffs
        if isinstance(row.get("initial"), dict)
        and row["initial"].get("control") != row["initial"].get("is_windows")
    ]
    lines.extend(
        [
            "",
            "## 후보20이 바뀐 문항 (최종 상태와 별도)",
            "",
            "| 실험 | ID | 범주 | 후보20 기존 → IS | 최종8 기존 → IS | 최종 상태 |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    if not candidates:
        lines.append("| — | 변화 없음 | — | — | — | — |")
    for row in candidates:
        final = row.get("final", {})
        status = (
            "개선"
            if row.get("status") == "improved"
            else "회귀"
            if row.get("status") == "regressed"
            else "동일"
        )
        initial = row["initial"]
        lines.append(
            f"| {row.get('experiment', '—')} | {row.get('id', '—')} | {row.get('category', '—')} | "
            f"{initial.get('control', '—')} → {initial.get('is_windows', '—')} | "
            f"{final.get('control', '—')} → {final.get('is_windows', '—')} | {status} |"
        )
    return "\n".join(lines) + "\n"


def missing_fact_rows(stage, retriever, audit):
    audit_verdict = {row["id"]: row["verdict"] for row in audit.get("verdicts", [])}
    retrieval = {row["id"]: row for row in retriever.get("records", [])}
    labels = (
        "retriever_union",
        "request_nomination_union",
        "merged20",
        "expanded20",
        "final8",
    )
    lines = [
        "## 미충족 필수 근거별 검색기 관측 깊이",
        "",
        "| ID | 첫 누락 | 계획 판정 | 요건 | BM25 최선(80) | Dense 최선(1000) | 보관 단계 충족 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for question in stage.get("records", []):
        record = retrieval.get(question.get("id"), {})
        for item in record.get("requirements", []):
            covered = item.get("archived_stages", {})
            if covered.get("retriever_union"):
                continue
            bm = item.get("bm25_required_depth_within80")
            dense = item.get("dense_required_depth_within1000")
            bm_text = str(bm) if bm is not None else ">80*"
            dense_text = str(dense) if dense is not None else ">1000*"
            flags = "; ".join(f"{key}={int(bool(covered.get(key)))}" for key in labels)
            lines.append(
                f"| {question.get('id')} | {question.get('first_miss')} | "
                f"{audit_verdict.get(question.get('id'), 'unknown')} | "
                f"{item.get('requirement')} | {bm_text} | {dense_text} | {flags} |"
            )
    lines.extend(
        [
            "",
            "`*` 해당 검색기 기록 상한 안에서 요구 묶음 위치가 관측되지 않음. 상한 초과 또는 미기록을 뜻하며 코퍼스 부재를 뜻하지 않는다. 저장 순위 목록과 출처 ID는 포함하지 않았다.",
            "",
        ]
    )
    return lines


def report_text(
    stage,
    retriever,
    audit,
    summary,
    diffs,
    manifest,
    replay,
    rank_manifest,
    stage_comparison=None,
):
    stage_summary = stage["summary"]
    records = [r for r in stage["records"] if r.get("candidate_failure")]
    verdict = {r["id"]: r["verdict"] for r in audit.get("verdicts", [])}
    taxonomy = Counter(
        (r.get("first_miss"), verdict.get(r.get("id"), "unknown")) for r in records
    )
    failure_counts = stage_summary.get("failure_stages", {})
    labels = {
        "retriever_union": "검색기 합집합",
        "plan_fallback": "미지원 계획 기준선 복귀",
        "merged20": "요청 후보 병합",
        "expanded20": "원문 확장",
        "final8": "최종 문맥",
    }
    ranks = sanitize_retriever(retriever)["rank_depth_observation_totals"]
    bm, dense = (
        ranks["bm25_required_depth_within80"],
        ranks["dense_required_depth_within1000"],
    )
    retriever_records = sanitize_retriever(retriever).get("records", [])
    total_rank_requirements = sum(
        row.get("requirements_count", 0) for row in retriever_records
    )
    missing_requirements = [
        requirement
        for row in retriever_records
        for requirement in row.get("requirements", [])
        if not requirement.get("archived_stages", {}).get("retriever_union")
    ]
    bm_missing_seen = sum(
        requirement.get("bm25_required_depth_within80") is not None
        for requirement in missing_requirements
    )
    dense_missing_seen = sum(
        requirement.get("dense_required_depth_within1000") is not None
        for requirement in missing_requirements
    )
    ctl, win = (
        summary["semantic_planned"]["control"]["semantic_all"],
        summary["semantic_planned"]["is_windows"]["semantic_all"],
    )
    delta = win["stages"]["final"]["complete"] - ctl["stages"]["final"]["complete"]
    statuses = Counter((r.get("experiment"), r.get("status")) for r in diffs)
    official = summary["regression"]["control"]["official_all"]
    categories = summary["regression"]["control"]["categories_all"]
    candidate_gains = changed_ids(diffs, "semantic_planned", "initial", "gain")
    candidate_losses = changed_ids(diffs, "semantic_planned", "initial", "loss")
    final_gains = [
        row
        for row in diffs
        if row.get("experiment") == "semantic_planned"
        and row.get("status") == "improved"
    ]
    final_losses = [
        row
        for row in diffs
        if row.get("experiment") == "semantic_planned"
        and row.get("status") == "regressed"
    ]

    def ids(rows):
        return ", ".join(row.get("id", "—") for row in rows) if rows else "없음"

    matched = sum(
        all(r.get(k) is True for k in ("pool_equal", "expanded_equal", "context_equal"))
        for r in replay
    )
    rank_rows = rank_manifest.get("query_records_completed", 0)
    rank_expected = sum(
        row.get("queries_expected", 0)
        for row in rank_manifest.get("question_status", [])
    )
    lines = [
        "# 일상 표현 검색의 첫 누락 진단과 IS 창 벡터 A/B",
        "",
        "## 범위와 고정 조건",
        "",
        "주 비교는 같은 캐시 계획을 사용하는 일상 표현 40개다. 회귀는 공식 60개와 카테고리 220개를 원 질문으로 검색하며 두 분모의 점수는 합치지 않았다. 비교 320쌍·검색 결과 640개이며, 일상 표현 40개가 두 경로에 중복되므로 고유 질문은 280개다.",
        "",
        "IS 벡터 검색은 내용 256토큰과 특수 토큰 2개(창 인코딩 총 258토큰), 중첩 64토큰의 원문 창을 사용하고 부모 페이지 점수는 창별 최대 코사인 유사도다. KURE와 최종 문맥에는 원본 페이지를 사용한다. 격리 프로세스에서 BM25 토크나이저를 `bigram-id`로 고정했다.",
        "",
        "계획·검색어, BM25, 검색기별 상위20, RRF 5:5/상수60, KURE 512/FP16, 후보 병합, HG 조회, 최종 8쪽·8,000토큰을 고정했다. 외부 API 호출 없이 수행하며 기존 기준선은 유지한다.",
        "",
        "## 보관 요청40의 첫 누락",
        "",
        f"후보 근거 실패는 {stage_summary.get('candidate_failures', 0)}/{stage_summary.get('questions', 0)}개다. 계획 판정과 첫 누락 단계를 함께 집계했다.",
        "",
        "| 첫 누락 | 문항 수 | 충실 계획 | 오류 계획 | 불확실 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for stage_name, count in sorted(failure_counts.items()):
        lines.append(
            f"| {labels.get(stage_name, stage_name)} | {count} | {taxonomy[stage_name, 'faithful']} | {taxonomy[stage_name, 'error']} | {taxonomy[stage_name, 'uncertain']} |"
        )
    verdict_labels = {
        "faithful": "충실",
        "error": "요청 누락/오류",
        "uncertain": "판단 불확실",
    }
    lines.extend(
        [
            "",
            "### 실패 19개 문항별 판정",
            "",
            "| ID | 첫 누락 단계 | 독립 계획 판정 |",
            "| --- | --- | --- |",
        ]
    )
    for row in records:
        stage_name = row.get("first_miss", "unknown")
        plan_verdict = verdict.get(row.get("id"), "unknown")
        lines.append(
            f"| {row.get('id', '—')} | {labels.get(stage_name, stage_name)} | "
            f"{verdict_labels.get(plan_verdict, plan_verdict)} |"
        )
    lines.extend(missing_fact_rows(stage, retriever, audit))
    vs = Counter(r.get("verdict") for r in audit.get("verdicts", []))
    lines.extend(
        [
            "",
            f"계획 감사 전체는 충실 {vs['faithful']}, 요청 누락/오류 {vs['error']}, 판단 불확실 {vs['uncertain']}개다. 감사는 사람 검수가 아니다. RRF20과 KURE head40은 별도 후보 지명 경로이므로, head40만으로 첫 누락을 정하지 않았다.",
            "",
            f"검색기 깊이 합계는 후보 실패 19문항의 전체 {total_rank_requirements}개 요건(이미 충족된 요건 포함) 대상이다. BM25 상위80에서 관측 {bm['within_cutoff']}, 상한 내 미관측 {bm['not_observed_within_cutoff']}; dense 상위1000에서 관측 {dense['within_cutoff']}, 상한 내 미관측 {dense['not_observed_within_cutoff']}다. 이 깊이는 원 질문과 실행된 모든 계획 질의에서 요건이 관측된 최선값을 합친 것이며 단일 질의의 순위가 아니다. 검색기 합집합에서 빠진 {len(missing_requirements)}개 요건만 보면 BM25 관측 {bm_missing_seen}, 상한 내 미관측 {len(missing_requirements) - bm_missing_seen}; dense 관측 {dense_missing_seen}, 상한 내 미관측 {len(missing_requirements) - dense_missing_seen}다. 미관측은 기록 상한 밖 또는 미기록을 뜻하며 코퍼스 부재를 의미하지 않는다.",
            "",
            f"동일 입력 로컬 재생은 요청 순위 {rank_rows}/{rank_expected}개, 결과 `{rank_manifest.get('outcome', 'unknown')}`, 보관 불일치 {rank_manifest.get('archive_mismatches', '—')}개다. 입력 지문 보존: {rank_manifest.get('inputs_unchanged', False)}; 지문 전후 일치: {rank_manifest.get('input_hashes') == rank_manifest.get('input_hashes_after')}. 첫 누락 진단의 19개 근거 충족 재현: {retriever.get('all_archived_fact_coverages_reproduced', False)}.",
            "",
            "## A/B 결과",
            "",
            "### 계획이 있는 의미 질의 40개",
            "",
            *stage_rows(summary, "semantic_planned", "semantic_all"),
            "",
            *(
                [
                    "",
                    "#### 요청별 후보 단계",
                    "",
                    *primary_stage_table(stage_comparison),
                ]
                if stage_comparison
                else []
            ),
            "",
            f"후보20 완전 충족은 기존 {complete(ctl, 'initial')}에서 IS 창 벡터 {complete(win, 'initial')}로 같았다. 후보 단계 개선 {len(candidate_gains)}개({ids(candidate_gains)}), 하락 {len(candidate_losses)}개({ids(candidate_losses)})다. 최종8 완전 충족은 {complete(ctl, 'final')}에서 {complete(win, 'final')}로 변했다 (차이 {delta:+d}); 최종 개선 {len(final_gains)}개({ids(final_gains)}), 하락 {len(final_losses)}개({ids(final_losses)}), 동일 {statuses['semantic_planned', 'unchanged']}개다. 이 표본 결과만으로 핵심 후보 실패가 해결됐다거나 통계적 유의성이 있다고 해석하지 않는다.",
            "",
            "### 계획 없는 원 질문 회귀 280개",
            "",
            f"공식 전체는 {official.get('scored', 0)}/{official.get('total', 0)}개 채점, 카테고리 전체는 {categories.get('scored', 0)}/{categories.get('total', 0)}개 채점이다. 카테고리의 미채점 20개는 negative control이며 성공으로 계산하지 않았다.",
            "",
            *regression_table(summary),
            "",
            "후보 열은 원문 조회까지 마친 최종 후보 20쪽이다. 초기 검색 후보와의 차이, 세부 집계와 문항별 변화는 `changes.md`에 있다. 공식은 58/60개가 채점됐고 카테고리는 200/220개가 채점됐다. 나머지 카테고리 20개는 negative control로 미채점이며 통과 수에 포함하지 않았다.",
            "",
            f"문항별 개선 {statuses['regression', 'improved']}, 회귀 {statuses['regression', 'regressed']}, 동일 {statuses['regression', 'unchanged']}, 미채점 {statuses['regression', 'not_scored']}개. 실행 manifest 상태 `{manifest.get('status', 'unknown')}`, 외부 API {manifest.get('external_api_calls', '미기록')}회. 보관 계획 재생에서 pool·확장·문맥이 모두 일치한 문항은 {matched}/{len(replay)}개다.",
            "",
            "이 결과는 개발 비교와 회귀 검사이며 변경 채택이나 기준선 교체 근거로 단정하지 않는다. 별도 독립 holdout은 실행하지 않았다.",
            "",
            "질문·문맥·순위 목록·페이지/출처 ID는 보고서와 요약 JSON에서 제외했다. 판단과 재현 절차는 [conclusions.md](conclusions.md), [reproduce.md](reproduce.md)에 있다. 계획 감사와 실행 조건은 [plan-audit.json](plan-audit.json), [process.md](process.md)에 있다. IS 창 인덱스의 모델·설정·입력 지문은 [is-window-manifest-sanitized.json](is-window-manifest-sanitized.json)에 기록했다.",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Write sanitized reports for the semantic retrieval A/B."
    )
    parser.add_argument("--stages-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if not args.output_dir.is_dir():
        raise ValueError("--output-dir must be the existing records directory.")
    stage, retriever = (
        read(args.stages_dir / name)
        for name in ("stage-diagnosis.json", "retriever-diagnosis.json")
    )
    summary, per_question, diffs, manifest, replay = (
        read(args.run_dir / name)
        for name in (
            "summary.json",
            "per-question.json",
            "differences.json",
            "manifest.json",
            "archive-replay.json",
        )
    )
    audit = read(args.output_dir / "plan-audit.json")
    stage_comparison_path = args.output_dir / "ab-stage-comparison-sanitized.json"
    stage_comparison = (
        read(stage_comparison_path) if stage_comparison_path.is_file() else None
    )
    rank_hash = retriever.get("rank_replay_manifest_sha256")
    rank_manifests = list(args.stages_dir.parent.glob("ranks-v*/manifest.json"))
    matches = [
        p
        for p in rank_manifests
        if hashlib.sha256(p.read_bytes()).hexdigest() == rank_hash
    ]
    if len(matches) != 1:
        raise ValueError(
            "Could not identify the rank replay manifest by its recorded SHA-256."
        )
    rank_manifest = read(matches[0])
    pair_keys = [(row.get("experiment"), row.get("id")) for row in diffs]
    per_question_count = sum(
        len(rows) for arms in per_question.values() for rows in arms.values()
    )
    if (
        manifest.get("status") != "complete"
        or manifest.get("results") != 640
        or len(diffs) != 320
        or len(set(pair_keys)) != 320
        or per_question_count != 640
        or len(replay) != 40
        or {
            exp: {arm: len(rows) for arm, rows in arms.items()}
            for exp, arms in per_question.items()
        }
        != {
            "semantic_planned": {"control": 40, "is_windows": 40},
            "regression": {"control": 280, "is_windows": 280},
        }
        or Counter(row.get("experiment") for row in diffs)
        != Counter({"semantic_planned": 40, "regression": 280})
        or len(
            {row.get("id") for row in diffs if row.get("experiment") == "regression"}
        )
        != 280
        or len(
            {row.get("id") for row in diffs if row.get("experiment") == "regression"}
            & {
                row.get("id")
                for row in diffs
                if row.get("experiment") == "semantic_planned"
            }
        )
        != 40
    ):
        raise ValueError(
            "A/B retrieval and paired score results are incomplete; no report was written."
        )
    if stage.get("summary", {}).get("candidate_failures") != 19 or not retriever.get(
        "all_archived_fact_coverages_reproduced"
    ):
        raise ValueError("The frozen first-miss diagnosis did not reproduce.")
    if (
        rank_manifest.get("outcome") != "pass"
        or rank_manifest.get("archive_mismatches") != 0
        or not rank_manifest.get("inputs_unchanged")
        or not rank_manifest.get("snapshots_unchanged")
        or rank_manifest.get("input_hashes") != rank_manifest.get("input_hashes_after")
    ):
        raise ValueError(
            "Archived query ranks or their source fingerprints did not reproduce."
        )
    payloads = {
        "stage-diagnosis-sanitized.json": sanitize_stage(stage),
        "retriever-diagnosis-sanitized.json": sanitize_retriever(retriever),
        "ab-summary.json": sanitize_summary(summary),
        "ab-per-question-sanitized.json": sanitize_per_question(per_question),
        "ab-differences-sanitized.json": [
            {
                k: r[k]
                for k in (
                    "experiment",
                    "id",
                    "category",
                    "scored",
                    "status",
                    "initial",
                    "available",
                    "final",
                )
                if k in r
            }
            for r in diffs
        ],
        "ab-manifest-sanitized.json": sanitize_manifest(manifest),
        "archive-replay-sanitized.json": [
            {
                k: r[k]
                for k in ("id", "pool_equal", "expanded_equal", "context_equal")
                if k in r
            }
            for r in replay
        ],
        "rank-replay-manifest-sanitized.json": sanitize_rank_manifest(rank_manifest),
    }
    for name, value in payloads.items():
        write(args.output_dir / name, value)
    (args.output_dir / "report.md").write_text(
        report_text(
            stage,
            retriever,
            audit,
            summary,
            diffs,
            manifest,
            replay,
            rank_manifest,
            stage_comparison,
        ),
        encoding="utf-8",
    )
    (args.output_dir / "changes.md").write_text(
        changes_markdown(summary, diffs), encoding="utf-8"
    )
    print(f"Wrote sanitized report to {args.output_dir}")


if __name__ == "__main__":
    main()
