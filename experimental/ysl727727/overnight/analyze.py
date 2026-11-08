"""저장된 질문별 순위로 k별·세트별 지표와 이김/짐을 계산한다 (검색 재실행 없음).

    <gpu python> analyze.py <level> <run name A> <run name B>
"""
import json
import sys

from common import HERE, gold, questions, score_one

RES = HERE / "results" / "runs"


def load(level, name):
    return json.loads((RES / f"{level}__{name}.json").read_text(encoding="utf-8"))["runs"]


def main(level, a, b, ks=(4, 5, 6, 7, 8, 9, 10, 12)):
    fpq = HERE / "data" / f"false_premise_{level}_questions.json"
    fpg = HERE / "data" / f"false_premise_{level}.json"
    split = {q["id"]: q["split"] for q in questions(level, [fpq])}
    g = gold(level, [fpg])
    ra, rb = load(level, a), load(level, b)
    print(f"## {level}: A={a} / B={b}")
    print("| 세트 | 지표 | " + " | ".join(f"@{k}" for k in ks) + " |")
    print("|---|---|" + "---|" * len(ks))
    for sp in ["development", "additional", f"false_premise_{level}_questions", "전체"]:
        ids = [q for q in ra if (sp == "전체" or split[q] == sp)]
        for label, runs in (("A", ra), ("B", rb)):
            for m in ("recall", "page"):
                vals = []
                for k in ks:
                    rows = [score_one([(x[0], x[1]) for x in runs[q]], g[q], (k,)) for q in ids]
                    rows = [r for r in rows if r]
                    vals.append(sum(r[f"{m}@{k}"] for r in rows) / len(rows))
                name = {"development": "dev", "additional": "add"}.get(sp, "거짓전제" if sp.startswith("false") else sp)
                print(f"| {name} {label} | {m} | " + " | ".join(f"{v:.3f}" for v in vals) + " |")
    win = lose = 0
    changed = []
    for q in ra:
        sa = score_one([(x[0], x[1]) for x in ra[q]], g[q], (8,))
        sb = score_one([(x[0], x[1]) for x in rb[q]], g[q], (8,))
        if not sa:
            continue
        d = sb["recall@8"] - sa["recall@8"]
        win += d > 0
        lose += d < 0
        if {x[2] for x in ra[q][:8]} != {x[2] for x in rb[q][:8]}:
            changed.append((q, round(d, 3), round(sb["page@8"] - sa["page@8"], 3)))
    print(f"\nR@8 이김 {win} / 짐 {lose} / 상위 8개 구성이 바뀐 문항 {len(changed)}")
    return changed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:4])
