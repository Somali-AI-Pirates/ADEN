"""Fresh DB-first control vs bounded source-unit workflow on the established 278 questions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from shutil import copyfile

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    object_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.table_rag_unified.agent import (
    INSTRUCTIONS,
    TOOLS,
    UnifiedAgent,
    integrated_runtime,
)
from experimental.u_10o0o.table_rag_unified.index import DEFAULT as RECORD_INDEX
from experimental.u_10o0o.table_rag_unified.index import RecordIndex
from experimental.u_10o0o.table_rag_workflow.agent import (
    PLAN_PROMPT,
    SELECT_PROMPT,
    Plan,
    Selection,
    WorkflowAgent,
)
from experimental.u_10o0o.table_rag_workflow.catalog import DEFAULT, Catalog

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"
BASE = "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111"


class Pair:
    def __init__(self, retriever, client, *, preserve_evidence):
        self.agent = (
            WorkflowAgent(retriever, client)
            if preserve_evidence
            else UnifiedAgent(retriever, client, db_first=True)
        )

    def run(self, *args, **kwargs):
        return self.agent.run(*args, **kwargs)


def runner():
    m = import_module(BASE + ".run_benchmark")
    m.OUT = OUT
    m.TableAgent = Pair
    m.ThreadPoolExecutor = lambda **kwargs: ThreadPoolExecutor(max_workers=8)
    return m


def freeze():
    if OUT.exists():
        raise FileExistsError("Use a new frozen run")
    OUT.mkdir(parents=True)
    source = ROOT / "artifacts/u_10o0o/advanced-table-rag-db-first-20261112/v1"
    for name in ("questions.json", "references.json", "question-metadata.json"):
        copyfile(source / name, OUT / name)
    runner().freeze()
    p = read_json(OUT / "protocol.json")
    paths = [Path(__file__)]
    for package in ("table_rag_unified", "table_rag_workflow"):
        paths += sorted((ROOT / "experimental/u_10o0o" / package).glob("*.py"))
    for path in paths:
        if path.name.startswith("test_"):
            continue
        relative = path.relative_to(ROOT)
        p["runtime_files"].append({"path": str(relative), "sha256": file_sha(path)})
        dest = OUT / "executed-code" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        copyfile(path, dest)
    p.update(
        arms={
            "A": "Existing unified DB-first",
            "B": "Request-bound source units and bounded workflow",
        },
        prompts={
            "A": object_sha([INSTRUCTIONS, TOOLS]),
            "B": object_sha(
                [
                    PLAN_PROMPT,
                    SELECT_PROMPT,
                    Plan.model_json_schema(),
                    Selection.model_json_schema(),
                ]
            ),
        },
        treatment="Bundled architecture: source-section evidence, conservative record fast path, one plan, request-bound retrieval, ID selection, at most one repair; no final union reranker.",
        workers=8,
        workflow_max_api_calls=3,
        workflow_max_document_searches=5,
        evidence_catalog={
            "directory": str(DEFAULT.relative_to(ROOT)),
            "manifest_sha256": file_sha(DEFAULT / "manifest.json"),
            "database_sha256": Catalog().manifest["database_sha256"],
        },
        record_index={
            "manifest_sha256": file_sha(RECORD_INDEX / "manifest.json"),
            "database_sha256": RecordIndex().manifest["database_sha256"],
        },
        scope="Known regression/development sets. No automatic baseline promotion. Structured values and original scopes retained; no inferred cross-event policy equivalence.",
    )
    write_json(OUT / "protocol.json", p)
    write_json(HERE / "protocol.json", p)
    write_json(
        OUT / "prompts.json",
        {"control": INSTRUCTIONS, "plan": PLAN_PROMPT, "selection": SELECT_PROMPT},
    )
    print("Frozen 278 questions; reference contents not read")


def run():
    p = read_json(OUT / "protocol.json")
    assert (
        file_sha(DEFAULT / "manifest.json") == p["evidence_catalog"]["manifest_sha256"]
    )
    assert (
        file_sha(RECORD_INDEX / "manifest.json") == p["record_index"]["manifest_sha256"]
    )
    with integrated_runtime():
        runner().run()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["freeze", "run"])
    globals()[p.parse_args().command]()
