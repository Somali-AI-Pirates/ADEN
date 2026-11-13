"""필수 사실 근거 평가(fact_support) 결과를 집계한다(읽기 전용, 모델 호출 없음).

주 지표
- 필수 사실 충족률: 문항별 (근거로 확인된 필수 사실 수 / 전체 필수 사실 수)를 먼저 구한 뒤 문항 평균(매크로 평균)
- 후보 전체 근거 충족률: 후보 문맥으로 모든 필수 사실이 확인되는 문항의 비율
- 최종 전체 근거 충족률: 실제 전달한 최종 문맥만으로 모든 필수 사실이 확인되는 문항의 비율

needs_review(검수 필요)와 평가 불가는 분모에서 빼지 않고 충족이 아닌 것으로 센 뒤 건수를 따로 보인다.
최종 문맥은 후보 문맥의 일부이므로, 최종에서 충족된 사실은 그 근거 출처가 후보에도 있으면 후보에서도 충족으로 본다
(후보 판정만 어긋난 건수는 '후보 보정'으로 따로 센다).

--overrides로 검수 판정(사실별 상태·출처·사유)을 겹쳐 적용할 수 있다. 원래 판정은 바꾸지 않고, 보고서에 평가기
판정과 검수 반영 결과를 함께 보인다.

문항 분류: 후보에도 부족 → 검색·조회 / 후보는 충분, 최종 부족 → 선택·문맥 구성 / 검수 필요가 남으면 검수 필요 /
최종 근거가 충분하면 기존 답변 Judge(3회 다수결 사실·조건)의 통과 여부로 나눈다. 기존 Judge는 이 필수 사실이 아니라
원래 참고 정답으로 채점했으므로 '미통과'를 곧바로 생성 오류로 단정하지 않는다.
최종 근거가 충분한데 기존 지정 근거 기준(final_required_evidence_all)이 0인 문항은, --references가 있으면 인정된
출처가 지정 페이지 안에만 있는지(지정 페이지 일부로 충분) 밖도 쓰는지(지정 밖 페이지 활용)로 나눈다.

    python -m experimental.Damsebi.fact_support_report --judgments <fact_support 출력 폴더> \
        --facts <필수 사실 JSON> --grades <채점 폴더1> <채점 폴더2> <채점 폴더3> --out <md> \
        [--references <정답 파일>] [--overrides <검수 판정 JSON>] [--with-quotes]

--with-quotes는 원문 구절을 넣는다(원문이 들어가므로 공개 저장소에 올리지 않는 로컬 검수용).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SUPPORTED = "supported"
PASS = "근거 충분·기존 Judge 통과"
FAIL = "근거 충분·기존 Judge 미통과"
NO_JUDGE = "근거 충분(답변 판정 없음)"
SUBSET = "지정 페이지 일부로 충분"
OUTSIDE = "지정 밖 페이지 활용"


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


def designated_pages(references: list[dict]) -> dict[str, set[str]]:
    pages = {}
    for reference in references:
        ids = set()
        for group in reference.get("evidence_groups") or []:
            for item in group:
                ids.add(f"{item['book_id']}:p{int(item['page']):04d}")
        pages[reference["id"]] = ids
    return pages


def fact_states(judgment: dict | None, fact_ids: list[str]) -> dict[str, dict]:
    states = {}
    for fact_id in fact_ids:
        states[fact_id] = {"status": "unavailable", "source_ids": [], "override": None}
    if judgment is None or judgment["status"] != "scored":
        return states
    for verdict in judgment["verdicts"]:
        states[verdict["id"]] = {"status": verdict["status"], "source_ids": list(verdict["source_ids"]),
                                 "override": None}
    return states


def apply_overrides(states: dict[str, dict], question_id: str, context: str, overrides: list[dict]) -> int:
    applied = 0
    for item in overrides:
        if item["question_id"] != question_id or context not in item["contexts"]:
            continue
        if item["fact_id"] not in states:
            raise SystemExit(f"검수 판정의 사실 ID가 필수 사실에 없다: {question_id} {item['fact_id']}")
        states[item["fact_id"]] = {"status": item["status"], "source_ids": list(item.get("source_ids") or []),
                                   "override": item}
        applied += 1
    return applied


def effective_candidate(candidate: dict[str, dict], final: dict[str, dict],
                        candidate_ids: set[str]) -> tuple[dict[str, dict], int]:
    """최종에서 충족된 사실의 근거 출처가 모두 후보에 있으면 후보에서도 충족으로 본다."""
    adjusted = dict(candidate)
    corrections = 0
    for fact_id, state in final.items():
        if state["status"] != SUPPORTED or candidate[fact_id]["status"] == SUPPORTED:
            continue
        sources = set(state["source_ids"])
        if sources and sources <= candidate_ids:
            adjusted[fact_id] = {"status": SUPPORTED, "source_ids": state["source_ids"],
                                 "override": candidate[fact_id]["override"]}
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
        return NO_JUDGE
    if judge["fact"] == 0 or judge["condition"] == 0:
        return FAIL
    return PASS


def _statuses(states: dict[str, dict]) -> dict[str, str]:
    result = {}
    for fact_id, state in states.items():
        result[fact_id] = state["status"]
    return result


def summarize(judgments: list[dict], facts_doc: dict, judges: dict[str, dict],
              overrides: list[dict] | None = None, designated: dict[str, set[str]] | None = None) -> dict:
    by_key = {}
    for judgment in judgments:
        by_key[(judgment["question_id"], judgment["context"])] = judgment
    rows = []
    override_count = 0
    for entry in facts_doc["questions"]:
        if entry["kind"] != "evidence":
            continue
        question_id = entry["id"]
        fact_ids = []
        for fact in entry["facts"]:
            fact_ids.append(fact["id"])
        candidate_judgment = by_key.get((question_id, "candidate"))
        final_judgment = by_key.get((question_id, "final"))
        candidate = fact_states(candidate_judgment, fact_ids)
        final = fact_states(final_judgment, fact_ids)
        if overrides:
            override_count += apply_overrides(candidate, question_id, "candidate", overrides)
            override_count += apply_overrides(final, question_id, "final", overrides)
        candidate_ids = set()
        if candidate_judgment is not None:
            candidate_ids = set(candidate_judgment["context_source_ids"])
        candidate, corrections = effective_candidate(candidate, final, candidate_ids)
        judge = judges.get(question_id)
        category = classify(_statuses(candidate), _statuses(final), judge)
        accepted = []
        for state in final.values():
            if state["status"] == SUPPORTED:
                for source_id in state["source_ids"]:
                    if source_id not in accepted:
                        accepted.append(source_id)
        gap = None
        if category in (PASS, FAIL, NO_JUDGE) and judge is not None and judge["designated_all"] == 0:
            gap = "지정 근거 전부 포함 아님"
            if designated is not None:
                if set(accepted) <= designated.get(question_id, set()):
                    gap = SUBSET
                else:
                    gap = OUTSIDE
        rows.append({"question_id": question_id, "facts": entry["facts"], "candidate": _statuses(candidate),
                     "final": _statuses(final), "candidate_states": candidate, "final_states": final,
                     "candidate_judgment": candidate_judgment, "final_judgment": final_judgment,
                     "corrections": corrections, "judge": judge, "category": category, "accepted_sources": accepted,
                     "designated_gap": gap})

    def coverage(context: str) -> tuple[float, int, int, int, int]:
        ratios = []
        all_supported = 0
        supported = 0
        review = 0
        unsupported = 0
        for row in rows:
            statuses = list(row[context].values())
            ratios.append(statuses.count(SUPPORTED) / len(statuses))
            if statuses.count(SUPPORTED) == len(statuses):
                all_supported += 1
            for status in statuses:
                if status == SUPPORTED:
                    supported += 1
                elif status == "not_supported":
                    unsupported += 1
                else:
                    review += 1
        return sum(ratios) / len(ratios), all_supported, supported, review, unsupported

    final_rate, final_all, final_supported, final_review, final_unsupported = coverage("final")
    candidate_rate, candidate_all, candidate_supported, candidate_review, candidate_unsupported = coverage("candidate")
    corrections = 0
    fact_total = 0
    for row in rows:
        corrections += row["corrections"]
        fact_total += len(row["facts"])
    return {"rows": rows, "questions": len(rows), "facts": fact_total, "final_fact_rate": final_rate,
            "final_all": final_all, "final_supported": final_supported, "final_review": final_review,
            "final_unsupported": final_unsupported, "candidate_fact_rate": candidate_rate,
            "candidate_all": candidate_all, "candidate_supported": candidate_supported,
            "candidate_review": candidate_review, "candidate_unsupported": candidate_unsupported,
            "candidate_corrections": corrections, "overrides_applied": override_count}


def _mark(status: str) -> str:
    return {"supported": "O", "not_supported": "X", "needs_review": "?", "unavailable": "UN"}[status]


def _clean(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _metric_rows(summary: dict) -> list[str]:
    n = summary["questions"]
    return [f"{summary['final_fact_rate']:.3f}", f"{summary['candidate_fact_rate']:.3f}",
            f"{summary['candidate_all']}/{n} ({summary['candidate_all'] / n:.3f})",
            f"{summary['final_all']}/{n} ({summary['final_all'] / n:.3f})",
            f"{summary['final_supported']} / {summary['final_review']} / {summary['final_unsupported']}",
            f"{summary['candidate_supported']} / {summary['candidate_review']} / {summary['candidate_unsupported']}"]


def render(summary: dict, manifest: dict, facts_doc: dict, with_quotes: bool, raw: dict | None = None) -> str:
    n = summary["questions"]
    lines = ["# 필수 사실 근거 평가 집계", "",
             f"- 평가기 `{manifest.get('evaluator')}`, 모델 `{manifest.get('model')}`, "
             f"프롬프트 `{manifest.get('prompt')}`",
             f"- 필수 사실 `{facts_doc.get('version')}` `{manifest.get('facts_sha256')}`",
             f"- 평가 대상 {n}문항 {summary['facts']}개 사실(근거 평가 대상이 아닌 확인 질문은 제외)",
             f"- 실행 기록 `{manifest.get('runs_sha256')}`, 평가 커밋 `{str(manifest.get('git_commit'))[:8]}`", "",
             "## 주 지표", "",
             "필수 사실 충족률은 문항별 충족 비율을 먼저 구해 문항 평균한 값(매크로 평균)이다. 검수 필요(?)와 "
             "평가 불가(UN)는 분모에서 빼지 않고 충족이 아닌 것으로 셌다.", ""]
    names = ["필수 사실 충족률(최종 문맥)", "필수 사실 충족률(후보 문맥)", "후보 전체 근거 충족률",
             "최종 전체 근거 충족률",
             "최종 문맥 사실 수: 충족 / 검수 필요 / 미충족", "후보 문맥 사실 수: 충족 / 검수 필요 / 미충족"]
    values = _metric_rows(summary)
    if raw is not None:
        raw_values = _metric_rows(raw)
        lines += [f"| 지표 | 검수 반영(판정 {summary['overrides_applied']}건 교정) | 평가기 판정 |", "|---|---|---|"]
        for name, value, raw_value in zip(names, values, raw_values, strict=True):
            lines.append(f"| {name} | {value} | {raw_value} |")
    else:
        lines += ["| 지표 | 값 |", "|---|---|"]
        for name, value in zip(names, values, strict=True):
            lines.append(f"| {name} | {value} |")
    lines += ["", f"후보 보정: 최종에서 충족됐고 근거 출처가 후보에도 있어 후보에서도 충족으로 바꾼 사실 "
              f"{summary['candidate_corrections']}개.", ""]

    categories = {}
    gaps = {}
    for row in summary["rows"]:
        categories.setdefault(row["category"], []).append(row["question_id"])
        if row["designated_gap"]:
            gaps.setdefault(row["designated_gap"], []).append(row["question_id"])
    lines += ["## 문항 분류", "",
              "기존 Judge는 이 필수 사실이 아니라 원래 참고 정답으로 채점했다. "
              "그래서 'Judge 미통과'는 생성 오류 후보이며, "
              "답변 원문으로 따로 확인해야 한다.", "", "| 분류 | 문항 수 | 문항 |", "|---|---|---|"]
    for name, ids in sorted(categories.items(), key=lambda item: -len(item[1])):
        lines.append(f"| {name} | {len(ids)} | {', '.join(ids)} |")
    gap_total = 0
    for ids in gaps.values():
        gap_total += len(ids)
    lines += ["", "기존 '지정 근거 전부 포함' 기준은 통과하지 못했지만 최종 문맥이 모든 필수 사실을 "
              f"뒷받침한 문항: {gap_total}", ""]
    for name, ids in gaps.items():
        lines.append(f"- {name}: {len(ids)} ({', '.join(ids)})")
    lines.append("")

    lines += ["## 문항별", "",
              "충족 표시: O 충족, X 미충족, ? 검수 필요, UN 평가 불가. `*`는 검수 판정을 반영한 문항. "
              "Judge는 3회 다수결(사실·조건).", "",
              "| 문항 | 사실 수 | 후보 | 최종 | 인정 출처(최종) | 분류 | Judge 사실·조건 | 기존 지정 근거 전부 포함 |",
              "|---|---|---|---|---|---|---|---|"]
    for row in summary["rows"]:
        candidate_marks = []
        final_marks = []
        overridden = False
        for fact in row["facts"]:
            candidate_marks.append(_mark(row["candidate"][fact["id"]]))
            final_marks.append(_mark(row["final"][fact["id"]]))
            if row["candidate_states"][fact["id"]]["override"] or row["final_states"][fact["id"]]["override"]:
                overridden = True
        judge = row["judge"] or {}
        category = row["category"]
        if row["designated_gap"]:
            category += f" · {row['designated_gap']}"
        if overridden:
            category += " *"
        designated = "-"
        if judge.get("designated_all") is not None:
            designated = str(int(judge["designated_all"]))
        source_text = ", ".join(row["accepted_sources"])
        if not source_text:
            source_text = "-"
        lines.append(f"| {row['question_id']} | {len(row['facts'])} | {''.join(candidate_marks)} | "
                     f"{''.join(final_marks)} | {source_text} | {category} | "
                     f"{judge.get('fact')}·{judge.get('condition')} | {designated} |")

    lines += ["", "## 검수 판정 반영", ""]
    shown = False
    for row in summary["rows"]:
        for context in ("candidate", "final"):
            for fact in row["facts"]:
                override = row[f"{context}_states"][fact["id"]]["override"]
                if override is None:
                    continue
                shown = True
                lines.append(f"- {row['question_id']} {fact['id']} [{context} → {_mark(override['status'])}] "
                             f"{fact['text']} — {_clean(override['reason'])}")
    if not shown:
        lines.append("없음")

    lines += ["", "## 미충족·검수 필요 사실(검수 반영 후)", ""]
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
                state = row[f"{context}_states"][verdict["id"]]
                if state["status"] == SUPPORTED:
                    continue
                if state["override"] is not None:
                    line = (f"- {row['question_id']} {verdict['id']} [{context} {_mark(state['status'])}, 검수] "
                            f"{verdict['fact']} — {_clean(state['override']['reason'])}")
                else:
                    line = (f"- {row['question_id']} {verdict['id']} [{context} {_mark(state['status'])}] "
                            f"{verdict['fact']} — {_clean(verdict['reason'])}")
                    problems = "; ".join(verdict.get("check_problems") or [])
                    if problems:
                        line += f" (구절 확인: {problems})"
                lines.append(line)
                if with_quotes:
                    for quote in verdict["quotes"]:
                        lines.append(f"  - {quote['source_id']} 「{quote['text']}」")
    if with_quotes:
        lines += ["", "## 충족 근거(최종 문맥, 평가기 구절)", ""]
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
    parser.add_argument("--references", help="정답 파일(지정 페이지 일부/밖 구분)")
    parser.add_argument("--overrides", help="검수 판정 JSON(원래 판정 위에 겹쳐 적용)")
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
    designated = None
    if args.references:
        designated = designated_pages(json.loads(Path(args.references).read_text(encoding="utf-8")))
    judgments = _jsonl(judgments_dir / "judgments.jsonl")
    judges = judge_majority(grade_dirs)
    raw = None
    overrides = None
    if args.overrides:
        overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))["items"]
        raw = summarize(judgments, facts_doc, judges, None, designated)
    summary = summarize(judgments, facts_doc, judges, overrides, designated)
    text = render(summary, manifest, facts_doc, args.with_quotes, raw)
    Path(args.out).write_bytes(text.encode("utf-8"))
    print(text)


if __name__ == "__main__":
    main()
