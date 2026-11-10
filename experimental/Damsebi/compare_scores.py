"""같은 저장 답변을 채점한 실험들의 점수를 요청별로 나란히 비교한다.

    python compare_scores.py <artifacts/experiments> <라벨=실험ID> [<라벨=실험ID> ...] [--reasons]
"""
import json
import sys
from pathlib import Path

CRITERIA = ("fact", "condition", "evidence", "decision")
KO = {"fact": "사실", "condition": "조건", "evidence": "근거", "decision": "판단"}
MODES = ("naive", "hybrid", "hybrid_rerank")


def cell(score):
    if score["status"] == "scored":
        v = score["value"]
        return str(int(v)) if float(v).is_integer() else f"{v:.2f}"
    return "NA" if score["status"] == "not_applicable" else "UN"


def load(base, exp_id):
    rows = [json.loads(line) for line in (base / exp_id / "scores.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return {(r["question_id"], r["retrieval_mode"]): r for r in rows}


def main():
    base = Path(sys.argv[1])
    reasons = "--reasons" in sys.argv
    runs = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    data = {label: load(base, exp_id) for label, exp_id in runs.items()}
    labels = list(data)
    keys = sorted(next(iter(data.values())), key=lambda k: (k[0], MODES.index(k[1])))

    print("## 요청별 점수 (사실/조건/근거/판단 · Faithfulness)\n")
    print("| 문항 | 방식 | " + " | ".join(labels) + " | 차이 |")
    print("| --- | --- |" + " --- |" * (len(labels) + 1))
    changed = []
    for key in keys:
        cells, sigs = [], []
        for label in labels:
            s = data[label][key]
            judge = "/".join(cell(s["judge"][c]) for c in CRITERIA)
            cells.append(f"{judge} · {cell(s['faithfulness'])}")
            sigs.append(judge)
        diff = [KO[c] for c in CRITERIA
                if len({cell(data[label][key]["judge"][c]) for label in labels}) > 1]
        if diff:
            changed.append((key, diff))
        print(f"| {key[0]} | {key[1]} | " + " | ".join(cells) + f" | {', '.join(diff) or '-'} |")

    print("\n## 방식별 충족률 (1점 수/평가 대상 수, 해당 없음·평가 불가 제외)\n")
    print("| 기준 | 방식 | 사실 | 조건 | 근거 | 판단 | Faithfulness 평균(대상) |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        for mode in MODES + ("전체",):
            rows = [s for (q, m), s in data[label].items() if mode in (m, "전체")]
            if not rows:
                continue
            out = []
            for c in CRITERIA:
                scored = [s["judge"][c]["value"] for s in rows if s["judge"][c]["status"] == "scored"]
                out.append(f"{int(sum(scored))}/{len(scored)}")
            f = [s["faithfulness"]["value"] for s in rows if s["faithfulness"]["status"] == "scored"]
            fa = f"{sum(f) / len(f):.3f} ({len(f)})" if f else "- (0)"
            print(f"| {label} | {mode} | " + " | ".join(out) + f" | {fa} |")

    usage = {}
    for label in labels:
        j = [s["grading_usage"]["judge"] for s in data[label].values() if s["grading_usage"]["judge"]]
        usage[label] = (len(j), sum(u["latency_ms"] for u in j), sum(u["input_tokens"] or 0 for u in j),
                        sum(u["output_tokens"] or 0 for u in j), sum(bool(u["error"]) for u in j))
    print("\n## Judge 호출 사용량\n")
    print("| 기준 | 호출 | 실패 | 시간 합(ms) | 입력 토큰 | 출력 토큰 |\n| --- | --- | --- | --- | --- | --- |")
    for label, (n, ms, i, o, e) in usage.items():
        print(f"| {label} | {n} | {e} | {ms} | {i} | {o} |")

    print(f"\n판정이 달라진 요청: {len(changed)}/{len(keys)}")
    if reasons:
        for key, diff in changed:
            print(f"\n### {key[0]} · {key[1]} — {', '.join(diff)}")
            for label in labels:
                s = data[label][key]
                for c in CRITERIA:
                    if KO[c] in diff:
                        print(f"- [{label}] {KO[c]} {cell(s['judge'][c])}: {s['judge'][c]['reason']}")


main()
