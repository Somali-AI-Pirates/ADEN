"""Keep established scores and final-context semantic review separately visible."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
import re
import unicodedata
from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
)
from .score import OUT, load, telemetry

RECORDS = Path(__file__).parent
LABELS = {
    "official_all": "과제 공식",
    "categories_positive": "카테고리 근거형 전체",
    "S01": "1. 직접 조회",
    "S02": "2. 번호 없이 사건 식별",
    "S03": "3. 한 사건의 여러 기록",
    "S04": "4. 두 사건 비교",
    "S05": "5. 적용 기록·조건",
    "S06": "6. 모호한 두 후보의 근거",
    "S07": "7. 확정 근거 부족 상태",
    "work_rules_all": "업무 절차·규정",
    "semantic_single": "일상 표현·한 문서",
    "semantic_multi": "일상 표현·여러 문서",
}


POLICY = RECORDS.parents[1] / "evaluation-policy.md"


def validate_manual_review(data, saved, decisions):
    """Reject incomplete, stale or unsupported adjudications before publishing."""
    by_id = {a: {r["id"]: r for r in rows} for a, rows in data.items()}
    failed_ids = {
        rid
        for arm in by_id.values()
        for rid, r in arm.items()
        if r["scored"] and not r["stages"]["final"][r["complete_metric"]]
    }
    expected = {(rid, arm) for rid in failed_ids for arm in by_id}
    manual = {(r["id"], r["arm"]): r for r in decisions}
    if len(manual) != len(decisions) or set(manual) != expected:
        raise ValueError(
            "Every strict miss and its comparison output must be reviewed once"
        )

    def norm(value):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()

    for (rid, arm), decision in manual.items():
        if decision.get("review_complete") is not True or not decision.get("reason"):
            raise ValueError(f"Incomplete adjudication: {arm}/{rid}")
        verdict = decision.get("verdict")
        if verdict not in {"sufficient", "insufficient", "ambiguous_scope"}:
            raise ValueError(f"Unresolved adjudication: {arm}/{rid}")
        if decision.get("sufficient") is not (verdict == "sufficient"):
            raise ValueError(f"Inconsistent verdict: {arm}/{rid}")
        sources = {
            f"{page['book_id']}:p{page['page']:04d}": page["text"]
            for page in saved[arm][rid]["contexts"]["table_rag"]
        }
        hashes = {
            key: sha256(text.encode()).hexdigest() for key, text in sources.items()
        }
        if hashes != decision.get("source_text_hashes"):
            raise ValueError(f"Final source content changed: {arm}/{rid}")
        proofs = decision.get("evidence", [])
        if verdict == "sufficient" and not proofs:
            raise ValueError(f"Sufficient verdict without evidence: {arm}/{rid}")
        for proof in proofs:
            quote = norm(proof["quote"])
            source = sources.get(proof["source_id"])
            if source is None or len(quote) < 8 or quote not in norm(source):
                raise ValueError(f"Quote absent from final evidence: {arm}/{rid}")
    return manual


def main():
    _, saved, _, _ = load()
    data = read_json(RECORDS / "per-question.json")
    by_id = {a: {r["id"]: r for r in rs} for a, rs in data.items()}
    review_run = read_json(OUT / "evidence-review/run.json")
    assert review_run["status"] == "complete"
    review_protocol = OUT / "evidence-review/protocol.json"
    assert review_run["protocol_sha256"] == file_sha(review_protocol)
    assert read_json(review_protocol)["input_sha256"] == file_sha(
        OUT / "review-inputs.json"
    )
    audits = {}
    for name, sha in review_run["files"].items():
        p = OUT / "evidence-review" / name
        assert file_sha(p) == sha
        row = read_json(p)
        audits[(row["id"], row["arm"])] = row
    manual_path = OUT / "manual-review.json"
    manual = validate_manual_review(data, saved, read_json(manual_path))
    if set(audits) != set(manual):
        raise ValueError("Automatic and final review scope differ")
    initial_control = {
        "questions": len(saved["A"]),
        "same_query_and_initial_ranked_pages": sum(
            saved["A"][rid]["searches"][0]["query"]
            == saved["B"][rid]["searches"][0]["query"]
            and [
                (p["book_id"], p["page"])
                for p in saved["A"][rid]["searches"][0]["ranked"]
            ]
            == [
                (p["book_id"], p["page"])
                for p in saved["B"][rid]["searches"][0]["ranked"]
            ]
            for rid in saved["A"]
        ),
    }
    for a, rs in data.items():
        for r in rs:
            if not r["scored"]:
                continue
            key = (r["id"], a)
            strict = r["stages"]["final"][r["complete_metric"]]
            review = audits.get(key)
            result = bool(strict)
            decision = manual.get(key)
            verdict = decision["verdict"] if decision else "strict_pass_not_reviewed"
            if decision:
                result = decision["sufficient"]
            r.update(
                review_complete=int(result),
                strict_complete=int(strict),
                adjudication_verdict=verdict,
                audit_complete=bool(decision),
                ambiguous_scope=verdict == "ambiguous_scope",
                review_verdict=review["review"]["verdict"]
                if review and review["review"]
                else None,
                review_quote_errors=len(review["quote_validation"]) if review else 0,
                assistant_adjudicated=key in manual,
            )
    groups = {}
    original = data["A"]
    groups["official_all"] = [
        r["id"] for r in original if r["suite"].startswith("official_")
    ]
    groups["categories_positive"] = [
        r["id"]
        for r in original
        if not r["suite"].startswith("official_") and r["scored"]
    ]
    for i in range(1, 8):
        groups[f"S{i:02}"] = [r["id"] for r in original if r["category"] == f"S{i:02}"]
    groups["work_rules_all"] = [r["id"] for r in original if r["suite"] == "work_rules"]
    for name, lo, hi in [("semantic_single", 1, 20), ("semantic_multi", 21, 40)]:
        groups[name] = [
            r["id"]
            for r in original
            if r["suite"] == "semantic_queries"
            and lo <= int(r["question_id"].rsplit("-", 1)[-1]) <= hi
        ]
    summary = {}
    for name, ids in groups.items():
        summary[name] = {"n": len(ids)}
        for a in ("A", "B"):
            rows = [by_id[a][rid] for rid in ids]
            summary[name][a] = {
                "strict": sum(r["strict_complete"] for r in rows),
                "reviewed": sum(r["review_complete"] for r in rows),
                "reviewed_insufficient": sum(
                    r["adjudication_verdict"] == "insufficient" for r in rows
                ),
                "ambiguous_scope": sum(r["ambiguous_scope"] for r in rows),
                "audited_outputs": sum(r["audit_complete"] for r in rows),
                "candidate": sum(
                    r["stages"]["available"][r["complete_metric"]] for r in rows
                ),
                "page_recall": sum(r["stages"]["final"]["page_recall"] for r in rows)
                / len(rows),
                "timing": telemetry([saved[a][rid] for rid in ids]),
            }
        summary[name]["reviewed_improvements"] = sum(
            by_id["B"][rid]["review_complete"] > by_id["A"][rid]["review_complete"]
            for rid in ids
        )
        summary[name]["reviewed_regressions"] = sum(
            by_id["B"][rid]["review_complete"] < by_id["A"][rid]["review_complete"]
            for rid in ids
        )
    paired = [
        {
            "id": r["id"],
            "category": r["category"],
            "A": r["review_complete"],
            "B": by_id["B"][r["id"]]["review_complete"],
        }
        for r in original
        if r["scored"]
        and r["review_complete"] != by_id["B"][r["id"]]["review_complete"]
    ]
    review_usage = {
        "calls": len(audits),
        "errors": sum(r["telemetry"]["status"] != "complete" for r in audits.values()),
        "tokens": {
            k: sum(r["telemetry"].get(k, 0) for r in audits.values())
            for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        },
    }
    corrections = [
        {
            "id": row["id"],
            "arm": arm,
            "strict": row["strict_complete"],
            "reviewed": row["review_complete"],
            "verdict": row["adjudication_verdict"],
            "reason": manual[(row["id"], arm)]["reason"],
        }
        for arm, rows in data.items()
        for row in rows
        if row["scored"] and row["strict_complete"] != row["review_complete"]
    ]
    write_json(RECORDS / "correction-changes.json", corrections)
    write_json(RECORDS / "reviewed-per-question.json", data)
    write_json(RECORDS / "reviewed-summary.json", summary)
    write_json(RECORDS / "reviewed-differences.json", paired)
    write_json(RECORDS / "review-usage.json", review_usage)
    write_json(
        RECORDS / "adjudications.json",
        [
            {
                k: r[k]
                for k in (
                    "id",
                    "arm",
                    "verdict",
                    "sufficient",
                    "classification",
                    "reason",
                )
            }
            for r in manual.values()
        ],
    )
    write_json(
        RECORDS / "audit-manifest.json",
        {
            "review_sha256": file_sha(OUT / "evidence-review/run.json"),
            "manual_sha256": file_sha(manual_path),
            "policy_sha256": file_sha(POLICY),
            "finalizer_sha256": file_sha(__file__),
            "strict_scores_sha256": file_sha(RECORDS / "per-question.json"),
            "required_review_outputs": len(manual),
            "completed_review_outputs": sum(
                r["review_complete"] for r in manual.values()
            ),
            "initial_search_control": initial_control,
            "quote_and_source_hash_checks_passed": True,
            "independent_human_review": False,
            "answer_generation": False,
            "semantic_review_scope": "Both outputs of every question with a strict miss; other strict passes inherited, not independently semantically audited",
        },
    )
    lines = [
        "# KURE-v3 공식58·카테고리220 A/B",
        "",
        "A는 기존 TableRAG 질문 계획·병합, B는 모호한 대상 확인 지침과 관측한 선택 출처의 보존을 더한 흐름이다. 모델은 모두 KURE-v3·KURE nano, 전체 코드 BM25와 벡터5:5다. 검색별 후보20쪽·최종 후보20쪽·최종8쪽·8,000토큰을 유지했다. 이번 실험에서 리랭커 또는 본문 관측 범위를 바꾸지 않았다. 현재 기본 설정도 변경하지 않았다.",
        "",
        "## 평가 범위",
        "",
        "기존 공식60 중 참고 근거가 없는 AS-DEV12·AS-ADD12를 제외한58개와 카테고리220개를 원문 질문 그대로 실행했다. 카테고리는 근거형200개와 자료 범위 밖20개로 나뉜다. 대상이 모호한6번도 두 후보 사건의 근거 확보를 요구하므로 근거형에 포함했다. 새로운 미사용 검증 세트가 아니다.",
        "",
        "지정 근거는 기존 채점기를 유지한다. 공식은 book_id+page의 필수 근거 그룹, 카테고리는 필수 인용과 미리 등록된 대체 근거를 평가한다. 보조 검수는 두 조건 중 하나라도 실패한 문항의 양쪽 최종 문맥을 대상으로 한다. 반환된 다른 원문으로도 질문의 모든 요구를 충족하는지 AI 검수·인용 대조·자체 검토로 확인했다. 검색되지 않은 원문을 반환 근거처럼 인정하지 않았다. 실제 답변 정확도와 사람 검수 완료를 의미하지 않는다.",
        "",
        "## 최종8쪽 전체 근거 충족",
        "",
        "| 묶음 | 문항 | 지정 A | 지정 B | 검수 반영 A | 검수 반영 B | 검수 개선/하락 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    def cell(v, n):
        return f"{v}/{n} ({100 * v / n:.1f}%)"

    for name, s in summary.items():
        n = s["n"]
        a = s["A"]
        b = s["B"]
        lines.append(
            f"| {LABELS[name]} | {n} | {cell(a['strict'], n)} | {cell(b['strict'], n)} | {cell(a['reviewed'], n)} | {cell(b['reviewed'], n)} | +{s['reviewed_improvements']}/−{s['reviewed_regressions']} |"
        )
    lines += [
        "",
        "## 보정 후 남은 항목",
        "",
        "적용 범위가 모호한 문항도 분모에 남기고 성공으로 세지 않았다. 일반 검색 누락과 구분한다. 답변을 생성하지 않았으므로 아래는 실제 답변 정오가 아니라 반환 근거의 판정이다.",
        "",
        "| 문항 ID | 조건 | 판정 | 남은 문제 |",
        "|---|---|---|---|",
    ]
    for rid in sorted({r["id"] for r in manual.values() if not r["sufficient"]}):
        rows = [r for r in manual.values() if r["id"] == rid and not r["sufficient"]]
        verdict = (
            "적용 범위 모호" if rows[0]["verdict"] == "ambiguous_scope" else "근거 부족"
        )
        lines.append(
            f"| {rid} | {', '.join(r['arm'] for r in rows)} | {verdict} | {rows[0]['reason']} |"
        )
    lines += [
        "",
        "## 후보 확보와 페이지 Recall",
        "",
        "| 묶음 | 후보20 지정 A | 후보20 지정 B | 최종 페이지 Recall A | 최종 페이지 Recall B |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, s in summary.items():
        a = s["A"]
        b = s["B"]
        n = s["n"]
        lines.append(
            f"| {LABELS[name]} | {a['candidate']}/{n} | {b['candidate']}/{n} | {a['page_recall']:.1%} | {b['page_recall']:.1%} |"
        )
    lines += [
        "",
        "공식·카테고리의 지정 근거 정의가 다르므로 전체를 한 점수로만 해석하지 않는다. 후보20은 여러 검색/SQL을 합친 뒤의 최종 후보 집합이다. 각 검색의 후보도 따로 저장했으며 검색 전체 합집합을20개 후보라고 부르지 않는다.",
        "",
        "## 검색 지연·호출",
        "",
        "| 묶음 | A p50/p95초 | B p50/p95초 | A/B 계획 호출 | A/B 검색 호출 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("official_all", "categories_positive"):
        s = summary[name]
        a = s["A"]["timing"]
        b = s["B"]["timing"]
        lines.append(
            f"| {LABELS[name]} | {a['retrieval_p50']:.2f}/{a['retrieval_p95']:.2f} | {b['retrieval_p50']:.2f}/{b['retrieval_p95']:.2f} | {a['planner_calls']}/{b['planner_calls']} | {a['search_calls']}/{b['search_calls']} |"
        )
    lines += [
        "",
        "동시4요청, CPU 임베딩·GPU 리랭커에서 질문 임베딩부터 검색·SQL·Luna 계획·문맥 조립까지 포함한 관측값이다. 모델 로딩·답변 생성·사후 채점을 제외했다. 두 조건은 각각 새 계획을 호출했으므로 같은 검색 결과의 순위만 비교한 것이 아니다. 단회 실행이며 API 변동과 공유 장치 부하의 영향을 받는다.",
        "",
        "## 범위 밖 대조·같은 검색 재생",
        "",
    ]
    for a in ("A", "B"):
        neg = [r for r in saved[a].values() if r["split"] == "negative_control"]
        lines.append(
            f"- {a} 범위 밖20문항의 계획 종료 상태: {dict(Counter(r['finish']['status'] for r in neg))}. 사용자 답변을 생성하지 않았으므로 거절 답변 정확도로 해석하지 않는다."
        )
    replay = read_json(RECORDS / "same-search-replay.json")
    lines += [
        "",
        f"A 검색·SQL·출처 선택을 고정한 재생: 기존 문맥 {len(replay)}/{len(replay)}개 재현. 선택했으나 빠진 관측 출처 {sum(len(r['old_not_retained']) for r in replay)}→{sum(len(r['new_not_retained']) for r in replay)}건. 지정 근거 전체 충족 개선 {sum(r.get('new_complete', 0) > r.get('old_complete', 0) for r in replay)}개, 하락 {sum(r.get('new_complete', 0) < r.get('old_complete', 0) for r in replay)}개. 최종 페이지 집합 변경 {sum(r['changed'] for r in replay)}개.",
        "",
        f"사후 모델 검수 {review_usage['calls']}회 중 {review_usage['errors']}회가 시간 초과였다. 재호출하지 않고 시간 초과를 포함한 {len(manual)}개 출력을 모두 자체 검수했다. 검수 호출·토큰은 검색 호출과 분리했으며 토큰 수는 응답에서 확인된 사용량만 포함한다. 모든 지정 통과 문항을 의미 검수한 것은 아니며 질문/참고답의 모호성은 별도 기록한다.",
        "",
        f"최초 원질문과 검색 순위는 {initial_control['same_query_and_initial_ranked_pages']}/{initial_control['questions']}문항에서 A/B가 동일했다. 후속 계획·검색은 별도로 실행했다.",
        "",
        "[개인 평가 기본 절차](../../evaluation-policy.md)에 따라 지정 실패와 비교 상대의 검수를 모두 마친 뒤 보고했다. 검수 누락, 최종 원문 지문 불일치, 반환 문맥에 없는 인용이 있으면 finalize가 보고서 생성을 중단한다. 기존 지정 점수와 페이지 Recall은 보정해 덮어쓰지 않는다.",
        "",
        "## 기록과 재현",
        "",
        "[문항별 지정 점수](per-question.json) · [검수 반영 점수](reviewed-per-question.json) · [보정 전후 변경 사유](correction-changes.json) · [최종 판단](conclusion.md) · [지정 개선·하락 흐름](difference-flows.json) · [검수 개선·하락](reviewed-differences.json) · [같은 검색 재생](same-search-replay.json) · [입력 지문](dataset-manifest.json) · [검사](verification.md)",
        "",
        "원문·질문·참고답·검수 인용은 artifacts/u_10o0o/advanced-table-rag-official-category-20261111/v1에 보관한다. 공유 기록에는 인용 원문을 복사하지 않는다.",
        "",
        "```bash",
    ]
    base = ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-official-category-20261111."
    lines += [
        base + "run_benchmark --help",
        base + "score",
        base + "replay",
        base + "finalize",
        "```",
        "",
        "실제 재실행은 새 출력 경로에서 질문·코드·입력 지문을 고정한 뒤 freeze/run을 수행한다. 완료된 경로는 덮어쓰지 않는다.",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines) + "\n")
    print(
        {
            g: {"n": s["n"], "A": s["A"]["reviewed"], "B": s["B"]["reviewed"]}
            for g, s in summary.items()
        }
    )


if __name__ == "__main__":
    main()
