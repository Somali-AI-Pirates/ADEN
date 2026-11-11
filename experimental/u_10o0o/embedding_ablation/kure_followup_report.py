"""Score saved KURE-v2 follow-ups, preserving original and audited metrics."""

from collections import Counter
from copy import deepcopy

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    differences,
    score_item,
)
from experimental.u_10o0o.prepare import CACHE_PATH

from .kure_followup import ARMS, RECORDS, key, validate_saved


def score(run, output):
    frozen, saved = validate_saved(run, output)
    if (output / "scores.json").exists():
        raise FileExistsError("Never overwrite scores")
    # Gold is opened only after every arm and its result hashes have passed.
    from app.evaluation.dataset import load_references

    audit = read_json(output / "source-audit.json")
    manifest = read_json(output / "manifest.json")
    if file_sha(output / "source-audit.json") != manifest["audit_sha256"]:
        raise ValueError("The pre-run audit changed")
    for reference in read_json(run / "reference-freeze.json")["references"]:
        if file_sha(ROOT / reference["path"]) != reference["sha256"]:
            raise ValueError("Original gold changed")
    references = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in frozen["question_sources"]
    }
    source_pages = {key(p): p for p in read_json(CACHE_PATH)}
    original, audited = {}, {}
    for arm, (traces, contexts) in saved.items():
        original[arm], audited[arm] = [], []
        for meta, trace, context in zip(frozen["items"], traces, contexts, strict=True):
            reference = references[meta["suite"]][meta["question_id"]]
            original[arm].append(
                score_item(meta, reference, trace, context, source_pages)
            )
            updated = deepcopy(reference)
            for amendment in audit["alternative_amendments"]:
                if meta["id"] == amendment["id"]:
                    updated["evidence_requirements"][amendment["requirement_index"]][
                        "alternatives"
                    ].append(amendment["evidence"])
            audited[arm].append(score_item(meta, updated, trace, context, source_pages))
    write_json(
        output / "scores.json",
        {
            "original": {arm: aggregates(rows) for arm, rows in original.items()},
            "audited_sensitivity": {
                arm: aggregates(rows) for arm, rows in audited.items()
            },
            "scope_clear_sensitivity": {
                arm: aggregates(
                    [r for r in rows if r["id"] not in audit["scope_ambiguous_ids"]]
                )
                for arm, rows in audited.items()
            },
            "original_rows": original,
            "audited_rows": audited,
            "diffs": {
                arm: differences({"baseline": original["base20"], "A": original[arm]})
                for arm in ARMS[1:]
            },
            "audited_diffs": {
                arm: differences({"baseline": audited["base20"], "A": audited[arm]})
                for arm in ARMS[1:]
            },
            "factor_diffs": {
                "scope_at_union40": differences(
                    {"baseline": original["union40"], "A": original["scope40"]}
                ),
                "union_at_scope": differences(
                    {"baseline": original["scope20"], "A": original["scope40"]}
                ),
            },
            "audit_sha256": manifest["audit_sha256"],
            "all_results_saved_before_gold": True,
            "original_references_unchanged": True,
        },
    )


def rate(summary, stage="final"):
    return f"{summary['stages'][stage]['complete']}/{summary['scored']}"


