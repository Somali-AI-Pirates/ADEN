"""One isolated output root and immutable input fingerprints."""

import os
import sys
from pathlib import Path
from experimental.u_10o0o.additional_evidence_ab.common import (
    ROOT,
    BUNDLE,
    read,
    write as write,
    digest as digest,
    file_sha,
)

NAME = "advanced-structural-retrieval-20261113"
OUT = ROOT / "artifacts/u_10o0o" / NAME
REC = ROOT / "experimental/u_10o0o/records" / NAME
PACKAGE = Path(__file__).parent
PRIOR = ROOT / "artifacts/u_10o0o/advanced-additional-evidence-20261113"
RUNTIME = PRIOR / "runtime"


def activate():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing")
    os.environ.update(
        LANGCHAIN_TRACING_V2="false",
        LANGSMITH_TRACING="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    if any(k == "app" or k.startswith("app.") for k in sys.modules):
        raise RuntimeError("Runtime must be activated before app imports")
    sys.path.insert(0, str(RUNTIME))


def check_frozen():
    p = read(OUT / "protocol.json")
    for name, expected in p["code"].items():
        if file_sha(PACKAGE / name) != expected:
            raise RuntimeError("Frozen experiment changed: " + name)
    for name, expected in p.get("dependencies", {}).items():
        if file_sha(ROOT / name) != expected:
            raise RuntimeError("Experiment dependency changed: " + name)
    for name, expected in p["inputs"].items():
        if file_sha(OUT / name) != expected:
            raise RuntimeError("Frozen input changed: " + name)
    for name, expected in p["bundle"].items():
        if file_sha(BUNDLE / name) != expected:
            raise RuntimeError("Bundle changed: " + name)
    for name, expected in read(PRIOR / "protocol.json")["runtime_files"].items():
        if file_sha(RUNTIME / name) != expected:
            raise RuntimeError("Runtime changed: " + name)
    return p


def key(repeat, arm, qid):
    return f"r{repeat}-{arm}-{qid}"
