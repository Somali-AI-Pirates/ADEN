"""캐시 없이 잰 검색 시간 비교 (질문마다 설정을 번갈아 실행해 시간대 차이를 줄임).

    <gpu python> bench_latency2.py [단계별 질문 수=15]
설정: 지금 기준(v2), 추론 강도 none·low, 단순 질문 건너뛰기, 로컬 모델 질문 분해, LLM 없는 ③.
리랭커 점수 캐시와 LLM 캐시를 쓰지 않는다. 결과: results/latency_v3.json
"""
import json
import statistics
import sys
import time

import qr
import run as ov
import sel
from common import HERE, log, questions
from index import embed_queries

G = "base+graph6+gfix+gfloor0.05+mmr0.2"
CONFIGS = {
    "③ LLM 없음": G,
    "지금 기준 v2": G + "+mq_decomp+sel30",
    "추론 none": G + "+mq_decomp_none+sel30+seff_none",
    "추론 low": G + "+mq_decomp_low+sel30+seff_low",
    "⑤만 추론 none": G + "+mq_decomp+sel30+seff_none",
    "건너뛰기(분해 1개)": G + "+mq_decomp+sel30+skip1",
    "건너뛰기(분해 2개 이하)": G + "+mq_decomp+sel30+skip2",
    "로컬 분해 qwen4b": G + "+mq_decomp_qwen4b+sel30",
}


class NoCache(ov.Reranker):
    def __init__(self, level):
        super().__init__(level)
        self.cache = {}

    def save(self):
        pass


def decompose(clients, cfg, question):
    """반환: (하위 질의 목록, 분해 개수, 걸린 ms)"""
    _, effort, local_model = qr.split_kind(cfg["mq"])
    t = time.perf_counter()
    raw = qr.ask(clients["local" if local_model else "api"], qr.DECOMP, question, effort=effort, model=local_model)
    ms = (time.perf_counter() - t) * 1000
    try:
        texts = [s for s in json.loads(raw[raw.index("["): raw.rindex("]") + 1]) if isinstance(s, str) and s.strip()][:4]
    except Exception:  # noqa: BLE001
        texts = []
    return [s for s in texts if s.strip() != question.strip()], len(texts), ms


def main(n):
    clients = {"api": qr.make_client(), "local": qr.make_client(qr.LOCAL["qwen4b"])}
    qr.ask(clients["local"], qr.DECOMP, "준비", model=qr.LOCAL["qwen4b"])  # 로컬 모델 적재 시간 제외
    out = {}
    for level in ("basic", "inter", "adv"):
        qs = questions(level)[:n]
        cfgs = {k: ov.preset(v) for k, v in CONFIGS.items()}
        ix = ov.get_index(level, cfgs["지금 기준 v2"])
        rr = NoCache(level)
        rr.score(cfgs["지금 기준 v2"]["rerank"], "준비", ["x"], ["준비"])  # 리랭커 적재 시간 제외
        rec = {k: {"total": [], "llm": [], "rr": [], "sel": [], "skipped": 0} for k in CONFIGS}
        for q in qs:
            for name, cfg in cfgs.items():
                rr.cache = {}
                rr.seconds = 0.0
                t = time.perf_counter()
                subs, n_dec = [], 99
                if cfg["mq"]:
                    texts, n_dec, ms = decompose(clients, cfg, q["question"])
                    rec[name]["llm"].append(ms)
                    subs = list(zip(texts, embed_queries(texts, cfg["emb"]))) if texts else []
                qv = embed_queries([q["question"]], cfg["emb"])[0]
                order = ov.retrieve(ix, rr, cfg, q["question"], qv, out_n=max(20, cfg["llmsel"]), subs=subs)
                if cfg["llmsel"]:
                    if cfg["selskip"] and n_dec <= cfg["selskip"]:
                        rec[name]["skipped"] += 1
                    else:
                        sel._cache = {}
                        t2 = time.perf_counter()
                        ov.llm_select_one(clients["api"], ix, cfg, q["question"], order)
                        rec[name]["sel"].append((time.perf_counter() - t2) * 1000)
                rec[name]["total"].append((time.perf_counter() - t) * 1000)
                rec[name]["rr"].append(rr.seconds * 1000)
        for name, r in rec.items():
            med = lambda xs: statistics.median(xs) if xs else 0  # noqa: E731
            out[f"{level}/{name}"] = {"p50_ms": med(r["total"]), "mean_ms": statistics.fmean(r["total"]),
                                      "p90_ms": sorted(r["total"])[int(0.9 * (len(r["total"]) - 1))],
                                      "llm_p50_ms": med(r["llm"]), "rerank_p50_ms": med(r["rr"]),
                                      "sel_p50_ms": med(r["sel"]), "skipped": r["skipped"], "n": len(r["total"])}
            log(f"{level:5s} {name:24s} p50 {med(r['total']) / 1000:.2f}s 평균 {statistics.fmean(r['total']) / 1000:.2f}s"
                f" | 분해 {med(r['llm']) / 1000:.2f}s 리랭커 {med(r['rr']) / 1000:.2f}s 선택 {med(r['sel']) / 1000:.2f}s"
                f" | 선택 생략 {r['skipped']}/{len(r['total'])}")
    (HERE / "results" / "latency_v3.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import warnings

    warnings.filterwarnings("ignore")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
