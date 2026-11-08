"""정답 문서 수별 검색 성능표 (main 기준 단계 ①~⑤).

공식(development+additional)은 정답 문서 수 1·2·3·4·5~7개로 나누고, 합성은 2개(synth_*, #all 실행)와
3·4개(synth_multi_*, #multi 실행)로 나눈다. 정상 질문과 거짓 전제 변형을 함께 센다.
합성 3·4개는 질문 모양으로도 나눈다: '여러 건 묶음'(질문에 예약·장비 번호 같은 식별자가 2개 이상)과
'한 건·여러 문서'(식별자 0~1개: 한 사례를 규칙·기록·승인 등 여러 문서로 확인).
지표: R@8(정답 문서 중 상위 8개에 든 비율)과 전부@8(정답 문서를 상위 8개에 모두 찾은 질문 비율).
"""
import json
import sys

from common import HERE, gold
from run import IDRE
from table2 import LADDER

RUNS = HERE / "results" / "runs"


def ndocs(g):
    return len({e["book_id"] for grp in g.get("evidence_groups") or [] for e in grp})


def per_q(level, name, suffix):
    path = RUNS / f"{level}__{name.split('#')[0]}#{suffix}.json"
    return json.loads(path.read_text(encoding="utf-8"))["per_q"] if path.exists() else {}


def main():
    for lv, label in (("basic", "Basic"), ("inter", "중급"), ("adv", "고급")):
        official = set(gold(lv))
        mpath = HERE / "data" / f"synth_multi_{lv}.json"
        g = gold(lv, [HERE / "data" / f"synth_{lv}.json"] + ([mpath] if mpath.exists() else []))
        text = {x["id"]: x["question"] for x in json.loads(mpath.read_text(encoding="utf-8"))} if mpath.exists() else {}
        kind = lambda q: "m" if q.startswith(("SY3", "SYF3", "SY4", "SYF4")) else (  # noqa: E731
            "s" if q.startswith(("SY-", "SYF-")) else "o" if q in official else "x")
        nid = lambda q: len(set(IDRE.findall(text.get(q, ""))))  # noqa: E731
        bins = [(f"공식 {n}", lambda q, n=n: kind(q) == "o" and ndocs(g[q]) == n) for n in (1, 2, 3, 4)]
        bins += [("공식 5~7", lambda q: kind(q) == "o" and ndocs(g[q]) >= 5),
                 ("합성 2", lambda q: kind(q) == "s"),
                 ("합성 3", lambda q: kind(q) == "m" and ndocs(g[q]) == 3),
                 ("합성 4", lambda q: kind(q) == "m" and ndocs(g[q]) == 4),
                 ("합성3·4 여러 건", lambda q: kind(q) == "m" and nid(q) >= 2),
                 ("합성3·4 한 건", lambda q: kind(q) == "m" and nid(q) < 2)]
        print(f"\n**{label}**\n")
        for metric, title in (("recall@8", "R@8"), ("all@8", "전부@8")):
            counts, body = None, []
            for step, name in LADDER:
                pq = {k: v for k, v in per_q(lv, name, "all").items() if kind(k) in "os"}
                pq |= {k: v for k, v in per_q(lv, name, "multi").items() if kind(k) == "m"}
                if not pq:
                    continue
                cells, ns = [], []
                for _, test in bins:
                    vals = [(1.0 if r["recall@8"] >= 0.999 else 0.0) if metric == "all@8" else r["recall@8"]
                            for q, r in pq.items() if test(q)]
                    ns.append(len(vals))
                    cells.append(f"{sum(vals) / len(vals):.3f}" if vals else "-")
                counts = counts or ns
                body.append(f"| {step} | " + " | ".join(cells) + " |")
            print("| " + title + " | " + " | ".join(f"{b} (n={n})" for (b, _), n in zip(bins, counts)) + " |")
            print("|---" * (len(bins) + 1) + "|")
            print("\n".join(body))
            print()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
