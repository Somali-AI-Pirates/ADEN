"""Delay only the RRF pruning boundary; keep one reranking pass."""

from time import perf_counter


class WideRetriever:
    def __init__(self, base):
        self.base = base

    def search(self, question):
        from app.rag.models import SearchResult
        from app.rag.retrieval import rank_dense, _rrf, _source_result

        base = self.base
        question = question.strip()
        if not question:
            raise ValueError("Empty query")
        t0 = perf_counter()
        vector = base._query_vector(question)
        t1 = perf_counter()
        bm25 = base._bm25.fielded_search(question, top_k=50)
        t2 = perf_counter()
        dense = rank_dense(base.corpus, vector, top_k=50)
        t3 = perf_counter()
        pool = _rrf(bm25, dense, limit=100)
        t4 = perf_counter()
        ranked, work = base._rerank(question, pool)
        t5 = perf_counter()
        visible = ranked[:20]
        pages = tuple(
            base.corpus.pages_by_source[d.metadata["source_id"]] for d, _ in visible
        )
        tables = base._table_ids(visible, pages)
        t6 = perf_counter()
        stamps = [t0, t1, t2, t3, t4, t5, t6]
        names = [
            "query_embedding",
            "bm25",
            "dense",
            "rrf",
            "reranker",
            "page_deduplication",
        ]
        diag = {
            "search_unit_count": len(base.corpus.documents),
            "page_count": base.corpus.page_count,
            "stages": {
                **{
                    k + "_ms": round((b - a) * 1000, 3)
                    for k, a, b in zip(names, stamps, stamps[1:])
                },
                "total_ms": round((t6 - t0) * 1000, 3),
            },
            "bm25_top": [_source_result(d, s) for d, s in bm25],
            "dense_top": [_source_result(d, s) for d, s in dense],
            "rrf_candidates": [_source_result(d, s) for d, s in pool],
            "reranker": {
                **work,
                "ranked_units": [_source_result(d, s) for d, s in ranked],
            },
            "ordered_source_ids": [p.metadata["source_id"] for p in pages],
            "table_ids": list(tables),
            "experiment": "delay_rrf_pruning",
        }
        assert len(set(diag["ordered_source_ids"])) == len(pages) <= 20
        return SearchResult(pages=pages, table_ids=tables, diagnostics=diag)
