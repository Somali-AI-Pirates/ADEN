"""Reproduce the selected baseline while replacing only its dense embeddings."""

import argparse
import fcntl
import gc
import os
from pathlib import Path
from time import perf_counter

import numpy as np

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    object_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    load_inputs,
    loaded_runtime,
)

from .assets import MODELS

DEFAULT_RUN = ROOT / "artifacts/u_10o0o/advanced-embedding-ablation-20261111"
DIMENSIONS = {"snowflake": 1024, "kure_v3": 2048, "nemotron": 4096, "qwen3": 4096}


def log(message):
    print(message, flush=True)


def validate_inputs(run):
    manifest = read_json(run / "frozen.json")
    check_runtime(manifest["runtime_files"])
    for item in manifest["baseline"]["inputs"] + manifest["question_sources"]:
        if file_sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError(f"Frozen input changed: {item['path']}")
    if file_sha(run / "questions.json") != manifest["questions_sha256"]:
        raise ValueError("Question snapshot changed")
    return manifest


def freeze(run):
    from experimental.u_10o0o.pattern_retrieval.legacy_baseline import LegacyBaseline

    if (run / "frozen.json").exists():
        validate_inputs(run)
        log("Existing protocol fingerprints verified")
        return
    run.mkdir(parents=True, exist_ok=True)
    questions, items, sources, counts = load_inputs()
    metadata = LegacyBaseline().metadata()
    runtime = loaded_runtime()
    # Own runner is independently fingerprinted after implementation stabilizes.
    runtime = [r for r in runtime if "/embedding_ablation/" not in r["path"]]
    write_json(run / "questions.json", questions)
    write_json(
        run / "frozen.json",
        {
            "schema_version": 1,
            "baseline": metadata,
            "items": items,
            "question_sources": sources,
            "suite_counts": counts,
            "questions_sha256": file_sha(run / "questions.json"),
            "runtime_files": runtime,
            "models": MODELS,
            "references_opened": False,
            "external_api_calls": 0,
        },
    )
    log("Frozen current baseline, 18000 pages and 280 questions; no references read")


def model_loader(name):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(8)
    model_id, revision = MODELS[name]
    if name == "snowflake":
        model = SentenceTransformer(
            model_id,
            revision=revision,
            device="cpu",
            local_files_only=True,
            model_kwargs={"dtype": torch.float32},
        )
    elif name == "kure_v3":
        model = SentenceTransformer(
            model_id,
            revision=revision,
            device="cuda",
            local_files_only=True,
            model_kwargs={"dtype": torch.bfloat16},
        )
    else:
        model = SentenceTransformer(
            model_id,
            revision=revision,
            local_files_only=True,
            config_kwargs={"use_cache": False},
            model_kwargs={
                "dtype": torch.bfloat16,
                "device_map": "auto",
                "max_memory": {0: "5GiB", "cpu": "19GiB"},
                "attn_implementation": "sdpa",
            },
        )
    if model.get_sentence_embedding_dimension() != DIMENSIONS[name]:
        raise ValueError("Unexpected embedding dimension")
    return model


def encode(model, texts, *, kind, batch_size):
    import torch

    method = model.encode_document if kind == "document" else model.encode_query
    with torch.inference_mode():
        vectors = method(
            texts,
            batch_size=batch_size,
            normalize_embeddings=False,
            show_progress_bar=False,
            convert_to_tensor=True,
        )
    values = torch.nn.functional.normalize(vectors.float(), p=2, dim=1).cpu().numpy()
    validate_vectors(values, len(texts), model.get_sentence_embedding_dimension())
    return values


