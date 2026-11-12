"""Build source evidence or execute the bounded experimental workflow."""

import argparse
from pathlib import Path

from experimental.u_10o0o.table_rag.speed import V5

from .catalog import DEFAULT, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("query")
    q.add_argument("question")
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--catalog", type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.command == "build":
        print(build(args.output, V5))
        return
    if args.output.exists():
        raise FileExistsError("Use a new result path")
    from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
    from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
    from experimental.u_10o0o.pattern_retrieval.common import write_json
    from experimental.u_10o0o.table_rag.retrieval import Retriever, source_id

    from .agent import WorkflowAgent
    from .catalog import Catalog

    r = Retriever(V5, device="cpu_gpu_rerank")
    client = StructuredLunaInterpreter().client
    try:
        result = WorkflowAgent(r, client, catalog=Catalog(args.catalog)).run(
            args.question
        )
        result["context"] = context_pages(result["context"])
        result["ranked"] = [
            {"source_id": source_id(p), "score": s} for p, s in result["ranked"]
        ]
        write_json(args.output, result)
        print(
            {
                "output": str(args.output),
                "status": result["finish"]["status"],
                "calls": len(result["turns"]),
                "seconds": result["seconds"],
            }
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
