import hashlib
import json

import pytest

from app.evaluation.reference_facts import attach_reviewed_facts
from app.evaluation.attribution import parse_fact_checks


def test_reviewed_facts_preserve_canonical_and_origin(tmp_path):
    original = tmp_path / "ref.json"
    original.write_text('[{"id":"custom","reference_answer":"x"}]')
    raw = original.read_bytes()
    payload = {"references_sha256": hashlib.sha256(raw).hexdigest(),
               "review_status": "independent_ai_reviewed",
               "items": [{"question_id": "custom", "required_facts": ["비용 10원"],
                          "review_status": "accepted"}]}
    sidecar = tmp_path / "facts.json"
    sidecar.write_text(json.dumps(payload))
    refs = {"custom": {"reference_answer": "비용 10원"}}
    attach_reviewed_facts(refs, sidecar, original)
    assert original.read_bytes() == raw
    assert refs["custom"]["required_facts_origin"] == "derived_reference"
    result = parse_fact_checks({"fact_checks": [{
        "fact_id": "F1", "fact": "비용 10원", "origin": "derived_reference",
        "final_support": "supported", "answer_status": "correct",
        "supporting_evidence": [{"source_id": "S1", "quote": "비용 10원"}],
        "answer_quote": "비용 10원", "reason": "일치",
    }]}, {"answer": "비용 10원", "final_context_chunks": [
        {"source_id": "S1", "text": "비용 10원"}]}, refs["custom"])
    assert result["status"] == "scored"
    assert result["items"][0]["attribution"] == "supported"
    with pytest.raises(ValueError, match="replace official"):
        attach_reviewed_facts(refs, sidecar, original)
    original.write_text("changed")
    with pytest.raises(ValueError, match="frozen"):
        attach_reviewed_facts({}, sidecar, original)