def report(run, output):
    _frozen, saved = validate_saved(run, output)
    scores = read_json(output / "scores.json")
    timing = read_json(output / "timing.json")
    manifest = read_json(output / "manifest.json")
    audit = read_json(output / "source-audit.json")
    original = scores["original"]
    lines = [
        "# KURE-v2 후보 보존·적용 범위 비교",
        "",
        "공식 60문항과 카테고리 220문항을 실행했다. 참고 근거가 있는 공식 58개와 카테고리 200개를 채점했다. 이전에 확인한 문항을 사용하는 개발·회귀 실험이다. 원래 채점과 대체 출처 검수에 따른 민감도 집계를 분리했다. [고정 조건](protocol.md) · [검수 기록](source-audit.md) · [문항별 변화](changes.md) · [원인과 채택 판단](diagnosis.md) · [검증](validation.md)",
        "",
        "| 조건 | 검색 범위 | 리랭킹 후보 |",
        "|---|---|---:|",
        "| base20 | 전체 | RRF 20 |",
        "| union40 | 전체 | 합집합 최대 40 |",
        "| scope20 | 대상 단서가 없으면 사건별 기록 제외 | RRF 20 |",
        "| scope40 | scope20과 같음 | 합집합 최대 40 |",
        "",
        "모든 조건에서 KURE-v2, KURE nano, KURE Top 3 + RRF, 기존 원문 조회와 최종 8쪽·8,000토큰은 같다. 40쪽 조건도 리랭킹 후 기존 선택기로 넘길 목록은 20쪽이다. 대상 코드·원문 화주/화물명이 있으면 범위 필터를 적용하지 않는다. 대상이 없는 계약 질문에는 추가 확인 표시를 남기지만, 답변이나 실제 추가 확인 대화는 생성하지 않았다.",
        "",
        "| 원래 채점의 최종 전체 근거 충족 | base20 | union40 | scope20 | scope40 |",
        "|---|---:|---:|---:|---:|",
    ]
    groups = [
        ("공식", "official_all"),
        ("카테고리 유효", "categories_positive"),
        ("일상 표현", "semantic_all"),
        ("업무 규정", "work_rules_all"),
    ]
    groups += [(f"상황 {i}", f"S{i:02}") for i in range(1, 8)]
    for label, group in groups:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(rate(original[a][group]) for a in ARMS)
            + " |"
        )
    lines.extend(
        [
            "",
            "| 후보 전체 근거 충족 | base20 | union40 | scope20 | scope40 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, group in groups[:4]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(rate(original[a][group], "initial") for a in ARMS)
            + " |"
        )
    lines.extend(
        [
            "",
            "| 최종 지표 | base20 | union40 | scope20 | scope40 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, group in [
        ("공식 페이지 Recall@8", "official_all"),
        ("일상 표현 페이지 Recall@8", "semantic_all"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(
                f"{original[a][group]['stages']['final']['page_recall']:.2%}"
                for a in ARMS
            )
            + " |"
        )
    for label, group in [
        ("공식 MRR", "official_all"),
        ("일상 표현 MRR", "semantic_all"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(
                f"{original[a][group]['stages']['final']['mrr']:.4f}" for a in ARMS
            )
            + " |"
        )
    for label, group in [
        ("공식 nDCG@8", "official_all"),
        ("일상 표현 nDCG@8", "semantic_all"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(
                f"{original[a][group]['stages']['final']['ndcg']:.4f}" for a in ARMS
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "| 대체 근거 검수 민감도 | base20 | union40 | scope20 | scope40 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, mode in [
        ("일상 표현: AS-SEM-40 대체 출처 추가", "audited_sensitivity"),
        ("위 조건에서 범위 모호 AS-SEM-32 제외", "scope_clear_sensitivity"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(rate(scores[mode][a]["semantic_all"]) for a in ARMS)
            + " |"
        )
    lines.extend(
        [
            "",
            "이 민감도 집계는 원래 평가를 대체하지 않는다. 한 에이전트의 자체 원문 검수이며, 모든 문항의 대체 출처를 전수 검수한 결과도 아니다.",
            "",
            "| 온라인 지연/계산량 | base20 | union40 | scope20 | scope40 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, field, quantile in [
        ("전체 p50(ms)", "total_ms", "p50"),
        ("전체 p95(ms)", "total_ms", "p95"),
        ("리랭킹 p50(ms)", "rerank_ms", "p50"),
        ("검색·필터·결합 p50(ms)", "sparse_scope_fusion_ms", "p50"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(f"{timing['metrics'][a][field][quantile]:.1f}" for a in ARMS)
            + " |"
        )
    work = {}
    for arm, (traces, _) in saved.items():
        work[arm] = {k: sum(t["work"][k] for t in traces) for k in traces[0]["work"]}
    for label, field in [
        ("280문항 리랭킹 쌍", "rerank_pairs"),
        ("리랭커 실제 입력 토큰 합", "rerank_tokens"),
    ]:
        lines.append(
            "| " + label + " | " + " | ".join(str(work[a][field]) for a in ARMS) + " |"
        )
    timing_diffs = {}
    for arm, (traces, _) in saved.items():
        expected = {t["id"]: t["context_sha256"] for t in traces}
        changed = [
            r
            for r in timing["samples"]
            if r["arm"] == arm and r["context_sha256"] != expected[r["id"]]
        ]
        timing_diffs[arm] = {
            "runs": len(changed),
            "ids": sorted({r["id"] for r in changed}),
        }
    lines.extend(
        [
            "",
            "현재 RTX 5060 Laptop 8GB에서 30문항 × 3회 × 4조건을 순환 측정했다. 모델과 색인은 상주하며, 매번 질문 인코딩·MaxSim·BM25·필터·RRF·리랭킹·원문 조회·문맥 조립을 새로 실행했다. 초기 로드·색인 준비·답변 생성은 제외한다. 품질 평가에서는 조건 간 공통 리랭킹 쌍을 재사용했지만 이 지연 측정에서는 재사용하지 않았다.",
            "",
            f"기존 KURE-v2와 base20 재현 차이는 {len(manifest['baseline_replay_differences'])}건이다. 온라인 배치 1과 품질 배치 실행의 문맥 지문 차이는 `{timing_diffs}`이다. 수치 정밀도·배치 차이에 따른 순위 변화 가능성을 함께 남긴다.",
            "",
            "## 재현",
            "",
            "```bash",
        ]
    )
    for cmd in ["prepare", "audit", "retrieve", "score", "timing", "report"]:
        lines.append(
            f".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_followup {cmd}"
        )
    lines.extend(
        [
            "```",
            "",
            "audit 명령으로 새 실행 전에 로컬 source-audit.json 검수 패킷을 고정한다. 다른 디렉터리에서 재현할 때는 --output을 command 앞에 지정하고, 원 실행의 source-audit.json 및 audit_sha256을 그대로 사용한다. 원문과 정답 인용은 artifacts에만 보관한다.",
            "",
        ]
    )
    (RECORDS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    changes = [
        "# 문항별 개선·하락",
        "",
        "원래 정답 기준으로 base20과 비교한다. 나머지 문항은 전체 근거 충족 여부가 같다. 상세 후보·출처 순위와 원문 문맥은 로컬 artifacts에 보관한다.",
        "",
        "| 조건 | 문항 | 변화 | 후보 충족 전→후 | 최종 충족 전→후 | 실패 위치 전→후 |",
        "|---|---|---|---|---|---|",
    ]
    for arm, rows in scores["diffs"].items():
        for row in rows:
            if row["status"] not in ("improved", "regressed"):
                continue
            changes.append(
                f"| {arm} | {row['id']} | {row['status']} | {row['baseline_initial']}→{row['A_initial']} | {row['baseline_complete']}→{row['A_complete']} | {row['baseline_failure_stage']}→{row['A_failure_stage']} |"
            )
    (RECORDS / "changes.md").write_text("\n".join(changes) + "\n", encoding="utf-8")
    public_audit = {k: v for k, v in audit.items() if k != "alternative_amendments"}
    public = {
        "original": scores["original"],
        "audited_sensitivity": scores["audited_sensitivity"],
        "scope_clear_sensitivity": scores["scope_clear_sensitivity"],
        "diffs": scores["diffs"],
        "factor_diffs": scores["factor_diffs"],
        "audit": public_audit,
        "timing_metrics": timing["metrics"],
        "work": work,
        "timing_context_differences": timing_diffs,
        "scope_routes": {
            a: dict(Counter(t["scope"]["view"] for t in ts))
            for a, (ts, _) in saved.items()
        },
        "scope_confirmation_ids": {
            a: [t["id"] for t in ts if t["scope"]["scope_confirmation_required"]]
            for a, (ts, _) in saved.items()
        },
        "page_kind_counts": manifest["page_kind_counts"],
        "manifest_sha256": file_sha(output / "manifest.json"),
        "local_artifacts": str(output.relative_to(ROOT)),
        "quality_unique_rerank_pairs": manifest["rerank_unique_pairs_computed"],
        "replay_differences": manifest["baseline_replay_differences"],
        "reference_fingerprints": read_json(run / "reference-freeze.json"),
    }
    write_json(RECORDS / "results.json", public)
    print("Report saved", flush=True)


def audit(run, output):
    """Freeze the previously inspected ten cases, separately from retrieval inputs."""
    from .kure_followup import check_prepared

    check_prepared(run, output)
    if (output / "source-audit.json").exists():
        raise FileExistsError("Audit is already frozen")
    pages = {key(p): p for p in read_json(CACHE_PATH)}
    alternative = pages["AS046", 72]
    paragraph = next(
        b
        for b in alternative["text"].split("\n\n")
        if "냉동 서비스 조건을 별도 확인" in b
    )
    cases = [
        (
            "official_development:AS-DEV20",
            "retain",
            "양쪽 후보에 있던 특정 사건 기록의 최종 순위 차이. 대체 출처를 추가하지 않음.",
        ),
        (
            "official_additional:AS-ADD05",
            "retain",
            "SI 마감과 수하인/통지처 구분을 각각 요구. 두 번째 근거가 RRF 후보에서 탈락한 차이.",
        ),
        (
            "official_additional:AS-ADD13",
            "retain",
            "동일 사건의 계약·사건·정산 기록이 필요하며 최종 9위 탈락이 차이를 만듦.",
        ),
        (
            "official_additional:AS-ADD27",
            "retain",
            "조건부 연장에 필요한 사건 기록이 9위로 밀림. 후보에서는 양쪽 모두 충족.",
        ),
        (
            "semantic_queries:AS-SEM-14",
            "retain",
            "일반 요율 대상 안내만으로 특정 개정의 20GP 제외를 충족했다고 인정하지 않음.",
        ),
        (
            "semantic_queries:AS-SEM-18",
            "retain",
            "다른 사건의 감액 규정은 적용 범위를 확인할 수 없어 대체 출처로 인정하지 않음.",
        ),
        (
            "semantic_queries:AS-SEM-25",
            "retain",
            "AS011 6쪽은 개별 대장의 배차·반입승인 구분을 제공하지만 일반 반입 절차 전체의 대체 근거로 확정하지 않음.",
        ),
        (
            "semantic_queries:AS-SEM-26",
            "retain",
            "서류 수신/내용 승인 근거만으로 실제 선적 상태 근거까지 충족했다고 인정하지 않음.",
        ),
        (
            "semantic_queries:AS-SEM-32",
            "scope_ambiguous",
            "질문에 터미널·계약 대상이 없는데 정답은 특정 일반 규정의 달력일을 전제함. 다른 계약에는 주말 제외 조건이 존재.",
        ),
        (
            "semantic_queries:AS-SEM-40",
            "alternative_sensitivity",
            "동일 회사·부산–LA 범위의 AS046 72쪽에 냉동 별도 조건이 명시됨. AS017 1쪽의 시간 범위와 함께 사용 가능.",
        ),
    ]
    data = {
        "reviewer": "single assistant self-review; not human or independent review",
        "source": "frozen restored original page cache; no new PDF extraction",
        "source_sha256": file_sha(CACHE_PATH),
        "cases": [{"id": i, "decision": d, "reason": r} for i, d, r in cases],
        "scope_ambiguous_ids": ["semantic_queries:AS-SEM-32"],
        "alternative_amendments": [
            {
                "id": "semantic_queries:AS-SEM-40",
                "requirement_index": 0,
                "evidence": [{"book_id": "AS046", "page": 72, "quote": paragraph}],
            }
        ],
        "original_gold_modified": False,
    }
    write_json(output / "source-audit.json", data)
    manifest = read_json(output / "manifest.json")
    manifest["audit_sha256"] = file_sha(output / "source-audit.json")
    write_json(output / "manifest.json", manifest)
    lines = [
        "# 차이가 난 10문항의 출처 검수",
        "",
        "새 검색 조건의 결과를 보기 전에 고정한 자체 검수다. 고정 복원 원본 캐시를 사용했으며 PDF를 새로 추출하거나 사람·독립 에이전트 검수를 수행하지 않았다. 원래 정답 파일은 유지한다.",
        "",
        "| 문항 | 판정 | 근거 |",
        "|---|---|---|",
    ]
    lines += [f"| {i} | {d} | {r} |" for i, d, r in cases]
    lines += [
        "",
        "AS-SEM-40의 대체 출처는 별도 민감도 채점에만 사용한다. AS-SEM-32 제외 집계도 별도다. 다른 문항의 미등록 대체 출처까지 모두 검수했다고 해석하지 않는다.",
        "",
    ]
    (RECORDS / "source-audit.md").write_text("\n".join(lines), encoding="utf-8")
    print("Frozen ten-case source review", flush=True)
