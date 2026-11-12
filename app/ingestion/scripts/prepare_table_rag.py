"""Build, verify, and prefetch assets for the Advanced TableRAG bundle."""

import argparse
import json
from pathlib import Path
from app.ingestion.table_rag_builder import build_bundle, check_bundle, prefetch_models


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build from raw Advanced PDFs")
    build.add_argument(
        "--documents",
        type=Path,
        required=True,
        help="Directory containing AS001.pdf through AS120.pdf",
    )
    build.add_argument(
        "--output", type=Path, default=Path("artifacts/advanced-table-rag")
    )
    build.add_argument("--device", choices=("cpu", "cuda", "cuda:0"), default="cpu")
    build.add_argument(
        "--work-dir", type=Path, help="Fingerprint-resumable staging directory"
    )
    build.add_argument(
        "--reuse-vectors-from",
        type=Path,
        help="Reuse only when pages/table chunks and pinned model match exactly",
    )
    check = commands.add_parser(
        "check", help="Verify hashes, SQLite, source alignment, and vectors"
    )
    check.add_argument(
        "--directory", type=Path, default=Path("artifacts/advanced-table-rag")
    )
    commands.add_parser(
        "models", help="Download the pinned embedding and reranker revisions"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_bundle(
                args.documents,
                args.output,
                device=args.device,
                work_dir=args.work_dir,
                cache_bundle=args.reuse_vectors_from,
            )
            print(f"Advanced TableRAG bundle ready: {result}")
            print(json.dumps(check_bundle(result), ensure_ascii=False, indent=2))
        elif args.command == "check":
            print(
                json.dumps(check_bundle(args.directory), ensure_ascii=False, indent=2)
            )
        else:
            print(json.dumps(prefetch_models(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"TableRAG {args.command} failed: {error}\n")


if __name__ == "__main__":
    main()
