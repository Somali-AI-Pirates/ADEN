"""Run symmetric post-hoc evidence review after every retrieval is saved."""

from importlib import import_module
from pathlib import Path
from experimental.u_10o0o.pattern_retrieval.common import ROOT

if __name__ == "__main__":
    m = import_module(
        "experimental.u_10o0o.records.advanced-table-rag-official-category-20261111.review"
    )
    m.RECORDS = Path(__file__).parent
    m.OUTPUT = ROOT / "artifacts/u_10o0o" / m.RECORDS.name / "v1"
    m.main()
