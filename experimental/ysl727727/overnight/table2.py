"""main 기준 단계적 개선표 (그래프 수정판: gfix=문서 번호 제외·순서 고정, gfloor0.05=점수 하한) (#all 실행: 공식 + 거짓 전제 + 합성). 같은 단계·이름은 마지막 기록."""
import json
import sys

from common import HERE

LADDER = [
    ("① main (hybrid + 리랭커)", "base#all"),
    ("② + 문서 다양화 (MMR 0.2)", "base+mmr0.2#all"),
    ("③ + 식별자 그래프 확장", "base+graph6+gfix+gfloor0.05+mmr0.2#all"),
    ("④ + LLM 질문 분해", "base+graph6+gfix+gfloor0.05+mmr0.2+mq_decomp#all"),
    ("⑤ + LLM 집합 선택 (최종 v2)", "base+graph6+gfix+gfloor0.05+mmr0.2+mq_decomp+sel30#all"),
]


def main():
    rows = {}
    for line in open(HERE / "results" / "leaderboard.jsonl", encoding="utf-8"):
        r = json.loads(line)
        rows[(r["level"], r["name"])] = r
    for lv, label in (("basic", "Basic"), ("inter", "중급"), ("adv", "고급")):
        r0 = rows.get((lv, LADDER[0][1]), {})
        n = {k: r0.get(k, {}).get("n") for k in ("official", "fp", "syn")}
        print(f"\n**{label}** (공식 {n['official']} · 거짓 전제 {n['fp']} · 합성 {n['syn']}문항)\n")
        print("| 단계 | 공식 R@8 | 거짓전제 R@8 | 합성 R@8 | 전체 R@8 | 전체 R@5 | 정답 페이지@8 |")
        print("|---|---|---|---|---|---|---|")
        for title, name in LADDER:
            r = rows.get((lv, name))
            if not r:
                print(f"| {title} | (없음) |")
                continue
            g = lambda k: r.get(k, {}).get("recall@8", float("nan"))  # noqa: E731
            print(f"| {title} | {g('official'):.3f} | {g('fp'):.3f} | {g('syn'):.3f} | {r['recall@8']:.3f} | "
                  f"{r['recall@5']:.3f} | {r['page@8']:.3f} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
