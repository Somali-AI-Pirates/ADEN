"""Evaluate KURE-v2 MaxSim with the frozen Advanced hybrid retrieval pipeline."""

import argparse
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np
import torch
from sentence_transformers import CrossEncoder, MultiVectorEncoder
from torch.nn.utils.rnn import pad_sequence

from experimental.u_10o0o import advanced_search
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    loaded_runtime,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
    KURE,
    MAX_TOKENS,
    MODELS,
    LegacyBaseline,
    _source_info,
)
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import top_pages

from .__main__ import (
    DEFAULT_RUN,
    encode,
    log,
    model_loader,
    validate_assets,
    validate_inputs,
    validate_results,
)
from .score_selected import score_saved

NAME = "kure_v2"
MODEL_ID = "nlpai-lab/KURE-v2"
REVISION = "3431f86d399d666083890dbb882aced6708873bc"
TOKEN_DIM = 128
BATCH_SIZE = 16
PAGE_BLOCK = 128
QUERY_BLOCK = 4
ARMS = ("snowflake", "kure_v3", "nemotron", NAME)


def load_model():
    return MultiVectorEncoder(
        MODEL_ID,
        revision=REVISION,
        device="cuda",
        local_files_only=True,
        model_kwargs={"dtype": torch.bfloat16},
    )


def build(run):
    validate_inputs(run)
    directory = run / NAME
    directory.mkdir(exist_ok=False)
    pages = read_json(CACHE_PATH)
    questions = read_json(run / "questions.json")
    model = load_model()
    lengths = []
    tokens_path = directory / "page-tokens.f16"
    torch.cuda.reset_peak_memory_stats()
    started = perf_counter()
    with tokens_path.open("wb") as stream:
        for start in range(0, len(pages), 256):
            rows = pages[start : start + 256]
            vectors = model.encode_document(
                [row["text"] for row in rows],
                batch_size=BATCH_SIZE,
                show_progress_bar=False,
            )
            for vector in vectors:
                array = vector.to(dtype=torch.float16, device="cpu").numpy()
                if array.shape[1:] != (TOKEN_DIM,) or not len(array):
                    raise ValueError("Unexpected KURE-v2 page embedding shape")
                if not np.isfinite(array).all():
                    raise ValueError("Nonfinite KURE-v2 page embedding")
                lengths.append(len(array))
                stream.write(array.tobytes())
            if len(lengths) % 2048 == 0 or len(lengths) == len(pages):
                log(
                    f"KURE-v2 stored token vectors for {len(lengths)}/{len(pages)} pages"
                )
    document_seconds = perf_counter() - started
    lengths_path = directory / "page-lengths.npy"
    np.save(lengths_path, np.asarray(lengths, dtype=np.int32), allow_pickle=False)
    started = perf_counter()
    vectors = model.encode_query(
        [row["question"] for row in questions],
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
    )
    queries = torch.stack(vectors).to(dtype=torch.float16, device="cpu").numpy()
    if (
        queries.shape != (len(questions), 64, TOKEN_DIM)
        or not np.isfinite(queries).all()
    ):
        raise ValueError("Unexpected KURE-v2 query embeddings")
    question_seconds = perf_counter() - started
    query_path = directory / "question-vectors.npy"
    np.save(query_path, queries, allow_pickle=False)
    if (
        len(lengths) != len(pages)
        or tokens_path.stat().st_size != sum(lengths) * TOKEN_DIM * 2
    ):
        raise ValueError("Page token count disagrees with the corpus")
    if max(lengths) >= model.max_seq_length:
        raise ValueError("Page may have reached the encoder length limit")
    write_json(
        directory / "vectors-manifest.json",
        {
            "status": "complete",
            "frozen_sha256": file_sha(run / "frozen.json"),
            "document_path": str(tokens_path.relative_to(ROOT)),
            "document_sha256": file_sha(tokens_path),
            "lengths_sha256": file_sha(lengths_path),
            "question_sha256": file_sha(query_path),
            "document_seconds": document_seconds,
            "question_seconds": question_seconds,
            "document_count": len(lengths),
            "token_count": sum(lengths),
            "maximum_page_vectors": max(lengths),
            "question_count": len(queries),
            "model": MODEL_ID,
            "revision": REVISION,
            "model_dtype": str(model.dtype),
            "index_dtype": "float16",
            "query_vectors": 64,
            "token_dimensions": TOKEN_DIM,
            "batch_size": BATCH_SIZE,
            "build_peak_cuda_bytes": torch.cuda.max_memory_allocated(),
            "index_bytes": tokens_path.stat().st_size + lengths_path.stat().st_size,
        },
    )


