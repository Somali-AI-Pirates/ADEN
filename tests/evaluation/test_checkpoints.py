import json

import pytest

from app.evaluation.checkpoints import (
    assert_inputs_unchanged,
    fingerprint_files,
    load_grade_checkpoint,
    save_grade_checkpoint,
    validate_run_prefix,
)
from app.evaluation.evidence import final_evidence, labeled_contexts, quote_exists


def test_resume_rejects_modified_input(tmp_path):
    source = tmp_path / "prompt.txt"
    source.write_text("first")
    frozen = fingerprint_files([source])
    assert_inputs_unchanged(frozen)
    source.write_text("second")
    with pytest.raises(ValueError, match="changed"):
        assert_inputs_unchanged(frozen)


def test_grade_checkpoint_binds_response_and_conditions(tmp_path):
    record = {"request_id": "../outside", "question_id": "Q1", "answer": "first"}
    graded = {"request_id": "../outside", "faithfulness": {"value": 1}}
    save_grade_checkpoint(tmp_path, record, "conditions", graded)
    assert load_grade_checkpoint(tmp_path, record, "conditions") == graded
    assert len(list((tmp_path / "checkpoints").glob("*.json"))) == 1
    with pytest.raises(ValueError, match="conditions"):
        load_grade_checkpoint(tmp_path, record, "changed")
    with pytest.raises(ValueError, match="response"):
        load_grade_checkpoint(tmp_path, {**record, "answer": "new"}, "conditions")
    with pytest.raises(FileExistsError):
        save_grade_checkpoint(tmp_path, record, "conditions", graded)
    assert not (tmp_path.parent / "outside").exists()


def test_resume_requires_exact_question_prefix():
    questions = [{"id": "Q1", "question": "one"}, {"id": "Q2", "question": "two"}]
    rows = [{"question_id": "Q1", "question": "one", "request_id": "r1",
             "experiment_id": "e", "config_hash": "h"}]
    validate_run_prefix(rows, questions, experiment_id="e", config_hash="h")
    with pytest.raises(ValueError, match="prefix"):
        validate_run_prefix([{**rows[0], "question": "changed"}], questions,
                            experiment_id="e", config_hash="h")
    with pytest.raises(ValueError, match="duplicate"):
        validate_run_prefix([*rows, {**rows[0], "question_id": "Q2", "question": "two"}],
                            questions, experiment_id="e", config_hash="h")


def test_final_evidence_never_imports_reference_or_excluded_sql():
    record = {"final_context_chunks": [{"source_id": "S1", "text": "실제 청구액은 12원."}],
              "included_sql_results": [{"rows": [["12"]]}],
              "sql_results": [{"rows": [["999"]]}],
              "reference_answer": "정답은 999"}
    evidence = final_evidence(record)
    assert set(evidence) == {"S1", "SQL:1"}
    assert "999" not in json.dumps(evidence, ensure_ascii=False)
    assert labeled_contexts(record)[0].startswith("[S1]\n")
    assert quote_exists("청구액은\n12원", evidence["S1"])
    assert not quote_exists("청구액은 120원", evidence["S1"])
