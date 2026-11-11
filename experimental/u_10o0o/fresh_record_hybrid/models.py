"""Pinned, local-only model wrappers for the fresh A/B/C retrieval run.

No model is loaded until a wrapper is explicitly constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

EMBEDDING_MODEL_ID = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
EMBEDDING_REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"
RERANK_MODEL_ID = "nlpai-lab/KURE-Reranker-nano"
RERANK_REVISION = "8514b9e113f3b27c441ed9c2b50f81d80388b2fd"
MAX_MODEL_TOKENS = 2048
DEFAULT_HUB_CACHE = Path("/home/jake/.cache/huggingface/hub")


@dataclass(frozen=True)
class TokenUsage:
    """Token counts before and after the model's configured truncation."""

    input_tokens: int
    truncated_tokens: int

    @property
    def used_tokens(self) -> int:
        return self.input_tokens - self.truncated_tokens


@dataclass(frozen=True)
class PairTokenUsage:
    query_tokens: int
    query_truncated_tokens: int
    document_tokens: int
    document_truncated_tokens: int
    pair_tokens: int
    pair_truncated_tokens: int

    @property
    def truncated(self) -> bool:
        return self.pair_truncated_tokens > 0


def snapshot_path(
    model_id: str,
    revision: str,
    cache_root: str | Path = DEFAULT_HUB_CACHE,
) -> Path:
    """Return only the requested local snapshot; never download or fall back."""
    repository = "models--" + model_id.replace("/", "--")
    path = Path(cache_root) / repository / "snapshots" / revision
    if not path.is_dir():
        raise FileNotFoundError(
            f"Required local model snapshot is missing: {model_id}@{revision} "
            f"(looked in {path})"
        )
    if not (path / "config.json").is_file():
        raise FileNotFoundError(f"Model snapshot has no config.json: {path}")
    if not any(
        (path / name).is_file() for name in ("model.safetensors", "pytorch_model.bin")
    ):
        raise FileNotFoundError(f"Model snapshot has no local weight file: {path}")
    return path.resolve()


def configure_torch(threads: int) -> Any:
    """Set a deterministic CPU thread budget without selecting a GPU."""
    if threads < 1:
        raise ValueError("threads must be at least 1")
    import torch

    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(threads)
    except RuntimeError:
        # Torch allows this setting only before its first parallel operation.
        pass
    return torch


def _check_device(device: str, torch: Any) -> None:
    if device not in {"cpu", "cuda"}:
        raise ValueError("device must be 'cpu' or 'cuda'")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")


def _force_float32(model: Any, torch: Any) -> None:
    model.to(dtype=torch.float32)
    bad = [
        str(parameter.dtype)
        for parameter in model.parameters()
        if parameter.dtype != torch.float32
    ]
    if bad:
        raise RuntimeError(f"Model parameters are not all float32: {bad[0]}")


def _length(tokenizer: Any, text: str, *, special_tokens: bool) -> int:
    encoded = tokenizer(
        text,
        add_special_tokens=special_tokens,
        truncation=False,
        return_attention_mask=False,
    )
    return len(encoded["input_ids"])


