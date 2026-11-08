"""leaderboard.jsonl → 보고서용 표 (같은 단계·이름은 마지막 기록 사용).

    <gpu python> table.py name1 name2 ...      (이름은 #fp 붙은 실행 이름)
열: 전체(공식+거짓전제) R@5·R@8·R@10·정답 페이지@8 | 공식만 R@8 | 거짓전제만 R@8
"""
import json
import sys

from common import HERE


def main(names):
    rows = {}
    for line in open(HERE / "results" / "leaderboard.jsonl", encoding="utf-8"):
        r = json.loads(line)
        rows[(r["level"], r["name"])] = r
    for lv in ("basic", "inter", "adv"):
        print(f"\n**{ {'basic': 'Basic', 'inter': '중급', 'adv': '고급'}[lv] }** (공식 {rows.get((lv, names[0]), {}).get('official', {}).get('n', '?')}문항 + 거짓 전제 20)\n")
        print("| 설정 | R@5 | R@8 | R@10 | 정답 페이지@8 | 공식만 R@8 | 거짓전제만 R@8 | R@20(상한) |")
        print("|---|---|---|---|---|---|---|---|")
        for nm in names:
            r = rows.get((lv, nm))
            if not r:
                print(f"| {nm} | (없음) |")
                continue
            o, f = r.get("official", {}), r.get("fp", {})
            print(f"| {nm.replace('#fp', '')} | {r['recall@5']:.3f} | {r['recall@8']:.3f} | {r['recall@10']:.3f} | "
                  f"{r['page@8']:.3f} | {o.get('recall@8', float('nan')):.3f} | {f.get('recall@8', float('nan')):.3f} | "
                  f"{r['recall@all']:.3f} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
