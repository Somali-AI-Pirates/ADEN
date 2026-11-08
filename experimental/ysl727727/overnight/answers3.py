"""합성 두 문서 질문 답변 집계: synth2(40문항, base·v1·v2) + synth(16문항, base·v2) 합산."""
import sys

from answers import KEYS, load, row


def main():
    print("| 단계 | 방식 | n | 사실 | 조건 | 근거 | 판단 | 4항목 평균 | R@8 | 정상 질문 평균 | 거짓 전제 평균 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in ("basic", "inter", "adv"):
        for tag in ("base", "v1", "v2"):
            sc, rn = {}, {}
            for st in ("synth2", "synth"):
                got = load(f"ov2-{lv}-{st}-{tag}")
                if got:
                    sc |= got[0]
                    rn |= got[1]
            if not sc:
                continue
            r = row(sc, rn)
            avg = sum(r[k] or 0 for k in KEYS) / 4

            def part(prefix):
                vals = [sum(s["judge"][k]["value"] or 0 for k in KEYS) / 4 for q, s in sc.items() if q.startswith(prefix)]
                return sum(vals) / len(vals) if vals else float("nan")

            print(f"| {lv} | {tag} | {r['n']} | {r['fact']:.3f} | {r['condition']:.3f} | {r['evidence']:.3f} | "
                  f"{r['decision']:.3f} | {avg:.3f} | {r['recall@8']:.3f} | {part('SY-'):.3f} | {part('SYF-'):.3f} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
