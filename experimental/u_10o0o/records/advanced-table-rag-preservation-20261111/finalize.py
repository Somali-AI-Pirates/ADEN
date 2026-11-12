"""Report source-checked AI review separately from strict page recall."""

from collections import Counter
from hashlib import sha256
from pathlib import Path

import numpy as np

from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from .score import OUT, load

RECORDS = Path(__file__).parent


def main():
    _, saved, _, _ = load()
    review_run = read_json(OUT / "evidence-review/run.json")
    assert review_run["status"] == "complete"
    audits = {}
    for name, sha in review_run["files"].items():
        p = OUT / "evidence-review" / name
        assert file_sha(p) == sha
        r = read_json(p)
        audits[(r["id"], r["arm"])] = r
    manual_path = OUT / "manual-review.json"
    manual = (
        {(r["id"], r["arm"]): r for r in read_json(manual_path)}
        if manual_path.exists()
        else {}
    )
    rows = read_json(RECORDS / "per-question.json")
    for row in rows:
        for arm, a in row["arms"].items():
            if row["kind"] == "clarification":
                continue
            review = audits.get((row["id"], arm))
            a["review_verdict"] = (
                review["review"]["verdict"] if review and review["review"] else None
            )
            a["quote_validation_errors"] = (
                len(review["quote_validation"]) if review else 0
            )
            sufficient = bool(
                review
                and review["eligible_for_assistant_review"]
                and review["review"]["verdict"] == "sufficient"
            )
            if (row["id"], arm) in manual:
                adjudication = manual[(row["id"], arm)]
                if adjudication["sufficient"]:
                    actual_hashes = {
                        f"{p['book_id']}:p{p['page']:04d}": sha256(
                            p["text"].encode()
                        ).hexdigest()
                        for p in saved[arm][row["id"]]["contexts"]["table_rag"]
                    }
                    assert actual_hashes == adjudication["source_text_hashes"]
                sufficient = adjudication["sufficient"]
            a["review_supported"] = int(sufficient)
            a["evidence_complete"] = int(
                sufficient
                or (row["kind"] == "normal" and a["metrics"]["final"]["all_groups"])
            )
            if (row["id"], arm) in manual:
                a["evidence_complete"] = int(manual[(row["id"], arm)]["sufficient"])
                a["assistant_adjudicated"] = True
    write_json(RECORDS / "reviewed-per-question.json", rows)
    groups = {
        "normal_all": [r for r in rows if r["kind"] == "normal"],
        "normal_without_known_ambiguity": [
            r for r in rows if r["kind"] == "normal" and not r["prior_ambiguous_normal"]
        ],
        "false_all": [r for r in rows if r["kind"] == "false_premise"],
        "false_repaired": [
            r for r in rows if r["kind"] == "false_premise" and r["repaired"]
        ],
        "false_unchanged": [
            r for r in rows if r["kind"] == "false_premise" and not r["repaired"]
        ],
        "clarification": [r for r in rows if r["kind"] == "clarification"],
    }
    groups["false_both_api_ok"] = [
        r
        for r in groups["false_all"]
        if not any(a["api_failures"] for a in r["arms"].values())
    ]
    summary = {}
    for name, group in groups.items():
        summary[name] = {"n": len(group), "arms": {}}
        for arm in ("A", "B"):
            data = [r["arms"][arm] for r in group]
            s = {
                "status": dict(Counter(a["status"] for a in data)),
                "p50_seconds": float(np.percentile([a["seconds"] for a in data], 50)),
                "p95_seconds": float(np.percentile([a["seconds"] for a in data], 95)),
                "api_calls": sum(a["api_calls"] for a in data),
                "api_failures": sum(a["api_failures"] for a in data),
                "search_calls": sum(a["search_calls"] for a in data),
                "selected_not_retained_questions": sum(
                    bool(a["selected_not_retained"]) for a in data
                ),
            }
            if name == "clarification":
                s.update(
                    correct_clarification=sum(a["correct_clarification"] for a in data),
                    overconfirmation=sum(a["overconfirmation"] for a in data),
                )
            else:
                s.update(
                    evidence_complete=sum(a["evidence_complete"] for a in data),
                    strict_complete=sum(
                        a["metrics"]["final"]["all_groups"] for a in data
                    ),
                    candidate_complete=sum(
                        a["metrics"]["candidate"]["all_groups"] for a in data
                    ),
                    final_page_recall=float(
                        np.mean([a["metrics"]["final"]["page_recall"] for a in data])
                    ),
                    review_verdicts=dict(
                        Counter(a["review_verdict"] or "not_reviewed" for a in data)
                    ),
                )
            summary[name]["arms"][arm] = s
    write_json(RECORDS / "summary.json", summary)
    diffs = []
    for r in rows:
        metric = (
            "correct_clarification"
            if r["kind"] == "clarification"
            else "evidence_complete"
        )
        a, b = r["arms"]["A"][metric], r["arms"]["B"][metric]
        if a != b:
            diffs.append(
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "repaired": r["repaired"],
                    "A": a,
                    "B": b,
                    "A_pages": r["arms"]["A"].get("pages"),
                    "B_pages": r["arms"]["B"].get("pages"),
                }
            )
    write_json(RECORDS / "differences.json", diffs)
    replay = read_json(RECORDS / "same-search-replay.json")
    lines = [
        "# KURE-v3 추가 확인·근거 보존 A/B",
        "",
        "두 조건 모두 KURE-v3·KURE nano·전체 코드 BM25 5:5다. KURE-v2는 실행하지 않았다. A는 기존 계획/병합, B는 대상 확인 지침과 관측한 최종 선택 출처의 보존을 추가했다.",
        "",
        "## 평가 범위",
        "",
        "동료 다문서170문항(일반85·거짓 전제85) 중 거짓 전제46개에 식별자나 계산 가정을 보충한 별도 사본을 사용했다. 원래 모호한43개는 추가 확인 대조군으로 별도 평가했다. 원본 데이터셋은 변경하지 않았다. 질문 작성에 원래 일반형·참고답·원본을 사용했으므로 미사용 검증 세트가 아니다. 대상 코드가 추가되어 쉬워진 효과를 검색기 개선으로 해석하지 않는다.",
        "",
        "## 근거 충족",
        "",
        "거짓 전제형은 지정 페이지 통과 여부와 무관하게 모든 최종 문맥을 AI 보조 검수하고 인용이 실제 문맥에 있는지 검사했다. 자동 검수의 인용 오류·자기모순·참고답 충돌은 실제 최종 원문을 다시 대조한 뒤 별도 보정 기록으로 처리했다. 해결되지 않은 불확실성은 통과시키지 않았다. 일반형은 지정 근거 통과에 검수로 인정한 대체 근거를 더했다. 두 종류의 검수 범위가 다르며 실제 답변 정확도가 아니다. 독립 사람 검수는 수행하지 않았다.",
        "",
        "| 유형 | 문항 | A 지정 근거 | B 지정 근거 | A 근거 충족 | B 근거 충족 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "normal_all": "일반형 전체",
        "normal_without_known_ambiguity": "일반형 기존 모호성 제외",
        "false_all": "거짓 전제 전체",
        "false_both_api_ok": "거짓 전제 양쪽 API 정상 완료",
        "false_repaired": "거짓 전제 보정 사본",
        "false_unchanged": "거짓 전제 질문 유지",
    }
    for key, label in labels.items():
        g = summary[key]
        n = g["n"]
        a, b = g["arms"]["A"], g["arms"]["B"]
        lines.append(
            f"| {label} | {n} | {a['strict_complete']} | {b['strict_complete']} | {a['evidence_complete']}/{n} ({a['evidence_complete'] / n:.1%}) | {b['evidence_complete']}/{n} ({b['evidence_complete'] / n:.1%}) |"
        )
    g = summary["clarification"]
    a, b = g["arms"]["A"], g["arms"]["B"]
    lines += [
        "",
        "## 모호성 대조군43개",
        "",
        f"추가 확인 종료: A {a['correct_clarification']}/43, B {b['correct_clarification']}/43. 충분하다고 종료한 건: A {a['overconfirmation']}, B {b['overconfirmation']}. 이 상태는 계획자의 종료 판정이며 사용자용 답변을 생성한 결과가 아니다.",
        "",
        "## 같은 검색 결과 재생",
        "",
        f"A의 저장된 검색·선택·SQL로 기존 최종 문맥 {len(replay)}/{len(replay)}를 재현했다. 선택 출처 미보존 건수는 {sum(len(r['old_not_retained']) for r in replay)} → {sum(len(r['new_not_retained']) for r in replay)}이다. 이 비교는 새 계획 호출 없이 문맥 조립만 바꾼 결과다.",
        "",
        "## 호출·지연",
        "",
        "| 유형 | A 호출 | B 호출 | A p50/p95초 | B p50/p95초 |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ["normal_all", "false_all", "clarification"]:
        g = summary[key]
        a, b = g["arms"]["A"], g["arms"]["B"]
        lines.append(
            f"| {key} | {a['api_calls']} | {b['api_calls']} | {a['p50_seconds']:.2f}/{a['p95_seconds']:.2f} | {b['p50_seconds']:.2f}/{b['p95_seconds']:.2f} |"
        )
    usage = {
        a: {
            k: sum(
                (t.get("api", {}).get(k) or 0)
                for r in saved[a].values()
                for t in r["turns"]
            )
            for k in ["input_tokens", "output_tokens", "cached_input_tokens"]
        }
        for a in ("A", "B")
    }
    write_json(RECORDS / "usage.json", usage)
    review_usage = {
        k: sum((r["telemetry"].get(k) or 0) for r in audits.values())
        for k in ["input_tokens", "output_tokens", "cached_input_tokens"]
    }
    review_usage.update(
        calls=len(audits),
        errors=sum(r["telemetry"]["status"] != "complete" for r in audits.values()),
    )
    write_json(RECORDS / "review-usage.json", review_usage)
    lines += [
        "",
        f"사후 근거 검수는 별도 {len(audits)}회 호출했다. 이 호출과 시간은 위 검색 지연에 포함하지 않았다. 검수 API 오류는 {review_usage['errors']}건이다.",
    ]
    errors = {a: sum(r["arms"][a]["api_failures"] for r in rows) for a in ("A", "B")}
    lines += [
        "",
        f"계획 API 실패 횟수: A {errors['A']}, B {errors['B']}. 자동 재시도하지 않았다. 전체 집계에서 임의로 제외하지 않고 양쪽 정상 완료 문항도 별도 표시했다.",
    ]
    lines += [
        "",
        "동시4요청, CPU 임베딩+GPU 리랭커 조건이다. 준비 시간을 제외한 질문 임베딩·검색·SQL·계획 호출·문맥 조립을 포함한다. 조건별 계획은 새로 호출했으므로 프롬프트·출처 보존의 결합 효과이며 순수 모델 효과가 아니다. 답변 생성은 제외했다.",
        "",
        "## 재현과 기록",
        "",
        "새 출력 경로와 protocol 지문으로 실행한다. 완료된 경로를 덮어쓰지 않는다.",
        "```bash",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-preservation-20261111.run_benchmark --help",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-preservation-20261111.score",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-preservation-20261111.replay",
        ".venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-preservation-20261111.finalize",
        "```",
        "",
        "문항별 결과: [reviewed-per-question.json](reviewed-per-question.json). 개선·하락: [differences.json](differences.json). 같은 검색 재생: [same-search-replay.json](same-search-replay.json). 입력 변경: [dataset-manifest.json](dataset-manifest.json). 검사: [verification.md](verification.md). 기준선: [baseline.md](baseline.md).",
        "",
        "원문·질문·정답·검수 인용은 artifacts/u_10o0o/advanced-table-rag-preservation-20261111/v1에 보관한다. 공유 기록에는 복사하지 않는다.",
    ]
    lines += [
        "",
        "## 채택 판단",
        "",
        "KURE-v3 기존 A 흐름을 기준선으로 유지하고, B는 비교 옵션으로 남긴다. 추가 확인 판정은 개선됐지만 일반형 근거 충족이 하락했다. [판단과 문항별 원인](conclusion.md)을 참고한다.",
        "",
        "검수 판정36개를 원문과 다시 대조했다. 인용 형식·짧은 셀, 검수 판정 자기모순, 참고답의 중복 감액, 질문 밖 추가 요구, 대상 미식별을 구분했다. [보정 분류](adjudications.json)에 문항별 결과를 기록했다.",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines) + "\n")
    write_json(
        RECORDS / "audit-manifest.json",
        {
            "review_sha256": file_sha(OUT / "evidence-review/run.json"),
            "manual_review_sha256": file_sha(manual_path)
            if manual_path.exists()
            else None,
            "reviewed_results_sha256": file_sha(RECORDS / "reviewed-per-question.json"),
            "independent_human_review": False,
            "answer_generation": False,
            "question_inputs_sha256": object_sha(read_json(OUT / "questions.json")),
        },
    )
    print(summary)


if __name__ == "__main__":
    main()
