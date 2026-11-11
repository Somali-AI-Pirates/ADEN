"""Build an IS-only Snowflake sliding-window dense snapshot.

Scope contract: replace only the IS dense page representation with 256-token
windows at a 64-token overlap. Preserve every original page and use its exact
text substrings; non-IS dense vectors, BM25, reranking, and source context stay
on the existing path. The output is a local immutable experiment snapshot.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_snapshots import (
    IS_HEADING,
    _page_snapshot_sha,
    _read_pages_and_sources,
    _sha_file,
)
from experimental.u_10o0o.embeding import (
    BATCH_SIZE,
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

WINDOW_TOKENS = 256
OVERLAP_TOKENS = 64
WINDOW_STEP = WINDOW_TOKENS - OVERLAP_TOKENS
SPECIAL_TOKENS_RESERVED = 2
SCHEMA_VERSION = 1
SNAPSHOT_TYPE = "is-window-dense"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "artifacts/u_10o0o/advanced-semantic-first-miss-20261111/is-windows-v1"
)
OUTPUT_FILES = ("windows.jsonl", "vectors.npy")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_json_bytes(value))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(_json_bytes(row))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _array_sha256(values: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(values)
    digest = sha256()
    digest.update(memoryview(contiguous))
    return digest.hexdigest()


def _source_id(page: Mapping[str, Any]) -> str:
    book_id = Path(page["file_name"]).stem
    return f"advanced:{book_id}:p{page['page']}"


def _model_config() -> dict[str, Any]:
    return {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "embedding_dimension": EMBEDDING_DIM,
        "dtype": "float32",
        "device": "cuda",
        "normalize_embeddings": True,
        "batch_size": BATCH_SIZE,
        "window_content_tokens": WINDOW_TOKENS,
        "overlap_content_tokens": OVERLAP_TOKENS,
        "special_tokens_reserved": SPECIAL_TOKENS_RESERVED,
        "maximum_encoded_tokens": WINDOW_TOKENS + SPECIAL_TOKENS_RESERVED,
        "tokenizer_offsets": "fast-tokenizer-original-text",
        "window_text": "exact-original-page-substring",
        "deduplicate_identical_window_text": False,
        "model_fetch_mode": "offline-local-cache",
    }


def _require_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return value


def _pdf_source_fingerprints(
    source_by_id: Mapping[str, Mapping[str, Any]], is_source_ids: Sequence[str]
) -> dict[str, str]:
    """Retain curation's already-verified source PDF hashes without rehashing PDFs."""
    manifest_path = CURATED / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    documents = {
        item["file_name"]: item
        for item in manifest.get("identity", {}).get("documents", [])
    }
    is_sources = [source_by_id[source_id] for source_id in is_source_ids]
    file_names = {source["file_name"] for source in is_sources}
    books = {source["book_id"] for source in is_sources}
    if len(file_names) != 50 or len(books) != 50:
        raise ValueError("IS headings must identify exactly 50 original PDFs.")

    hashes = {}
    for file_name in sorted(file_names):
        document = documents.get(file_name)
        if document is None:
            raise ValueError(f"Curated original IS PDF source is missing: {file_name}")
        source_sha = _require_sha256(
            document.get("sha256"), f"curated PDF hash for {file_name}"
        )
        pages = [
            source["page"] for source in is_sources if source["file_name"] == file_name
        ]
        if any(
            not isinstance(page, int) or page < 1 or page > document["pages"]
            for page in pages
        ):
            raise ValueError(f"IS source page number is outside {file_name}.")
        hashes[file_name] = source_sha
    return hashes


def _read_experiment_inputs():
    pages, source_by_id = _read_pages_and_sources()
    is_source_set = {
        source_id
        for source_id, source in source_by_id.items()
        if IS_HEADING.match(source["heading"])
    }
    is_source_ids = [
        _source_id(page) for page in pages if _source_id(page) in is_source_set
    ]
    books = {source_by_id[source_id]["book_id"] for source_id in is_source_ids}
    if (
        len(is_source_ids) != 3_750
        or len(set(is_source_ids)) != 3_750
        or is_source_set != set(is_source_ids)
        or len(books) != 50
    ):
        raise ValueError("Expected 50 IS books and their 3,750 curated source pages.")

    vectors = np.load(VECTORS_PATH, mmap_mode="r", allow_pickle=False)
    if vectors.shape != (len(pages), EMBEDDING_DIM) or vectors.dtype != np.float32:
        raise ValueError("Existing page vectors have the wrong shape or dtype.")
    if not np.isfinite(vectors).all() or not np.allclose(
        np.linalg.norm(vectors, axis=1), 1.0, atol=2e-3
    ):
        raise ValueError("Existing page vectors must be finite and unit normalized.")

    pdf_hashes = _pdf_source_fingerprints(source_by_id, is_source_ids)
    inputs = {
        "code_sha256": _sha_file(Path(__file__)),
        "curation_manifest_sha256": _sha_file(CURATED / "manifest.json"),
        "curated_sources_sha256": _sha_file(CURATED / "sources.jsonl"),
        "page_cache_sha256": _sha_file(CACHE_PATH),
        "page_snapshot_sha256": _page_snapshot_sha(pages),
        "base_vectors_file_sha256": _sha_file(VECTORS_PATH),
        "base_vectors_content_sha256": _array_sha256(vectors),
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "embedding_dimension": EMBEDDING_DIM,
        "is_source_ids": is_source_ids,
        "pdf_source_sha256": pdf_hashes,
    }
    return pages, vectors, source_by_id, inputs


