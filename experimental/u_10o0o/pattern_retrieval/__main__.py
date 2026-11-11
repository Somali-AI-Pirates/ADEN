"""Run the source-only Advanced A/B/C experiment."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "build", help="Extract original PDFs and build fresh local vectors."
    )
    build.add_argument(
        "--source-dir", type=Path, default=Path("data/advanced/documents")
    )
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--corpus-only", action="store_true")
    retrieve = commands.add_parser(
        "retrieve", help="Save A/B/C retrievals without references."
    )
    retrieve.add_argument("--index-dir", type=Path, required=True)
    retrieve.add_argument("--questions", type=Path, required=True)
    retrieve.add_argument("--output-dir", type=Path, required=True)
    score = commands.add_parser(
        "score", help="Score completed saved retrievals against new references."
    )
    score.add_argument("--index-dir", type=Path, required=True)
    score.add_argument("--retrieval-dir", type=Path, required=True)
    score.add_argument("--evaluation-dir", type=Path, required=True)
    score.add_argument("--output-dir", type=Path, required=True)
    timing = commands.add_parser(
        "timing", help="Measure the frozen CPU subset without query caching."
    )
    timing.add_argument("--index-dir", type=Path, required=True)
    timing.add_argument("--evaluation-dir", type=Path, required=True)
    timing.add_argument("--gpu-dir", type=Path, required=True)
    timing.add_argument("--output-dir", type=Path, required=True)
    report = commands.add_parser(
        "report", help="Write a sanitized report with A/B/C comparisons."
    )
    report.add_argument("--scored-dir", type=Path, required=True)
    report.add_argument("--timing-dir", type=Path, required=True)
    report.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    # Local cached model assets only. No API credentials are loaded.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ[name] = "4"
    if args.command == "build":
        from .common import file_sha, logger, read_json
        from .corpus import build_corpus
        from .models import build_vectors

        args.output_dir.mkdir(parents=True, exist_ok=True)
        log = logger(args.output_dir)
        if not (args.output_dir / "corpus.sqlite3").exists():
            build_corpus(args.source_dir, args.output_dir, log)
        else:
            manifest = read_json(args.output_dir / "corpus-manifest.json")
            if manifest.get("status") != "complete":
                raise ValueError("Incomplete corpus; use a new output directory.")
            if manifest["corpus_sha256"] != file_sha(
                args.output_dir / "corpus.sqlite3"
            ):
                raise ValueError("Completed corpus bytes changed.")
            source_names = {
                p.name
                for p in args.source_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".pdf"
            }
            if source_names != {d["file_name"] for d in manifest["documents"]}:
                raise ValueError("Original PDF inventory changed since corpus build.")
            for document in manifest["documents"]:
                source = args.source_dir / document["file_name"]
                if file_sha(source) != document["pdf_sha256"]:
                    raise ValueError("Original PDF bytes changed since corpus build.")
            log("Using this run's completed fresh corpus for vector preparation.")
        if not args.corpus_only:
            build_vectors(args.output_dir, log)
    elif args.command == "retrieve":
        from .engine import run_retrieval

        run_retrieval(args.index_dir, args.questions, args.output_dir)
    elif args.command == "score":
        from .evaluation import score_run

        score_run(
            args.index_dir, args.retrieval_dir, args.evaluation_dir, args.output_dir
        )
    elif args.command == "timing":
        from .engine import run_timing

        run_timing(args.index_dir, args.evaluation_dir, args.gpu_dir, args.output_dir)
    else:
        from .reporting import make_report

        make_report(args.scored_dir, args.timing_dir, args.output_dir)


if __name__ == "__main__":
    main()
