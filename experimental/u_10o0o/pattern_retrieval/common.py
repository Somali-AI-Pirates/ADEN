"""Frozen settings and small serialization utilities for the fresh experiment."""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUN = ROOT / "artifacts/u_10o0o/pattern-retrieval-v1-20261110"
EMBED_MODEL = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
EMBED_REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"
RERANK_MODEL = "nlpai-lab/KURE-Reranker-nano"
RERANK_REVISION = "8514b9e113f3b27c441ed9c2b50f81d80388b2fd"
MAX_INPUT = 2048
EMBED_BATCH = 4
RERANK_BATCH = 8
CPU_THREADS = 4
FIRST_K = 50
POOL_K = 20
FINAL_K = 8
CONTEXT_TOKENS = 8000
RRF_K = 60
ARMS = ("A", "B", "C")
CODE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"[A-Za-z][A-Za-z0-9]*(?:\s*[-‐‑–—−－]\s*[A-Za-z0-9]+)+"
    r"(?![A-Za-z0-9])"
)
DASHES = str.maketrans({c: "-" for c in "‐‑–—−－"})
CONFIG = {
    "schema_version": 1,
    "embedding": {
        "model": EMBED_MODEL,
        "revision": EMBED_REVISION,
        "max_tokens": MAX_INPUT,
        "batch": EMBED_BATCH,
        "dtype": "float32",
        "query_prompt": "query: ",
        "normalization": "L2",
    },
    "reranker": {
        "model": RERANK_MODEL,
        "revision": RERANK_REVISION,
        "max_tokens": MAX_INPUT,
        "batch": RERANK_BATCH,
        "dtype": "float32",
    },
    "bm25": {"k1": 1.5, "b": 0.75, "tokenizer": "korean_bigram_complete_code"},
    "first_k": FIRST_K,
    "rrf_weights": [0.5, 0.5],
    "rrf_k": RRF_K,
    "pool_k": POOL_K,
    "final_k": FINAL_K,
    "context_tokens": CONTEXT_TOKENS,
    "context_tokenizer": "cl100k_base",
    "relation_max_cases": 2,
    "relation_max_units": 8,
    "cpu_threads": CPU_THREADS,
    "external_api_calls": 0,
}


def normalize_code(value):
    value = unicodedata.normalize("NFKC", value).translate(DASHES).upper()
    return re.sub(r"\s*-\s*", "-", value).strip()


def code_tokens(text):
    return [
        normalize_code(m.group())
        for m in CODE.finditer(unicodedata.normalize("NFKC", text))
        if any(c.isdigit() for c in m.group())
    ]


def codes_in(text):
    return list(dict.fromkeys(code_tokens(text)))


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def object_sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def text_sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def runtime_files():
    paths = [
        *[
            Path(__file__).with_name(name)
            for name in (
                "common.py",
                "models.py",
                "engine.py",
                "__main__.py",
                "corpus.py",
            )
        ],
        ROOT / "experimental/u_10o0o/bm25.py",
        ROOT / "app/ingestion/layout.py",
        ROOT / "app/generation/context.py",
        ROOT / "shared/contracts.py",
    ]
    return sorted(
        (
            {"path": str(p.relative_to(ROOT)), "sha256": file_sha(p)}
            for p in paths
            if p.is_file()
        ),
        key=lambda row: row["path"],
    )


def check_runtime(stamps):
    for row in stamps:
        if file_sha(ROOT / row["path"]) != row["sha256"]:
            raise ValueError(f"Runtime changed: {row['path']}")


def logger(output_dir):
    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (Path(output_dir) / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    return log


def load_questions(path):
    rows = read_json(path)
    if not rows or any(set(r) != {"id", "question"} for r in rows):
        raise ValueError("Retrieval input must contain only nonempty id/question rows.")
    if any(not isinstance(r["id"], str) or not r["id"].strip() for r in rows):
        raise ValueError("Question ID must be a nonempty string.")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate question IDs.")
    if any(
        not isinstance(r["question"], str) or not r["question"].strip() for r in rows
    ):
        raise ValueError("Question must be a nonempty string.")
    return rows