def _assert_nonwhitespace_coverage(
    text: str, spans: Sequence[tuple[int, int]], *, label: str
) -> None:
    cursor = 0
    for start, end in sorted(spans):
        if start < 0 or end < start or end > len(text):
            raise ValueError(f"{label}: invalid character span {start}:{end}.")
        if start > cursor and any(
            not character.isspace() for character in text[cursor:start]
        ):
            raise ValueError(
                f"{label}: tokenizer offsets leave non-whitespace text uncovered."
            )
        cursor = max(cursor, end)
    if any(not character.isspace() for character in text[cursor:]):
        raise ValueError(
            f"{label}: tokenizer offsets leave non-whitespace text uncovered."
        )


def _window_specs(text: str, tokenizer) -> list[dict[str, int]]:
    if not isinstance(text, str):
        raise ValueError("IS page text must be a string.")
    if not getattr(tokenizer, "is_fast", False):
        raise ValueError("Snowflake tokenizer must provide fast offset mappings.")
    encoded = tokenizer(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
        truncation=False,
        verbose=False,
    )
    token_ids = encoded["input_ids"]
    raw_offsets = encoded["offset_mapping"]
    if len(token_ids) != len(raw_offsets):
        raise ValueError("Tokenizer IDs and offset mappings have different lengths.")

    content_offsets = []
    for token_index, pair in enumerate(raw_offsets):
        if len(pair) != 2:
            raise ValueError("Tokenizer returned an invalid offset pair.")
        start, end = map(int, pair)
        if start == 0 and end == 0:
            continue
        if start < 0 or end <= start or end > len(text):
            raise ValueError("Tokenizer returned an invalid non-empty offset.")
        content_offsets.append((token_index, start, end))

    content_count = len(content_offsets)
    if content_count == 0:
        specs = [
            {
                "char_start": 0,
                "char_end": len(text),
                "token_start": 0,
                "token_end": 0,
                "token_count": 0,
                "page_token_count": 0,
                "tokenizer_start": 0,
                "tokenizer_end": len(token_ids),
                "tokenizer_token_count": len(token_ids),
            }
        ]
    else:
        specs = []
        content_start = 0
        while content_start < content_count:
            content_end = min(content_start + WINDOW_TOKENS, content_count)
            chunk = content_offsets[content_start:content_end]
            specs.append(
                {
                    "char_start": 0 if content_start == 0 else chunk[0][1],
                    "char_end": len(text)
                    if content_end == content_count
                    else chunk[-1][2],
                    "token_start": content_start,
                    "token_end": content_end,
                    "token_count": content_end - content_start,
                    "page_token_count": content_count,
                    "tokenizer_start": 0 if content_start == 0 else chunk[0][0],
                    "tokenizer_end": (
                        len(token_ids)
                        if content_end == content_count
                        else chunk[-1][0] + 1
                    ),
                    "tokenizer_token_count": len(token_ids),
                }
            )
            if content_end == content_count:
                break
            content_start += WINDOW_STEP

    _assert_nonwhitespace_coverage(
        text,
        [(spec["char_start"], spec["char_end"]) for spec in specs],
        label="IS page",
    )
    return specs


