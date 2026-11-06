"""검색만으로 Hit@k·Recall@k 측정 (LLM 호출 없음). 검색을 모두 마친 뒤에 정답 파일을 읽는다.

    python -m app.evaluation.scripts.eval_retrieval --mode naive
    python -m app.evaluation.scripts.eval_retrieval --mode naive --split additional

지표 (docs/design/architecture.md 9장):
    정답 문서  evidence_groups 전체 book_id 중복 제거. 비어 있으면 N/A로 평균에서 뺀다
    Hit@k      상위 k개 청크의 문서 중 정답 문서가 하나라도 있으면 1
    Recall@k   찾은 정답 문서 수 / 정답 문서 수
실패 분석용 보조 지표 (공식 아님):
    근거쪽@k   근거 그룹마다 그 그룹의 (문서, 페이지)가 상위 k개에 하나라도 있는지의 비율
    Recall@후보 후보 전체(top_n)의 Recall. 리랭크로 끌어올릴 수 있는 상한

공식 값은 B의 build_context가 고른 최종 문맥으로 C의 평가에서 다시 잰다.
결과: artifacts/basic/retrieval_<split>_<mode>.jsonl
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from app.indexing.embeddings import Embedder
from app.indexing.vector_store import open_vector_index
from app.retrieval.service import retrieve
from app.retrieval.resources import RetrievalResources

sys.stdout.reconfigure(encoding="utf-8")


def score(run, gold, k):
    groups = [g for g in gold["evidence_groups"] if g]
    books = {e["book_id"] for g in groups for e in g}
    if not books:
        return None
    top = run["candidates"][:k]
    found = {c["book_id"] for c in top} & books
    pages = {(c["book_id"], c["page"]) for c in top}
    return {
        "hit": int(bool(found)),
        "recall": len(found) / len(books),
        "group_cov": sum(
            any((e["book_id"], e["page"]) in pages for e in g) for g in groups
        )
        / len(groups),
        "recall_all": len({c["book_id"] for c in run["candidates"]} & books)
        / len(books),
        "gold_books": sorted(books),
        "found_books": sorted(found),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="naive")
    ap.add_argument("--split", default="development")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--data", default="data/basic")
    ap.add_argument("--artifacts", default="artifacts/basic")
    args = ap.parse_args()
    load_dotenv()
    ev = Path(args.data, "evaluation")
    manifest = json.loads(
        Path(args.artifacts, "index_manifest.json").read_text(encoding="utf-8")
    )
    collection = manifest["vector_store"]["collection"]
    res = SimpleNamespace(
        embedder=Embedder(manifest["embedding"]["model"]),
        qdrant=open_vector_index(collection),
        collection=collection,
        retrieval=RetrievalResources(),
    )

    try:
        # 1) 질문 파일에서 id, question만 읽어 검색
        questions = json.loads(
            (ev / f"{args.split}_questions.json").read_text(encoding="utf-8")
        )
        retrieve(
            questions[0]["question"], args.mode, res
        )  # 첫 호출 준비 시간을 측정에서 뺌
        runs = []
        for q in questions:
            t = time.perf_counter()
            cands = retrieve(q["question"], args.mode, res, top_n=args.top_n)
            runs.append(
                {
                    "id": q["id"],
                    "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                    "candidates": [
                        {
                            "rank": c.rank,
                            "chunk_id": c.chunk.chunk_id,
                            "book_id": c.chunk.book_id,
                            "page": c.chunk.page,
                            "score": round(c.score, 4),
                        }
                        for c in cands
                    ],
                }
            )
    finally:
        res.retrieval.clear()
        res.qdrant.close()

    # 2) 검색이 끝난 뒤에 정답 연결
    gold = {
        g["id"]: g
        for g in json.loads((ev / f"{args.split}.json").read_text(encoding="utf-8"))
    }
    assert set(gold) == {r["id"] for r in runs}, "질문 파일과 정답 파일의 id가 다름"
    for r in runs:
        r["expected_action"] = gold[r["id"]]["expected_action"]
        r["score"] = score(r, gold[r["id"]], args.k)

    out = Path(args.artifacts, f"retrieval_{args.split}_{args.mode}.jsonl")
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs),
        encoding="utf-8",
    )

    scored = [r for r in runs if r["score"]]
    na = [r["id"] for r in runs if not r["score"]]

    def avg(key, rows=scored):
        return sum(r["score"][key] for r in rows) / len(rows)

    k = args.k
    print(
        f"[{args.mode} / {args.split}] 질문 {len(runs)}개, 채점 {len(scored)}개, N/A {len(na)}개 {na}"
    )
    print(
        f"  Hit@{k} {avg('hit'):.3f} | Recall@{k} {avg('recall'):.3f} | 근거쪽@{k} {avg('group_cov'):.3f}"
        f" | Recall@{args.top_n} {avg('recall_all'):.3f} | 검색 시간 중앙값 "
        f"{statistics.median(r['latency_ms'] for r in runs):.0f}ms"
    )
    for action in sorted({r["expected_action"] for r in scored}):
        rows = [r for r in scored if r["expected_action"] == action]
        print(
            f"  {action:8s} {len(rows):2d}개  Hit@{k} {avg('hit', rows):.3f}  Recall@{k} {avg('recall', rows):.3f}"
        )
    print(f"Recall@{k} < 1 문항:")
    for r in scored:
        s = r["score"]
        if s["recall"] < 1:
            top = ", ".join(c["chunk_id"] for c in r["candidates"][:k])
            print(
                f"  {r['id']} {r['expected_action']:7s} 정답 {','.join(s['gold_books'])} "
                f"찾음 {','.join(s['found_books']) or '-'} | 상위{k}: {top}"
            )
    print(f"→ {out}")


if __name__ == "__main__":
    main()
