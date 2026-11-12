"""Require evidence review before publishing v2/v3 comparison tables."""

from collections import Counter
from hashlib import sha256
import re
import unicodedata
import numpy as np
from .score import OUT, RECORDS, ARMS, load
from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
)
from experimental.u_10o0o.table_rag.retrieval import json_payload

LABELS = {
    "official": "공식58",
    "categories": "카테고리 근거형200",
    **{f"S{i:02}": f"상황{i}" for i in range(1, 8)},
    "work_rules": "업무 절차·규정",
    "semantic_single": "일상 표현·한 문서",
    "semantic_multi": "일상 표현·여러 문서",
    "normal": "동료 일반85",
    "normal_without_known_ambiguity": "동료 일반·기존 모호9개 제외",
    "false_premise": "거짓 전제85",
    "false_repaired": "거짓 전제·식별/가정 보충46",
    "false_unchanged": "거짓 전제·질문 유지39",
    "clarification": "원래 모호한 질문43",
}


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def source_texts(row):
    return {
        **{
            f"{p['book_id']}:p{p['page']:04d}": p["text"]
            for p in row["contexts"]["table_rag"]
        },
        **{f"SQL{i}": json_payload(s) for i, s in enumerate(row["sql_results"])},
    }


def main():
    qs, saved, meta, refs = load()
    rows = read_json(RECORDS / "per-question.json")
    required = {
        (r["id"], r["arm"]) for r in read_json(RECORDS / "required-reviews.json")
    }
    run = read_json(OUT / "evidence-review/run.json")
    assert run["status"] == "complete"
    assert run["protocol_sha256"] == file_sha(OUT / "evidence-review/protocol.json")
    assert read_json(OUT / "evidence-review/protocol.json")["input_sha256"] == file_sha(
        OUT / "review-inputs.json"
    )
    audits = {}
    for name, digest in run["files"].items():
        p = OUT / "evidence-review" / name
        assert file_sha(p) == digest
        r = read_json(p)
        audits[(r["id"], r["arm"])] = {"audit": r, "reused": False}
    for r in read_json(OUT / "reused-reviews.json"):
        audits[(r["id"], r["arm"])] = {
            "audit": r["audit"],
            "reused": True,
            "origin": r["origin"],
        }
    assert set(audits) == required
    manual_path = OUT / "manual-review.json"
    manual_rows = read_json(manual_path) if manual_path.exists() else []
    manual = {(r["id"], r["arm"]): r for r in manual_rows}
    assert len(manual) == len(manual_rows) and set(manual) <= required
    decisions = {}
    pending = []
    for key, wrapped in audits.items():
        rid, arm = key
        audit = wrapped["audit"]
        override = manual.get(key) or audit.get("prior_adjudication")
        context = source_texts(saved[arm][rid])
        page_hashes = {
            k: sha256(v.encode()).hexdigest()
            for k, v in context.items()
            if not k.startswith("SQL")
        }
        if override:
            if override.get("source_text_hashes"):
                assert override["source_text_hashes"] == page_hashes, key
            if key in manual:
                assert override.get("review_complete") is True, key
                assert override.get("reason"), key
                assert override.get("source_text_hashes") == page_hashes, key
                assert override.get("verdict") in {
                    "sufficient",
                    "insufficient",
                    "ambiguous_scope",
                }, key
                assert override["sufficient"] is (
                    override["verdict"] == "sufficient"
                ), key
                proofs = override.get("evidence", [])
                if override["sufficient"]:
                    assert proofs, key
                for proof in proofs:
                    assert proof["source_id"] in context, key
                    assert len(norm(proof["quote"])) >= 8 and norm(
                        proof["quote"]
                    ) in norm(context[proof["source_id"]]), key
            verdict = override.get("verdict") or (
                "sufficient" if override["sufficient"] else "insufficient"
            )
            decision = {
                "sufficient": bool(override["sufficient"]),
                "verdict": verdict,
                "method": "assistant_adjudication",
                "reason": override["reason"]
                if key in manual
                else "동일 질문·동일 출력에 대한 기존 자체 검수 판정을 재사용했다. 상세 사유와 인용은 원래 로컬 검수 기록에 보존한다.",
                "reused": wrapped["reused"] and key not in manual,
            }
        elif (
            audit.get("eligible_for_assistant_review")
            and audit["review"]["verdict"] == "sufficient"
        ):
            review = audit["review"]
            for claim in review["claims"]:
                assert claim["status"] == "supported" and claim["evidence"], key
                for proof in claim["evidence"]:
                    assert proof["source_id"] in context, key
                    assert len(norm(proof["quote"])) >= 8 and norm(
                        proof["quote"]
                    ) in norm(context[proof["source_id"]]), key
            decision = {
                "sufficient": True,
                "verdict": "sufficient",
                "method": "model_review_with_quote_check",
                "reason": "전체 필수 사실을 지원한다는 모델 판정과 최종 반환 출처의 인용 존재를 확인했다. 상세 인용은 로컬 검수 기록에 보존한다.",
                "reused": wrapped["reused"],
            }
        else:
            pending.append(
                {
                    "id": rid,
                    "arm": arm,
                    "cohort": meta[rid]["cohort"],
                    "reused": wrapped["reused"],
                    "reason": audit.get("review"),
                    "telemetry": audit.get("telemetry"),
                    "quote_validation": audit.get("quote_validation"),
                }
            )
            continue
        decisions[key] = decision
    for key in required:
        if (
            key[0] == "semantic_queries:AS-SEM-32"
            and key[1].startswith("v2_")
            and key not in manual
        ):
            if not any((r["id"], r["arm"]) == key for r in pending):
                pending.append(
                    {
                        "id": key[0],
                        "arm": key[1],
                        "cohort": "core",
                        "reason": "Known contract-scope ambiguity requires explicit adjudication",
                        "reused": False,
                    }
                )
    write_json(OUT / "adjudication-pending.json", pending)
    if pending:
        print("Assistant adjudication pending:", len(pending))
        return
    for row in rows:
        for arm, d in row["arms"].items():
            audit = saved[arm][row["id"]].get("selection_audit", {})
            d["selected_unobserved"] = audit.get("unobserved", [])
            d["observed_not_retained"] = [
                source for source in d["selected_not_retained"]
                if source not in d["selected_unobserved"]
            ]
            if not row["scored"]:
                continue
            dec = decisions.get((row["id"], arm))
            d.update(
                reviewed=int(dec["sufficient"]) if dec else d["strict"],
                verdict=dec["verdict"] if dec else "strict_pass_not_reviewed",
                reviewed_output=bool(dec),
            )
    groups = {
        "official": [
            r
            for r in rows
            if r["cohort"] == "core" and r["kind"].startswith("official_")
        ],
        "categories": [
            r
            for r in rows
            if r["cohort"] == "core"
            and r["scored"]
            and not r["kind"].startswith("official_")
        ],
        **{
            f"S{i:02}": [r for r in rows if r["category"] == f"S{i:02}"]
            for i in range(1, 8)
        },
        "work_rules": [r for r in rows if r["kind"] == "work_rules"],
        "semantic_single": [
            r
            for r in rows
            if r["kind"] == "semantic_queries" and int(r["id"].rsplit("-", 1)[-1]) <= 20
        ],
        "semantic_multi": [
            r
            for r in rows
            if r["kind"] == "semantic_queries" and int(r["id"].rsplit("-", 1)[-1]) > 20
        ],
        "normal": [r for r in rows if r["kind"] == "normal"],
        "normal_without_known_ambiguity": [
            r for r in rows if r["kind"] == "normal" and not r["prior_ambiguous_normal"]
        ],
        "false_premise": [r for r in rows if r["kind"] == "false_premise"],
        "false_repaired": [
            r for r in rows if r["kind"] == "false_premise" and r["repaired"]
        ],
        "false_unchanged": [
            r for r in rows if r["kind"] == "false_premise" and not r["repaired"]
        ],
        "clarification": [r for r in rows if r["kind"] == "clarification"],
    }
    summary = {}
    changes = []
    for name, rs in groups.items():
        summary[name] = {"n": len(rs), "arms": {}}
        for arm in ARMS:
            ds = [r["arms"][arm] for r in rs]
            s = {
                "p50": float(np.percentile([d["seconds"] for d in ds], 50)),
                "p95": float(np.percentile([d["seconds"] for d in ds], 95)),
                "calls": sum(d["api_calls"] for d in ds),
                "api_failures": sum(d["api_failures"] for d in ds),
                "search_calls": sum(d["search_calls"] for d in ds),
                "selected_source_losses": sum(
                    len(d["selected_not_retained"]) for d in ds
                ),
                "observed_source_losses": sum(len(d["observed_not_retained"]) for d in ds),
                "selected_unobserved": sum(len(d["selected_unobserved"]) for d in ds),
                "status": dict(Counter(d["status"] for d in ds)),
            }
            if name == "clarification":
                s.update(
                    clarifications=sum(d["correct_clarification"] for d in ds),
                    overconfirmations=sum(d["overconfirmation"] for d in ds),
                )
            else:
                s.update(
                    strict=sum(d["strict"] for d in ds),
                    reviewed=sum(d["reviewed"] for d in ds),
                    candidate=sum(d["candidate"] for d in ds),
                    page_recall=float(np.mean([d["page_recall"] for d in ds])),
                    verdicts=dict(Counter(d["verdict"] for d in ds)),
                )
            summary[name]["arms"][arm] = s
    for r in rows:
        metric = (
            "reviewed"
            if r["scored"]
            else "correct_clarification"
            if r["kind"] == "clarification"
            else None
        )
        if metric:
            a, b = [r["arms"][arm][metric] for arm in ("v2_A", "v2_B")]
            if a != b:
                changes.append(
                    {
                        "id": r["id"],
                        "category": r["category"],
                        "metric": metric,
                        "A": a,
                        "B": b,
                        "A_sources": r["arms"]["v2_A"]["pages"],
                        "B_sources": r["arms"]["v2_B"]["pages"],
                    }
                )
    write_json(RECORDS / "reviewed-per-question.json", rows)
    write_json(RECORDS / "summary.json", summary)
    write_json(RECORDS / "differences.json", changes)
    write_json(
        RECORDS / "adjudications.json",
        [{"id": rid, "arm": arm, **d} for (rid, arm), d in decisions.items()],
    )
    write_json(
        RECORDS / "audit-manifest.json",
        {
            "required": len(required),
            "completed": len(decisions),
            "manual_sha256": file_sha(manual_path) if manual_path.exists() else None,
            "review_run_sha256": file_sha(OUT / "evidence-review/run.json"),
            "policy_sha256": file_sha(RECORDS.parents[1] / "evaluation-policy.md"),
            "independent_human_review": False,
            "answer_generation": False,
        },
    )
    lines = [
        "# KURE-v2 추가 확인·근거 보존 비교",
        "",
        "v2 A는 기존 정책, v2 B는 추가 확인 지침과 관측한 선택 출처 보존이다. 공식·카테고리의 v2 A는 과거 출력을 재사용했고 추가213문항의 v2 A/B는 새로 실행했다. v3 A/B는 이전 동일 질문 실행이다. 모두 최종8쪽·8,000토큰, 검색별/최종 후보20쪽이다. v2는 질문128·리랭커1024·GPU MaxSim, v3는2048/2048·CPU 질문 인코딩이다. 구성과 실행 시점 차이가 있어 순수 모델 효과나 안정적인 지연 우위로 해석하지 않는다.",
        "",
        "[결론과 이전 수치 정정](conclusion.md) · [문항별 변화 해설](question-changes.md)",
        "",
        "## 보정 후 전체 근거 충족",
        "",
        "| 유형 | n | v2 A | v2 B | v3 A | v3 B |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, s in summary.items():
        if name == "clarification":
            continue
        n = s["n"]
        cells = [
            f"{s['arms'][a]['reviewed']}/{n} ({s['arms'][a]['reviewed'] / n:.1%})"
            for a in ARMS
        ]
        lines.append("| " + LABELS[name] + f" | {n} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "카테고리220 중 범위 밖20개는 근거 검색률에서 분리했다. 기존 모호성 제외76개는 보조 통계이며 전체85개도 그대로 제시했다. 새로 확인한 모호 문항은 분모에서 임의로 빼지 않는다. 거짓 전제46개 보정 사본과 원래43개 질문은 서로 다른 목적의 세트다.",
        "",
        "## 지정 근거 점수와 페이지 Recall",
        "",
        "| 유형 | v2 A 지정 / Recall | v2 B 지정 / Recall | v3 A 지정 / Recall | v3 B 지정 / Recall |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("official", "categories", "normal", "false_premise"):
        s = summary[name]
        lines.append(
            "| "
            + LABELS[name]
            + " | "
            + " | ".join(
                f"{s['arms'][a]['strict']}/{s['n']} / {s['arms'][a]['page_recall']:.1%}"
                for a in ARMS
            )
            + " |"
        )
    lines += [
        "",
        "## 모호성 종료와 선택 출처",
        "",
        "| 조건 | 추가 확인 종료 /43 | 충분 판정 /43 | 관측·선택 출처 유실 / 미관측 선택 |",
        "|---|---:|---:|---:|",
    ]
    for a in ARMS:
        d = summary["clarification"]["arms"][a]
        lines.append(
            f"| {a} | {d['clarifications']} | {d['overconfirmations']} | {sum(len(r['arms'][a]['observed_not_retained']) for r in rows)} / {sum(len(r['arms'][a]['selected_unobserved']) for r in rows)} |"
        )
    lines += [
        "",
        "v2 B에서 미관측 출처 선택 1건은 AS-SEM-32다. 실제 검색에서 관측하지 않은 출처이므로 보존하지 않았다. 유실과 구분한다.",
        "",
        "추가 확인은 계획자의 종료 상태다. 실제 사용자 답변의 정오나 되묻기 표현 품질은 측정하지 않았다. 보존 대상은 실제 관측하고 선택한 출처이므로 필요한 출처 자체를 찾거나 선택하지 못한 문제는 남는다.",
        "",
        "## 지연과 호출",
        "",
        "| 유형 | 조건 | 검색 p50/p95초 | 계획 호출 | 검색 호출 | API 오류 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name in ("official", "categories", "normal", "false_premise", "clarification"):
        for a in ARMS:
            s = summary[name]["arms"][a]
            lines.append(
                f"| {LABELS[name]} | {a} | {s['p50']:.2f}/{s['p95']:.2f} | {s['calls']} | {s['search_calls']} | {s['api_failures']} |"
            )
    lines += [
        "",
        "동시4요청, 단회 관측이다. 모델 준비·답변 생성·사후 검수는 검색 시간에 포함하지 않았다. 과거 출력 재사용과 새로운 실행의 장치/API 부하가 다르다.",
        "",
        "## 검수와 재현",
        "",
        f"필수 검수{len(required)}개를 완료했다. 지정 실패와 비교 상대를 대칭적으로 확인했고 거짓 전제형은 지정 점수와 관계없이 전부 검수했다. 이전 검수는 같은 질문·같은 최종 출력일 때만 재사용했다. 자동 충분 판정은 실제 반환 출처의 인용을 검사했고 미충족·오류·모호 판정은 자체 재확인했다. 독립 사람 검수와 실제 답변 정확도를 의미하지 않는다.",
        "",
        "[문항별 결과](reviewed-per-question.json) · [v2 A/B 개선·하락](differences.json) · [판정 근거](adjudications.json) · [같은 검색 재생](same-search-replay.json) · [검증](verification.md) · [과정](work-log.md)",
        "",
        "원문·질문·정답·검수 인용은 로컬 artifacts/u_10o0o/advanced-table-rag-kure-v2-preservation-20261111/v2에 보관한다. 기존 기준선·원본 데이터셋·운영 API는 변경하지 않았다.",
    ]
    replay = read_json(RECORDS / "same-search-replay.json")
    lines += [
        "",
        "## 같은 A 검색으로 출처 보존만 재생",
        "",
        "| 묶음 | 재현 문항 | 관측·선택 출처 유실 전→후 | 지정 근거 개선/하락 |",
        "|---|---:|---:|---:|",
    ]
    for cohort in ("core", "peer"):
        rr = [r for r in replay if r["cohort"] == cohort]
        lines.append(
            f"| {cohort} | {len(rr)} | {sum(len(r['old_not_retained']) for r in rr)}→{sum(len(r['new_not_retained']) for r in rr)} | +{sum(r.get('new_complete', 0) > r.get('old_complete', 0) for r in rr)}/−{sum(r.get('new_complete', 0) < r.get('old_complete', 0) for r in rr)} |"
        )
    lines += [
        "",
        "이 재생은 질문 계획과 검색 결과를 고정하고 문맥 배치만 비교한다. 출처 유실 감소가 모든 요청 사실의 의미상 충족 개선을 자동으로 보장하지 않는다.",
    ]
    fresh = [
        r for a in ("v2_A", "v2_B") for r in saved[a].values() if not r["historical"]
    ]
    fresh_apis = [t["api"] for r in fresh for t in r["turns"]]
    new_audits = [v["audit"]["telemetry"] for v in audits.values() if not v["reused"]]
    usage = {
        "new_retrievals": len(fresh),
        "new_planner_calls": len(fresh_apis),
        "new_planner_errors": sum(a["status"] != "complete" for a in fresh_apis),
        "new_review_calls": len(new_audits),
        "new_review_errors": sum(a["status"] != "complete" for a in new_audits),
        "reused_reviews": sum(v["reused"] for v in audits.values()),
        "known_usage": {
            stage: {
                k: sum(a.get(k, 0) for a in aa)
                for k in ("input_tokens", "output_tokens", "reasoning_tokens")
            }
            for stage, aa in [("retrieval", fresh_apis), ("review", new_audits)]
        },
    }
    write_json(RECORDS / "usage.json", usage)
    lines += [
        "",
        f"새 검색 {usage['new_retrievals']}개, 새 계획 호출 {usage['new_planner_calls']}회(오류 {usage['new_planner_errors']}회), 새 검수 {usage['new_review_calls']}회(오류 {usage['new_review_errors']}회), 이전 검수 재사용 {usage['reused_reviews']}개다. 인용 오류와 불확실한 판정은 원문 자체 검수로 마무리했고 자동 재호출하지 않았다. 토큰은 응답에서 확인된 사용량만 합산했다.",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines) + "\n")
    print(
        {
            g: {
                a: s["arms"][a].get("reviewed", s["arms"][a].get("clarifications"))
                for a in ARMS
            }
            for g, s in summary.items()
        }
    )


if __name__ == "__main__":
    main()