def maxsim(queries, documents, valid_tokens):
    """Return one score per query and page, excluding padding from each maximum."""
    query_count, query_tokens, _ = queries.shape
    page_count, page_tokens, _ = documents.shape
    dots = queries.flatten(0, 1) @ documents.flatten(0, 1).T
    dots = dots.view(query_count, query_tokens, page_count, page_tokens)
    dots.masked_fill_(~valid_tokens, -torch.inf)
    return dots.amax(dim=-1).sum(dim=1, dtype=torch.float32)


class MaxSimIndex:
    """Keep length-grouped page vectors on CUDA for an exhaustive MaxSim scan."""

    def __init__(self, run):
        assets = validate_assets(run, NAME)
        length_file = run / NAME / "page-lengths.npy"
        if file_sha(length_file) != assets["lengths_sha256"]:
            raise ValueError("Page token offsets changed")
        lengths = np.load(length_file, allow_pickle=False)
        self.page_count = assets["document_count"]
        if lengths.shape != (self.page_count,) or np.any(lengths <= 0):
            raise ValueError("Invalid page token lengths")
        offsets = np.concatenate(([0], lengths.cumsum(dtype=np.int64)))
        if offsets[-1] != assets["token_count"]:
            raise ValueError("Invalid token index size")
        tokens = np.memmap(
            ROOT / assets["document_path"],
            mode="r",
            dtype=np.float16,
            shape=(int(offsets[-1]), TOKEN_DIM),
        )
        sorted_pages = lengths.argsort(kind="stable")
        self.blocks = []
        for start in range(0, len(sorted_pages), PAGE_BLOCK):
            indices = sorted_pages[start : start + PAGE_BLOCK]
            rows = [
                torch.from_numpy(tokens[offsets[i] : offsets[i + 1]].copy())
                for i in indices
            ]
            documents = pad_sequence(rows, batch_first=True).to("cuda")
            limits = torch.from_numpy(lengths[indices]).to("cuda")
            valid = torch.arange(documents.shape[1], device="cuda") < limits[:, None]
            self.blocks.append((torch.from_numpy(indices).to("cuda"), documents, valid))

    @torch.inference_mode()
    def score(self, queries):
        scores = torch.empty((len(queries), self.page_count), device="cuda")
        for indices, documents, valid in self.blocks:
            scores[:, indices] = maxsim(queries, documents, valid)
        return scores


class SavedMaxSim:
    """Provide saved page scores in question order to the unchanged hybrid pipeline."""

    def __init__(self, questions, scores):
        self.by_text = {
            question["question"]: row
            for question, row in zip(questions, scores, strict=True)
        }

    def encode_query(self, questions, **kwargs):
        return [self.by_text[question] for question in questions]


def rank_maxsim(scores, _vectors, pages, *, top_k):
    return top_pages(scores, pages, top_k=top_k)


