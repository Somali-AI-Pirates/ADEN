"""필수 사실 근거 평가(fact_support) 결과를 집계한다(읽기 전용, 모델 호출 없음).

주 지표
- 필수 사실 충족률: 문항별 (근거로 확인된 필수 사실 수 / 전체 필수 사실 수)의 평균
- 후보 전체 근거 충족률: 후보 문맥으로 모든 필수 사실이 확인되는 문항의 비율
- 최종 전체 근거 충족률: 실제 전달한 최종 문맥만으로 모든 필수 사실이 확인되는 문항의 비율

needs_review(검수 필요)와 평가 불가는 분모에서 빼지 않고 충족이 아닌 것으로 센 뒤 건수를 따로 보인다.
최종 문맥은 후보 문맥의 일부이므로, 최종에서 충족된 사실은 그 근거 구절이 후보에도 있으면 후보에서도 충족으로 본다
(후보 판정만 어긋난 건수는 '후보 보정'으로 따로 센다).

실패 원인 분류(문항): 후보에도 부족 → 검색·조회 / 후보는 충분, 최종 부족 → 선택·문맥 구성 /
최종 충분, Judge 다수결 사실·조건 중 0 → 생성·해석 / 검수 필요가 남으면 검수 필요. 최종이 충분한데 기존 지정 근거
기준(final_required_evidence_all)이 0이면 '다른 출처로 충분'(정답 출처 목록의 한계)으로 표시한다.

    python -m experimental.Damsebi.fact_support_report --judgments <fact_support 출력 폴더> \
        --facts <필수 사실 JSON> --grades <채점 폴더1> <채점 폴더2> <채점 폴더3> --out <md> [--with-quotes]

--with-quotes는 원문 구절을 넣는다(원문이 들어가므로 공개 저장소에 올리지 않는 로컬 검수용).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

SUPPORTED = "supported"


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def judge_majority(grade_dirs: list[Path]) -> dict[str, dict]:
    """문항별 Judge 사실·조건 다수결과 지정 근거 지표(회차 무관)."""
    rounds = []
    for grade_dir in grade_dirs:
        by_question = {}
        for row in _jsonl(grade_dir / "scores.jsonl"):
            by_question[row["question_id"]] = row
        rounds.append(by_question)
    result = {}
    for question_id, first in rounds[0].items():
        votes = {}
        for criterion in ("fact", "condition"):
            values = []
            for by_question in rounds:
                item = by_question[question_id]["judge"][criterion]
                if item["status"] == "scored":
                    values.append(int(item["value"]))
            ones = values.count(1)
            zeros = values.count(0)
            if ones > zeros:
                votes[criterion] = 1
            elif zeros > ones:
                votes[criterion] = 0
            else:
                votes[criterion] = None
        designated = first["table_rag_metrics"]["final_required_evidence_all"]
        designated_all = None
        if designated["status"] == "scored":
            designated_all = designated.get("value")
        result[question_id] = {"fact": votes["fact"], "condition": votes["condition"], "designated_all": designated_all}
    return result


def fact_statuses(judgment: dict | None, fact_ids: list[str]) -> dict[str, str]:
    statuses = {}
    for fact_id in fact_ids:
        statuses[fact_id] = "unavailable"
    if judgment is None or judgment["status"] != "scored":
        return statuses
    for verdict in judgment["verdicts"]:
        statuses[verdict["id"]] = verdict["status"]
    return statuses


def effective_candidate(candidate: dict[str, str], final: dict[str, str], final_judgment: dict | None,
                        candidate_ids: set[str]) -> tuple[dict[str, str], int]:
    """최종에서 충족된 사실의 근거 출처가 모두 후보에 있으면 후보에서도 충족으로 본다."""
    verdicts = {}
    if final_judgment is not None and final_judgment["status"] == "scored":
        for verdict in final_judgment["verdicts"]:
            verdicts[verdict["id"]] = verdict
    adjusted = dict(candidate)
    corrections = 0
    for fact_id, status in final.items():
        if status != SUPPORTED or candidate.get(fact_id) == SUPPORTED:
            continue
        sources = set(verdicts[fact_id]["source_ids"])
        if sources and sources <= candidate_ids:
            adjusted[fact_id] = SUPPORTED
            corrections += 1
    return adjusted, corrections


def classify(candidate: dict[str, str], final: dict[str, str], judge: dict | None) -> str:
    candidate_values = list(candidate.values())
    final_values = list(final.values())
    if "not_supported" in candidate_values:
        return "검색·조회"
    if any(value != SUPPORTED for value in candidate_values):
        return "검수 필요(후보)"
    if "not_supported" in final_values:
        return "선택·문맥 구성"
    if any(value != SUPPORTED for value in final_values):
        return "검수 필요(최종)"
    if judge is None or judge["fact"] is None or judge["condition"] is None:
        return "근거 충분(답변 판정 없음)"
    if judge["fact"] == 0 or judge["condition"] == 0:
        return "생성·해석"
    return "근거 충분·답변 정확"


def summarize(judgments: list[dict], facts_doc: dict, judges: dict[str, dict]) -> dict:
    by_key = {}
    for judgment in judgments:
        by_key[(judgment["question_id"], judgment["context"])] = judgment
    rows = []
    for entry in facts_doc["questions"]:
        if entry["kind"] != "evidence":
            continue
        question_id = entry["id"]
        fact_ids = []
        for fact in entry["facts"]:
            fact_ids.append(fact["id"])
        candidate_judgment = by_key.get((question_id, "candidate"))
        final_judgment = by_key.get((question_id, "final"))
        candidate = fact_statuses(candidate_judgment, fact_ids)
        final = fact_statuses(final_judgment, fact_ids)
        candidate_ids = set()
        if candidate_judgment is not None:
            candidate_ids = set(candidate_judgment["context_source_ids"])
        candidate, corrections = effective_candidate(candidate, final, final_judgment, candidate_ids)
        judge = judges.get(question_id)
        category = classify(candidate, final, judge)
        designated_gap = (category in ("생성·해석", "근거 충분·답변 정확", "근거 충분(답변 판정 없음)")
                          and judge is not None and judge["designated_all"] == 0)
        rows.append({"question_id": question_id, "facts": entry["facts"], "candidate": candidate, "final": final,
                     "candidate_judgment": candidate_judgment, "final_judgment": final_judgment,
                     "corrections": corrections, "judge": judge, "category": category,
                     "designated_gap": designated_gap})

    def coverage(context: str) -> tuple[float, int, int, int]:
        ratios = []
        all_supported = 0
        review = 0
        unsupported = 0
        for row in rows:
            statuses = list(row[context].values())
            ratios.append(statuses.count(SUPPORTED) / len(statuses))
            if statuses.count(SUPPORTED) == len(statuses):
                all_supported += 1
            for status in statuses:
                if status == "not_supported":
                    unsupported += 1
                elif status != SUPPORTED:
                    review += 1
        return sum(ratios) / len(ratios), all_supported, review, unsupported

    final_rate, final_all, final_review, final_unsupported = coverage("final")
    candidate_rate, candidate_all, candidate_review, candidate_unsupported = coverage("candidate")
    corrections = 0
    for row in rows:
        corrections += row["corrections"]
    return {"rows": rows, "questions": len(rows), "final_fact_rate": final_rate, "final_all": final_all,
            "final_review": final_review, "final_unsupported": final_unsupported, "candidate_fact_rate": candidate_rate,
            "candidate_all": candidate_all, "candidate_review": candidate_review,
            "candidate_unsupported": candidate_unsupported, "candidate_corrections": corrections}


def _mark(status: str) -> str:
    return {"supported": "O", "not_supported": "X", "needs_review": "?", "unavailable": "UN"}[status]


def render(summary: dict, manifest: dict, facts_doc: dict, with_quotes: bool) -> str:
    n = summary["questions"]
    fact_total = 0
    for row in summary["rows"]:
        fact_total += len(row["facts"])
    lines = ["# 필수 사실 근거 평가 집계", "",
             f"- 평가기 `{manifest.get('evaluator')}`, 모델 `{manifest.get('model')}`, "
             f"프롬프트 `{manifest.get('prompt')}`",
             f"- 필수 사실 `{facts_doc.get('version')}` `{manifest.get('facts_sha256')}`({n}문항 {fact_total}개)",
             f"- 실행 기록 `{manifest.get('runs_sha256')}`, 평가 커밋 `{str(manifest.get('git_commit'))[:8]}`", "",
             "## 주 지표", "",
             "검수 필요(?)와 평가 불가(UN)는 분모에서 빼지 않고 충족이 아닌 것으로 셌다.", "",
             "| 지표 | 값 | 검수 필요 사실 | 미충족 사실 |", "|---|---|---|---|",
             f"| 필수 사실 충족률(최종 문맥) | {summary['final_fact_rate']:.3f} | {summary['final_review']} | "
             f"{summary['final_unsupported']} |",
             f"| 필수 사실 충족률(후보 문맥) | {summary['candidate_fact_rate']:.3f} | {summary['candidate_review']} | "
             f"{summary['candidate_unsupported']} |",
             f"| 후보 전체 근거 충족률 | {summary['candidate_all']}/{n} ({summary['candidate_all'] / n:.3f}) | | |",
             f"| 최종 전체 근거 충족률 | {summary['final_all']}/{n} ({summary['final_all'] / n:.3f}) | | |", "",
             f"후보 보정: 최종에서 충족됐고 근거 출처가 후보에도 있어 후보에서도 충족으로 바꾼 사실 "
             f"{summary['candidate_corrections']}개.", ""]
    categories = Counter()
    gaps = 0
    for row in summary["rows"]:
        categories[row["category"]] += 1
        if row["designated_gap"]:
            gaps += 1
    lines += ["## 실패 원인 분류", "", "| 분류 | 문항 수 |", "|---|---|"]
    for name, count in categories.most_common():
        lines.append(f"| {name} | {count} |")
    lines += ["", "최종 근거가 충분한데 기존 지정 근거 기준(필수 근거 전부 포함)이 0인 문항"
              f"(정답 출처 목록의 한계): {gaps}", ""]
    lines += ["## 문항별", "",
              "충족 표시: O 충족, X 미충족, ? 검수 필요, UN 평가 불가. Judge는 3회 다수결(사실·조건).", "",
              "| 문항 | 사실 수 | 후보 | 최종 | 인정 출처(최종) | 분류 | Judge 사실·조건 | 기존 지정 근거 전부 포함 |",
              "|---|---|---|---|---|---|---|---|"]
    for row in summary["rows"]:
        candidate_marks = []
        final_marks = []
        for fact in row["facts"]:
            candidate_marks.append(_mark(row["candidate"][fact["id"]]))
            final_marks.append(_mark(row["final"][fact["id"]]))
        sources = []
        if row["final_judgment"] is not None and row["final_judgment"]["status"] == "scored":
            for verdict in row["final_judgment"]["verdicts"]:
                for source_id in verdict["source_ids"]:
                    if source_id not in sources:
                        sources.append(source_id)
        judge = row["judge"] or {}
        category = row["category"]
        if row["designated_gap"]:
            category += " · 다른 출처로 충분"
        designated = "-"
        if judge.get("designated_all") is not None:
            designated = str(int(judge["designated_all"]))
        source_text = ", ".join(sources)
        if not source_text:
            source_text = "-"
        lines.append(f"| {row['question_id']} | {len(row['facts'])} | {''.join(candidate_marks)} | "
                     f"{''.join(final_marks)} | {source_text} | {category} | "
                     f"{judge.get('fact')}·{judge.get('condition')} | {designated} |")
    lines += ["", "## 미충족·검수 필요 사실", ""]
    for row in summary["rows"]:
        for context in ("candidate", "final"):
            judgment = row[f"{context}_judgment"]
            if judgment is not None and judgment["status"] != "scored":
                lines.append(f"- {row['question_id']} {context}: 평가 불가 ({judgment.get('error')})")
                continue
            if judgment is None:
                lines.append(f"- {row['question_id']} {context}: 판정 기록 없음")
                continue
            for verdict in judgment["verdicts"]:
                if row[context][verdict["id"]] == SUPPORTED:
                    continue
                reason = verdict["reason"].replace("|", "\\|").replace("\n", " ")
                problems = "; ".join(verdict.get("check_problems") or [])
                line = (f"- {row['question_id']} {verdict['id']} [{context} {_mark(row[context][verdict['id']])}] "
                        f"{verdict['fact']} — {reason}")
                if problems:
                    line += f" (구절 확인: {problems})"
                lines.append(line)
                if with_quotes:
                    for quote in verdict["quotes"]:
                        lines.append(f"  - {quote['source_id']} 「{quote['text']}」")
    if with_quotes:
        lines += ["", "## 충족 근거(최종 문맥)", ""]
        for row in summary["rows"]:
            judgment = row["final_judgment"]
            if judgment is None or judgment["status"] != "scored":
                continue
            for verdict in judgment["verdicts"]:
                if verdict["status"] != SUPPORTED:
                    continue
                lines.append(f"- {row['question_id']} {verdict['id']} {verdict['fact']}")
                for quote in verdict["quotes"]:
                    lines.append(f"  - {quote['source_id']} 「{quote['text']}」")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--judgments", required=True, help="fact_support 출력 폴더(judgments.jsonl·manifest.json)")
    parser.add_argument("--facts", required=True, help="평가에 쓴 필수 사실 JSON")
    parser.add_argument("--grades", nargs="+", required=True, help="Judge 채점 폴더(회차별)")
    parser.add_argument("--out", required=True, help="Markdown 출력 파일")
    parser.add_argument("--with-quotes", action="store_true", help="원문 구절 포함(로컬 검수용)")
    args = parser.parse_args()

    judgments_dir = Path(args.judgments)
    manifest = json.loads((judgments_dir / "manifest.json").read_text(encoding="utf-8"))
    facts_path = Path(args.facts)
    facts_doc = json.loads(facts_path.read_text(encoding="utf-8"))
    facts_sha = "sha256:" + hashlib.sha256(facts_path.read_bytes()).hexdigest()
    if manifest.get("facts_sha256") != facts_sha:
        raise SystemExit("필수 사실 파일이 평가 때 쓴 파일과 다르다(facts_sha256 불일치)")
    grade_dirs = []
    for grade in args.grades:
        grade_dirs.append(Path(grade))
    summary = summarize(_jsonl(judgments_dir / "judgments.jsonl"), facts_doc, judge_majority(grade_dirs))
    text = render(summary, manifest, facts_doc, args.with_quotes)
    Path(args.out).write_bytes(text.encode("utf-8"))
    print(text)


if __name__ == "__main__":
    main()
