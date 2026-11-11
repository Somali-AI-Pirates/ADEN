"""Run the frozen Advanced selected-search baseline without evaluation labels."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import torch
from sentence_transformers import CrossEncoder

from app.generation import context as generation_context
from experimental.u_10o0o import advanced_search, advanced_source_lookup
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_field_roles import (
    FIELD_POOL,
    HEADING_BLOCK_LIMIT,
    HEADING_CHAR_LIMIT,
    HEADING_WEIGHT,
    TOKENIZER_NAME,
    PatternIndex,
)
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.bm25 import K1, B, load_tokenizer
from experimental.u_10o0o.embeding import (
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.hybrid import CANDIDATES, RRF_K
from experimental.u_10o0o.prepare import CACHE_PATH, DATASET
from experimental.u_10o0o.rerank import MAX_TOKENS, MODELS
from experimental.u_10o0o.search import load_index

ROOT = Path(__file__).resolve().parents[3]
KURE = "nlpai-lab/KURE-Reranker-nano"
RERANK_BATCH_SIZE = 32
RERANKED_HEAD = 3
FINAL_K = 8
CONTEXT_TOKENS = 8000
CONTEXT_TOKENIZER = "o200k_base"
HYBRID_WEIGHTS = (0.5, 0.5)

RUNTIME_MODULES = (
    advanced_search,
    advanced_source_lookup,
    generation_context,
)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_id(page: dict[str, Any]) -> str:
    book_id = page.get("book_id") or Path(page["file_name"]).stem
    page_number = int(page["page"])
    return f"{book_id}:p{page_number:04d}"


def _source_info(page: dict[str, Any], score: float) -> dict[str, Any]:
    source_id = _source_id(page)
    return {
        "unit_id": f"baseline:{source_id}",
        "source_id": source_id,
        "book_id": page.get("book_id") or Path(page["file_name"]).stem,
        "page": int(page["page"]),
        "score": float(score),
    }


class LegacyBaseline:
    """Batch adapter around the existing selected Advanced retrieval path.

    The supported run accepts only caller-supplied question IDs and question text,
    uses the pinned local CPU embedding model plus the CUDA FP16 reranker, and
    returns source metadata and original-page context. Evaluation references,
    answers, labels, and per-question latency are outside this adapter's contract.
    """

    def __init__(
        self,
        *,
        curated: Path = CURATED,
        case_index: Path = CASE_INDEX,
        facts: Path = FACTS,
        cache_path: Path = CACHE_PATH,
        vectors_path: Path = VECTORS_PATH,
        context_tokens: int = CONTEXT_TOKENS,
    ) -> None:
        if context_tokens <= 0:
            raise ValueError("context_tokens must be positive")
        self.curated = Path(curated)
        self.case_index = Path(case_index)
        self.facts = Path(facts)
        self.cache_path = Path(cache_path)
        self.vectors_path = Path(vectors_path)
        self.context_tokens = context_tokens

        self.pages: list[dict[str, Any]] | None = None
        self.vectors = None
        self.index: PatternIndex | None = None
        self.lookup = None
        self.storage = None
        self.embedder = None
        self.reranker = None

    def _ensure_corpus(self) -> None:
        if self.pages is not None:
            return

        pages, vectors = load_index()
        manifest = (self.curated / "manifest.json").read_bytes()
        if json.loads(manifest)["identity"]["dataset"] != DATASET:
            raise ValueError("Advanced curation snapshot is required.")
        sources = {
            "curated": self.curated,
            "case_index": self.case_index,
            "facts": self.facts,
        }
        index = PatternIndex(pages)
        lookup = load_target_lookup(pages, **sources)
        storage = load_storage_lookup(pages, **sources)

        self.pages = pages
        self.vectors = vectors
        self.index = index
        self.lookup = lookup
        self.storage = storage

    def _ensure_models(self) -> None:
        if self.embedder is not None and self.reranker is not None:
            return
        if not torch.cuda.is_available():
            raise RuntimeError("The Advanced baseline requires CUDA for reranking.")

        # Keep the existing Snowflake loader unchanged: it selects CPU, 8 threads,
        # and float32 by default. Only the reranker uses CUDA FP16.
        self.embedder = load_model()
        self.reranker = CrossEncoder(
            KURE,
            revision=MODELS[KURE],
            device="cuda",
            max_length=MAX_TOKENS,
            local_files_only=True,
            model_kwargs={"dtype": torch.float16},
        )

    def _input_paths(self) -> list[Path]:
        paths = [
            self.cache_path,
            self.vectors_path,
            self.curated / "manifest.json",
            self.curated / "sources.jsonl",
            *sorted((self.curated / "records").glob("*.jsonl")),
            self.case_index / "summary.json",
            self.case_index / "case_sources.jsonl",
            self.facts / "summary.json",
            self.facts / "role_facts.jsonl",
        ]
        paths.extend(Path(module.__file__).resolve() for module in RUNTIME_MODULES)
        paths.extend(
            Path(path).resolve()
            for path in (
                __file__,
                Path(__file__).parents[1] / "advanced_field_roles.py",
                Path(__file__).parents[1] / "advanced_hg_lookup.py",
                Path(__file__).parents[1] / "advanced_pair_context.py",
                Path(__file__).parents[1] / "advanced_pair_source_lookup.py",
                Path(__file__).parents[1] / "advanced_clarify_lookup.py",
                Path(__file__).parents[1] / "advanced_target_lookup.py",
                Path(__file__).parents[1] / "advanced_case_sources.py",
                Path(__file__).parents[1] / "advanced_role_context.py",
                Path(__file__).parents[1] / "embeding.py",
                Path(__file__).parents[1] / "hybrid.py",
                Path(__file__).parents[1] / "rerank.py",
                Path(__file__).parents[1] / "search.py",
                Path(__file__).parents[1] / "bm25.py",
                ROOT / "app/generation/context.py",
            )
        )
        unique = {path.resolve() for path in paths}
        return sorted(unique, key=lambda path: path.as_posix())

    def metadata(self) -> dict[str, Any]:
        """Return stable, label-free settings and repository-relative input hashes."""
        _, tokenizer_config = load_tokenizer(TOKENIZER_NAME)
        inputs = []
        for path in self._input_paths():
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(ROOT)
            except ValueError as exc:
                raise ValueError(
                    f"Baseline inputs must be inside the repository: {resolved}"
                ) from exc
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
            inputs.append({"path": relative.as_posix(), "sha256": _sha256(resolved)})

        config = {
            "dataset": DATASET,
            "fielded": {
                "tokenizer": tokenizer_config,
                "k1": K1,
                "b": B,
                "fusion": "max_normalized_bm25_heading_bonus",
                "field_pool": FIELD_POOL,
                "heading_weight": HEADING_WEIGHT,
                "heading_block_limit": HEADING_BLOCK_LIMIT,
                "heading_char_limit": HEADING_CHAR_LIMIT,
            },
            "hybrid": {
                "embedding_model": MODEL_ID,
                "embedding_revision": MODEL_REVISION,
                "candidates_per_retriever": CANDIDATES,
                "rrf_k": RRF_K,
                "weights": {
                    "fielded": HYBRID_WEIGHTS[0],
                    "dense": HYBRID_WEIGHTS[1],
                },
            },
            "reranker": {
                "model": KURE,
                "revision": MODELS[KURE],
                "device": "cuda",
                "dtype": "float16",
                "max_tokens": MAX_TOKENS,
                "batch_size": RERANK_BATCH_SIZE,
                "reranked_head": RERANKED_HEAD,
            },
            "context": {
                "page_limit": FINAL_K,
                "token_limit": self.context_tokens,
                "tokenizer": CONTEXT_TOKENIZER,
                "selection": "advanced_source_lookup.complete_sources",
            },
            "execution": {
                "embedding_device": "cpu",
                "embedding_threads": 8,
                "embedding_dtype": "float32",
                "mode": "batched_evaluation",
                "per_question_latency": "not_measured",
            },
        }
        return {
            "config": config,
            "inputs": inputs,
            "models": {
                "embedding": {"id": MODEL_ID, "revision": MODEL_REVISION},
                "reranker": {"id": KURE, "revision": MODELS[KURE]},
            },
            "versions": {
                name: version(name)
                for name in (
                    "torch",
                    "transformers",
                    "sentence-transformers",
                    "numpy",
                )
            },
        }

    def retrieve_batch(
        self,
        questions: list[dict[str, Any]],
        log: Callable[[str], Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Retrieve an ordered batch of {id, question} rows through existing helpers."""
        normalized_questions = []
        for row in questions:
            if not isinstance(row, dict) or "id" not in row or "question" not in row:
                raise ValueError("Each row must contain id and question.")
            if not isinstance(row["question"], str) or not row["question"].strip():
                raise ValueError("Question text must be a non-empty string.")
            normalized_questions.append({"id": row["id"], "question": row["question"]})
        if not normalized_questions:
            return []

        self._ensure_corpus()
        self._ensure_models()
        assert self.pages is not None
        assert self.vectors is not None
        assert self.index is not None
        assert self.lookup is not None
        assert self.storage is not None
        assert self.embedder is not None
        assert self.reranker is not None

        queries = [row["question"] for row in normalized_questions]
        fielded = [self.index.fielded(query, top_k=CANDIDATES) for query in queries]
        embeddings = self.embedder.encode_query(
            queries,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        _, hybrid = advanced_search.hybrid_candidates(
            self.pages, self.vectors, fielded, embeddings
        )
        log(f"Legacy baseline hybrid candidates ready · {len(questions)} questions")

        candidates = {advanced_search.HYBRID_SELECTED: hybrid}
        reranked = advanced_search.rerank_variants(
            self.reranker, normalized_questions, candidates, log
        )[advanced_search.HYBRID_SELECTED]

        results = []
        for question, pool, reranked_pool in zip(
            normalized_questions, hybrid, reranked, strict=True
        ):
            order = advanced_search.retain_reranked_head(pool, reranked_pool)
            after_hg_pool, selection, hg_route = (
                advanced_source_lookup.complete_sources(
                    question["question"],
                    order,
                    self.lookup,
                    self.storage,
                    self.context_tokens,
                )
            )
            selected, context, _, source_route, selection_route = selection

            work = self._rerank_work(question["question"], pool)
            trace = {
                "id": question["id"],
                "arm": "baseline",
                "question_sha256": hashlib.sha256(
                    question["question"].encode("utf-8")
                ).hexdigest(),
                "pool": [_source_info(page, score) for page, score in pool],
                "ranked": [_source_info(page, score) for page, score in selected],
                "final_pages": [
                    {
                        "source_id": _source_id(chunk.chunk.__dict__),
                        "book_id": chunk.chunk.book_id,
                        "page": chunk.chunk.page,
                    }
                    for chunk in context.final_chunks
                ],
                "context_sha256": context.context_hash,
                "context_tokens": context.token_count,
                "routes": {
                    "hg": hg_route,
                    "source": source_route,
                    "selection": selection_route,
                },
                "after_hg_pool": [
                    _source_info(page, score) for page, score in after_hg_pool
                ],
                "reranked_pool": [
                    _source_info(page, score) for page, score in reranked_pool
                ],
                "work": work,
            }
            context_row = {
                "id": question["id"],
                "arm": "baseline",
                "pages": [
                    {
                        "book_id": chunk.chunk.book_id,
                        "page": chunk.chunk.page,
                        "text": chunk.chunk.text,
                    }
                    for chunk in context.final_chunks
                ],
            }
            results.append((trace, context_row))
        return results

    def _rerank_work(
        self, question: str, pool: list[tuple[dict[str, Any], float]]
    ) -> dict[str, int]:
        assert self.reranker is not None
        texts = [page["text"] for page, _ in pool]
        encoded = self.reranker.tokenizer(
            [question] * len(texts), texts, truncation=False, verbose=False
        )["input_ids"]
        lengths = [len(tokens) for tokens in encoded]
        return {
            "rerank_pairs": len(lengths),
            "rerank_raw_tokens": sum(lengths),
            "rerank_tokens": sum(min(length, MAX_TOKENS) for length in lengths),
            "rerank_truncated": sum(length > MAX_TOKENS for length in lengths),
        }