class SnowflakeEmbedder:
    """SentenceTransformer adapter pinned to the local Snowflake snapshot."""

    def __init__(
        self,
        *,
        device: str = "cpu",
        threads: int = 4,
        cache_root: str | Path = DEFAULT_HUB_CACHE,
    ) -> None:
        torch = configure_torch(threads)
        _check_device(device, torch)
        from sentence_transformers import SentenceTransformer

        path = snapshot_path(EMBEDDING_MODEL_ID, EMBEDDING_REVISION, cache_root)
        self.device = device
        self.path = path
        self.model = SentenceTransformer(
            str(path),
            device=device,
            local_files_only=True,
            model_kwargs={"torch_dtype": torch.float32},
        )
        self.model.max_seq_length = MAX_MODEL_TOKENS
        _force_float32(self.model, torch)
        self.tokenizer = self.model.tokenizer
        self.query_prefix = self.model.prompts.get("query", "")
        self.dimension = int(self.model.get_sentence_embedding_dimension())
        self.dtype = str(next(self.model.parameters()).dtype)

    def token_usage(self, text: str, *, query: bool) -> TokenUsage:
        value = (self.query_prefix + text) if query else text
        count = _length(self.tokenizer, value, special_tokens=True)
        return TokenUsage(count, max(0, count - MAX_MODEL_TOKENS))

    def encode(
        self,
        texts: Sequence[str],
        *,
        query: bool = False,
        batch_size: int = 8,
    ) -> tuple[np.ndarray, list[TokenUsage]]:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32), []
        prompt = self.query_prefix if query and self.query_prefix else None
        matrix = self.model.encode(
            list(texts),
            prompt=prompt,
            batch_size=batch_size,
            show_progress_bar=False,
            precision="float32",
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        matrix = np.asarray(matrix, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape != (len(texts), self.dimension):
            raise RuntimeError(
                f"Unexpected embedding shape {matrix.shape}; expected "
                f"({len(texts)}, {self.dimension})"
            )
        if not np.isfinite(matrix).all():
            raise RuntimeError("Embedding model produced a non-finite vector")
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(norms).all() or np.any(norms == 0):
            raise RuntimeError("Embedding model produced an invalid vector norm")
        return matrix, [self.token_usage(text, query=query) for text in texts]

    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": EMBEDDING_MODEL_ID,
            "revision": EMBEDDING_REVISION,
            "snapshot_path": str(self.path),
            "device": self.device,
            "dtype": self.dtype,
            "max_tokens": MAX_MODEL_TOKENS,
            "dimension": self.dimension,
            "query_prefix": self.query_prefix,
            "normalize_embeddings": True,
        }


class KureReranker:
    """CrossEncoder adapter pinned to the local KURE nano snapshot."""

    def __init__(
        self,
        *,
        device: str = "cpu",
        threads: int = 4,
        cache_root: str | Path = DEFAULT_HUB_CACHE,
    ) -> None:
        torch = configure_torch(threads)
        _check_device(device, torch)
        from sentence_transformers import CrossEncoder

        path = snapshot_path(RERANK_MODEL_ID, RERANK_REVISION, cache_root)
        self.device = device
        self.path = path
        self.model = CrossEncoder(
            str(path),
            device=device,
            local_files_only=True,
            max_length=MAX_MODEL_TOKENS,
            model_kwargs={"torch_dtype": torch.float32},
        )
        _force_float32(self.model.model, torch)
        self.tokenizer = self.model.tokenizer
        self.max_length = MAX_MODEL_TOKENS
        self.dtype = str(next(self.model.model.parameters()).dtype)
        self._pair_special_tokens = int(
            self.tokenizer.num_special_tokens_to_add(pair=True)
        )

    def pair_token_usage(self, query: str, document: str) -> PairTokenUsage:
        query_ids = self.tokenizer(
            query,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
        )["input_ids"]
        document_ids = self.tokenizer(
            document,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
        )["input_ids"]
        before = len(query_ids) + len(document_ids) + self._pair_special_tokens
        to_remove = max(0, before - self.max_length)
        if to_remove:
            kept_query, kept_document, _ = self.tokenizer.truncate_sequences(
                query_ids,
                pair_ids=document_ids,
                num_tokens_to_remove=to_remove,
                truncation_strategy="longest_first",
                stride=0,
            )
        else:
            kept_query, kept_document = query_ids, document_ids
        after = len(kept_query) + len(kept_document) + self._pair_special_tokens
        return PairTokenUsage(
            query_tokens=len(query_ids),
            query_truncated_tokens=len(query_ids) - len(kept_query),
            document_tokens=len(document_ids),
            document_truncated_tokens=len(document_ids) - len(kept_document),
            pair_tokens=after,
            pair_truncated_tokens=before - after,
        )

    def score_pairs(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        batch_size: int = 4,
    ) -> tuple[np.ndarray, list[PairTokenUsage]]:
        if not pairs:
            return np.empty((0,), dtype=np.float32), []
        usages = [self.pair_token_usage(query, document) for query, document in pairs]
        scores = self.model.predict(
            list(pairs),
            batch_size=batch_size,
            show_progress_bar=False,
            apply_softmax=False,
            convert_to_numpy=True,
            device=self.device,
        )
        scores = np.asarray(scores, dtype=np.float32)
        if scores.ndim == 2:
            if scores.shape[1] != 1:
                raise RuntimeError(
                    "KURE snapshot returned multiple logits; expected a scalar "
                    "rerank score per pair"
                )
            scores = scores[:, 0]
        scores = scores.reshape(-1)
        if scores.shape != (len(pairs),):
            raise RuntimeError(
                f"Unexpected reranker output shape {scores.shape}; "
                f"expected ({len(pairs)},)"
            )
        if not np.isfinite(scores).all():
            raise RuntimeError("Reranker produced a non-finite score")
        return scores, usages

    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": RERANK_MODEL_ID,
            "revision": RERANK_REVISION,
            "snapshot_path": str(self.path),
            "device": self.device,
            "dtype": self.dtype,
            "max_pair_tokens": self.max_length,
            "scoring": "raw single-logit",
        }
