"""Isolated unified TableRAG CLI; existing defaults are not changed."""

import argparse
import json
from pathlib import Path

from .agent import UnifiedAgent, integrated_runtime
from .index import DEFAULT, RecordIndex, build
from experimental.u_10o0o.table_rag.speed import V5


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--output", required=True, type=Path)
    k = sub.add_parser("lookup")
    k.add_argument("identifiers", nargs="+")
    k.add_argument("--index", type=Path, default=DEFAULT)
    q = sub.add_parser("query")
    q.add_argument("question")
    q.add_argument("--output", required=True, type=Path)
    q.add_argument("--index", type=Path, default=DEFAULT)
    q.add_argument(
        "--db-first",
        action="store_true",
        help="Try unique code records before hybrid retrieval",
    )
    q.add_argument(
        "--verify-evidence",
        action="store_true",
        help="Require request-level source quotations before finishing",
    )
    args = p.parse_args()
    if args.command == "build":
        print(json.dumps(build(args.output, V5), ensure_ascii=False))
    elif args.command == "lookup":
        print(
            json.dumps(
                RecordIndex(args.index).lookup(
                    [{"identifiers": [k]} for k in args.identifiers]
                ),
                ensure_ascii=False,
            )
        )
    else:
        from experimental.u_10o0o.advanced_structured_luna import (
            StructuredLunaInterpreter,
        )
        from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
        from experimental.u_10o0o.pattern_retrieval.common import write_json
        from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id

        if args.output.exists():
            raise FileExistsError("Use a new local result path")
        r = Retriever(V5, device="cpu_gpu_rerank")
        client = StructuredLunaInterpreter().client
        try:
            with integrated_runtime():
                result = UnifiedAgent(
                    r,
                    client,
                    RecordIndex(args.index),
                    db_first=args.db_first,
                    verify_evidence=args.verify_evidence,
                ).run(args.question)
            result["context"] = context_pages(result["context"])
            result["ranked"] = [
                {"source_id": source_id(p), "score": s} for p, s in result["ranked"]
            ]
            write_json(args.output, result)
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "finish": result["finish"]["status"],
                        "seconds": result["seconds"],
                    }
                )
            )
        finally:
            client.close()


if __name__ == "__main__":
    main()
