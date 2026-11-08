"""CPU vs GPU 리랭커 시간 측정 (개인 실험, API 비용 없음).

같은 코드로 hybrid_rerank와 llm_fuse(LLM 재작성 결과는 저장된 것 재사용)를 돌려
질문당 검색 시간과 그중 리랭커 시간을 잰다. GPU 결과는 어제 CPU 결과와 순위가 같은지도 비교한다.

    <python> experimental/ysl727727/query_rewrite/bench_device.py --device cuda
    <python> experimental/ysl727727/query_rewrite/bench_device.py --device cpu --limit 10
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(ROOT / ".env")

ap = argparse.ArgumentParser()
ap.add_argument("--device", default="cuda")
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()

import torch  # noqa: E402
from rewrite import retrieve_qr  # noqa: E402

from app.indexing.embeddings import Embedder  # noqa: E402
from app.indexing.keyword_store import load_keyword_index  # noqa: E402
from app.indexing.vector_store import open_vector_index  # noqa: E402
from app.retrieval.reranker import Reranker  # noqa: E402
from app.retrieval.service import retrieve  # noqa: E402

OUT = HERE / "results"
splits = ["development", "additional"]
questions = [q for s in splits for q in json.load(open(ROOT / f"data/basic/evaluation/{s}_questions.json", encoding="utf-8"))]
if args.limit:
    questions = questions[: args.limit]
llm = json.loads((OUT / "llm_rewrites.json").read_text(encoding="utf-8"))

manifest = json.loads((ROOT / "artifacts/basic/index_manifest.json").read_text(encoding="utf-8"))
col = manifest["vector_store"]["collection"]
t = time.perf_counter()
qdrant = open_vector_index(col)
res = SimpleNamespace(embedder=Embedder(manifest["embedding"]["model"], device=args.device), qdrant=qdrant, collection=col)
res.keyword_index = load_keyword_index(qdrant, col)
res.reranker = Reranker(device=args.device)
load_s = time.perf_counter() - t

rerank_ms = [0.0]
_score = res.reranker.score


def timed_score(question, texts, batch_size=8):
    t0 = time.perf_counter()
    out = _score(question, texts, batch_size=batch_size)
    rerank_ms[0] += (time.perf_counter() - t0) * 1000
    return out


res.reranker.score = timed_score
print(f"장치 {args.device} ({torch.cuda.get_device_name(0) if args.device == 'cuda' else 'CPU'}) | 모델·색인 준비 {load_s:.1f}s", flush=True)

MODES = {"hybrid_rerank": lambda q: retrieve(q["question"], "hybrid_rerank", res, top_n=20),
         "llm_fuse": lambda q: retrieve_qr(q["question"], res, 20, generic=llm.get(q["id"]), fuse=True)}
MODES["hybrid_rerank"](questions[0])  # 첫 호출 준비 시간 제외
runs = {m: {} for m in MODES}
for q in questions:
    for m, fn in MODES.items():
        rerank_ms[0] = 0.0
        t0 = time.perf_counter()
        out = fn(q)
        runs[m][q["id"]] = {"ms": (time.perf_counter() - t0) * 1000, "rerank_ms": rerank_ms[0],
                            "top": [(c.chunk.chunk_id, c.chunk.book_id, c.chunk.page) for c in out]}
qdrant.close()
tag = f"{args.device}{'_' + str(args.limit) if args.limit else ''}"
json.dump({"runs": runs}, open(OUT / f"bench_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print(f"\n| 방식 | 문항 | 검색 시간 중앙값 | 평균 | 그중 리랭커 중앙값 | 리랭커 비중 |")
print("|---|---|---|---|---|---|")
for m in MODES:
    ms = [r["ms"] for r in runs[m].values()]
    rr = [r["rerank_ms"] for r in runs[m].values()]
    print(f"| {m} | {len(ms)} | {statistics.median(ms) / 1000:.2f}s | {statistics.mean(ms) / 1000:.2f}s | "
          f"{statistics.median(rr) / 1000:.2f}s | {sum(rr) / sum(ms):.0%} |")
if args.device == "cuda":
    print(f"GPU 메모리 최대 사용 {torch.cuda.max_memory_allocated() / 1024 ** 3:.2f}GB")

# 어제 CPU 결과와 순위 비교 (같은 코드)
cpu = {"hybrid_rerank": json.loads((OUT / "retrieval_runs.json").read_text(encoding="utf-8"))["runs"]["hybrid_rerank"],
       "llm_fuse": json.loads((OUT / "retrieval_runs2.json").read_text(encoding="utf-8"))["runs"]["llm_fuse"]}
for m in MODES:
    same8 = sum([tuple(x) for x in cpu[m][q]["top"][:8]] == [tuple(x) for x in runs[m][q]["top"][:8]] for q in runs[m])
    set8 = sum({x[0] for x in cpu[m][q]["top"][:8]} == {x[0] for x in runs[m][q]["top"][:8]} for q in runs[m])
    print(f"{m}: 어제 CPU와 상위 8개 순서까지 같음 {same8}/{len(runs[m])}, 구성(순서 무시) 같음 {set8}/{len(runs[m])}")
