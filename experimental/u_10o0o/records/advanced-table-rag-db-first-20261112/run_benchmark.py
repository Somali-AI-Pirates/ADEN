"""Paired unified search-first vs DB-first; original prompts and inputs are fixed."""

from importlib import import_module
from pathlib import Path
from shutil import copyfile
import argparse

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.table_rag_unified.agent import (
    UnifiedAgent,
    integrated_runtime,
    INSTRUCTIONS,
    TOOLS,
)
from experimental.u_10o0o.table_rag_unified.index import DEFAULT, RecordIndex

RECORDS = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / RECORDS.name / "v1"
BASE = "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111"


class Pair:
    def __init__(self, retriever, client, *, preserve_evidence):
        self.agent = UnifiedAgent(retriever, client, db_first=preserve_evidence)

    def run(self, *args, **kwargs):
        return self.agent.run(*args, **kwargs)


def runner():
    module = import_module(BASE + ".run_benchmark")
    module.OUT = OUT
    module.TableAgent = Pair
    return module


def freeze():
    if OUT.exists():
        raise FileExistsError("Use a new frozen run")
    OUT.mkdir(parents=True)
    source = ROOT / "artifacts/u_10o0o/advanced-table-rag-unified-20261112/v3"
    for name in ("questions.json", "references.json", "question-metadata.json"):
        copyfile(source / name, OUT / name)
    runner().freeze()
    p = read_json(OUT / "protocol.json")
    paths = [
        Path(__file__),
        *sorted((ROOT / "experimental/u_10o0o/table_rag_unified").glob("*.py")),
    ]
    for path in paths:
        if path.name.startswith("test_"):
            continue
        relative = path.relative_to(ROOT)
        p["runtime_files"].append({"path": str(relative), "sha256": file_sha(path)})
        dest = OUT / "executed-code" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        copyfile(path, dest)
    digest = object_sha([INSTRUCTIONS, TOOLS])
    previous = read_json(source / "protocol.json")
    assert digest == previous["prompts"]["B"], (
        "Prompt/tool change is outside this experiment"
    )
    idx = RecordIndex()
    p.update(
        arms={
            "A": "Unified search-first",
            "B": "Unified DB-first for unique complete identifiers",
        },
        prompts={"A": digest, "B": digest},
        treatment="Initial hybrid search is skipped only if every explicit identifier resolves uniquely to original sources. The planner decides follow-up retrieval.",
        fallback="No code, excluded code, unknown/ambiguous identifier or missing source starts with hybrid search. DB errors fail the run.",
        unified_index={
            "path": str(DEFAULT.relative_to(ROOT)),
            "manifest_sha256": file_sha(DEFAULT / "manifest.json"),
            "database_sha256": idx.manifest["database_sha256"],
        },
    )
    write_json(OUT / "protocol.json", p)
    write_json(RECORDS / "protocol.json", p)
    write_json(OUT / "prompts.json", {"instructions": INSTRUCTIONS, "tools": TOOLS})
    print("Frozen DB-first A/B; references copied without reading")


def run():
    p = read_json(OUT / "protocol.json")
    assert file_sha(DEFAULT / "manifest.json") == p["unified_index"]["manifest_sha256"]
    assert (
        RecordIndex().manifest["database_sha256"]
        == p["unified_index"]["database_sha256"]
    )
    with integrated_runtime():
        runner().run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run"])
    globals()[parser.parse_args().command]()
