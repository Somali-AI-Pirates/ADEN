"""Build, verify, and prefetch assets for the Advanced TableRAG bundle."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from app.ingestion.table_rag_builder import build_bundle, check_bundle, prefetch_models
from app.rag.config import load_table_rag_settings, resolve_project_path
from app.rag.qdrant_index import check_qdrant_index, upload_qdrant_index


def _build_device(argument: str | None, configured: str) -> str:
    if argument is not None:
        return argument
    # cpu_gpu_rerank is a serving layout; ingestion embeds on CPU in that mode.
    return "cuda" if configured == "cuda" else "cpu"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Build from raw Advanced PDFs")
    build.add_argument(
        "--documents",
        type=Path,
        help="Directory containing AS001.pdf through AS120.pdf; defaults to configured documents_dir",
    )
    build.add_argument(
        "--output",
        type=Path,
        help="Bundle directory; defaults to the shared TableRAG data_directory",
    )
    build.add_argument("--device", choices=("cpu", "cuda", "cuda:0"))
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
        "--directory",
        type=Path,
        help="Bundle directory; defaults to the shared TableRAG data_directory",
    )

    upload_qdrant = commands.add_parser(
        "upload-qdrant",
        help="Upload the existing KURE vectors.npy to a dedicated Qdrant collection",
    )
    upload_qdrant.add_argument("--directory", type=Path)
    upload_qdrant.add_argument("--collection")

    check_qdrant = commands.add_parser(
        "check-qdrant", help="Check a ready TableRAG Qdrant collection"
    )
    check_qdrant.add_argument("--directory", type=Path)
    check_qdrant.add_argument("--collection")
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
            settings = load_table_rag_settings(data_directory=args.output)
            documents = (
                resolve_project_path(args.documents)
                if args.documents is not None
                else settings.documents_dir
            )
            work_dir = (
                resolve_project_path(args.work_dir)
                if args.work_dir is not None
                else None
            )
            cache_bundle = (
                resolve_project_path(args.reuse_vectors_from)
                if args.reuse_vectors_from is not None
                else None
            )
            result = build_bundle(
                documents,
                settings.data_directory,
                device=_build_device(args.device, settings.device),
                work_dir=work_dir,
                cache_bundle=cache_bundle,
            )
            print(f"Advanced TableRAG bundle ready: {result}")
            print(json.dumps(check_bundle(result), ensure_ascii=False, indent=2))
        elif args.command == "check":
            settings = load_table_rag_settings(data_directory=args.directory)
            print(
                json.dumps(
                    check_bundle(settings.data_directory),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command in {"upload-qdrant", "check-qdrant"}:
            settings = load_table_rag_settings(data_directory=args.directory)
            if args.collection is not None:
                settings = replace(settings, collection=args.collection)
            if args.command == "upload-qdrant":
                result = upload_qdrant_index(
                    settings.data_directory,
                    collection=settings.collection,
                    credentials=settings.credentials,
                )
            else:
                result = check_qdrant_index(
                    settings.data_directory,
                    collection=settings.collection,
                    full=args.full,
                    credentials=settings.credentials,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(prefetch_models(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"TableRAG {args.command} failed: {error}\n")


if __name__ == "__main__":
    main()
