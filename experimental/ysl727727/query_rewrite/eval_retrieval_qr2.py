"""검색 평가 2차: 리랭커 점수 합치기(fuse) + LLM 재작성 (개인 실험).

1차 결과(results/retrieval_runs.json)의 hybrid_rerank, 규칙 재작성(v1)을 그대로 불러와 함께 비교한다.
새로 재는 방식:
    rule_fuse  규칙 재작성 + 리랭커를 원래 질문·일반 질문으로 각각 돌려 RRF로 합침
    llm_fuse   LLM 재작성(gpt-6-luna, 질문당 1회, results/llm_rewrites.json에 저장) + 같은 합치기
검색을 모두 끝낸 뒤에만 정답 파일을 연다.
"""
import json
import os
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

from openai import OpenAI  # noqa: E402
from rewrite import llm_rewrite, retrieve_qr, rewrite  # noqa: E402

from app.indexing.embeddings import Embedder  # noqa: E402
from app.indexing.vector_store import open_vector_index  # noqa: E402
from app.retrieval.service import retrieve  # noqa: E402
from shared.settings import load_settings  # noqa: E402

OUT = HERE / "results"
splits = ["development", "additional"]
questions = [q for s in splits for q in json.load(open(ROOT / f"data/basic/evaluation/{s}_questions.json", encoding="utf-8"))]

# LLM 재작성 (저장해 두고 재사용)
cache_path = OUT / "llm_rewrites.json"
cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
model = load_settings().generation_model
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=60, max_retries=1)
for q in questions:
    if q["id"] not in cache:
        try:
            cache[q["id"]] = llm_rewrite(q["question"], client, model)
        except Exception as e:  # 실패하면 재작성 없이 진행
            print("LLM 재작성 실패", q["id"], type(e).__name__)
            cache[q["id"]] = None
cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
print("LLM 재작성 예시:", {k: cache[k] for k in ["S-FINAL-27", "S-DEV-16", "S-DEV-02"]}, flush=True)

manifest = json.loads((ROOT / "artifacts/basic/index_manifest.json").read_text(encoding="utf-8"))
col = manifest["vector_store"]["collection"]
res = SimpleNamespace(embedder=Embedder(manifest["embedding"]["model"]), qdrant=open_vector_index(col), collection=col)
retrieve(questions[0]["question"], "hybrid_rerank", res)

prev = json.loads((OUT / "retrieval_runs.json").read_text(encoding="utf-8"))["runs"]
runs = {"hybrid_rerank": prev["hybrid_rerank"], "rule_v1": prev["hybrid_rerank_qr"], "rule_fuse": {}, "llm_fuse": {}}
NEW = {"rule_fuse": lambda q: retrieve_qr(q["question"], res, 20, fuse=True),
       "llm_fuse": lambda q: retrieve_qr(q["question"], res, 20, generic=cache.get(q["id"]), fuse=True)}
for i, q in enumerate(questions, 1):
    for m, fn in NEW.items():
        t = time.perf_counter()
        out = fn(q)
        runs[m][q["id"]] = {"ms": (time.perf_counter() - t) * 1000,
                            "top": [(c.chunk.chunk_id, c.chunk.book_id, c.chunk.page) for c in out]}
    if i % 10 == 0:
        print(f"  {i}/{len(questions)}", flush=True)
res.qdrant.close()
json.dump({"runs": runs}, open(OUT / "retrieval_runs2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---- 채점 ----
gold = {g["id"]: g for s in splits for g in json.load(open(ROOT / f"data/basic/evaluation/{s}.json", encoding="utf-8"))}


def score(top, g, k):
    groups = [x for x in g["evidence_groups"] if x]
    books = {e["book_id"] for x in groups for e in x}
    if not books:
        return None
    top = [tuple(t) for t in top[:k]]
    found = {b for _, b, _ in top} & books
    pages = {(b, p) for _, b, p in top}
    return (int(bool(found)), len(found) / len(books),
            sum(any((e["book_id"], e["page"]) in pages for e in x) for x in groups) / len(groups))


ids = [q["id"] for q in questions if score([], gold[q["id"]], 5) is not None]
print("\n| 방식 | Hit@5 | Recall@5 | 정답 페이지@5 | Hit@8 | Recall@8 | 정답 페이지@8 | 검색 시간 중앙값 |")
print("|---|---|---|---|---|---|---|---|")
for m in runs:
    row = []
    for k in (5, 8):
        sc = [score(runs[m][q]["top"], gold[q], k) for q in ids]
        row += [statistics.mean(x[0] for x in sc), statistics.mean(x[1] for x in sc), statistics.mean(x[2] for x in sc)]
    print(f"| {m} | " + " | ".join(f"{v:.3f}" for v in row) + f" | {statistics.median(r['ms'] for r in runs[m].values()) / 1000:.1f}s |")
for m in ("rule_fuse", "llm_fuse"):
    for k in (5, 8):
        for j, name in [(1, "Recall"), (2, "정답 페이지")]:
            d = [(q, score(runs[m][q]["top"], gold[q], k)[j] - score(runs["hybrid_rerank"][q]["top"], gold[q], k)[j]) for q in ids]
            print(f"{m} vs 기존 @{k} {name}: 이김 {sum(x > 1e-9 for _, x in d)} 비김 {sum(abs(x) <= 1e-9 for _, x in d)} 짐 {sum(x < -1e-9 for _, x in d)}"
                  f" | 이김 {[q for q, x in d if x > 1e-9]} | 짐 {[q for q, x in d if x < -1e-9]}")
g27 = {(e["book_id"], e["page"]) for x in gold["S-FINAL-27"]["evidence_groups"] for e in x}
for m in runs:
    print(f"S-FINAL-27 [{m}] 정답 페이지 순위: {[i for i, t in enumerate(runs[m]['S-FINAL-27']['top'], 1) if (t[1], t[2]) in g27] or '없음'}")
print("규칙 재작성된 질문:", sum(1 for q in questions if rewrite(q['question'])[0]), "/ LLM 재작성 성공:", sum(1 for q in questions if cache.get(q['id'])))
