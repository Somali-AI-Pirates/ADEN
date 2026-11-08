"""지연 줄이기 후보의 검색 성능표 (지금 기준 v2와 비교). 공식+거짓 전제 R@8·정답 페이지@8, 2문서 합성 R@8."""
import json
import sys

from common import HERE

G = "base+graph6+gfix+gfloor0.05+mmr0.2"
ROWS = [
    ("③ LLM 없음", G),
    ("④까지 (분해 기본)", G + "+mq_decomp"),
    ("④까지 (분해 none)", G + "+mq_decomp_none"),
    ("④까지 (분해 low)", G + "+mq_decomp_low"),
    ("④까지 (분해 로컬 qwen4b)", G + "+mq_decomp_qwen4b"),
    ("지금 기준 v2", G + "+mq_decomp+sel30"),
    ("1) 추론 none (④⑤ 모두)", G + "+mq_decomp_none+sel30+seff_none"),
    ("1) 추론 low (④⑤ 모두)", G + "+mq_decomp_low+sel30+seff_low"),
    ("1) ⑤만 추론 none (④는 기본)", G + "+mq_decomp+sel30+seff_none"),
    ("2) 건너뛰기: 분해 1개면 ⑤ 생략", G + "+mq_decomp+sel30+skip1"),
    ("2) 건너뛰기: 분해 2개 이하면 ⑤ 생략", G + "+mq_decomp+sel30+skip2"),
    ("4) 로컬 분해 + ⑤", G + "+mq_decomp_qwen4b+sel30"),
]


def main():
    rows = {}
    for line in open(HERE / "results" / "leaderboard.jsonl", encoding="utf-8"):
        r = json.loads(line)
        rows[(r["level"], r["name"])] = r
    for lv, label in (("basic", "Basic"), ("inter", "중급"), ("adv", "고급")):
        print(f"\n**{label}**\n")
        print("| 설정 | 공식+거짓전제 R@8 | 공식+거짓전제 정답 페이지@8 | 합성 2문서 R@8 |")
        print("|---|---|---|---|")
        for title, name in ROWS:
            r = rows.get((lv, name + "#all"))
            if not r:
                print(f"| {title} | (없음) | | |")
                continue
            o, f, s = r["official"], r["fp"], r["syn"]
            w = lambda k: (o[k] * o["n"] + f[k] * f["n"]) / (o["n"] + f["n"])  # noqa: E731
            print(f"| {title} | {w('recall@8'):.3f} | {w('page@8'):.3f} | {s['recall@8']:.3f} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
