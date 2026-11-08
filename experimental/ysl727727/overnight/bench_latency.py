"""캐시 없이 잰 검색 시간: base(main hybrid_rerank) vs 최종 후보 (GPU 리랭커, 질문 분해는 실시간 LLM 호출).

    <gpu python> bench_latency.py [질문 수=15]
단계마다 공식 질문 앞쪽 N개. 리랭커 점수 캐시를 쓰지 않고, 하위 질의도 매번 새로 만든다.
"""
import json
import statistics
import sys
import time

from dotenv import load_dotenv

import run as ov
from common import HERE, ROOT, log, questions
from index import embed_queries

FINAL = "base+graph6+mmr0.2+mq_decomp"
NAMES = ("base", "base+graph6+mmr0.2", FINAL, FINAL + "+sel30")


class NoCache(ov.Reranker):
    def __init__(self, level):
        super().__init__(level)
        self.cache = {}

    def save(self):
        pass


def main(n):
    from openai import OpenAI

    import qr

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    out = {}
    for level in ("basic", "inter", "adv"):
        qs = questions(level)[:n]
        for name in NAMES:
            cfg = ov.preset(name)
            ix = ov.get_index(level, cfg)
            rr = NoCache(level)
            rr.score(cfg["rerank"], "준비", ["x"], ["준비"])  # 모델 적재 시간 제외
            rr.cache = {}
            times, llm_ms, rr_ms, sel_ms = [], [], [], []
            for q in qs:
                rr.cache = {}
                rr.seconds = 0.0
                t = time.perf_counter()
                subs = []
                if cfg["mq"]:
                    t1 = time.perf_counter()
                    raw = qr.ask(client, qr.DECOMP, q["question"])
                    llm_ms.append((time.perf_counter() - t1) * 1000)
                    try:
                        texts = [s for s in json.loads(raw[raw.index("["): raw.rindex("]") + 1]) if isinstance(s, str)][:4]
                    except Exception:  # noqa: BLE001
                        texts = []
                    texts = [s for s in texts if s.strip() != q["question"].strip()]
                    subs = list(zip(texts, embed_queries(texts, cfg["emb"]))) if texts else []
                qv = embed_queries([q["question"]], cfg["emb"])[0]
                order = ov.retrieve(ix, rr, cfg, q["question"], qv, out_n=max(20, cfg["llmsel"]), subs=subs)
                if cfg["llmsel"]:
                    import sel

                    sel._cache = {}  # 캐시 없이 매번 호출
                    t2 = time.perf_counter()
                    ov.llm_select_one(client, ix, cfg, q["question"], order)
                    sel_ms.append((time.perf_counter() - t2) * 1000)
                times.append((time.perf_counter() - t) * 1000)
                rr_ms.append(rr.seconds * 1000)
            out[f"{level}/{name}"] = {"p50_ms": statistics.median(times), "mean_ms": statistics.fmean(times),
                                      "rerank_p50_ms": statistics.median(rr_ms),
                                      "llm_p50_ms": statistics.median(llm_ms) if llm_ms else 0,
                                      "sel_p50_ms": statistics.median(sel_ms) if sel_ms else 0}
            log(f"{level:5s} {name:40s} 검색 p50 {statistics.median(times) / 1000:.2f}s (평균 {statistics.fmean(times) / 1000:.2f}s)"
                f" | 리랭커 p50 {statistics.median(rr_ms) / 1000:.2f}s | 분해 LLM p50 "
                f"{(statistics.median(llm_ms) / 1000) if llm_ms else 0:.2f}s | 집합 선택 LLM p50 "
                f"{(statistics.median(sel_ms) / 1000) if sel_ms else 0:.2f}s")
    (HERE / "results" / f"latency{'_v2' if len(NAMES) > 2 else ''}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import warnings

    warnings.filterwarnings("ignore")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
