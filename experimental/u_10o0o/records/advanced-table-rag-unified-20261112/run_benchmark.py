"""Frozen current TableRAG vs integrated record/table retrieval, question-only execution."""

import argparse
from importlib import import_module
from pathlib import Path
from shutil import copyfile

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
)
from experimental.u_10o0o.table_rag.agent import (
    PRESERVATION_INSTRUCTIONS,
    TOOLS as OLD_TOOLS,
)
from experimental.u_10o0o.table_rag_unified.agent import (
    AgentPair,
    INSTRUCTIONS,
    TOOLS,
    integrated_runtime,
)
from experimental.u_10o0o.table_rag_unified.index import DEFAULT, RecordIndex

BASE = "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111"
RECORDS = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / RECORDS.name / "v3"


def runner():
    m = import_module(BASE + ".run_benchmark")
    m.OUT = OUT
    return m


def freeze():
    if OUT.exists():
        raise FileExistsError("Use a new experiment directory")
    source = ROOT / "artifacts/u_10o0o/advanced-table-rag-official-category-20261111/v1"
    OUT.mkdir(parents=True)
    # Copy labels without parsing; retrieval never loads them.
    for name in ("questions.json", "references.json", "question-metadata.json"):
        copyfile(source / name, OUT / name)
    runner().freeze()
    p = read_json(OUT / "protocol.json")
    package = ROOT / "experimental/u_10o0o/table_rag_unified"
    for path in [Path(__file__), *sorted(package.glob("*.py"))]:
        if path.name.startswith("test_"):
            continue
        relative = path.relative_to(ROOT)
        p["runtime_files"].append({"path": str(relative), "sha256": file_sha(path)})
        dest = OUT / "executed-code" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        copyfile(path, dest)
    idx = RecordIndex()
    p.update(
        arms={
            "A": "Current KURE-v3 B: native HG + clarification + selected-source preservation",
            "B": "Unified identifier/record/table tools; native HG disabled",
        },
        prompts={
            "A": object_sha([PRESERVATION_INSTRUCTIONS, OLD_TOOLS]),
            "B": object_sha([INSTRUCTIONS, TOOLS]),
        },
        unified_index={
            "path": str(DEFAULT.relative_to(ROOT)),
            "manifest_sha256": file_sha(DEFAULT / "manifest.json"),
            "database_sha256": idx.manifest["database_sha256"],
        },
        limits_note="Five tool operations plus finish. Every hybrid operation ranks <=20, final <=8 pages and 8000 tokens including retained SQL.",
    )
    write_json(OUT / "protocol.json", p)
    write_json(
        OUT / "prompts.json",
        {
            "A": PRESERVATION_INSTRUCTIONS,
            "B": INSTRUCTIONS,
            "A_tools": OLD_TOOLS,
            "B_tools": TOOLS,
        },
    )
    print("Unified protocol frozen; references copied but not read.")


def run():
    p = read_json(OUT / "protocol.json")
    assert file_sha(DEFAULT / "manifest.json") == p["unified_index"]["manifest_sha256"]
    assert (
        RecordIndex().manifest["database_sha256"]
        == p["unified_index"]["database_sha256"]
    )
    m = runner()
    m.TableAgent = AgentPair
    with integrated_runtime():
        m.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run"])
    globals()[parser.parse_args().command]()
