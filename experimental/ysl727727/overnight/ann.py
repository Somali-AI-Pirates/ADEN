"""ANN(근사 최근접) 실험: 정확 검색(GPU 행렬곱) 대비 FAISS HNSW·양자화의 후보 재현율과 최종 Recall@k 영향.

    <gpu python> ann.py <level> <emb>
측정
  ann_recall@20  같은 질문에서 정확 검색 상위 20개 중 ANN 상위 20개에 들어온 비율 (ANN 자체 품질)
  ms/query       CPU 1스레드 검색 시간
  R@8 (base)     main 파이프라인(Dense20+BM25 20→RRF→리랭커)에서 Dense만 ANN으로 바꿨을 때
정답 파일은 R@8 채점에서만 읽는다.
"""
import json
import sys
import time

import numpy as np

import run as ov
from common import HERE, log, questions
from index import embed_queries, embeddings


def build(kind, X, **p):
    import faiss

    d = X.shape[1]
    if kind == "flat":
        ix = faiss.IndexFlatIP(d)
    elif kind == "hnsw":
        ix = faiss.IndexHNSWFlat(d, p["M"], faiss.METRIC_INNER_PRODUCT)
        ix.hnsw.efConstruction = p["efc"]
    elif kind == "hnsw_sq8":
        ix = faiss.IndexHNSWSQ(d, faiss.ScalarQuantizer.QT_8bit, p["M"], faiss.METRIC_INNER_PRODUCT)
        ix.hnsw.efConstruction = p["efc"]
    elif kind == "ivfpq":
        q = faiss.IndexFlatIP(d)
        ix = faiss.IndexIVFPQ(q, d, p["nlist"], p["m"], 8, faiss.METRIC_INNER_PRODUCT)
    else:
        raise ValueError(kind)
    t = time.perf_counter()
    if not ix.is_trained:
        ix.train(X)
    ix.add(X)
    return ix, time.perf_counter() - t


def main(level, emb):
    import faiss

    faiss.omp_set_num_threads(1)
    fpq = HERE / "data" / f"false_premise_{level}_questions.json"
    qs = questions(level, [fpq])
    X = embeddings(level, "p800", emb).astype(np.float32)
    Q = embed_queries([q["question"] for q in qs], emb)
    exact, _ = build("flat", X)
    _, E = exact.search(Q, 20)
    rows = []
    settings = [("hnsw", {"M": 16, "efc": 100}, [16, 32, 64, 128]),
                ("hnsw", {"M": 32, "efc": 200}, [16, 32, 64, 128]),
                ("hnsw_sq8", {"M": 32, "efc": 200}, [64]),
                ("ivfpq", {"nlist": 256, "m": X.shape[1] // 16}, [8, 32])]
    cfg = ov.preset("base")
    base_ix = ov.get_index(level, cfg)
    for kind, p, efs in settings:
        ix, build_s = build(kind, X, **p)
        for ef in efs:
            if kind.startswith("hnsw"):
                ix.hnsw.efSearch = ef
            else:
                ix.nprobe = ef
            t = time.perf_counter()
            _, A = ix.search(Q, 20)
            ms = (time.perf_counter() - t) * 1000 / len(Q)
            rec = float(np.mean([len(set(a) & set(e)) / 20 for a, e in zip(A, E)]))
            # 최종 Recall: Dense 후보만 ANN 결과로 바꿔 main 파이프라인 실행
            ann_top = {q["id"]: list(map(int, a)) for q, a in zip(qs, A)}
            orig = ov.Index.dense
            ov.Index.dense = lambda self, qv, n, _m=ann_top, _qs=qs, _Q=Q: _m[_lookup(_Q, qv, _qs)][:n]
            try:
                r = ov.evaluate(level, f"ann_{kind}_{'_'.join(f'{k}{v}' for k, v in p.items())}_ef{ef}",
                                cfg=cfg, fp=True, save=True)
            finally:
                ov.Index.dense = orig
            rows.append({"kind": kind, **p, "ef_or_nprobe": ef, "build_s": round(build_s, 1),
                         "ann_recall@20": round(rec, 4), "ms_per_query": round(ms, 3),
                         "R@8": round(r["recall@8"], 4), "page@8": round(r["page@8"], 4)})
            log(f"  {kind} {p} ef/nprobe={ef}: ann_recall@20 {rec:.4f}, {ms:.3f}ms/q, build {build_s:.1f}s, "
                f"R@8 {r['recall@8']:.3f}")
    t = time.perf_counter()
    exact.search(Q, 20)
    flat_ms = (time.perf_counter() - t) * 1000 / len(Q)
    out = {"level": level, "emb": emb, "n_vectors": int(X.shape[0]), "dim": int(X.shape[1]),
           "flat_ms_per_query": round(flat_ms, 3), "rows": rows}
    (HERE / "results" / f"ann_{level}_{emb}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                               encoding="utf-8")
    log(f"정확 검색(FAISS Flat, CPU 1스레드) {flat_ms:.3f}ms/q, 벡터 {X.shape}")
    del base_ix


def _lookup(Q, qv, qs):
    j = int(np.argmax(Q @ np.asarray(qv, dtype=np.float32)))
    return qs[j]["id"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import warnings

    warnings.filterwarnings("ignore")
    main(sys.argv[1], sys.argv[2])