def validate_vectors(vectors, count, dimensions):
    if vectors.shape != (count, dimensions) or not np.isfinite(vectors).all():
        raise ValueError("Invalid embedding shape or non-finite values")
    norms = np.linalg.norm(vectors.astype(np.float32), axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise ValueError("Embedding rows are not L2-normalized")


def input_stats(model, texts, kind):
    prompt = model.prompts.get(kind, "")
    lengths = []
    for start in range(0, len(texts), 256):
        tokenized = model.tokenizer(
            [prompt + text for text in texts[start : start + 256]],
            truncation=False,
            padding=False,
            verbose=False,
        )
        lengths.extend(map(len, tokenized["input_ids"]))
    maximum = model.max_seq_length
    return {
        "count": len(lengths),
        "prompt": prompt,
        "max_seq_length": maximum,
        "raw_tokens": sum(lengths),
        "max_raw_tokens": max(lengths),
        "truncated_inputs": sum(length > maximum for length in lengths),
    }


def vectors(run, name, batch_size, stop_after):
    frozen = validate_inputs(run)
    directory = run / name
    directory.mkdir(exist_ok=True)
    with (directory / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        build_vectors(run, directory, frozen, name, batch_size, stop_after)


def build_vectors(run, directory, frozen, name, batch_size, stop_after):
    import torch

    from experimental.u_10o0o.embeding import VECTORS_PATH
    from experimental.u_10o0o.prepare import CACHE_PATH

    stamp = object_sha({"frozen": frozen, "name": name, "batch_size": batch_size})
    state_path = directory / "vectors-manifest.json"
    state = (
        read_json(state_path)
        if state_path.exists()
        else {
            "name": name,
            "stamp": stamp,
            "status": "building",
            "chunks": [],
            "batch_size": batch_size,
            "document_seconds": 0.0,
        }
    )
    if state["stamp"] != stamp:
        raise ValueError("Resume configuration differs from saved build")
    if state["status"] == "complete":
        validate_assets(run, name)
        log(f"{name}: existing vectors verified")
        return
    next_row = 0
    for chunk in state["chunks"]:
        if chunk["start"] != next_row or chunk["count"] <= 0:
            raise ValueError("Vector chunks are not contiguous and ordered")
        next_row += chunk["count"]
        if file_sha(directory / chunk["file"]) != chunk["sha256"]:
            raise ValueError("Incomplete/corrupt saved vector chunk")
    pages = read_json(CACHE_PATH)
    questions = read_json(run / "questions.json")
    start_load = perf_counter()
    model = model_loader(name)
    state["load_seconds"] = perf_counter() - start_load
    state["model"] = {
        "id": MODELS[name][0],
        "revision": MODELS[name][1],
        "dimensions": DIMENSIONS[name],
        "dtype": str(model.dtype),
        "device_map": {
            k: str(v)
            for k, v in getattr(model[0].auto_model, "hf_device_map", {}).items()
        },
        "device": str(model.device),
        "prompts": model.prompts,
        "max_seq_length": model.max_seq_length,
        "quantized": False,
    }
    log(
        f"{name}: loaded in {state['load_seconds']:.1f}s, dtype={model.dtype}, batch={batch_size}"
    )
    texts = [row["text"] for row in pages]
    state["document_inputs"] = input_stats(model, texts, "document")
    state["question_inputs"] = input_stats(
        model, [q["question"] for q in questions], "query"
    )
    if (
        state["document_inputs"]["truncated_inputs"]
        or state["question_inputs"]["truncated_inputs"]
    ):
        raise ValueError("Unexpected truncation; inspect before continuing")
    write_json(state_path, state)
    if name == "snowflake":
        docs_path = VECTORS_PATH
        validate_vectors(np.load(docs_path), len(pages), DIMENSIONS[name])
        state["document_vectors_reused"] = True
    else:
        state["document_vectors_reused"] = False
        completed = sum(row["count"] for row in state["chunks"])
        chunk_size = 256
        session_start, initial_completed = perf_counter(), completed
        while completed < len(texts):
            end = min(completed + chunk_size, len(texts))
            started = perf_counter()
            result = encode(
                model, texts[completed:end], kind="document", batch_size=batch_size
            )
            elapsed = perf_counter() - started
            filename = f"documents-{completed:05d}-{end:05d}.npy"
            np.save(directory / filename, result, allow_pickle=False)
            state["chunks"].append(
                {
                    "start": completed,
                    "count": end - completed,
                    "file": filename,
                    "sha256": file_sha(directory / filename),
                    "seconds": elapsed,
                }
            )
            state["document_seconds"] += elapsed
            completed = end
            write_json(state_path, state)
            rate = (completed - initial_completed) / (perf_counter() - session_start)
            log(
                f"{name}: {completed}/{len(texts)} pages, {rate:.2f} pages/s, estimated remaining {(len(texts) - completed) / rate / 60:.1f} min"
            )
            if stop_after and completed >= stop_after:
                log("Stopped at requested smoke checkpoint; safe to resume")
                return
        rows = [
            np.load(directory / row["file"], allow_pickle=False)
            for row in state["chunks"]
        ]
        doc_vectors = np.concatenate(rows)
        validate_vectors(doc_vectors, len(pages), DIMENSIONS[name])
        docs_path = directory / "document-vectors.npy"
        np.save(docs_path, doc_vectors, allow_pickle=False)
    started = perf_counter()
    if name == "snowflake":
        question_vectors = np.zeros(
            (len(questions), DIMENSIONS[name]), dtype=np.float32
        )
        positions = {question["id"]: i for i, question in enumerate(questions)}
        for batch in batch_questions(questions, frozen["items"]):
            values = model.encode_query(
                [row["question"] for row in batch],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            for row, vector in zip(batch, values, strict=True):
                question_vectors[positions[row["id"]]] = vector
        state["query_batches"] = [60, 180, 40]
    else:
        question_vectors = encode(
            model,
            [q["question"] for q in questions],
            kind="query",
            batch_size=batch_size,
        )
    validate_vectors(question_vectors, len(questions), DIMENSIONS[name])
    state["question_seconds"] = perf_counter() - started
    np.save(directory / "question-vectors.npy", question_vectors, allow_pickle=False)
    state.update(
        status="complete",
        document_path=str(docs_path.relative_to(ROOT)),
        document_sha256=file_sha(docs_path),
        question_sha256=file_sha(directory / "question-vectors.npy"),
        frozen_sha256=file_sha(run / "frozen.json"),
    )
    state["peak_cuda_allocated_bytes"] = (
        torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
    )
    write_json(state_path, state)
    log(f"{name}: vectors complete; no reference files opened")


def validate_assets(run, name):
    state = read_json(run / name / "vectors-manifest.json")
    if state["status"] != "complete" or state["frozen_sha256"] != file_sha(
        run / "frozen.json"
    ):
        raise ValueError("Vector build not complete or belongs to different inputs")
    if file_sha(ROOT / state["document_path"]) != state["document_sha256"]:
        raise ValueError("Document vector fingerprint mismatch")
    if file_sha(run / name / "question-vectors.npy") != state["question_sha256"]:
        raise ValueError("Question vector fingerprint mismatch")
    return state


class SavedQueries:
    """Supply the exact precomputed query vectors to the unchanged baseline."""

    def __init__(self, questions, embeddings):
        if len(questions) != len(embeddings):
            raise ValueError("Question/vector count differs")
        self.by_text = {}
        for row, vector in zip(questions, embeddings, strict=True):
            text = row["question"]
            if text in self.by_text and not np.allclose(
                self.by_text[text], vector, atol=1e-5
            ):
                raise ValueError("Duplicate text has inconsistent embeddings")
            self.by_text[text] = vector

    def encode_query(self, queries, **kwargs):
        return np.stack([self.by_text[query] for query in queries])


def retrieve(run, name):
    import torch
    from sentence_transformers import CrossEncoder

    from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
        KURE,
        MAX_TOKENS,
        LegacyBaseline,
        _source_info,
    )
    from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
        MODELS as RERANK_MODELS,
    )
    from experimental.u_10o0o.search import rank_pages

    frozen = validate_inputs(run)
    assets = validate_assets(run, name)
    directory = run / name / "retrieval"
    directory.mkdir(exist_ok=False)
    questions = read_json(run / "questions.json")
    started = perf_counter()
    torch.set_num_threads(8)
    engine = LegacyBaseline()
    engine._ensure_corpus()
    engine.vectors = np.load(ROOT / assets["document_path"], allow_pickle=False)
    queries = np.load(run / name / "question-vectors.npy", allow_pickle=False)
    validate_vectors(engine.vectors, len(engine.pages), DIMENSIONS[name])
    validate_vectors(queries, len(questions), DIMENSIONS[name])
    engine.embedder = SavedQueries(questions, queries)
    engine.reranker = CrossEncoder(
        KURE,
        revision=RERANK_MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    manifest = {
        "name": name,
        "status": "running",
        "frozen_sha256": file_sha(run / "frozen.json"),
        "assets_sha256": file_sha(run / name / "vectors-manifest.json"),
        "references_opened": False,
        "external_api_calls": 0,
        "batches": [],
        "timing_scope": "batched quality run excluding precomputed query embeddings",
        "runtime_files": loaded_runtime(),
    }
    write_json(directory / "manifest.json", manifest)
    done = 0
    for batch in batch_questions(questions, frozen["items"]):
        batch_start = perf_counter()
        results = engine.retrieve_batch(batch, log)
        elapsed = perf_counter() - batch_start
        for expected, (trace, context) in zip(batch, results, strict=True):
            if trace["id"] != expected["id"] or context["id"] != expected["id"]:
                raise ValueError("Baseline result identity mismatch")
            trace["arm"] = context["arm"] = name
            dense = rank_pages(
                engine.embedder.by_text[expected["question"]],
                engine.vectors,
                engine.pages,
                top_k=50,
            )
            trace["dense_top50"] = [_source_info(page, score) for page, score in dense]
            append_json(directory / "traces.jsonl", trace)
            append_json(directory / "contexts.jsonl", context)
        done += len(batch)
        manifest["batches"].append({"count": len(batch), "seconds": elapsed})
        write_json(directory / "manifest.json", manifest)
        log(f"{name}: saved {done}/280 retrievals")
    validate_inputs(run)
    check_runtime(manifest["runtime_files"])
    manifest.update(
        status="complete",
        count=done,
        wall_seconds=perf_counter() - started,
        traces_sha256=file_sha(directory / "traces.jsonl"),
        contexts_sha256=file_sha(directory / "contexts.jsonl"),
    )
    write_json(directory / "manifest.json", manifest)
    del engine
    gc.collect()
    torch.cuda.empty_cache()


def validate_results(run, name, frozen):
    directory = run / name / "retrieval"
    manifest = read_json(directory / "manifest.json")
    if (
        manifest["status"] != "complete"
        or manifest["count"] != 280
        or manifest["references_opened"]
    ):
        raise ValueError("All four conditions must finish before scoring")
    if manifest["frozen_sha256"] != file_sha(run / "frozen.json"):
        raise ValueError("Different frozen run")
    validate_assets(run, name)
    if manifest["assets_sha256"] != file_sha(run / name / "vectors-manifest.json"):
        raise ValueError("Retrieval/vector binding changed")
    check_runtime(manifest["runtime_files"])
    for part in ("traces", "contexts"):
        if file_sha(directory / f"{part}.jsonl") != manifest[f"{part}_sha256"]:
            raise ValueError("Saved retrieval corrupted")
    traces, contexts = (
        read_jsonl(directory / "traces.jsonl"),
        read_jsonl(directory / "contexts.jsonl"),
    )
    ids = [row["id"] for row in frozen["items"]]
    if [r["id"] for r in traces] != ids or [r["id"] for r in contexts] != ids:
        raise ValueError("Saved question order differs")
    questions = read_json(run / "questions.json")
    import hashlib

    from experimental.u_10o0o.pattern_retrieval.established_score import page_key

    for question, trace, context in zip(questions, traces, contexts, strict=True):
        if (
            trace["question_sha256"]
            != hashlib.sha256(question["question"].encode()).hexdigest()
        ):
            raise ValueError("Trace was produced for another question")
        keys = [page_key(p) for p in context["pages"]]
        if keys != [page_key(p) for p in trace["final_pages"]] or len(keys) != len(
            set(keys)
        ):
            raise ValueError("Final source metadata differs from actual context")
        if trace["arm"] != name or context["arm"] != name:
            raise ValueError("Wrong condition")
        if (
            len(trace["pool"]) > 20
            or len(context["pages"]) > 8
            or trace["context_tokens"] > 8000
        ):
            raise ValueError("Budget exceeded")
    return traces, contexts


def score(run):
    # This gate validates EVERY condition before importing/reading evaluation references.
    frozen = validate_inputs(run)
    saved = {name: validate_results(run, name, frozen) for name in MODELS}
    from app.evaluation.dataset import load_references
    from experimental.u_10o0o.advanced_full_metrics import fact_coverage
    from experimental.u_10o0o.pattern_retrieval.established_score import (
        aggregates,
        category,
        differences,
        official_metrics,
        page_key,
        page_metrics,
        score_item,
    )
    from experimental.u_10o0o.prepare import CACHE_PATH

    references = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in frozen["question_sources"]
    }
    source_pages = {page_key(row): row for row in read_json(CACHE_PATH)}
    by_arm, dense_summary = {}, {}
    for name, (traces, contexts) in saved.items():
        rows, dense_rows = [], []
        for meta, trace, context in zip(frozen["items"], traces, contexts, strict=True):
            reference = references[meta["suite"]][meta["question_id"]]
            rows.append(score_item(meta, reference, trace, context, source_pages))
            for k in (20, 50):
                pages = [source_pages[page_key(p)] for p in trace["dense_top50"][:k]]
                if meta["suite"].startswith("official_"):
                    metrics = official_metrics(reference, pages, cutoff=k)
                    complete = metrics["all_groups"] if metrics else None
                elif meta["suite"] != "negative_control":
                    expected = {
                        page_key(evidence)
                        for requirement in reference["evidence_requirements"]
                        for evidence in requirement["primary"]
                    }
                    metrics = {
                        **page_metrics(expected, pages, cutoff=k),
                        **fact_coverage(reference, pages),
                    }
                    complete = metrics["all_facts"]
                else:
                    metrics, complete = None, None
                dense_rows.append(
                    {
                        **meta,
                        "category": category(reference, meta["suite"]),
                        "k": k,
                        "complete": complete,
                        "metrics": metrics,
                    }
                )
        by_arm[name] = rows
        dense_summary[name] = dense_rows
    output = run / "scores"
    output.mkdir(exist_ok=False)
    write_json(output / "per-question.json", by_arm)
    write_json(output / "dense-per-question.json", dense_summary)
    write_json(
        output / "summary.json",
        {name: aggregates(rows) for name, rows in by_arm.items()},
    )
    write_json(
        output / "differences.json",
        {
            name: differences({"baseline": by_arm["snowflake"], "A": by_arm[name]})
            for name in MODELS
            if name != "snowflake"
        },
    )
    write_json(
        output / "manifest.json",
        {
            "references": [
                {"path": s["references"], "sha256": file_sha(ROOT / s["references"])}
                for s in frozen["question_sources"]
            ],
            "all_results_saved_before_references": True,
            "runtime_files": loaded_runtime(),
        },
    )
    log("Scored all four conditions with existing official/category metrics")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    build = sub.add_parser("vectors")
    build.add_argument("model", choices=tuple(MODELS))
    build.add_argument("--batch-size", type=int, default=8)
    build.add_argument("--stop-after", type=int, default=0)
    retrieve_parser = sub.add_parser("retrieve")
    retrieve_parser.add_argument("model", choices=tuple(MODELS))
    sub.add_parser("score")
    args = parser.parse_args()
    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false"
    )
    if args.command == "freeze":
        freeze(args.run)
    elif args.command == "vectors":
        vectors(args.run, args.model, args.batch_size, args.stop_after)
    elif args.command == "retrieve":
        retrieve(args.run, args.model)
    else:
        score(args.run)


if __name__ == "__main__":
    main()
