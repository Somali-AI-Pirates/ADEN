"""3차 답변 재평가 집계 (ov3-*): 그래프 수정판 v1·v2와 main을 같은 조건(출처 ID 보정 모두 켬)으로 비교.

추가로 '출처 ID 보정이 필요했던 답변 수'를 센다: 모델 출력의 source_ids에 S# 형식이 아닌 값(문서 ID 등)이
있으면 현재 B 코드 그대로였다면 검증 오류가 났을 답변이다.
"""
import json
import re
import sys

from answers import KEYS, load, row

SETS = ("fp", "development", "additional", "synth")


def needed_fix(run):
    for a in run.get("attempts") or []:
        try:
            content = json.loads((a.get("model_output") or {}).get("content") or "{}")
        except Exception:  # noqa: BLE001
            continue
        if any(not re.fullmatch(r"S\d+", str(s)) for s in content.get("source_ids") or []):
            return True
    return False


def main():
    print("| 단계 | 세트 | 방식 | n | 사실 | 조건 | 근거 | 판단 | 4항목 평균 | R@8 | 보정 필요 답변 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in ("basic", "inter", "adv"):
        for group, sets in (("공식+거짓전제 44", SETS[:3]), ("합성 16", SETS[3:])):
            for tag in ("base", "v1", "v2"):
                sc, rn = {}, {}
                for st in sets:
                    got = load(f"ov3-{lv}-{st}-{tag}")
                    if got:
                        sc |= {f"{st}:{k}": v for k, v in got[0].items()}
                        rn |= {f"{st}:{k}": v for k, v in got[1].items()}
                if not sc:
                    continue
                r = row(sc, rn)
                avg = sum(r[k] or 0 for k in KEYS) / 4
                fix = sum(needed_fix(x) for x in rn.values())
                print(f"| {lv} | {group} | {tag} | {r['n']} | {r['fact']:.3f} | {r['condition']:.3f} | "
                      f"{r['evidence']:.3f} | {r['decision']:.3f} | {avg:.3f} | {r['recall@8']:.3f} | {fix} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