def _verify_token_ranges(rows: Sequence[Mapping[str, Any]], source_id: str) -> None:
    ordered = sorted(rows, key=lambda row: row["token_start"])
    content_total = ordered[0]["page_token_count"]
    raw_total = ordered[0]["tokenizer_token_count"]
    if any(
        row["page_token_count"] != content_total
        or row["tokenizer_token_count"] != raw_total
        for row in ordered
    ):
        raise ValueError(f"{source_id}: inconsistent page token counts.")
    expected = []
    start = 0
    while start < content_total:
        end = min(start + WINDOW_TOKENS, content_total)
        expected.append((start, end))
        if end == content_total:
            break
        start += WINDOW_STEP
    if not expected:
        expected = [(0, 0)]
    actual = [(row["token_start"], row["token_end"]) for row in ordered]
    if actual != expected:
        raise ValueError(
            f"{source_id}: token windows do not cover the page as configured."
        )
    raw_ranges = sorted(
        (row["tokenizer_start"], row["tokenizer_end"]) for row in ordered
    )
    cursor = 0
    for start, end in raw_ranges:
        if start > cursor or start < 0 or end < start or end > raw_total:
            raise ValueError(
                f"{source_id}: raw tokenizer ranges have a gap or invalid edge."
            )
        cursor = max(cursor, end)
    if cursor != raw_total:
        raise ValueError(f"{source_id}: raw tokenizer ranges do not cover the page.")


def _validate_rows(pages, rows, expected_source_ids) -> tuple[np.ndarray, np.ndarray]:
    pages_by_id = {_source_id(page): index for index, page in enumerate(pages)}
    if len(pages_by_id) != len(pages):
        raise ValueError("Original page source IDs are not unique.")
    if not isinstance(expected_source_ids, list) or len(
        set(expected_source_ids)
    ) != len(expected_source_ids):
        raise ValueError("Snapshot IS source IDs must be a unique list.")
    if any(source_id not in pages_by_id for source_id in expected_source_ids):
        raise ValueError(
            "Snapshot contains an IS source ID absent from the page input."
        )

    windows_by_source = defaultdict(list)
    parent_indexes = []
    previous_key = (-1, -1)
    for row_index, row in enumerate(rows):
        source_id = row.get("source_id")
        page_index = row.get("page_index")
        if source_id not in expected_source_ids or page_index != pages_by_id.get(
            source_id
        ):
            raise ValueError(f"Window {row_index} does not map to an expected page.")
        page = pages[page_index]
        if (
            row.get("book_id") != Path(page["file_name"]).stem
            or row.get("file_name") != page["file_name"]
            or row.get("page") != page["page"]
        ):
            raise ValueError(
                f"Window {row_index} source metadata does not match its page."
            )
        char_start, char_end = row.get("char_start"), row.get("char_end")
        token_start, token_end = row.get("token_start"), row.get("token_end")
        tokenizer_start = row.get("tokenizer_start")
        tokenizer_end = row.get("tokenizer_end")
        numeric_fields = (
            page_index,
            char_start,
            char_end,
            token_start,
            token_end,
            row.get("token_count"),
            row.get("page_token_count"),
            tokenizer_start,
            tokenizer_end,
            row.get("tokenizer_token_count"),
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in numeric_fields
        ):
            raise ValueError(f"Window {row_index} has a non-integer range value.")
        text = row.get("text")
        if (
            not isinstance(text, str)
            or char_start < 0
            or char_end < char_start
            or char_end > len(page["text"])
            or text != page["text"][char_start:char_end]
            or row.get("text_sha256") != sha256(text.encode("utf-8")).hexdigest()
        ):
            raise ValueError(
                f"Window {row_index} is not an exact original-page substring."
            )
        if (
            token_start < 0
            or token_end < token_start
            or token_end > row["page_token_count"]
            or row["token_count"] != token_end - token_start
            or row["token_count"] > WINDOW_TOKENS
            or tokenizer_start < 0
            or tokenizer_end < tokenizer_start
            or tokenizer_end > row["tokenizer_token_count"]
        ):
            raise ValueError(f"Window {row_index} has an invalid token range.")
        window_index = row.get("window_index")
        if window_index != row_index:
            raise ValueError("Window rows must retain their deterministic index order.")
        order_key = (page_index, token_start)
        if order_key < previous_key:
            raise ValueError("Window rows must be in source-page and token order.")
        previous_key = order_key
        windows_by_source[source_id].append(row)
        parent_indexes.append(page_index)

    if set(windows_by_source) != set(expected_source_ids):
        raise ValueError("Snapshot windows do not cover every expected IS page.")
    for source_id in expected_source_ids:
        grouped = windows_by_source[source_id]
        _verify_token_ranges(grouped, source_id)
        page = pages[pages_by_id[source_id]]
        _assert_nonwhitespace_coverage(
            page["text"],
            [(row["char_start"], row["char_end"]) for row in grouped],
            label=source_id,
        )
    return np.asarray(parent_indexes, dtype=np.int64), np.asarray(
        [pages_by_id[source_id] for source_id in expected_source_ids], dtype=np.int64
    )