def retrieve(run):
    frozen = validate_inputs(run)
    assets = validate_assets(run, NAME)
    directory = run / NAME / "retrieval"
    directory.mkdir(exist_ok=False)
    questions = read_json(run / "questions.json")
    started = perf_counter()
    torch.cuda.reset_peak_memory_stats()
    index = MaxSimIndex(run)
    queries = np.load(run / NAME / "question-vectors.npy", allow_pickle=False)
    if queries.shape != (len(questions), 64, TOKEN_DIM):
        raise ValueError("Question vectors do not match the frozen questions")
    search_start = perf_counter()
    scores = np.concatenate(
        [
            index.score(
                torch.from_numpy(queries[start : start + QUERY_BLOCK]).to("cuda")
            )
            .cpu()
            .numpy()
            for start in range(0, len(queries), QUERY_BLOCK)
        ]
    )
    search_seconds = perf_counter() - search_start
    peak_bytes = torch.cuda.max_memory_allocated()
    del index
    torch.cuda.empty_cache()
    if not np.isfinite(scores).all() or scores.shape != (
        len(questions),
        assets["document_count"],
    ):
        raise ValueError("MaxSim must produce one finite score per question and page")
    log(
        f"KURE-v2 scored all {len(questions)} questions against all {scores.shape[1]} pages"
    )
    engine = LegacyBaseline()
    engine._ensure_corpus()
    engine.embedder = SavedMaxSim(questions, scores)
    engine.reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    runtime = loaded_runtime()
    count = 0
    with patch.object(advanced_search, "rank_pages", rank_maxsim):
        for batch in batch_questions(questions, frozen["items"]):
            for expected, (trace, context) in zip(
                batch, engine.retrieve_batch(batch, log), strict=True
            ):
                if trace["id"] != expected["id"] or context["id"] != expected["id"]:
                    raise ValueError("Unexpected retrieval order")
                trace["arm"] = context["arm"] = NAME
                dense = top_pages(
                    engine.embedder.by_text[expected["question"]],
                    engine.pages,
                    top_k=50,
                )
                trace["dense_top50"] = [
                    _source_info(page, score) for page, score in dense
                ]
                append_json(directory / "traces.jsonl", trace)
                append_json(directory / "contexts.jsonl", context)
                count += 1
            log(f"KURE-v2 saved retrievals for {count}/{len(questions)} questions")
    validate_inputs(run)
    check_runtime(runtime)
    write_json(
        directory / "manifest.json",
        {
            "status": "complete",
            "count": count,
            "frozen_sha256": file_sha(run / "frozen.json"),
            "assets_sha256": file_sha(run / NAME / "vectors-manifest.json"),
            "references_opened": False,
            "external_api_calls": 0,
            "runtime_files": runtime,
            "wall_seconds": perf_counter() - started,
            "search_seconds": search_seconds,
            "search_peak_cuda_bytes": peak_bytes,
            "search_method": "exhaustive FP16 MaxSim; FP32 reduction",
            "traces_sha256": file_sha(directory / "traces.jsonl"),
            "contexts_sha256": file_sha(directory / "contexts.jsonl"),
        },
    )


def score(run):
    frozen = validate_inputs(run)
    saved = {name: validate_results(run, name, frozen) for name in ARMS}
    for reference in read_json(run / "reference-freeze.json")["references"]:
        if file_sha(ROOT / reference["path"]) != reference["sha256"]:
            raise ValueError("Reference changed from the first comparison")
    score_saved(frozen, saved, run / "scores-kure-v2")


def timing(run, name):
    frozen = validate_inputs(run)
    questions = read_json(run / "questions.json")
    ids = {}
    for i, item in enumerate(frozen["items"]):
        ids.setdefault(item["suite"], []).append(i)
    sample = [i for suite in ids.values() for i in suite[:5]]
    model = load_model() if name == NAME else model_loader("nemotron")
    if name == NAME:
        index = MaxSimIndex(run)
    else:
        assets = validate_assets(run, name)
        documents = np.load(ROOT / assets["document_path"], allow_pickle=False)
    results = []
    for iteration, i in enumerate(sample[:3] + sample):
        torch.cuda.synchronize()
        start = perf_counter()
        if name == NAME:
            vector = model.encode_query(
                [questions[i]["question"]], batch_size=1, show_progress_bar=False
            )[0].to(torch.float16)
        else:
            vector = encode(
                model, [questions[i]["question"]], kind="query", batch_size=1
            )[0]
        torch.cuda.synchronize()
        encoded = perf_counter()
        if name == NAME:
            values = index.score(vector[None])[0].cpu().numpy()
        else:
            values = documents @ vector
        _ = values.argsort()[-20:]
        ended = perf_counter()
        if iteration >= 3:
            results.append(
                {
                    "id": questions[i]["id"],
                    "encode_ms": (encoded - start) * 1000,
                    "search_ms": (ended - encoded) * 1000,
                    "total_ms": (ended - start) * 1000,
                }
            )
    write_json(
        run / NAME / f"timing-{name}.json",
        {
            "model": name,
            "samples": results,
            "warmup": 3,
            "metrics": {
                key: {
                    "p50": float(np.percentile(vals, 50)),
                    "p95": float(np.percentile(vals, 95)),
                }
                for key in ("encode_ms", "search_ms", "total_ms")
                for vals in [[row[key] for row in results]]
            },
        },
    )
    log(f"Saved batch-1 timing for {name}: {len(results)} questions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "retrieve", "score"):
        commands.add_parser(command)
    timer = commands.add_parser("timing")
    timer.add_argument("model", choices=(NAME, "nemotron"))
    args = parser.parse_args()
    torch.set_num_threads(8)
    if args.command == "timing":
        timing(args.run, args.model)
    else:
        {"build": build, "retrieve": retrieve, "score": score}[args.command](args.run)
