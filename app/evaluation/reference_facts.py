"""Apply independently checked evaluation-only facts without editing a dataset."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def attach_reviewed_facts(
    references: dict[str, dict[str, Any]], sidecar: Path, reference_path: Path
) -> str:
    raw = sidecar.read_bytes()
    payload = json.loads(raw)
    expected = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    if payload.get("references_sha256") != expected:
        raise ValueError("Derived facts do not match the frozen reference dataset")
    if payload.get("review_status") != "independent_ai_reviewed":
        raise ValueError("Derived facts require an independent review before grading")
    entries = payload.get("items")
    if not isinstance(entries, list):
        raise ValueError("Derived facts require an items list")
    seen = set()
    for row in entries:
        if not isinstance(row, Mapping):
            raise ValueError("Invalid derived fact entry")
        qid, facts = row.get("question_id"), row.get("required_facts")
        if qid in seen or not isinstance(qid, str):
            raise ValueError("Duplicate or invalid derived fact question ID")
        seen.add(qid)
        if not isinstance(facts, list) or not facts or any(
            not isinstance(fact, str) or not fact.strip() for fact in facts
        ):
            raise ValueError("Derived facts must be nonempty statements")
        if row.get("review_status") != "accepted":
            raise ValueError("Each derived fact list must be accepted")
        if qid not in references:
            continue  # A subset grade may use the same reviewed full sidecar.
        if references[qid].get("required_facts") is not None:
            raise ValueError("Derived facts must not replace official required facts")
        references[qid] = {
            **references[qid], "required_facts": facts,
            "required_facts_origin": "derived_reference",
        }
    missing = [
        qid for qid, reference in references.items()
        if reference.get("required_facts") is None
        and reference.get("expected_action") != "abstain"
    ]
    if missing:
        raise ValueError(f"Missing reviewed required facts: {missing}")
    return hashlib.sha256(raw).hexdigest()
