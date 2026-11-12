"""Build, verify, and prefetch assets for the Advanced TableRAG bundle."""

import argparse
import json
from pathlib import Path

from app.ingestion.table_rag_builder import build_bundle, check_bundle, prefetch_models
from app.rag.config import DEFAULT_DATA_DIRECTORY
from app.rag.qdrant_index import (
    DEFAULT_COLLECTION,
    check_qdrant_index,
    upload_qdrant_index,
)
from shared.settings import PROJECT_ROOT


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
    upload_qdrant = commands.add_parser(
        "upload-qdrant",
        help="Upload the existing KURE vectors.npy to a dedicated Qdrant collection",
    )
    upload_qdrant.add_argument(
        "--directory", type=Path, default=DEFAULT_DATA_DIRECTORY
    )
    upload_qdrant.add_argument("--collection", default=DEFAULT_COLLECTION)
    check_qdrant = commands.add_parser(
        "check-qdrant", help="Check a ready TableRAG Qdrant collection"
    )
    check_qdrant.add_argument(
        "--directory", type=Path, default=DEFAULT_DATA_DIRECTORY
    )
    check_qdrant.add_argument("--collection", default=DEFAULT_COLLECTION)
    check_qdrant.add_argument(
        "--full",
        action="store_true",
        help="Read every point and compare its payload and vector to the local corpus",
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
        elif args.command in {"upload-qdrant", "check-qdrant"}:
            from dotenv import load_dotenv

            load_dotenv(PROJECT_ROOT / ".env", override=False)
            if args.command == "upload-qdrant":
                result = upload_qdrant_index(
                    args.directory,
                    collection=args.collection,
                )
            else:
                result = check_qdrant_index(
                    args.directory,
                    collection=args.collection,
                    full=args.full,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(prefetch_models(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"TableRAG {args.command} failed: {error}\n")


if __name__ == "__main__":
    main()