class ISWindowDense:
    """Use window maxima for IS pages and the existing page vectors elsewhere."""

    def __init__(self, pages, vectors, snapshot):
        self.pages = pages
        self.page_vectors = np.asarray(vectors)
        root = Path(snapshot)
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"IS window snapshot manifest is missing: {root}")
        manifest = json.loads(manifest_path.read_bytes())
        if (
            manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("snapshot_type") != SNAPSHOT_TYPE
            or manifest.get("status") != "complete"
        ):
            raise ValueError("IS window snapshot is not a complete supported snapshot.")
        inputs = manifest.get("inputs", {})
        config = manifest.get("config", {})
        outputs = manifest.get("outputs", {})
        if (
            inputs.get("model") != MODEL_ID
            or inputs.get("model_revision") != MODEL_REVISION
            or inputs.get("embedding_dimension") != EMBEDDING_DIM
            or config != _model_config()
            or set(outputs) != set(OUTPUT_FILES)
        ):
            raise ValueError(
                "IS window snapshot model, configuration, or outputs differ."
            )
        for name, expected_sha in outputs.items():
            path = root / name
            if not path.is_file() or _sha_file(path) != expected_sha:
                raise ValueError(f"IS window snapshot output hash mismatch: {path}")
        expected_id = sha256(
            _json_bytes({"inputs": inputs, "outputs": outputs, "config": config})
        ).hexdigest()
        if manifest.get("snapshot_id") != expected_id:
            raise ValueError("IS window snapshot manifest fingerprint does not match.")

        if (
            self.page_vectors.shape != (len(pages), EMBEDDING_DIM)
            or self.page_vectors.dtype != np.float32
            or not np.isfinite(self.page_vectors).all()
            or not np.allclose(
                np.linalg.norm(self.page_vectors, axis=1), 1.0, atol=2e-3
            )
        ):
            raise ValueError(
                "Loaded page vectors have an invalid shape, dtype, or norm."
            )
        if inputs.get("page_snapshot_sha256") != _page_snapshot_sha(pages):
            raise ValueError(
                "Loaded pages do not match the snapshot's source text hash."
            )
        if inputs.get("base_vectors_content_sha256") != _array_sha256(
            self.page_vectors
        ):
            raise ValueError(
                "Loaded page vectors do not match the snapshot input hash."
            )
        if inputs.get("code_sha256") != _sha_file(Path(__file__)):
            raise ValueError(
                "IS window implementation changed after the snapshot build."
            )
        pdf_hashes = inputs.get("pdf_source_sha256")
        if not isinstance(pdf_hashes, dict) or any(
            not isinstance(value, str) or len(value) != 64
            for value in pdf_hashes.values()
        ):
            raise ValueError("Snapshot is missing original PDF source fingerprints.")
        is_source_ids = inputs.get("is_source_ids")
        expected_pdf_names = {
            pages[index]["file_name"]
            for index in range(len(pages))
            if _source_id(pages[index]) in (is_source_ids or ())
        }
        if set(pdf_hashes) != expected_pdf_names:
            raise ValueError(
                "Snapshot PDF fingerprints do not match its IS source pages."
            )

        if inputs.get("page_snapshot_sha256") != _page_snapshot_sha(pages):
            raise ValueError("Snapshot page input fingerprint does not match.")
        rows = _read_jsonl(root / "windows.jsonl")
        self.window_page_indexes, self.is_page_indexes = _validate_rows(
            pages, rows, is_source_ids
        )
        self.window_vectors = np.load(root / "vectors.npy", allow_pickle=False)
        if (
            self.window_vectors.shape != (len(rows), EMBEDDING_DIM)
            or self.window_vectors.dtype != np.float32
            or not np.isfinite(self.window_vectors).all()
            or not np.allclose(
                np.linalg.norm(self.window_vectors, axis=1), 1.0, atol=2e-3
            )
        ):
            raise ValueError(
                "Loaded window vectors have an invalid shape, dtype, or norm."
            )
        counts = manifest.get("counts", {})
        if (
            counts.get("source_pages") != len(is_source_ids)
            or counts.get("windows") != len(rows)
            or counts.get("books") != len(pdf_hashes)
        ):
            raise ValueError(
                "IS window snapshot counts do not match its loaded inputs."
            )
        self.manifest = manifest

    def rank(self, query_vector, *, top_k: int = 20) -> list[tuple[dict, float]]:
        if top_k < 0:
            raise ValueError("top_k must be non-negative.")
        query = np.asarray(query_vector, dtype=np.float32)
        if query.shape == (1, EMBEDDING_DIM):
            query = query[0]
        if query.shape != (EMBEDDING_DIM,) or not np.isfinite(query).all():
            raise ValueError("Query vector must be a finite Snowflake vector.")
        if not np.isclose(np.linalg.norm(query), 1.0, atol=2e-3):
            raise ValueError("Query vector must be normalized for cosine ranking.")

        page_scores = self.page_vectors @ query
        window_scores = self.window_vectors @ query
        is_scores = np.full(len(self.pages), -np.inf, dtype=np.float32)
        np.maximum.at(is_scores, self.window_page_indexes, window_scores)
        page_scores[self.is_page_indexes] = is_scores[self.is_page_indexes]
        indices = page_scores.argsort()[::-1][:top_k]
        return [
            (self.pages[int(index)], float(page_scores[index])) for index in indices
        ]


