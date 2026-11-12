import threading

import numpy as np

from app.rag.keyword_retriever import BM25Index
from shared.table_rag_policy import (
    RERANK_BATCH_SIZE as _RERANK_BATCH_SIZE,
    RERANK_MAX_LENGTH as _RERANK_MAX_LENGTH,
    DENSE_TOP_K as _DENSE_TOP_K,
    BM25_TOP_K as _BM25_TOP_K,
    RRF_CONSTANT as _RRF_CONSTANT,
    RRF_WEIGHTS as _RRF_WEIGHTS,
    CANDIDATE_LIMIT as _CANDIDATE_LIMIT,
    TABLE_LIMIT as _TABLE_LIMIT,
    CPU_THREADS as _CPU_THREADS,
    RERANKER_ID as _RERANKER_ID,
    RERANKER_REVISION as _RERANKER_REVISION,
    EMBEDDING_ID as _EMBEDDING_ID,
    EMBEDDING_REVISION as _EMBEDDING_REVISION,
    BM25_B as _BM25_B,
    BM25_K1 as _BM25_K1,
    HEADING_POOL as _HEADING_POOL,
    HEADING_WEIGHT as _HEADING_WEIGHT,
    QUERY_EMBEDDING_DTYPE_BY_DEVICE as _QUERY_EMBEDDING_DTYPE_BY_DEVICE,
)


def rank_dense(corpus, query_vector, *, top_k=5):
    scores = corpus.vectors @ query_vector
    args = np.argsort(-scores, kind="stable")[:top_k]
    return [(corpus.documents[i], float(scores[i])) for i in args]


def _unit_key(document):
    """Stable identity and tie order used by the frozen TableRAG A run."""
    metadata = document.metadata
    table_id = metadata.get("table_id")
    return (
        metadata["unit_id"],
        (
            metadata["file_name"],
            metadata["page"],
            metadata["unit_id"] if table_id else "",
        ),
    )


def _source_result(document, score):
    metadata = document.metadata
    return {
        "source_id": metadata["source_id"],
        "unit_id": metadata["unit_id"],
        "file_name": metadata["file_name"],
        "page": metadata["page"],
        "table_id": metadata.get("table_id"),
        "score": round(float(score), 8),
    }


def _rrf(bm25_results, dense_results, *, limit=_CANDIDATE_LIMIT):
    scores = {}
    documents = {}
    for ranked, weight in zip((bm25_results, dense_results), _RRF_WEIGHTS, strict=True):
        for rank, (document, _score) in enumerate(ranked, start=1):
            unit_id, _ = _unit_key(document)
            documents[unit_id] = document
            scores[unit_id] = scores.get(unit_id, 0.0) + weight / (_RRF_CONSTANT + rank)
    ordered_ids = sorted(
        scores,
        key=lambda unit_id: (-scores[unit_id], _unit_key(documents[unit_id])[1]),
    )
    seen_sources = set()
    distinct_pages = []
    for unit_id in ordered_ids:
        document = documents[unit_id]
        source_id = document.metadata["source_id"]
        if source_id in seen_sources:
            continue
        distinct_pages.append((document, scores[unit_id]))
        seen_sources.add(source_id)
        if len(distinct_pages) == limit:
            break
    return distinct_pages


