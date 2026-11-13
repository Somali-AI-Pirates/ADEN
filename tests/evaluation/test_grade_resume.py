"""Public saved-output CLI resume must not spend again or expose private judgments."""
import json

import pytest

from app.evaluation import table_rag_a as cli
from app.evaluation.samples import SampleServiceAdapter
from app.evaluation.runner import run_comparison


def saved_run(tmp_path):
    path = tmp_path / "run"
    path.mkdir()
    questions = [{"id": "SAMPLE-Q1", "question": "first"}, {"id": "SAMPLE-Q2", "question": "second"}]
    rows = run_comparison(
        questions, SampleServiceAdapter(), experiment_id="saved", dataset_id="sample",
        config_hash="frozen", modes=("naive",),
    )
    for row in rows:
        row["retrieval_mode"] = "table_rag"
        row.update(status="answered", answer="saved answer", reason="",
                   error_type=None, final_context_chunks=[], final_context_hash="empty")
    (path / "runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    (path / "manifest.json").write_text(json.dumps({
        "experiment_id": "saved", "dataset_id": "sample", "config_hash": "frozen",
        "sample": True, "table_rag": {"run_status": "complete",
                                     "question_ids": [q["id"] for q in questions]},
    }))
    return path


def test_grade_resume_reuses_only_matching_checkpoint_and_keeps_details_private(tmp_path, monkeypatch):
    path = saved_run(tmp_path)
    calls = []

    def grader(question, *_):
        calls.append(question)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {"value": 1.0, "details": {"private_prompt": "PRIVATE-CLAIM"},
                "input_tokens": 4, "output_tokens": 2, "logical_calls": 2}

    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "make_ragas_faithfulness", lambda *a, **k: grader)
    command = ["grade", "--run-directory", str(path), "--grade-id", "checked",
               "--ragas-model", "gpt-6-luna"]
    with pytest.raises(KeyboardInterrupt):
        cli._grade_command(cli.parse_args(command))
    assert len(list((path / "grades/checked/checkpoints").glob("*.json"))) == 1
    assert json.loads((path / "grades/checked/manifest.json").read_text())["grading_status"] == "interrupted"

    cli._grade_command(cli.parse_args([*command, "--resume"]))
    assert calls == ["first", "second", "second"]
    scores = (path / "grades/checked/scores.jsonl").read_text()
    assert "PRIVATE-CLAIM" not in scores and "evaluation_details" not in scores
    assert "PRIVATE-CLAIM" in (path / "grades/checked/evaluation-details.jsonl").read_text()
    cli._grade_command(cli.parse_args([*command, "--resume"]))
    assert len(calls) == 3


def test_resume_refuses_changed_saved_answers_without_calls(tmp_path, monkeypatch):
    path = saved_run(tmp_path)
    calls = []
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "make_ragas_faithfulness",
                        lambda *a, **k: lambda *_: calls.append(1) or {"value": 1.0})
    command = ["grade", "--run-directory", str(path), "--grade-id", "checked",
               "--ragas-model", "gpt-6-luna"]
    cli._grade_command(cli.parse_args(command))
    before = len(calls)
    rows = cli.read_jsonl(path / "runs.jsonl")
    rows[0]["answer"] = "changed"
    (path / "runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(SystemExit, match="Grading inputs changed"):
        cli._grade_command(cli.parse_args([*command, "--resume"]))
    assert len(calls) == before


def test_generation_resume_refuses_unknown_inflight_without_second_call(tmp_path):
    import asyncio

    class InterruptedService:
        calls = 0

        async def ainvoke(self, question, *, request_id):
            self.calls += 1
            raise asyncio.CancelledError

    service = InterruptedService()
    path = tmp_path / "runs.jsonl"
    kwargs = dict(experiment_id="run", dataset_id="advanced", config_hash="frozen",
                  output_path=path)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli.run_questions([{"id": "q", "question": "question"}], service, **kwargs))
    pending = json.loads((tmp_path / "generation-inflight.json").read_text())
    assert pending["question_id"] == "q"
    with pytest.raises(ValueError, match="uncertain outcome"):
        asyncio.run(cli.run_questions([{"id": "q", "question": "question"}], service,
                                      resume=True, **kwargs))
    assert service.calls == 1


def test_incomplete_tail_is_never_silently_dropped(tmp_path):
    path = tmp_path / "runs.jsonl"
    raw = '{"request_id": "possibly_paid"'
    path.write_text(raw)
    with pytest.raises(ValueError, match="recover the saved request"):
        cli.read_jsonl(path)
    assert path.read_text() == raw


def test_complete_row_without_newline_is_recovered_before_append(tmp_path):
    import asyncio
    path = saved_run(tmp_path) / "runs.jsonl"
    rows = cli.read_jsonl(path)
    path.write_text(json.dumps(rows[0]))
    (path.parent / "generation-inflight.json").write_text(json.dumps({
        "request_id": rows[0]["request_id"], "question_id": rows[0]["question_id"],
    }))

    class Service:
        calls = 0

        async def ainvoke(self, question, *, request_id):
            self.calls += 1
            return rows[1]

    service = Service()
    questions = [{"id": row["question_id"], "question": row["question"]} for row in rows]
    asyncio.run(cli.run_questions(
        questions, service, experiment_id="saved", dataset_id="sample",
        config_hash="frozen", output_path=path, resume=True,
    ))
    assert service.calls == 1
    assert [r["question_id"] for r in cli.read_jsonl(path)] == [q["id"] for q in questions]
    assert not (path.parent / "generation-inflight.json").exists()


def test_checkpoint_never_precedes_durable_effort_pin(tmp_path, monkeypatch):
    path = saved_run(tmp_path)
    monkeypatch.setattr(cli, "load_env", lambda: None)

    def factory(_model, *, session, **kwargs):
        def evaluate(*_):
            session.restore_effective_effort("xhigh")
            return {"value": 1.0}
        return evaluate

    original = cli.save_grade_checkpoint

    def check_order(directory, record, condition_hash, score):
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["benchmark"]["evaluation_effective"]["effective_reasoning_effort"] == "xhigh"
        original(directory, record, condition_hash, score)

    monkeypatch.setattr(cli, "make_ragas_faithfulness", factory)
    monkeypatch.setattr(cli, "save_grade_checkpoint", check_order)
    cli._grade_command(cli.parse_args([
        "grade", "--run-directory", str(path), "--grade-id", "pin",
        "--ragas-model", "gpt-6-luna",
    ]))


def test_generation_sample_pause_preserves_full_selection_and_reuses_answers(tmp_path):
    import asyncio
    source = saved_run(tmp_path)
    rows = cli.read_jsonl(source / "runs.jsonl")
    questions = [{"id": row["question_id"], "question": row["question"]} for row in rows]

    class Service:
        seen = []

        async def ainvoke(self, question, *, request_id):
            self.seen.append(question)
            return next(row for row in rows if row["question"] == question)

    service = Service()
    target = tmp_path / "new/runs.jsonl"
    settings = dict(experiment_id="saved", dataset_id="sample", config_hash="frozen",
                    output_path=target)
    partial = asyncio.run(cli.run_questions(questions, service, stop_after=1, **settings))
    assert len(partial) == 1
    completed = asyncio.run(cli.run_questions(questions, service, resume=True, **settings))
    assert len(completed) == 2 and service.seen == ["first", "second"]
