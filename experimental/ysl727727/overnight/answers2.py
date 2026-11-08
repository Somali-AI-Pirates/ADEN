"""2차 답변 평가 집계: main(base) / v1(분해+그래프+다양화) / v2(+LLM 집합 선택).

공식·거짓 전제: base·v1은 1차 실행(ov-…, 출처 ID 오류 문항은 보정 재실행으로 대체), v2는 2차(ov2-…, 보정 켬).
합성: 2차에서 base·v2를 같은 16문항으로 실행.
"""
import json
import sys

from answers import KEYS, load, load_merged, row
from common import HERE


def get(level, st, tag):
    if tag == "v2":
        got = load(f"ov2-{level}-{st}-v2")
    elif st == "synth":
        got = load(f"ov2-{level}-{st}-base") if tag == "base" else None
    else:
        got = load_merged(f"ov-{level}-{st}-{'final' if tag == 'v1' else 'base'}")
    return (got[0], got[1]) if got else None


def main():
    out = {}
    print("| 단계 | 세트 | 방식 | n | 사실 | 조건 | 근거 | 판단 | 4항목 평균 | R@8 | 응답 p50 | 오류 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in ("basic", "inter", "adv"):
        agg = {t: ({}, {}) for t in ("base", "v1", "v2")}
        for st in ("fp", "development", "additional", "synth"):
            for tag in ("base", "v1", "v2"):
                got = get(lv, st, tag)
                if not got:
                    continue
                if st != "synth":
                    agg[tag][0].update({f"{st}:{k}": v for k, v in got[0].items()})
                    agg[tag][1].update({f"{st}:{k}": v for k, v in got[1].items()})
                r = row(*got)
                out[f"{lv}/{st}/{tag}"] = r
                avg = sum(r[k] or 0 for k in KEYS) / 4
                print(f"| {lv} | {st} | {tag} | {r['n']} | {r['fact']:.3f} | {r['condition']:.3f} | {r['evidence']:.3f} | "
                      f"{r['decision']:.3f} | {avg:.3f} | {r['recall@8']:.3f} | {r['latency_p50_s']:.1f}s | {r['errors']} |")
        for tag in ("base", "v1", "v2"):
            if agg[tag][0]:
                r = row(*agg[tag])
                out[f"{lv}/공식+거짓전제/{tag}"] = r
                avg = sum(r[k] or 0 for k in KEYS) / 4
                print(f"| **{lv}** | **공식+거짓전제 44** | {tag} | {r['n']} | {r['fact']:.3f} | {r['condition']:.3f} | "
                      f"{r['evidence']:.3f} | {r['decision']:.3f} | {avg:.3f} | {r['recall@8']:.3f} | "
                      f"{r['latency_p50_s']:.1f}s | {r['errors']} |")
    (HERE / "results" / "answers_summary_v2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                              encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
