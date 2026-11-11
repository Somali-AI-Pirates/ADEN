"""Fresh-origin A/B/C retrieval runner with optional page-relation arm D.

The CLI consumes only the current raw-page extraction, the fresh record-unit
manifest, and question id/question pairs. It does not read scoring references.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import resource
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .models import (
    DEFAULT_HUB_CACHE,
    EMBEDDING_MODEL_ID,
    EMBEDDING_REVISION,
    MAX_MODEL_TOKENS,
    KureReranker,
    SnowflakeEmbedder,
)

EXPERIMENT_ROOT = Path("artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111")
DEFAULT_PAGES = EXPERIMENT_ROOT / "pages.jsonl"
DEFAULT_UNITS = EXPERIMENT_ROOT / "units.jsonl"
DEFAULT_RELATIONS = EXPERIMENT_ROOT / "relation_index.json"
DEFAULT_QUESTIONS = EXPERIMENT_ROOT / "evaluation/questions.jsonl"
DEFAULT_VECTORS = EXPERIMENT_ROOT / "vectors-v1"
DEFAULT_RESULTS = EXPERIMENT_ROOT / "run-v1/results.jsonl"

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60
RETRIEVER_TOP_K = 50
CANDIDATE_LIMIT = 20
MAX_RELATED_UNITS = 8
MAX_RELATED_EVENTS = 2
MAX_FINAL_SOURCES = 8
MAX_CONTEXT_TOKENS = 8000
SPARSE_CODE_PREFIX = "code:"
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]+")
_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+(?:[-/_.:][A-Za-z0-9]+)+)(?![A-Za-z0-9])"
)


@dataclass(frozen=True)
class Unit:
    unit_id: str
    text: str
    sources: tuple[str, ...]
    corpus: str
    title: str = ""
    kind: str = ""
    event_ids: tuple[str, ...] = ()
    line_spans: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Question:
    question_id: Any
    text: str


@dataclass
class _ConditionState:
    condition: str
    sparse_top: list[str]
    dense_top: list[str]
    hybrid: list[str]
    candidates: list[str]
    related: list[str] = field(default_factory=list)
    relation: dict[str, Any] = field(
        default_factory=lambda: {"status": "not_applicable", "event_ids": []}
    )


def jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rss_peak_kb() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _hash_json(value: Any) -> str:
    packed = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def load_pages(
    path: str | Path,
) -> tuple[list[Unit], dict[str, dict[str, Any]], dict[str, int]]:
    pages: list[Unit] = []
    page_map: dict[str, dict[str, Any]] = {}
    source_order: dict[str, int] = {}
    for row in jsonl_rows(path):
        source_id = row.get("source_id")
        text = row.get("text")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"Page row missing source_id in {path}")
        if not isinstance(text, str):
            raise ValueError(f"Page {source_id} has no text")
        if source_id in page_map:
            raise ValueError(f"Duplicate page source_id {source_id}")
        page_map[source_id] = row
        source_order[source_id] = len(source_order)
        pages.append(
            Unit(
                unit_id=f"A:{source_id}:raw",
                text=text,
                sources=(source_id,),
                corpus="A",
                title=f"{row.get('book_id', '')}:{row.get('page', '')}",
                kind="raw_page",
                line_spans=(
                    {
                        "source_id": source_id,
                        "start": 1,
                        "end": max(1, len(row.get("lines", []))),
                    },
                ),
            )
        )
    if not pages:
        raise ValueError(f"No raw page rows found in {path}")
    return pages, page_map, source_order


def load_record_units(path: str | Path) -> list[Unit]:
    units: list[Unit] = []
    seen: set[str] = set()
    for row in jsonl_rows(path):
        if row.get("corpus") != "B":
            continue
        unit_id = row.get("unit_id")
        text = row.get("text")
        sources = row.get("sources")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError(f"Record unit missing unit_id in {path}")
        if unit_id in seen:
            raise ValueError(f"Duplicate record unit_id {unit_id}")
        if not isinstance(text, str):
            raise ValueError(f"Record unit {unit_id} has no text")
        if not isinstance(sources, list) or not all(
            isinstance(s, str) for s in sources
        ):
            raise ValueError(f"Record unit {unit_id} has invalid sources")
        seen.add(unit_id)
        spans = row.get("line_spans", [])
        if not isinstance(spans, list):
            raise ValueError(f"Record unit {unit_id} has invalid line_spans")
        units.append(
            Unit(
                unit_id=unit_id,
                text=text,
                sources=tuple(dict.fromkeys(sources)),
                corpus="B",
                title=str(row.get("title") or ""),
                kind=str(row.get("kind") or ""),
                event_ids=tuple(str(x) for x in row.get("event_ids", [])),
                line_spans=tuple(x for x in spans if isinstance(x, dict)),
            )
        )
    if not units:
        raise ValueError(f"No fresh B record units found in {path}")
    return units


def load_questions(path: str | Path) -> list[Question]:
    questions: list[Question] = []
    seen_ids: set[str] = set()
    for row in jsonl_rows(path):
        if "id" not in row or not isinstance(row.get("question"), str):
            raise ValueError(f"Question row must contain id and question in {path}")
        key = json.dumps(row["id"], ensure_ascii=False, sort_keys=True)
        if key in seen_ids:
            raise ValueError(f"Duplicate question id {row['id']!r}")
        seen_ids.add(key)
        # Deliberately project to id/question only; references never enter retrieval.
        questions.append(Question(row["id"], row["question"]))
    if not questions:
        raise ValueError(f"No questions found in {path}")
    return questions


def normalize_code(value: str) -> str:
    """Remove punctuation and whitespace while preserving Unicode and letter case."""
    return "".join(character for character in value if character.isalnum())


def tokenize(text: str) -> list[str]:
    """Korean word/bigram terms plus complete case-sensitive code terms."""
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        tokens.append(f"word:{word.casefold()}")
        for run in _HANGUL_RE.findall(word):
            if len(run) == 1:
                tokens.append(f"ko:{run}")
            else:
                tokens.extend(f"ko:{run[i : i + 2]}" for i in range(len(run) - 1))
        if any(character.isdigit() for character in word):
            tokens.append(f"{SPARSE_CODE_PREFIX}exact:{word}")
            tokens.append(f"{SPARSE_CODE_PREFIX}norm:{normalize_code(word)}")
    for match in _CODE_RE.finditer(text):
        raw_code = match.group(0)
        tokens.append(f"{SPARSE_CODE_PREFIX}exact:{raw_code}")
        tokens.append(f"{SPARSE_CODE_PREFIX}norm:{normalize_code(raw_code)}")
    return tokens


class BM25Index:
    def __init__(
        self,
        docs: Sequence[Unit],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
    ) -> None:
        if not docs:
            raise ValueError("BM25 requires at least one document")
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid BM25 parameters")
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(doc.text) for doc in self.docs]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.average_length = sum(self.doc_lengths) / len(self.doc_lengths) or 1.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.document_frequency: Counter[str] = Counter()
        for index, tokens in enumerate(self.doc_tokens):
            counts = Counter(tokens)
            for term, count in counts.items():
                self.postings[term].append((index, count))
                self.document_frequency[term] += 1

    def scores(self, query: str) -> np.ndarray:
        result = np.zeros(len(self.docs), dtype=np.float64)
        query_terms = set(tokenize(query))
        corpus_size = len(self.docs)
        for term in query_terms:
            df = self.document_frequency.get(term, 0)
            if not df:
                continue
            inverse_document_frequency = math.log1p(
                (corpus_size - df + 0.5) / (df + 0.5)
            )
            for index, term_frequency in self.postings[term]:
                length_norm = (
                    1 - self.b + self.b * self.doc_lengths[index] / self.average_length
                )
                denominator = term_frequency + self.k1 * length_norm
                result[index] += (
                    inverse_document_frequency
                    * term_frequency
                    * (self.k1 + 1)
                    / denominator
                )
        return result


def rank_scores(
    docs: Sequence[Unit],
    scores: Sequence[float] | np.ndarray,
    *,
    top_k: int,
) -> list[str]:
    if len(docs) != len(scores):
        raise ValueError("Score vector length does not match document count")
    order = sorted(
        range(len(docs)),
        key=lambda index: (-float(scores[index]), docs[index].unit_id),
    )
    return [docs[index].unit_id for index in order[: max(0, top_k)]]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    limit: int = CANDIDATE_LIMIT,
    k: int = RRF_K,
) -> list[str]:
    totals: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, unit_id in enumerate(ranking, 1):
            totals[unit_id] += 1.0 / (k + rank)
    return sorted(totals, key=lambda unit_id: (-totals[unit_id], unit_id))[:limit]


class DenseIndex:
    def __init__(self, docs: Sequence[Unit], vectors: np.ndarray) -> None:
        if vectors.ndim != 2 or vectors.shape[0] != len(docs):
            raise ValueError("Dense vector shape does not match document count")
        if not np.isfinite(vectors).all():
            raise ValueError("Dense vectors contain NaN or infinity")
        self.docs = list(docs)
        self.vectors = vectors
        self.norms = np.linalg.norm(vectors, axis=1)
        if not np.isfinite(self.norms).all() or np.any(self.norms == 0):
            raise ValueError("Dense vectors contain an invalid norm")

    def scores(self, query_vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.vectors.shape[1]:
            raise ValueError("Query and index vector dimensions differ")
        if not np.isfinite(vector).all():
            raise ValueError("Query vector contains NaN or infinity")
        query_norm = float(np.linalg.norm(vector))
        if query_norm == 0:
            raise ValueError("Query embedding has zero norm")
        dot_products = self.vectors @ vector
        return dot_products / (self.norms * query_norm)


def _model_descriptor(embedder: SnowflakeEmbedder) -> dict[str, Any]:
    return {
        "model_id": EMBEDDING_MODEL_ID,
        "revision": EMBEDDING_REVISION,
        "max_tokens": MAX_MODEL_TOKENS,
        "dtype": "float32",
        "dimension": embedder.dimension,
        "normalize_embeddings": True,
        "document_prompt": "",
        "tokenizer_name": type(embedder.tokenizer).__name__,
    }


def _corpus_fingerprint(
    corpus: str,
    docs: Sequence[Unit],
    model: Mapping[str, Any],
    *,
    chunk_size: int,
    embedding_batch_size: int,
) -> str:
    rows = [
        {"unit_id": doc.unit_id, "text_sha256": sha256_text(doc.text)} for doc in docs
    ]
    return _hash_json(
        {
            "format": "fresh-record-hybrid-vectors-v1",
            "corpus": corpus,
            "model": model,
            "units": rows,
            "embedding_batch_size": embedding_batch_size,
            "chunk_size": chunk_size,
        }
    )


def _load_progress(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid vector progress file {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid vector progress file {path}")
    return value


def _done_batches(progress: Mapping[str, Any], count: int, chunk_size: int) -> set[int]:
    done: set[int] = set()
    for item in progress.get("completed_batches", []):
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], int)
            and isinstance(item[1], int)
            and item[0] >= 0
            and item[1] > item[0]
            and item[1] <= count
            and item[0] % chunk_size == 0
            and item[1] == min(count, item[0] + chunk_size)
        ):
            done.add(item[0])
    return done


def build_vectors(
    *,
    corpus: str,
    pages_path: str | Path,
    units_path: str | Path,
    vectors_dir: str | Path,
    device: str,
    threads: int,
    cache_root: str | Path = DEFAULT_HUB_CACHE,
    chunk_size: int = 100,
    embedding_batch_size: int = 4,
) -> dict[str, Any]:
    if corpus not in {"A", "B", "all"}:
        raise ValueError("corpus must be A, B, or all")
    if chunk_size < 1 or embedding_batch_size < 1:
        raise ValueError("chunk and embedding batch sizes must be positive")
    from threadpoolctl import threadpool_limits

    threadpool_limits(limits=threads)
    vectors_root = Path(vectors_dir)
    vectors_root.mkdir(parents=True, exist_ok=True)
    raw_docs, _, _ = load_pages(pages_path)
    doc_sets: dict[str, list[Unit]] = {"A": raw_docs}
    if corpus in {"B", "all"}:
        doc_sets["B"] = load_record_units(units_path)
    model_load_start = time.perf_counter()
    embedder = SnowflakeEmbedder(device=device, threads=threads, cache_root=cache_root)
    model_load_seconds = time.perf_counter() - model_load_start
    descriptor = _model_descriptor(embedder)
    existing_cache: dict[str, tuple[np.ndarray, int]] = {}
    summaries: dict[str, Any] = {}
    for current in ("A", "B"):
        if current not in doc_sets:
            continue
        docs = sorted(doc_sets[current], key=lambda item: item.unit_id)
        fingerprint = _corpus_fingerprint(
            current,
            docs,
            descriptor,
            chunk_size=chunk_size,
            embedding_batch_size=embedding_batch_size,
        )
        count = len(docs)
        vector_path = vectors_root / f"{current}.vectors.npy"
        progress_path = vectors_root / f"{current}.progress.json"
        usage_path = vectors_root / f"{current}.token_usage.jsonl"
        progress = _load_progress(progress_path)
        if progress is not None:
            if progress.get("fingerprint") != fingerprint:
                raise ValueError(
                    f"Refusing to resume {current}: vector fingerprint differs "
                    f"from fresh input/model ({progress_path})"
                )
            if (
                progress.get("unit_count") != count
                or progress.get("dimension") != embedder.dimension
            ):
                raise ValueError(f"Vector manifest dimensions changed: {progress_path}")
            if progress.get("chunk_size") != chunk_size:
                raise ValueError(f"Vector chunk size changed: {progress_path}")
            if progress.get("embedding_batch_size") != embedding_batch_size:
                raise ValueError(f"Embedding batch size changed: {progress_path}")
            if not vector_path.is_file():
                raise FileNotFoundError(
                    f"Vector progress exists but vectors are missing: {vector_path}"
                )
            if progress.get("status") == "complete":
                if not progress.get("vectors_sha256"):
                    raise ValueError("Completed vector index has no file checksum")
                if sha256_file(vector_path) != progress["vectors_sha256"]:
                    raise ValueError(f"Vector file SHA-256 mismatch: {vector_path}")
            vectors = np.load(vector_path, mmap_mode="r+", allow_pickle=False)
            if vectors.shape != (count, embedder.dimension):
                raise ValueError(f"Vector matrix shape changed: {vector_path}")
            done = _done_batches(progress, count, chunk_size)
            if progress.get("status") != "complete":
                saved_hashes = progress.get("completed_batch_sha256", {})
                for batch_start in done:
                    batch_end = min(count, batch_start + chunk_size)
                    actual_hash = hashlib.sha256(
                        vectors[batch_start:batch_end].tobytes()
                    ).hexdigest()
                    if saved_hashes.get(str(batch_start)) != actual_hash:
                        raise ValueError(
                            "Saved vector batch checksum is missing or mismatched; use a fresh output directory"
                        )
        else:
            if vector_path.exists():
                raise ValueError(
                    f"Refusing to replace unmanifested vector data: {vector_path}"
                )
            vectors = np.lib.format.open_memmap(
                vector_path,
                mode="w+",
                dtype=np.float32,
                shape=(count, embedder.dimension),
            )
            done = set()
            progress = {
                "format": "fresh-record-hybrid-vectors-v1",
                "corpus": current,
                "fingerprint": fingerprint,
                "unit_count": count,
                "dimension": embedder.dimension,
                "chunk_size": chunk_size,
                "embedding_batch_size": embedding_batch_size,
                "model": descriptor,
                "completed_batches": [],
                "status": "building",
            }
            write_json_atomic(progress_path, progress)

        hashes = [sha256_text(doc.text) for doc in docs]
        token_usage = [embedder.token_usage(doc.text, query=False) for doc in docs]
        if usage_path.exists():
            usage_rows = jsonl_rows(usage_path)
            if len(usage_rows) != count or any(
                row.get("text_sha256") != hashes[index]
                for index, row in enumerate(usage_rows)
            ):
                raise ValueError(
                    f"Document token usage does not match fresh inputs: {usage_path}"
                )
        else:
            temporary_usage = usage_path.with_suffix(usage_path.suffix + ".tmp")
            with temporary_usage.open("w", encoding="utf-8", newline="\n") as handle:
                for doc, content_hash, usage in zip(docs, hashes, token_usage):
                    handle.write(
                        json.dumps(
                            {
                                "unit_id": doc.unit_id,
                                "text_sha256": content_hash,
                                "input_tokens": usage.input_tokens,
                                "truncated_tokens": usage.truncated_tokens,
                                "used_tokens": usage.used_tokens,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_usage, usage_path)

        # Recover completed rows from this fresh run; hashes are exact-text only.
        for batch_start in sorted(done):
            batch_end = min(count, batch_start + chunk_size)
            for row_index in range(batch_start, batch_end):
                existing_cache.setdefault(hashes[row_index], (vectors, row_index))

        embedded_unique = 0
        reused_vectors = sum(min(count, start + chunk_size) - start for start in done)
        unique_text_count = len(set(hashes))
        started = time.perf_counter()
        for start in range(0, count, chunk_size):
            end = min(count, start + chunk_size)
            if start in done:
                continue
            unique_texts: list[str] = []
            unique_hashes: list[str] = []
            first_new_row: dict[str, int] = {}
            row_hashes = hashes[start:end]
            for row_index, content_hash in zip(range(start, end), row_hashes):
                if (
                    content_hash not in existing_cache
                    and content_hash not in first_new_row
                ):
                    first_new_row[content_hash] = row_index
                    unique_texts.append(docs[row_index].text)
                    unique_hashes.append(content_hash)
            if unique_texts:
                new_vectors, _usages = embedder.encode(
                    unique_texts,
                    query=False,
                    batch_size=embedding_batch_size,
                )
                embedded_unique += len(unique_texts)
                for offset, content_hash in enumerate(unique_hashes):
                    row_index = first_new_row[content_hash]
                    vectors[row_index] = new_vectors[offset]
                    existing_cache[content_hash] = (vectors, row_index)
            new_rows = set(first_new_row.values())
            for row_index in range(start, end):
                if row_index in new_rows:
                    continue
                cached = existing_cache.get(hashes[row_index])
                if cached is None:
                    raise RuntimeError(f"Missing fresh vector for row {row_index}")
                source_matrix, source_row = cached
                vectors[row_index] = source_matrix[source_row]
                reused_vectors += 1
            vectors.flush()
            done.add(start)
            progress.setdefault("completed_batch_sha256", {})[str(start)] = (
                hashlib.sha256(vectors[start:end].tobytes()).hexdigest()
            )
            completed = sorted(done)
            progress["completed_batches"] = [
                [batch_start, min(count, batch_start + chunk_size)]
                for batch_start in completed
            ]
            progress["status"] = "building"
            progress["last_progress_utc"] = datetime.now(timezone.utc).isoformat()
            write_json_atomic(progress_path, progress)
            elapsed = time.perf_counter() - started
            print(
                f"vectors {current}: {end}/{count} units, "
                f"{len(unique_texts)} new texts in chunk, elapsed={elapsed:.1f}s",
                flush=True,
            )
        vectors.flush()
        completed_batches = _done_batches(progress, count, chunk_size)
        vectors_sha256 = sha256_file(vector_path)
        token_usage_sha256 = sha256_file(usage_path)
        progress["vectors_sha256"] = vectors_sha256
        progress["vector_file_size_bytes"] = vector_path.stat().st_size
        progress["token_usage_sha256"] = token_usage_sha256
        summary = {
            "unit_count": count,
            "unique_text_count": unique_text_count,
            "new_unique_texts_embedded_this_invocation": embedded_unique,
            "reused_vector_count_this_invocation": reused_vectors,
            "completed_batches": len(completed_batches),
            "elapsed_seconds_this_invocation": round(time.perf_counter() - started, 3),
            "vector_path": str(vector_path),
            "vectors_sha256": vectors_sha256,
            "vector_file_size_bytes": vector_path.stat().st_size,
            "token_usage_path": str(usage_path),
            "token_usage_sha256": token_usage_sha256,
            "fingerprint": fingerprint,
        }
        progress["status"] = (
            "complete"
            if len(completed_batches) == math.ceil(count / chunk_size)
            else "building"
        )
        progress["summary"] = summary
        progress["completed_utc"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(progress_path, progress)
        summaries[current] = summary
        if current == "A":
            for row_index, content_hash in enumerate(hashes):
                existing_cache.setdefault(content_hash, (vectors, row_index))
    run_manifest = {
        "format": "fresh-record-hybrid-vector-build-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "device": device,
        "threads": threads,
        "embedding_batch_size": embedding_batch_size,
        "chunk_size": chunk_size,
        "model": embedder.metadata(),
        "model_load_seconds": round(model_load_seconds, 3),
        "rss_peak_kb": _rss_peak_kb(),
        "corpora": summaries,
    }
    write_json_atomic(vectors_root / "build-manifest.json", run_manifest)
    return run_manifest


def load_vector_index(
    corpus: str,
    docs: Sequence[Unit],
    vectors_dir: str | Path,
    embedder: SnowflakeEmbedder,
) -> tuple[np.ndarray, dict[str, Any]]:
    root = Path(vectors_dir)
    progress_path = root / f"{corpus}.progress.json"
    vector_path = root / f"{corpus}.vectors.npy"
    progress = _load_progress(progress_path)
    if progress is None or progress.get("status") != "complete":
        raise ValueError(
            f"Vector build for corpus {corpus} is incomplete: {progress_path}"
        )
    ordered_docs = sorted(docs, key=lambda item: item.unit_id)
    expected = _corpus_fingerprint(
        corpus,
        ordered_docs,
        _model_descriptor(embedder),
        chunk_size=progress.get("chunk_size"),
        embedding_batch_size=progress.get("embedding_batch_size"),
    )
    if progress.get("fingerprint") != expected:
        raise ValueError(
            f"Vector fingerprint mismatch for corpus {corpus}; "
            "rebuild only from current inputs"
        )
    expected_file_sha = progress.get("vectors_sha256")
    if not expected_file_sha:
        raise ValueError("Completed vector index has no file checksum")
    if expected_file_sha and sha256_file(vector_path) != expected_file_sha:
        raise ValueError(f"Vector file SHA-256 mismatch for corpus {corpus}")
    vectors = np.load(vector_path, mmap_mode="r", allow_pickle=False)
    if vectors.shape != (len(ordered_docs), embedder.dimension):
        raise ValueError(f"Vector shape mismatch for corpus {corpus}")
    if not np.isfinite(vectors).all():
        raise ValueError(f"Vector data contains NaN or infinity for corpus {corpus}")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise ValueError(f"Corpus {corpus} vectors are not unit normalized")
    return vectors, progress


def _code_patterns(
    relation_index: Mapping[str, Any],
) -> dict[str, list[re.Pattern[str]]]:
    raw_by_normalized: dict[str, set[str]] = defaultdict(set)
    for event in relation_index.get("events", {}).values():
        for relation in event.get("relations", []):
            code = relation.get("code")
            normalized = relation.get("normalized_code") or (
                normalize_code(code) if isinstance(code, str) else ""
            )
            if normalized and isinstance(code, str):
                raw_by_normalized[normalized].add(code)
    for normalized in relation_index.get("code_to_events", {}):
        raw_by_normalized.setdefault(normalized, set()).add(normalized)
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for normalized, raw_codes in raw_by_normalized.items():
        patterns: list[re.Pattern[str]] = []
        for raw_code in sorted(raw_codes):
            components = _WORD_RE.findall(raw_code)
            if not components:
                continue
            separator = r"[\s\-./_:]*"
            body = separator.join(re.escape(component) for component in components)
            patterns.append(
                re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.UNICODE)
            )
        compiled[normalized] = patterns
    return compiled


def _has_code_continuation(query: str, start: int, end: int) -> bool:
    """Reject prefixes/suffixes within punctuation-separated complete codes."""
    if start > 0 and query[start - 1] in "-/_.:":
        prior = start - 2
        while prior >= 0 and query[prior].isspace():
            prior -= 1
        if prior >= 0 and (
            (query[prior].isascii() and query[prior].isalnum()) or query[prior] == "_"
        ):
            return True
    if end < len(query) and query[end] in "-/_.:":
        following = end + 1
        while following < len(query) and query[following].isspace():
            following += 1
        if following < len(query) and (
            (query[following].isascii() and query[following].isalnum())
            or query[following] == "_"
        ):
            return True
    previous = re.search(r"([^\W_]+)\s+$", query[:start], re.UNICODE)
    if previous:
        token = previous.group(1)
        if token.isdigit() or (token.isascii() and token.isupper()):
            return True
    following = re.match(r"\s+([^\W_]+)\b", query[end:], re.UNICODE)
    if following:
        token = following.group(1)
        if token.isdigit() or (token.isascii() and token.isupper()):
            return True
    return False


def find_explicit_codes(
    query: str,
    relation_index: Mapping[str, Any],
    *,
    patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
) -> list[tuple[str, int]]:
    """Find known complete code variants; return normalized key and query offset."""
    matched: dict[str, int] = {}
    for normalized, code_patterns in (
        patterns or _code_patterns(relation_index)
    ).items():
        for pattern in code_patterns:
            for match in pattern.finditer(query):
                if _has_code_continuation(query, match.start(), match.end()):
                    continue
                matched[normalized] = min(
                    matched.get(normalized, match.start()), match.start()
                )
                break
    return sorted(matched.items(), key=lambda item: (item[1], item[0]))


def _span_overlaps(
    span: Mapping[str, Any], source_id: str, start: int, end: int
) -> bool:
    if span.get("source_id") != source_id:
        return False
    try:
        left = int(span["start"])
        right = int(span["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return left <= end and start <= right


def related_unit_ids(
    query: str,
    base_candidates: Sequence[str],
    docs: Sequence[Unit],
    relation_index: Mapping[str, Any],
    source_order: Mapping[str, int],
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
    *,
    page_units_by_source: Mapping[str, str] | None = None,
    include_existing: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    mapping = relation_index.get("code_to_events", {})
    events = relation_index.get("events", {})
    if not isinstance(mapping, Mapping) or not isinstance(events, Mapping):
        raise ValueError("Relation index must contain code_to_events and events maps")
    explicit = find_explicit_codes(query, relation_index, patterns=code_patterns)
    if not explicit:
        return [], {"status": "no_explicit_code", "event_ids": [], "matched_codes": []}

    selected_events: list[str] = []
    matched_codes: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    for code, _offset in explicit:
        event_ids = mapping.get(code, [])
        if not isinstance(event_ids, list):
            raise ValueError(f"Invalid event list for normalized code {code}")
        unique_events = sorted(set(str(event_id) for event_id in event_ids))
        matched_codes.append(code)
        if len(unique_events) != 1:
            if len(unique_events) > 1:
                ambiguous.append({"code": code, "event_ids": unique_events})
            continue
        event_id = unique_events[0]
        if event_id not in events:
            raise ValueError(f"Relation index references missing event {event_id}")
        if event_id not in selected_events:
            selected_events.append(event_id)
    if ambiguous:
        return [], {
            "status": "ambiguous_code",
            "event_ids": [],
            "matched_codes": matched_codes,
            "ambiguous": ambiguous,
            "added_unit_ids": [],
        }
    if not selected_events:
        return [], {
            "status": "unlinked_code",
            "event_ids": [],
            "matched_codes": matched_codes,
            "added_unit_ids": [],
        }
    if len(selected_events) > MAX_RELATED_EVENTS:
        return [], {
            "status": "too_many_events_unsupported",
            "event_ids": selected_events,
            "matched_codes": matched_codes,
            "added_unit_ids": [],
        }

    doc_by_id = {doc.unit_id: doc for doc in docs}
    base_set = set(base_candidates)
    relation_lists: list[list[tuple[tuple[Any, ...], str, dict[str, Any]]]] = []
    for event_id in selected_events:
        event = events[event_id]
        event_units = set(str(unit_id) for unit_id in event.get("unit_ids", []))
        eligible: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
        for relation in event.get("relations", []):
            source_id = relation.get("source_id")
            try:
                start = int(relation["start"])
                end = int(relation["end"])
            except (KeyError, TypeError, ValueError):
                continue
            matching_units: list[Unit] = []
            for unit_id in event_units:
                doc = doc_by_id.get(unit_id)
                if doc is not None and any(
                    _span_overlaps(span, str(source_id), start, end)
                    for span in doc.line_spans
                ):
                    matching_units.append(doc)
            if not matching_units:
                continue
            unit = sorted(matching_units, key=lambda item: item.unit_id)[0]
            role = str(relation.get("role") or "")
            source_key = (
                source_order.get(str(source_id), 10**9),
                str(source_id),
            )
            normalized = str(
                relation.get("normalized_code")
                or normalize_code(str(relation.get("code") or ""))
            )
            proof = {
                "event_id": event_id,
                "unit_id": unit.unit_id,
                "unit_kind": unit.kind,
                "source_id": str(source_id),
                "start": start,
                "end": end,
                "code": str(relation.get("code") or ""),
                "normalized_code": normalized,
                "edge_role": role,
            }
            order_key = (
                unit.kind,
                source_key,
                unit.unit_id,
                start,
                end,
                normalized,
            )
            candidate_id = unit.unit_id
            if page_units_by_source is not None:
                # Preserve record-role ordering, but deduplicate pages before the cap.
                try:
                    candidate_id = page_units_by_source[str(source_id)]
                except KeyError as exc:
                    raise ValueError(
                        f"Relation source has no A page: {source_id}"
                    ) from exc
                proof = {
                    **proof,
                    "record_unit_id": unit.unit_id,
                    "unit_id": candidate_id,
                }
            eligible.append((order_key, candidate_id, proof))
        relation_lists.append(sorted(eligible, key=lambda item: item[0]))

    added: list[str] = []
    expanded_relations: list[dict[str, Any]] = []
    cursors = [0] * len(relation_lists)
    while len(added) < MAX_RELATED_UNITS and any(
        cursor < len(items) for cursor, items in zip(cursors, relation_lists)
    ):
        progressed = False
        for list_index, items in enumerate(relation_lists):
            cursor = cursors[list_index]
            while cursor < len(items):
                _order, unit_id, proof = items[cursor]
                cursor += 1
                cursors[list_index] = cursor
                progressed = True
                if unit_id in base_set or unit_id in added:
                    continue
                added.append(unit_id)
                expanded_relations.append(proof)
                break
            if len(added) >= MAX_RELATED_UNITS:
                break
        if not progressed:
            break

    metadata = {
        "status": "expanded" if added else "unique_no_additions",
        "event_ids": selected_events,
        "matched_codes": matched_codes,
        "added_unit_ids": added,
        "expanded_relations": expanded_relations,
    }
    if not include_existing:
        return added, metadata

    # Freeze D's new-page selection first. E only adds proven hybrid overlaps
    # to the relation ranking; it cannot introduce an unselected graph page.
    allowed = base_set | set(added)
    ranked: list[str] = []
    ranked_proofs: list[dict[str, Any]] = []
    cursors = [0] * len(relation_lists)
    while any(cursor < len(items) for cursor, items in zip(cursors, relation_lists)):
        for list_index, items in enumerate(relation_lists):
            cursor = cursors[list_index]
            while cursor < len(items):
                _order, unit_id, proof = items[cursor]
                cursor += 1
                cursors[list_index] = cursor
                if unit_id not in allowed or unit_id in ranked:
                    continue
                ranked.append(unit_id)
                ranked_proofs.append(proof)
                break
    return ranked, {
        **metadata,
        "overlap_unit_ids": [uid for uid in ranked if uid in base_set],
        "ranked_unit_ids": ranked,
        "ranking_relations": ranked_proofs,
    }


def _candidate_source_ids(
    candidate_ids: Sequence[str],
    docs_by_id: Mapping[str, Unit],
) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for unit_id in candidate_ids:
        for source_id in docs_by_id[unit_id].sources:
            if source_id not in seen:
                seen.add(source_id)
                output.append(source_id)
    return output


def _relation_condition_candidates(
    condition: str,
    query: str,
    base: Sequence[str],
    docs: Sequence[Unit],
    relation_index: Mapping[str, Any],
    source_order: Mapping[str, int],
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
    *,
    relation_docs: Sequence[Unit] | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    if condition not in ("C", "D", "E"):
        return list(base), [], {"status": "not_applicable", "event_ids": []}
    page_units_by_source = None
    if condition in ("D", "E"):
        if relation_docs is None:
            raise ValueError(
                "D/E requires the fresh B record units for relation proofs"
            )
        page_units_by_source = {
            source: unit.unit_id for unit in docs for source in unit.sources
        }
    related, relation = related_unit_ids(
        query,
        base,
        relation_docs if condition in ("D", "E") else docs,
        relation_index,
        source_order,
        code_patterns=code_patterns,
        page_units_by_source=page_units_by_source,
        include_existing=condition == "E",
    )
    if not related:
        return list(base), [], relation
    fused = reciprocal_rank_fusion([base, related], limit=CANDIDATE_LIMIT)
    return fused, related, relation


def _context_encoding() -> Any:
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError(
            "tiktoken is required for the o200k_base context budget"
        ) from exc
    return tiktoken.get_encoding("o200k_base")


def _encode_context_tokens(encoding: Any, text: str) -> list[int]:
    return encoding.encode(text, disallowed_special=())


def _decode_original_prefix(encoding: Any, tokens: Sequence[int], original: str) -> str:
    """Decode token prefixes without replacement characters or non-prefix text."""
    end = len(tokens)
    while end >= 0:
        value = encoding.decode(tokens[:end], errors="ignore")
        if original.startswith(value):
            return value
        end -= 1
    return ""


def _restore_context(
    ranked_unit_ids: Sequence[str],
    docs_by_id: Mapping[str, Unit],
    page_map: Mapping[str, Mapping[str, Any]],
    encoding: Any,
) -> tuple[list[dict[str, str]], int, str]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    pieces: list[str] = []
    for unit_id in ranked_unit_ids:
        doc = docs_by_id[unit_id]
        for source_id in doc.sources:
            if source_id in seen or source_id not in page_map:
                continue
            if len(sources) >= MAX_FINAL_SOURCES:
                break
            seen.add(source_id)
            page_text = str(page_map[source_id].get("text") or "")
            if not page_text:
                continue
            prefix = "\n\n".join(pieces)
            separator = "\n\n" if pieces else ""
            full_trial = prefix + separator + page_text
            if len(_encode_context_tokens(encoding, full_trial)) <= MAX_CONTEXT_TOKENS:
                piece = page_text
            else:
                page_tokens = _encode_context_tokens(encoding, page_text)
                low = 0
                high = len(page_tokens)
                while low < high:
                    middle = (low + high + 1) // 2
                    candidate = _decode_original_prefix(
                        encoding, page_tokens[:middle], page_text
                    )
                    trial = prefix + separator + candidate
                    if (
                        len(_encode_context_tokens(encoding, trial))
                        <= MAX_CONTEXT_TOKENS
                    ):
                        low = middle
                    else:
                        high = middle - 1
                if low == 0:
                    continue
                piece = _decode_original_prefix(encoding, page_tokens[:low], page_text)
            pieces.append(piece)
            sources.append({"source_id": source_id, "text": piece})
        if len(sources) >= MAX_FINAL_SOURCES:
            break
    context = "\n\n".join(pieces)
    context_tokens = len(_encode_context_tokens(encoding, context))
    if context_tokens > MAX_CONTEXT_TOKENS:
        raise RuntimeError("Restored context exceeds the 8000-token budget")
    return sources, context_tokens, hashlib.sha256(context.encode("utf-8")).hexdigest()


def _timed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _build_condition_state(
    condition: str,
    query: str,
    docs: list[Unit],
    vectors: np.ndarray,
    sparse: BM25Index,
    embedder: SnowflakeEmbedder,
    relation_index: Mapping[str, Any],
    source_order: Mapping[str, int],
    *,
    dense_index: DenseIndex | None = None,
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
    relation_docs: Sequence[Unit] | None = None,
) -> tuple[_ConditionState, dict[str, Any], dict[str, float]]:
    timings: dict[str, float] = {}
    start = time.perf_counter()
    sparse_scores = sparse.scores(query)
    sparse_top = rank_scores(docs, sparse_scores, top_k=RETRIEVER_TOP_K)
    timings["sparse"] = _timed_ms(start)

    start = time.perf_counter()
    query_vectors, query_usages = embedder.encode([query], query=True, batch_size=4)
    timings["embed_query"] = _timed_ms(start)

    start = time.perf_counter()
    dense = dense_index or DenseIndex(docs, vectors)
    dense_scores = dense.scores(query_vectors[0])
    dense_top = rank_scores(docs, dense_scores, top_k=RETRIEVER_TOP_K)
    timings["dense"] = _timed_ms(start)

    start = time.perf_counter()
    hybrid = reciprocal_rank_fusion([sparse_top, dense_top], limit=CANDIDATE_LIMIT)
    timings["rrf"] = _timed_ms(start)

    start = time.perf_counter()
    candidate_ids, related, relation = _relation_condition_candidates(
        condition,
        query,
        hybrid,
        docs,
        relation_index,
        source_order,
        code_patterns=code_patterns,
        relation_docs=relation_docs,
    )
    timings["relation_fusion"] = _timed_ms(start)
    state = _ConditionState(
        condition=condition,
        sparse_top=sparse_top,
        dense_top=dense_top,
        hybrid=hybrid,
        candidates=candidate_ids,
        related=related,
        relation=relation,
    )
    usage = query_usages[0]
    query_metadata = {
        "input_tokens": usage.input_tokens,
        "truncated_tokens": usage.truncated_tokens,
        "used_tokens": usage.used_tokens,
        "prefix": embedder.query_prefix,
    }
    return state, query_metadata, timings


def _run_condition(
    condition: str,
    query: str,
    docs: list[Unit],
    vectors: np.ndarray,
    sparse: BM25Index,
    docs_by_id: Mapping[str, Unit],
    page_map: Mapping[str, Mapping[str, Any]],
    source_order: Mapping[str, int],
    relation_index: Mapping[str, Any],
    embedder: SnowflakeEmbedder,
    reranker: KureReranker,
    encoding: Any,
    *,
    rerank_batch_size: int,
    dense_index: DenseIndex | None = None,
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
    relation_docs: Sequence[Unit] | None = None,
) -> dict[str, Any]:
    overall_start = time.perf_counter()
    state, query_embedding, timings = _build_condition_state(
        condition,
        query,
        docs,
        vectors,
        sparse,
        embedder,
        relation_index,
        source_order,
        dense_index=dense_index,
        code_patterns=code_patterns,
        relation_docs=relation_docs,
    )
    return _rerank_condition(
        state,
        query,
        docs_by_id,
        page_map,
        reranker,
        encoding,
        rerank_batch_size=rerank_batch_size,
        query_embedding=query_embedding,
        timings=timings,
        overall_start=overall_start,
    )


def _rerank_condition(
    state: _ConditionState,
    query: str,
    docs_by_id: Mapping[str, Unit],
    page_map: Mapping[str, Mapping[str, Any]],
    reranker: KureReranker,
    encoding: Any,
    *,
    rerank_batch_size: int,
    query_embedding: dict[str, Any],
    timings: dict[str, float],
    overall_start: float,
) -> dict[str, Any]:
    candidates = [docs_by_id[unit_id] for unit_id in state.candidates]
    pair_inputs = [(query, item.text) for item in candidates]
    start = time.perf_counter()
    rerank_scores, pair_usages = reranker.score_pairs(
        pair_inputs,
        batch_size=rerank_batch_size,
    )
    timings["rerank"] = _timed_ms(start)
    reranked = sorted(
        zip(state.candidates, rerank_scores.tolist()),
        key=lambda item: (-float(item[1]), item[0]),
    )
    reranked_ids = [unit_id for unit_id, _score in reranked]
    start = time.perf_counter()
    final_sources, context_tokens, context_sha256 = _restore_context(
        reranked_ids,
        docs_by_id,
        page_map,
        encoding,
    )
    timings["restore"] = _timed_ms(start)
    timings["total"] = round((time.perf_counter() - overall_start) * 1000, 3)

    rerank_metadata = {
        "pairs": len(pair_usages),
        "input_tokens": sum(usage.pair_tokens for usage in pair_usages),
        "truncated_pairs": sum(usage.truncated for usage in pair_usages),
        "query_tokens": sum(usage.query_tokens for usage in pair_usages),
        "query_truncated_tokens": sum(
            usage.query_truncated_tokens for usage in pair_usages
        ),
        "doc_tokens": sum(usage.document_tokens for usage in pair_usages),
        "doc_truncated_tokens": sum(
            usage.document_truncated_tokens for usage in pair_usages
        ),
    }
    return {
        "condition": state.condition,
        "bm25_top50": state.sparse_top,
        "dense_top50": state.dense_top,
        "hybrid_unit_ids": state.hybrid,
        "candidate_unit_ids": state.candidates,
        "candidate_source_ids": _candidate_source_ids(state.candidates, docs_by_id),
        "related_unit_ids": state.related,
        "reranked_unit_ids": reranked_ids,
        "rerank_order": [
            {"unit_id": unit_id, "score": float(score)} for unit_id, score in reranked
        ],
        "relation": state.relation,
        "final_sources": final_sources,
        "timings_ms": timings,
        "query_embedding": query_embedding,
        "rerank": rerank_metadata,
        "context_tokens": context_tokens,
        "context_sha256": context_sha256,
    }


def _input_fingerprints(
    pages_path: str | Path,
    units_path: str | Path,
    relations_path: str | Path,
    questions: Sequence[Question],
) -> dict[str, Any]:
    return {
        "pages_file_sha256": hashlib.sha256(Path(pages_path).read_bytes()).hexdigest(),
        "units_file_sha256": hashlib.sha256(Path(units_path).read_bytes()).hexdigest(),
        "relations_file_sha256": hashlib.sha256(
            Path(relations_path).read_bytes()
        ).hexdigest(),
        "id_question_sha256": _hash_json(
            [
                {"id": question.question_id, "question": question.text}
                for question in questions
            ]
        ),
    }


def _warmup(
    questions: Sequence[Question],
    docs_by_corpus: Mapping[str, list[Unit]],
    vectors_by_corpus: Mapping[str, np.ndarray],
    sparse_by_corpus: Mapping[str, BM25Index],
    docs_by_id: Mapping[str, Unit],
    page_map: Mapping[str, Mapping[str, Any]],
    source_order: Mapping[str, int],
    relation_index: Mapping[str, Any],
    embedder: SnowflakeEmbedder,
    reranker: KureReranker,
    encoding: Any,
    rerank_batch_size: int,
    *,
    dense_by_corpus: Mapping[str, DenseIndex] | None = None,
    code_patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
    conditions: Sequence[str] = ("A", "B", "C"),
    relation_docs: Sequence[Unit] | None = None,
) -> int:
    query = questions[0].text
    pair_count = 0
    for condition in conditions:
        corpus = "A" if condition in ("A", "D") else "B"
        result = _run_condition(
            condition,
            query,
            docs_by_corpus[corpus],
            vectors_by_corpus[corpus],
            sparse_by_corpus[corpus],
            docs_by_id,
            page_map,
            source_order,
            relation_index,
            embedder,
            reranker,
            encoding,
            rerank_batch_size=rerank_batch_size,
            dense_index=dense_by_corpus[corpus] if dense_by_corpus else None,
            code_patterns=code_patterns,
            relation_docs=relation_docs,
        )
        pair_count += result["rerank"]["pairs"]
    return pair_count


def retrieve(
    *,
    pages_path: str | Path,
    units_path: str | Path,
    relations_path: str | Path,
    questions_path: str | Path,
    vectors_dir: str | Path,
    results_path: str | Path,
    device: str,
    threads: int,
    cache_root: str | Path = DEFAULT_HUB_CACHE,
    rerank_batch_size: int = 8,
    warmup: bool = True,
    conditions: Sequence[str] = ("A", "B", "C"),
) -> dict[str, Any]:
    conditions = tuple(conditions)
    if (
        not conditions
        or len(set(conditions)) != len(conditions)
        or any(c not in ("A", "B", "C", "D") for c in conditions)
    ):
        raise ValueError("conditions must be a nonempty unique subset of A/B/C/D")
    corpora = sorted({"A" if c in ("A", "D") else "B" for c in conditions})
    if rerank_batch_size < 1:
        raise ValueError("rerank_batch_size must be positive")
    pages, page_map, source_order = load_pages(pages_path)
    records = load_record_units(units_path)
    questions = load_questions(questions_path)
    with Path(relations_path).open("r", encoding="utf-8") as handle:
        relation_index = json.load(handle)
    if not isinstance(relation_index, dict):
        raise ValueError("relation index must be a JSON object")

    docs_by_corpus = {
        "A": sorted(pages, key=lambda item: item.unit_id),
        "B": sorted(records, key=lambda item: item.unit_id),
    }
    docs_by_corpus = {c: docs_by_corpus[c] for c in corpora}
    docs_by_id: dict[str, Unit] = {}
    for docs in docs_by_corpus.values():
        for doc in docs:
            if doc.unit_id in docs_by_id:
                raise ValueError(f"Unit ID collides across corpora: {doc.unit_id}")
            docs_by_id[doc.unit_id] = doc

    model_load_start = time.perf_counter()
    embedder = SnowflakeEmbedder(device=device, threads=threads, cache_root=cache_root)
    reranker = KureReranker(device=device, threads=threads, cache_root=cache_root)
    model_load_seconds = time.perf_counter() - model_load_start
    vector_load_start = time.perf_counter()
    vector_by_corpus: dict[str, np.ndarray] = {}
    vector_progress: dict[str, Any] = {}
    for corpus in corpora:
        vectors, progress = load_vector_index(
            corpus,
            docs_by_corpus[corpus],
            vectors_dir,
            embedder,
        )
        vector_by_corpus[corpus] = vectors
        vector_progress[corpus] = progress
    vector_load_seconds = time.perf_counter() - vector_load_start
    index_prepare_start = time.perf_counter()
    dense_by_corpus = {
        corpus: DenseIndex(docs, vector_by_corpus[corpus])
        for corpus, docs in docs_by_corpus.items()
    }
    code_patterns = _code_patterns(relation_index)
    sparse_by_corpus = {
        corpus: BM25Index(docs) for corpus, docs in docs_by_corpus.items()
    }
    encoding = _context_encoding()
    index_prepare_seconds = time.perf_counter() - index_prepare_start
    try:
        from threadpoolctl import threadpool_info, threadpool_limits
    except ImportError as exc:
        raise RuntimeError(
            "threadpoolctl is required to enforce the CPU thread budget"
        ) from exc

    output = Path(results_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing results: {output}")
    partial = output.with_suffix(output.suffix + ".partial")
    if partial.exists():
        raise FileExistsError(
            f"Partial results exist at {partial}; preserve them and "
            "choose a new run path"
        )
    manifest_path = output.parent / "run-manifest.json"
    settings = {
        "format": "fresh-record-hybrid-run-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "conditions": list(conditions),
        "condition_order": "rotate selected conditions by question index",
        "bm25": {
            "k1": BM25_K1,
            "b": BM25_B,
            "tokenizer": "Korean words+bigram+complete code",
        },
        "dense": {"metric": "exact cosine", "top_k": RETRIEVER_TOP_K},
        "sparse_top_k": RETRIEVER_TOP_K,
        "rrf_k": RRF_K,
        "candidate_limit": CANDIDATE_LIMIT,
        "max_related_events": MAX_RELATED_EVENTS,
        "max_related_units": MAX_RELATED_UNITS,
        "rerank_batch_size": rerank_batch_size,
        "context": {
            "tokenizer": "o200k_base",
            "max_sources": MAX_FINAL_SOURCES,
            "max_tokens": MAX_CONTEXT_TOKENS,
        },
        "max_model_tokens": MAX_MODEL_TOKENS,
        "device": device,
        "threads": threads,
        "warmup": warmup,
        "timing_boundary": (
            "model/vector loading and warm-up are excluded from per-row timings"
        ),
        "model_load_seconds": round(model_load_seconds, 3),
        "vector_load_seconds": round(vector_load_seconds, 3),
        "index_prepare_seconds": round(index_prepare_seconds, 3),
        "entrypoint_commands": [
            f".venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.retrieval "
            f"build-vectors --corpus all --device {device} --threads {threads} "
            "--embedding-batch-size 4",
            f".venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.retrieval "
            f"retrieve --device {device} --threads {threads} "
            f"--rerank-batch-size {rerank_batch_size} "
            f"--conditions {' '.join(conditions)}",
        ],
        "c_relation_order": (
            "matched events in query order; within event sort unit.kind, source order, "
            "unit_id; round-robin events; edge role is proof metadata only"
        ),
        "d_relation_mapping": (
            "same B proof/order; project edge to A page before exclusion/dedup/8 cap; "
            "keep new-only RRF policy"
        )
        if "D" in conditions
        else None,
        "c_relation_limit": (
            "up to 8 new units, then equal RRF with the 20-item hybrid list"
        ),
        "code_sha256": {
            "models.py": sha256_file(Path(__file__).with_name("models.py")),
            "retrieval.py": sha256_file(__file__),
        },
        "fresh_units_manifest": {
            "path": str(units_path),
            "sha256": hashlib.sha256(Path(units_path).read_bytes()).hexdigest(),
        },
        "embedding_model": embedder.metadata(),
        "reranker_model": reranker.metadata(),
        "vector_fingerprints": {
            corpus: progress["fingerprint"]
            for corpus, progress in vector_progress.items()
        },
        "inputs": _input_fingerprints(
            pages_path, units_path, relations_path, questions
        ),
    }
    write_json_atomic(manifest_path, settings)
    result_count = 0
    rerank_pair_count = 0
    query_embedding_count = 0
    warmup_pair_count = 0
    warmup_start = time.perf_counter()
    with threadpool_limits(limits=threads):
        if warmup:
            warmup_pair_count = _warmup(
                questions,
                docs_by_corpus,
                vector_by_corpus,
                sparse_by_corpus,
                docs_by_id,
                page_map,
                source_order,
                relation_index,
                embedder,
                reranker,
                encoding,
                rerank_batch_size,
                dense_by_corpus=dense_by_corpus,
                code_patterns=code_patterns,
                conditions=conditions,
                relation_docs=records,
            )
        warmup_seconds = time.perf_counter() - warmup_start
        measured_start = time.perf_counter()
        with partial.open("x", encoding="utf-8", newline="\n") as handle:
            base_order = conditions
            for question_index, question in enumerate(questions):
                rotate = question_index % len(base_order)
                execution_order = base_order[rotate:] + base_order[:rotate]
                by_condition: dict[str, dict[str, Any]] = {}
                for condition in execution_order:
                    corpus = "A" if condition in ("A", "D") else "B"
                    result = _run_condition(
                        condition,
                        question.text,
                        docs_by_corpus[corpus],
                        vector_by_corpus[corpus],
                        sparse_by_corpus[corpus],
                        docs_by_id,
                        page_map,
                        source_order,
                        relation_index,
                        embedder,
                        reranker,
                        encoding,
                        rerank_batch_size=rerank_batch_size,
                        dense_index=dense_by_corpus[corpus],
                        code_patterns=code_patterns,
                        relation_docs=records,
                    )
                    by_condition[condition] = result
                    query_embedding_count += 1
                    rerank_pair_count += result["rerank"]["pairs"]
                for condition in base_order:
                    row = {
                        "id": question.question_id,
                        "question_sha256": sha256_text(question.text),
                        **by_condition[condition],
                    }
                    handle.write(
                        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    result_count += 1
                handle.flush()
                if (question_index + 1) % 10 == 0 or question_index + 1 == len(
                    questions
                ):
                    print(
                        f"retrieval: {question_index + 1}/{len(questions)} questions, "
                        f"{result_count} rows written",
                        flush=True,
                    )
            handle.flush()
            os.fsync(handle.fileno())
    if result_count != len(questions) * len(conditions):
        raise RuntimeError(
            f"Expected {len(questions) * len(conditions)} result rows, wrote {result_count}"
        )
    os.replace(partial, output)
    vector_files = {
        corpus: {
            "path": str(Path(vectors_dir) / f"{corpus}.vectors.npy"),
            "size_bytes": (Path(vectors_dir) / f"{corpus}.vectors.npy").stat().st_size,
            "sha256": vector_progress[corpus].get("vectors_sha256"),
        }
        for corpus in corpora
    }
    measured_elapsed = time.perf_counter() - measured_start
    warmup_query_count = len(conditions) if warmup else 0
    summary = {
        "results_path": str(output),
        "question_count": len(questions),
        "result_rows": result_count,
        "query_embedding_count": query_embedding_count + warmup_query_count,
        "measured_query_embedding_count": query_embedding_count,
        "warmup_query_embedding_count": warmup_query_count,
        "rerank_pair_count": rerank_pair_count + warmup_pair_count,
        "measured_rerank_pair_count": rerank_pair_count,
        "warmup_rerank_pair_count": warmup_pair_count,
        "warmup_seconds": round(warmup_seconds, 3),
        "measured_elapsed_seconds": round(measured_elapsed, 3),
        "elapsed_seconds": round(warmup_seconds + measured_elapsed, 3),
        "rss_peak_kb": _rss_peak_kb(),
        "vector_files": vector_files,
        "threadpool_info": threadpool_info(),
    }
    settings["summary"] = summary
    write_json_atomic(manifest_path, settings)
    print(
        f"retrieval complete: rows={result_count}, "
        f"query_embeddings={query_embedding_count}, rerank_pairs={rerank_pair_count}, "
        f"elapsed={summary['elapsed_seconds']:.1f}s",
        flush=True,
    )
    return summary


def _add_common_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_HUB_CACHE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fresh-origin A/B/C hybrid retrieval with optional page-relation D"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser(
        "build-vectors",
        help="Build resumable float32 vectors from fresh A pages and/or B units",
    )
    build.add_argument("--corpus", choices=("A", "B", "all"), default="all")
    build.add_argument("--pages", type=Path, default=DEFAULT_PAGES)
    build.add_argument("--units", type=Path, default=DEFAULT_UNITS)
    build.add_argument("--vectors-dir", type=Path, default=DEFAULT_VECTORS)
    build.add_argument("--chunk-size", type=int, default=100)
    build.add_argument("--embedding-batch-size", type=int, default=4)
    _add_common_model_args(build)

    run = commands.add_parser("retrieve", help="Retrieve and save selected conditions")
    run.add_argument(
        "--conditions",
        nargs="+",
        choices=("A", "B", "C", "D"),
        default=["A", "B", "C"],
    )
    run.add_argument("--pages", type=Path, default=DEFAULT_PAGES)
    run.add_argument("--units", type=Path, default=DEFAULT_UNITS)
    run.add_argument("--relations", type=Path, default=DEFAULT_RELATIONS)
    run.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    run.add_argument("--vectors-dir", type=Path, default=DEFAULT_VECTORS)
    run.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    run.add_argument("--rerank-batch-size", type=int, default=8)
    run.add_argument("--no-warmup", action="store_true")
    _add_common_model_args(run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.threads < 1:
        parser.error("--threads must be at least one")
    if args.command == "build-vectors":
        result = build_vectors(
            corpus=args.corpus,
            pages_path=args.pages,
            units_path=args.units,
            vectors_dir=args.vectors_dir,
            device=args.device,
            threads=args.threads,
            cache_root=args.cache_root,
            chunk_size=args.chunk_size,
            embedding_batch_size=args.embedding_batch_size,
        )
    else:
        result = retrieve(
            pages_path=args.pages,
            units_path=args.units,
            relations_path=args.relations,
            questions_path=args.questions,
            vectors_dir=args.vectors_dir,
            results_path=args.results,
            device=args.device,
            threads=args.threads,
            cache_root=args.cache_root,
            rerank_batch_size=args.rerank_batch_size,
            warmup=not args.no_warmup,
            conditions=args.conditions,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
