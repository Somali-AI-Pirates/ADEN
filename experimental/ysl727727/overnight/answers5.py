"""추론 none 답변 재평가 집계 (ov3-*-none vs ov3-*-v2, 같은 조건·같은 문항).

    <gpu python> answers5.py
v2 = base+graph6+gfix+gfloor0.05+mmr0.2+mq_decomp+sel30 (추론 기본값)
none = 같은 설정에서 ④ 분해·⑤ 집합 선택 모두 reasoning_effort=none
잡음 추정: 두 설정의 상위 8개 근거가 똑같은 문항끼리의 점수 차이는 검색 차이가 아니라 답변 생성·Judge의 흔들림이다.
"""
import json
import statistics
import sys

from answers import KEYS, load, row
from answers4 import needed_fix

GROUPS = (("공식+거짓전제 44", ("fp", "development", "additional")), ("합성 16", ("synth",)))


def merged(lv, sets, tag):
    sc, rn = {}, {}
    for st in sets:
        got = load(f"ov3-{lv}-{st}-{tag}")
        if got:
            sc |= {f"{st}:{k}": v for k, v in got[0].items()}
            rn |= {f"{st}:{k}": v for k, v in got[1].items()}
    return sc, rn


def avg4(s):
    vals = [s["judge"][k]["value"] for k in KEYS if s["judge"][k]["status"] == "scored"]
    return statistics.fmean(vals) if vals else None


def top8(r):
    return tuple(c["chunk_id"] for c in (r.get("retrieved_candidates") or [])[:8])


def main():
    out = {}
    print("| 단계 | 세트 | 설정 | n | 사실 | 조건 | 근거 | 판단 | 4항목 평균 | R@8 | 답변/되묻기/보류 | 오류 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    pairs = []
    for lv in ("basic", "inter", "adv"):
        for group, sets in GROUPS:
            got = {}
            for tag in ("v2", "none"):
                sc, rn = merged(lv, sets, tag)
                if not sc:
                    continue
                got[tag] = (sc, rn)
                r = row(sc, rn)
                a = sum(r[k] or 0 for k in KEYS) / 4
                s = r["status"]
                out[f"{lv}/{group}/{tag}"] = {**{k: r[k] for k in KEYS}, "avg4": a, "recall@8": r["recall@8"],
                                              "n": r["n"], "errors": r["errors"], "fix": sum(needed_fix(x) for x in rn.values())}
                print(f"| {lv} | {group} | {tag} | {r['n']} | {r['fact']:.3f} | {r['condition']:.3f} | {r['evidence']:.3f} | "
                      f"{r['decision']:.3f} | {a:.3f} | {r['recall@8']:.3f} | {s['answered']}/{s['needs_clarification']}/"
                      f"{s['abstained']} | {r['errors']} |")
            if len(got) == 2:
                (sv, rv), (sn, rnn) = got["v2"], got["none"]
                for q in sv.keys() & sn.keys():
                    a, b = avg4(sv[q]), avg4(sn[q])
                    if a is None or b is None:
                        continue
                    pairs.append({"lv": lv, "group": group, "q": q, "same": top8(rv[q]) == top8(rnn[q]), "v2": a, "none": b})
    print("\n**문항별 비교 (4항목 평균, none − v2)**\n")
    print("| 단계 | 상위 8개 근거 | 문항 수 | 평균 차이 | none이 높음 | 같음 | none이 낮음 | 평균 |차이| |")
    print("|---|---|---|---|---|---|---|---|")
    for lv in ("basic", "inter", "adv", "전체"):
        for same in (True, False):
            ps = [p for p in pairs if (lv == "전체" or p["lv"] == lv) and p["same"] == same]
            if not ps:
                continue
            d = [p["none"] - p["v2"] for p in ps]
            print(f"| {lv} | {'같음' if same else '다름'} | {len(ps)} | {statistics.fmean(d):+.3f} | {sum(x > 0 for x in d)} | "
                  f"{sum(x == 0 for x in d)} | {sum(x < 0 for x in d)} | {statistics.fmean(abs(x) for x in d):.3f} |")
    from common import HERE

    (HERE / "results" / "answers_none.json").write_text(json.dumps({"summary": out, "pairs": pairs}, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
