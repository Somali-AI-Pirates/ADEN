"""Save current baseline or fresh A results on the established 60+220 questions."""

import argparse
import os
import sys
from pathlib import Path
from time import perf_counter

from .common import (
    CONFIG,
    DEFAULT_RUN,
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    logger,
    object_sha,
    write_json,
)


def question_sources():
    from experimental.u_10o0o.advanced_search import evaluation_files

    standard = evaluation_files("standard")
    return {
        "official_development": standard["development"],
        "official_additional": standard["additional"],
        **evaluation_files("categories"),
    }


def load_inputs():
    from app.evaluation.dataset import load_questions

    sources = question_sources()
    metadata, questions, stamps = [], [], []
    seen = set()
    for suite, (question_path, reference_path) in sources.items():
        stamps.append(
            {
                "suite": suite,
                "path": str(question_path.relative_to(ROOT)),
                "sha256": file_sha(question_path),
                "references": str(reference_path.relative_to(ROOT)),
            }
        )
        for row in load_questions(question_path):
            run_id = f"{suite}:{row['id']}"
            if run_id in seen:
                raise ValueError("Duplicate suite/question ID.")
            seen.add(run_id)
            questions.append({"id": run_id, "question": row["question"]})
            metadata.append({"id": run_id, "suite": suite, "question_id": row["id"]})
    counts = {
        suite: sum(row["suite"] == suite for row in metadata) for suite in sources
    }
    if sum(counts[s] for s in counts if s.startswith("official_")) != 60:
        raise ValueError("Expected the complete official 60-question set.")
    if sum(counts.values()) != 280 or counts.get("semantic_queries") != 40:
        raise ValueError("Expected the complete official 60 + category 220 set.")
    return questions, metadata, stamps, counts


def loaded_runtime():
    """Fingerprint the actual repository helpers imported by this process."""
    paths = set()
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if (
            path.suffix == ".py"
            and path.is_relative_to(ROOT)
            and ".venv" not in path.relative_to(ROOT).parts
            and path.is_file()
        ):
            paths.add(path)
    return sorted(
        [{"path": str(p.relative_to(ROOT)), "sha256": file_sha(p)} for p in paths],
        key=lambda row: row["path"],
    )


def batch_questions(questions, metadata):
    by_id = {row["id"]: row for row in questions}
    groups = ("official", "categories_first_180", "semantic_queries")
    batches = {group: [] for group in groups}
    for row in metadata:
        suite = row["suite"]
        group = (
            "official"
            if suite.startswith("official_")
            else "semantic_queries"
            if suite == "semantic_queries"
            else "categories_first_180"
        )
        batches[group].append(by_id[row["id"]])
    if [len(batches[key]) for key in groups] != [60, 180, 40]:
        raise ValueError("Unexpected official/category batch boundaries.")
    return list(batches.values())


def run(arm, index_dir, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    log = logger(output)
    started = perf_counter()
    questions, metadata, sources, counts = load_inputs()
    write_json(output / "questions.json", questions)
    manifest = {
        "schema_version": 1,
        "status": "running",
        "arm": arm,
        "items": metadata,
        "suite_counts": counts,
        "question_sources": sources,
        "questions_sha256": file_sha(output / "questions.json"),
        "external_api_calls": 0,
        "context_policy": "native preset, max 8 pages and nominal 8000 tokens",
        "timing_scope": "batched quality run; not comparable online request latency",
    }
    write_json(output / "manifest.json", manifest)
    if arm == "A":
        from .engine import Engine

        engine = Engine(index_dir, "cuda")
        index_dir = Path(index_dir)
        manifest["native_config"] = CONFIG
        manifest["inputs"] = [
            {
                "path": str((index_dir / name).resolve().relative_to(ROOT)),
                "sha256": file_sha(index_dir / name),
            }
            for name in (
                "corpus.sqlite3",
                "corpus-manifest.json",
                "vectors.npy",
                "vectors-manifest.json",
            )
        ]
        manifest["model_assets"] = engine.model_assets
        manifest["index_dir"] = str(index_dir.resolve().relative_to(ROOT))
    else:
        from .legacy_baseline import LegacyBaseline

        engine = LegacyBaseline()
        manifest["baseline"] = engine.metadata()
        manifest["native_config"] = manifest["baseline"]["config"]
        manifest["inputs"] = manifest["baseline"]["inputs"]
    stamps = loaded_runtime()
    manifest["runtime_files"] = stamps
    manifest["code_sha256"] = object_sha(stamps)
    write_json(output / "manifest.json", manifest)
    if arm == "A":
        engine.retrieve(questions[0], "A")
        for number, row in enumerate(questions, 1):
            trace, context = engine.retrieve(row, "A")
            append_json(output / "traces.jsonl", trace)
            append_json(output / "contexts.jsonl", context)
            if number % 20 == 0 or number == len(questions):
                log(f"A saved {number}/{len(questions)} questions.")
    else:
        completed = 0
        for batch in batch_questions(questions, metadata):
            results = engine.retrieve_batch(batch, log)
            if len(results) != len(batch):
                raise ValueError("Baseline did not return every input question.")
            for expected, (trace, context) in zip(batch, results, strict=True):
                if trace["id"] != expected["id"] or context["id"] != expected["id"]:
                    raise ValueError("Baseline output/question identity mismatch.")
                append_json(output / "traces.jsonl", trace)
                append_json(output / "contexts.jsonl", context)
            completed += len(batch)
            log(f"Baseline saved {completed}/{len(questions)} questions.")
    check_runtime(stamps)
    for item in sources + manifest["inputs"]:
        if file_sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("A question or index input changed during retrieval.")
    manifest.update(
        status="complete",
        trace_count=len(questions),
        traces_sha256=file_sha(output / "traces.jsonl"),
        contexts_sha256=file_sha(output / "contexts.jsonl"),
        wall_seconds=perf_counter() - started,
        references_opened=False,
    )
    write_json(output / "manifest.json", manifest)
    log(f"{arm}: all 280 results saved; references were not opened.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("A", "baseline"), required=True)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_RUN / "index-v4")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if args.arm == "A":
        for key in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
            os.environ[key] = "4"
    run(args.arm, args.index_dir, args.output_dir)


if __name__ == "__main__":
    main()
