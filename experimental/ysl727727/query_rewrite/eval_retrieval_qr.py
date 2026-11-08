"""검색 평가: hybrid_rerank vs hybrid_rerank + 질문 재작성 (개인 실험).

질문 파일로 두 방식의 검색을 모두 끝낸 뒤에만 정답 파일을 열어 채점한다.
지표: 발제식 Hit@k·Recall@k(문서 단위) + 보조 지표 정답 페이지(근거쪽@k), k = 5, 8.

    python experimental/ysl727727/query_rewrite/eval_retrieval_qr.py      (저장소 루트에서)
"""
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

from rewrite import retrieve_qr, rewrite  # noqa: E402

from app.indexing.embeddings import Embedder  # noqa: E402
from app.indexing.vector_store import open_vector_index  # noqa: E402
from app.retrieval.service import retrieve  # noqa: E402

OUT = HERE / "results"
OUT.mkdir(exist_ok=True)
manifest = json.loads((ROOT / "artifacts/basic/index_manifest.json").read_text(encoding="utf-8"))
col = manifest["vector_store"]["collection"]
res = SimpleNamespace(embedder=Embedder(manifest["embedding"]["model"]), qdrant=open_vector_index(col), collection=col)

splits = ["development", "additional"]
questions = [q for s in splits for q in json.load(open(ROOT / f"data/basic/evaluation/{s}_questions.json", encoding="utf-8"))]
MODES = {"hybrid_rerank": lambda q: retrieve(q, "hybrid_rerank", res, top_n=20),
         "hybrid_rerank_qr": lambda q: retrieve_qr(q, res, top_n=20)}

retrieve(questions[0]["question"], "hybrid_rerank", res)  # 모델·BM25 준비 시간 제외
runs = {m: {} for m in MODES}
for i, q in enumerate(questions, 1):
    for m, fn in MODES.items():
        t = time.perf_counter()
        out = fn(q["question"])
        runs[m][q["id"]] = {"ms": (time.perf_counter() - t) * 1000,
                            "top": [(c.chunk.chunk_id, c.chunk.book_id, c.chunk.page) for c in out]}
    if i % 10 == 0:
        print(f"  {i}/{len(questions)}", flush=True)
res.qdrant.close()
json.dump({"rewrites": {q["id"]: rewrite(q["question"]) for q in questions}, "runs": runs},
          open(OUT / "retrieval_runs.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---- 채점 (검색이 모두 끝난 뒤) ----
gold = {g["id"]: g for s in splits for g in json.load(open(ROOT / f"data/basic/evaluation/{s}.json", encoding="utf-8"))}


def score(top, g, k):
    groups = [x for x in g["evidence_groups"] if x]
    books = {e["book_id"] for x in groups for e in x}
    if not books:
        return None
    top = top[:k]
    found = {b for _, b, _ in top} & books
    pages = {(b, p) for _, b, p in top}
    return (int(bool(found)), len(found) / len(books),
            sum(any((e["book_id"], e["page"]) in pages for e in x) for x in groups) / len(groups))


ids = [q["id"] for q in questions if score([], gold[q["id"]], 5) is not None]
print(f"\n채점 {len(ids)}문항 (N/A {len(questions) - len(ids)}), 재작성된 질문 {sum(1 for q in questions if rewrite(q['question'])[0])}/{len(questions)}")
print("| 방식 | Hit@5 | Recall@5 | 정답 페이지@5 | Hit@8 | Recall@8 | 정답 페이지@8 | 검색 시간 중앙값 |")
print("|---|---|---|---|---|---|---|---|")
for m in MODES:
    row = []
    for k in (5, 8):
        sc = [score(runs[m][q]["top"], gold[q], k) for q in ids]
        row += [statistics.mean(x[0] for x in sc), statistics.mean(x[1] for x in sc), statistics.mean(x[2] for x in sc)]
    ms = statistics.median(runs[m][q]["ms"] for q in runs[m])
    print(f"| {m} | " + " | ".join(f"{v:.3f}" for v in row) + f" | {ms / 1000:.1f}s |")

for k in (5, 8):
    for j, name in [(1, "Recall"), (2, "정답 페이지")]:
        d = [(q, score(runs["hybrid_rerank_qr"][q]["top"], gold[q], k)[j] - score(runs["hybrid_rerank"][q]["top"], gold[q], k)[j]) for q in ids]
        print(f"재작성 vs 기존 @{k} {name}: 이김 {sum(x > 1e-9 for _, x in d)} 비김 {sum(abs(x) <= 1e-9 for _, x in d)} 짐 {sum(x < -1e-9 for _, x in d)}"
              f" | 이긴 문항 {[q for q, x in d if x > 1e-9]} | 진 문항 {[q for q, x in d if x < -1e-9]}")

g27 = {(e["book_id"], e["page"]) for x in gold["S-FINAL-27"]["evidence_groups"] for e in x}
for m in MODES:
    ranks = [i for i, (_, b, p) in enumerate(runs[m]["S-FINAL-27"]["top"], 1) if (b, p) in g27]
    print(f"S-FINAL-27 [{m}] 정답 페이지 순위(20개 중): {ranks or '없음'}")