class TableRetriever:
    """Synchronous 5:5 hybrid page/table search with one KURE pass."""

    def __init__(
        self,
        corpus,
        *,
        embedder,
        reranker,
        device="cpu",
        embedding_device=None,
        reranker_device=None,
        dense_index=None,
    ):
        from app.rag.keyword_retriever import _page_heading

        self.corpus = corpus
        self.embedder = embedder
        self.reranker = reranker
        self._dense_index = dense_index
        self._closed = False
        self.device = device
        self.embedding_device = embedding_device or device
        self.reranker_device = reranker_device or device
        self._bm25 = BM25Index(
            corpus.documents,
            headings=tuple(
                _page_heading(document.page_content) for document in corpus.documents
            ),
        )
        self._embedding_lock = threading.Lock()
        self._reranker_lock = threading.Lock()
        embedding = corpus.embedding
        self.manifest = {
            "retrieval": {
                "name": "table_rag_unified_db_first",
                "bm25": {
                    "tokenizer": "korean_bigram_normalized_complete_code",
                    "k1": _BM25_K1,
                    "b": _BM25_B,
                    "heading_weight": _HEADING_WEIGHT,
                    "heading_pool": _HEADING_POOL,
                    "heading_max_chars": 600,
                    "heading_max_blocks": 3,
                    "top_k": _BM25_TOP_K,
                },
                "dense_top_k": _DENSE_TOP_K,
                "dense": (
                    dense_index.manifest
                    if dense_index is not None
                    else {"backend": "numpy", "exact": True, "distance": "Cosine"}
                ),
                "rrf": {
                    "weights": list(_RRF_WEIGHTS),
                    "constant": _RRF_CONSTANT,
                },
                "page_deduplication": "first-ranked-unit-per-source-page",
                "candidate_limit": _CANDIDATE_LIMIT,
                "reranker": {
                    "model_id": _RERANKER_ID,
                    "revision": _RERANKER_REVISION,
                    "max_length": _RERANK_MAX_LENGTH,
                    "batch_size": _RERANK_BATCH_SIZE,
                    "dtype": "float32",
                },
                "embedding": {
                    "model_id": embedding["model_id"],
                    "revision": embedding["revision"],
                    "dimensions": embedding["dimensions"],
                    "query_prefix": embedding["query_prefix"],
                    "max_length": embedding["query_max_length"],
                    "dtype": _QUERY_EMBEDDING_DTYPE_BY_DEVICE[
                        self.embedding_device
                    ],
                },
                "device": device,
                "embedding_device": self.embedding_device,
                "reranker_device": self.reranker_device,
                "torch_threads": _CPU_THREADS,
                "native_hg_enrichment": False,
                "table_id_limit": _TABLE_LIMIT,
            }
        }

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._dense_index is not None:
            self._dense_index.close()

    def _query_vector(self, question):
        import torch
        import torch.nn.functional as functional

        with self._embedding_lock, torch.inference_mode():
            vector = self.embedder.encode_query(
                question,
                normalize_embeddings=False,
                show_progress_bar=False,
                convert_to_tensor=True,
            )
        if hasattr(vector, "detach"):
            vector = vector.detach().float().cpu().numpy()
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        if (
            vector.shape != (self.corpus.embedding["dimensions"],)
            or not np.isfinite(vector).all()
        ):
            raise ValueError(
                "Query embedding does not match the corpus vector dimensions."
            )
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise ValueError("Query embedding is zero-length.")
        vector = functional.normalize(torch.from_numpy(vector), p=2, dim=0).numpy()
        return vector

    def _rerank(self, question, candidates):
        if not candidates:
            return [], {"pairs": 0, "raw_pair_tokens": 0, "truncated_pairs": 0}
        pairs = [(question, document.page_content) for document, _score in candidates]
        with self._reranker_lock:
            tokens = self.reranker.tokenizer(
                [question for _ in pairs],
                [document.page_content for document, _score in candidates],
                truncation=False,
                padding=False,
                verbose=False,
            )["input_ids"]
            work = {
                "pairs": len(pairs),
                "raw_pair_tokens": sum(map(len, tokens)),
                "truncated_pairs": sum(
                    len(token_ids) > _RERANK_MAX_LENGTH for token_ids in tokens
                ),
            }
            scores = self.reranker.predict(
                pairs,
                batch_size=_RERANK_BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        scores = np.asarray(scores).reshape(-1)
        if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
            raise ValueError("KURE returned invalid reranking scores.")
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: (
                -float(pair[1]),
                _unit_key(pair[0][0])[1],
            ),
        )
        return [(document, float(score)) for ((document, _), score) in ranked], work

    def _table_ids(self, ranked_units, pages):
        table_ids = []
        for unit, _score in ranked_units:
            if table_id := unit.metadata.get("table_id"):
                if table_id not in table_ids:
                    table_ids.append(table_id)
            else:
                key = (unit.metadata["book_id"], unit.metadata["page"])
                for table_id in self.corpus.table_store.by_page.get(key, ()):
                    if table_id not in table_ids:
                        table_ids.append(table_id)
        for page in pages[:8]:
            key = (page.metadata["book_id"], page.metadata["page"])
            for table_id in self.corpus.table_store.by_page.get(key, ()):
                if table_id not in table_ids:
                    table_ids.append(table_id)
        return tuple(table_ids[:_TABLE_LIMIT])

    def search(self, question: str):
        """Return up to twenty source-deduplicated pages for one question."""
        from time import perf_counter

        from app.rag.models import SearchResult

        if self._closed:
            raise RuntimeError("TableRAG retriever is closed")
        question = question.strip()
        if not question:
            raise ValueError("질문을 입력해 주세요.")
        started = perf_counter()
        query_vector = self._query_vector(question)
        embedded_at = perf_counter()
        bm25_ranked = self._bm25.fielded_search(question, top_k=_BM25_TOP_K)
        bm25_at = perf_counter()
        dense_ranked = (
            rank_dense(self.corpus, query_vector, top_k=_DENSE_TOP_K)
            if self._dense_index is None
            else self._dense_index.search(query_vector, top_k=_DENSE_TOP_K)
        )
        dense_at = perf_counter()
        candidates = _rrf(bm25_ranked, dense_ranked)
        fused_at = perf_counter()
        reranked_units, rerank_work = self._rerank(question, candidates)
        reranked_at = perf_counter()

        pages = []
        seen = set()
        for unit, _score in reranked_units:
            page = self.corpus.pages_by_source[unit.metadata["source_id"]]
            source_id = page.metadata["source_id"]
            if source_id in seen:
                continue
            pages.append(page)
            seen.add(source_id)
            if len(pages) == _CANDIDATE_LIMIT:
                break
        table_ids = self._table_ids(reranked_units, tuple(pages))
        completed_at = perf_counter()

        def milliseconds(end, begin):
            return round((end - begin) * 1000, 3)

        diagnostics = {
            "dense_backend": self.manifest["retrieval"]["dense"],
            "search_unit_count": len(self.corpus.documents),
            "page_count": self.corpus.page_count,
            "stages": {
                "query_embedding_ms": milliseconds(embedded_at, started),
                "bm25_ms": milliseconds(bm25_at, embedded_at),
                "dense_ms": milliseconds(dense_at, bm25_at),
                "rrf_ms": milliseconds(fused_at, dense_at),
                "reranker_ms": milliseconds(reranked_at, fused_at),
                "page_deduplication_ms": milliseconds(completed_at, reranked_at),
                "total_ms": milliseconds(completed_at, started),
            },
            "bm25_top": [
                _source_result(document, score) for document, score in bm25_ranked
            ],
            "dense_top": [
                _source_result(document, score) for document, score in dense_ranked
            ],
            "rrf_candidates": [
                _source_result(document, score) for document, score in candidates
            ],
            "reranker": {
                **rerank_work,
                "ranked_units": [
                    _source_result(document, score)
                    for document, score in reranked_units
                ],
            },
            "ordered_source_ids": [page.metadata["source_id"] for page in pages],
            "table_ids": list(table_ids),
        }
        return SearchResult(
            pages=tuple(pages),
            table_ids=table_ids,
            diagnostics=diagnostics,
        )