def _window_rows(pages, page_indexes, source_by_id, tokenizer):
    rows = []
    for page_index in page_indexes:
        page = pages[page_index]
        source_id = _source_id(page)
        specs = _window_specs(page["text"], tokenizer)
        source = source_by_id[source_id]
        for spec in specs:
            char_start = spec["char_start"]
            char_end = spec["char_end"]
            text = page["text"][char_start:char_end]
            rows.append(
                {
                    "window_index": len(rows),
                    "page_index": page_index,
                    "source_id": source_id,
                    "book_id": source["book_id"],
                    "file_name": source["file_name"],
                    "page": source["page"],
                    **spec,
                    "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
                    "text": text,
                }
            )
    return rows


def build(output_dir: Path = DEFAULT_OUTPUT) -> Path:
    pages, base_vectors, source_by_id, inputs = _read_experiment_inputs()
    source_indexes = {_source_id(page): index for index, page in enumerate(pages)}
    is_page_indexes = [
        source_indexes[source_id] for source_id in inputs["is_source_ids"]
    ]
    output_dir = Path(output_dir)
    if output_dir.exists():
        ISWindowDense(pages, base_vectors, output_dir)
        return output_dir
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    config = _model_config()
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent, prefix=f".{output_dir.name}-"
    ) as temporary:
        staging = Path(temporary)
        _write_json(
            staging / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "snapshot_type": SNAPSHOT_TYPE,
                "status": "running",
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "inputs": inputs,
                "config": config,
            },
        )
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        model = load_model("cuda")
        if not getattr(model.tokenizer, "is_fast", False):
            raise ValueError("Snowflake tokenizer must provide fast offset mappings.")
        if (
            model.tokenizer.num_special_tokens_to_add(pair=False)
            != SPECIAL_TOKENS_RESERVED
        ):
            raise ValueError(
                "Snowflake special-token count differs from the frozen config."
            )
        if (
            model.max_seq_length is None
            or model.max_seq_length < WINDOW_TOKENS + SPECIAL_TOKENS_RESERVED
        ):
            raise ValueError(
                "Snowflake model input limit is shorter than a configured window."
            )
        if str(model.device).split(":", 1)[0] != "cuda":
            raise ValueError("Window vectors must be encoded on CUDA.")

        rows = _window_rows(pages, is_page_indexes, source_by_id, model.tokenizer)
        texts = [row["text"] for row in rows]
        embeddings = model.encode_document(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        window_vectors = np.asarray(embeddings, dtype=np.float32)
        if (
            window_vectors.shape != (len(rows), EMBEDDING_DIM)
            or not np.isfinite(window_vectors).all()
            or not np.allclose(np.linalg.norm(window_vectors, axis=1), 1.0, atol=2e-3)
        ):
            raise ValueError("Snowflake returned invalid normalized window vectors.")

        _write_jsonl(staging / "windows.jsonl", rows)
        np.save(staging / "vectors.npy", window_vectors, allow_pickle=False)
        outputs = {name: _sha_file(staging / name) for name in OUTPUT_FILES}
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_type": SNAPSHOT_TYPE,
            "status": "complete",
            "snapshot_id": sha256(
                _json_bytes({"inputs": inputs, "outputs": outputs, "config": config})
            ).hexdigest(),
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "inputs": inputs,
            "outputs": outputs,
            "counts": {
                "source_pages": len(is_page_indexes),
                "books": len(inputs["pdf_source_sha256"]),
                "windows": len(rows),
            },
            "config": config,
        }
        _write_json(staging / "manifest.json", manifest)
        ISWindowDense(pages, base_vectors, staging)
        try:
            staging.rename(output_dir)
        except OSError:
            if output_dir.exists():
                ISWindowDense(pages, base_vectors, output_dir)
            else:
                raise
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(build(args.output_dir))


if __name__ == "__main__":
    main()
