"""답변 평가 결과 집계 (artifacts/overnight/answers/ov-<level>-<set>-<base|final>).

    <gpu python> answers.py [level ...]
"""
import json
import statistics
import sys

from common import ROOT

A = ROOT / "artifacts" / "overnight" / "answers"
KEYS = ("fact", "condition", "evidence", "decision")


def load(exp):
    d = A / exp
    if not (d / "scores.jsonl").exists():
        return None
    scores = {}
    for line in open(d / "scores.jsonl", encoding="utf-8"):
        s = json.loads(line)
        scores[s["question_id"]] = s
    runs = {}
    for line in open(d / "runs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        runs[r["question_id"]] = r
    return scores, runs


def load_merged(exp):
    """원래 실행 + 출처 ID 형식 오류 문항만 보정해 다시 돌린 결과(-fix)로 덮어쓴 것. 보정 문항 수도 돌려준다."""
    base = load(exp)
    fix = load(exp + "-fix")
    if not base:
        return None
    if not fix:
        return base[0], base[1], 0
    return base[0] | fix[0], base[1] | fix[1], len(fix[0])


def mean(vals):
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else None


def row(scores, runs):
    out = {}
    for k in KEYS:
        out[k] = mean([s["judge"][k]["value"] for s in scores.values() if s["judge"][k]["status"] == "scored"])
    out["recall@8"] = mean([s["retrieval"]["recall_at_k"]["value"] for s in scores.values()
                            if s["retrieval"]["recall_at_k"]["status"] == "scored"])
    out["n"] = len(scores)
    out["errors"] = sum(r["status"] == "error" for r in runs.values())
    out["latency_p50_s"] = statistics.median([r["latency_ms"] for r in runs.values() if r.get("latency_ms")]) / 1000
    out["status"] = {s: sum(r["status"] == s for r in runs.values()) for s in ("answered", "needs_clarification", "abstained")}
    out["in_tok"] = sum((r.get("usage") or {}).get("input_tokens") or 0 for r in runs.values())
    return out


def fmt(v):
    return "-" if v is None else f"{v:.3f}"


def main(levels):
    report = {}
    print("| 단계 | 세트 | 방식 | n | 사실 | 조건 | 근거 | 판단 | R@8 | 응답 p50 | 오류 | 답변/되묻기/보류 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in levels:
        for st in ("fp", "development", "additional"):
            for tag in ("base", "final"):
                got = load_merged(f"ov-{lv}-{st}-{tag}") if MERGE else load(f"ov-{lv}-{st}-{tag}")
                if not got:
                    continue
                r = row(got[0], got[1])
                report[f"{lv}/{st}/{tag}"] = r
                s = r["status"]
                print(f"| {lv} | {st} | {tag} | {r['n']} | {fmt(r['fact'])} | {fmt(r['condition'])} | {fmt(r['evidence'])} | "
                      f"{fmt(r['decision'])} | {fmt(r['recall@8'])} | {r['latency_p50_s']:.1f}s | {r['errors']} | "
                      f"{s['answered']}/{s['needs_clarification']}/{s['abstained']} |")
        # 합계 (세 세트 합)
        for tag in ("base", "final"):
            allsc, allrun = {}, {}
            for st in ("fp", "development", "additional"):
                got = load_merged(f"ov-{lv}-{st}-{tag}") if MERGE else load(f"ov-{lv}-{st}-{tag}")
                if got:
                    allsc |= {f"{st}:{k}": v for k, v in got[0].items()}
                    allrun |= {f"{st}:{k}": v for k, v in got[1].items()}
            if allsc:
                r = row(allsc, allrun)
                report[f"{lv}/all/{tag}"] = r
                print(f"| **{lv}** | **합계** | {tag} | {r['n']} | {fmt(r['fact'])} | {fmt(r['condition'])} | {fmt(r['evidence'])} | "
                      f"{fmt(r['decision'])} | {fmt(r['recall@8'])} | {r['latency_p50_s']:.1f}s | {r['errors']} | "
                      f"{r['status']['answered']}/{r['status']['needs_clarification']}/{r['status']['abstained']} |")
    (ROOT / f"experimental/ysl727727/overnight/results/answers_summary{'' if MERGE else '_raw'}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def diff(level, st):
    """문항별 Judge 4항목 합 변화 (기존 → 최종)."""
    a, b = load_merged(f"ov-{level}-{st}-base"), load_merged(f"ov-{level}-{st}-final")
    if not a or not b:
        return
    for q in a[0]:
        sa = sum(a[0][q]["judge"][k]["value"] or 0 for k in KEYS)
        sb = sum(b[0][q]["judge"][k]["value"] or 0 for k in KEYS)
        if sa != sb:
            print(f"  {q}: {sa:.0f} → {sb:.0f} ({a[1][q]['status']} → {b[1][q]['status']})")


MERGE = True

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if "--raw" in args:
        MERGE = False
        args.remove("--raw")
    lv = args or ["basic", "inter", "adv"]
    main(lv)
    for level in lv:
        for st in ("fp", "development", "additional"):
            print(f"[{level} {st}] 문항별 변화")
            diff(level, st)