def load_retriever(corpus, *, device: str = "cpu", dense_index=None) -> TableRetriever:
    """Load pinned local query and reranking models for the shared-record path."""
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    if device not in {"cpu", "cuda", "cpu_gpu_rerank"}:
        raise ValueError("device must be cpu, cuda, or cpu_gpu_rerank")
    if device in {"cuda", "cpu_gpu_rerank"} and not torch.cuda.is_available():
        raise ValueError(f"CUDA is unavailable for device={device!r}.")

    embedding_device = "cpu" if device == "cpu_gpu_rerank" else device
    reranker_device = "cuda" if device == "cpu_gpu_rerank" else device
    torch.set_num_threads(_CPU_THREADS)
    embedding = corpus.embedding
    if (
        embedding["model_id"] != _EMBEDDING_ID
        or embedding["revision"] != _EMBEDDING_REVISION
    ):
        raise ValueError("Query encoder differs from the selected TableRAG baseline.")

    embedding_dtype = _QUERY_EMBEDDING_DTYPE_BY_DEVICE[embedding_device]
    embedder = SentenceTransformer(
        _EMBEDDING_ID,
        revision=_EMBEDDING_REVISION,
        device=embedding_device,
        local_files_only=True,
        model_kwargs={"dtype": getattr(torch, embedding_dtype)},
    )
    if embedder.get_embedding_dimension() != embedding["dimensions"]:
        raise ValueError("Query encoder dimension differs from the corpus.")
    if embedder.max_seq_length != embedding["query_max_length"]:
        embedder.max_seq_length = embedding["query_max_length"]
    if embedder.prompts.get("query", "") != embedding["query_prefix"]:
        raise ValueError("Query encoder prompt differs from the corpus manifest.")

    reranker = CrossEncoder(
        _RERANKER_ID,
        revision=_RERANKER_REVISION,
        device=reranker_device,
        max_length=_RERANK_MAX_LENGTH,
        local_files_only=True,
        model_kwargs={"dtype": torch.float32, "attn_implementation": "sdpa"},
    )
    return TableRetriever(
        corpus,
        embedder=embedder,
        reranker=reranker,
        device=device,
        embedding_device=embedding_device,
        reranker_device=reranker_device,
        dense_index=dense_index,
    )
