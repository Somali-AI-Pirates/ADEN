import asyncio
import json
import os
from hashlib import sha256

import pytest

from app.evaluation.report import summarize
from app.evaluation.table_rag_a import (
    _evaluation_record,
    grade_saved_runs,
    parse_args,
    run_questions,
)
from app.observability.records import compute_context_hash

QUESTION = {"id": "q-1", "question": "예약 내용을 알려줘"}
PAGE = {
    "chunk_id": "AS001:p0001:page",
    "book_id": "AS001",
    "file_name": "AS001.pdf",
    "page": 1,
    "source_id": "AS001:p0001",
    "text": "예약 정보를 담은 원문",
}
CANDIDATE = {key: value for key, value in PAGE.items() if key != "source_id"}


class FakeService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.questions = []
        self.closed = False
        self.manifest = {"model": "fake"}

    async def ainvoke(self, question, *, request_id):
        self.questions.append((question, request_id))
        if self.error:
            raise self.error
        return self.result

    async def aclose(self):
        self.closed = True


def rag_result():
    return {
        "status": "answered",
        "answer": "예약 번호는 EV-SH-00660-D입니다.",
        "reason": "표 원문을 확인했습니다.",
        "citations": [{"source_id": "AS001:p0001"}],
        "retrieved_candidates": [CANDIDATE],
        "candidate_source_ids": ["AS001:p0001"],
        "final_context_chunks": [PAGE],
        "final_context_hash": compute_context_hash([PAGE]),
        "model_context_text": '{"pages":[],"sql":[]}',
        "model_context_hash": None,
        "trace_id": "trace-1",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 4,
            "status": "complete",
        },
        "latency_ms": 17,
        "sql_results": [],
        "included_sql_results": [
            {
                "status": "ok",
                "sql": "SELECT decimal_sum(amount) AS fee FROM rates",
                "columns": ["fee"],
                "rows": [{"fee": "12.00"}],
                "sources": [{"book_id": "AS001", "page": 1}],
            }
        ],
        "diagnostics": {"planner_calls": 1},
    }


def test_run_questions_only_passes_question_and_persists_record(tmp_path):
    service = FakeService(result=rag_result())
    output = tmp_path / "runs.jsonl"

    records = asyncio.run(
        run_questions(
            [QUESTION],
            service,
            experiment_id="table-a-test",
            dataset_id="advanced",
            config_hash="snapshot",
            output_path=output,
        )
    )

    assert service.questions[0][0] == QUESTION["question"]
    assert service.questions[0][1] == records[0]["request_id"]
    assert records[0]["retrieval_mode"] == "table_rag"
    assert records[0]["question_id"] == QUESTION["id"]
    assert records[0]["final_context_hash"] == compute_context_hash([PAGE])
    stored = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert stored["request_id"] == records[0]["request_id"]
    assert stored["answer"] == rag_result()["answer"]


def test_run_questions_writes_individual_failures_without_stopping(tmp_path):
    service = FakeService(error=TimeoutError())
    output = tmp_path / "runs.jsonl"

    records = asyncio.run(
        run_questions(
            [QUESTION, {"id": "q-2", "question": "두 번째 질문"}],
            service,
            experiment_id="table-a-test",
            dataset_id="advanced",
            config_hash="snapshot",
            output_path=output,
        )
    )

    assert len(records) == 2
    assert all(record["status"] == "error" for record in records)
    assert all(record["error_type"] == "TimeoutError" for record in records)
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_candidate_ids_are_normalized_for_page_metrics():
    from app.evaluation.table_rag_a import _source_fields

    assert _source_fields("AS001:p0001") == {
        "source_id": "AS001:p0001",
        "book_id": "AS001",
        "page": 1,
    }


def test_saved_grades_and_summary_keep_table_rag_mode():
    reference = {
        "id": "q-1",
        "reference_answer": "예약 번호",
        "evidence_groups": [[{"book_id": "AS001", "page": 1}]],
    }
    record = _evaluation_record(
        rag_result(),
        experiment_id="table-a-test",
        question=QUESTION,
        request_id="request-1",
        dataset_id="advanced",
        config_hash="snapshot",
        latency_ms=5,
    )

    def judge(prompt):
        assert "예약 번호" in prompt
        assert "12.00" in prompt
        assert "SELECT decimal_sum(amount) AS fee FROM rates" in prompt
        return {
            "text": json.dumps(
                {
                    "has_factual_claims": True,
                    "criteria": {
                        name: {"score": 1, "status": "scored", "reason": "verified"}
                        for name in ("fact", "condition", "evidence", "decision")
                    },
                }
            )
        }

    faithfulness_calls = []

    def faithfulness(question, response, contexts):
        faithfulness_calls.append((question, response, contexts))
        return {"value": 1.0}

    graded = grade_saved_runs(
        [record],
        {"q-1": reference},
        k=8,
        judge_fn=judge,
        faithfulness_fn=faithfulness,
    )
    summary = summarize([record], graded)

    assert record["retrieval_mode"] == "table_rag"
    assert graded[0]["retrieval"]["hit_at_k"]["value"] == 1.0
    assert graded[0]["evidence_diagnostics"]["final_page_recall"]["value"] == 1.0
    assert graded[0]["faithfulness"]["value"] == 1.0
    assert faithfulness_calls[0][0] == QUESTION["question"]
    assert "12.00" in faithfulness_calls[0][2][-1]
    assert "SELECT decimal_sum(amount) AS fee FROM rates" in faithfulness_calls[0][2][-1]
    assert "table_rag" in summary


def test_cli_subcommands_require_run_or_grade_inputs():
    run_args = parse_args(["run", "--questions", "questions.json", "--ids", "q-1"])
    grade_args = parse_args(["grade", "--run-directory", "run"])

    assert run_args.command == "run"
    assert grade_args.command == "grade"
    assert grade_args.k == 8


def test_grade_and_publish_cli_accept_runtime_config():
    grade_args = parse_args(
        ["grade", "--run-directory", "run", "--config", "configs/staging.yaml"]
    )
    publish_args = parse_args(
        [
            "publish",
            "--grade-directory",
            "run/grades/one",
            "--config",
            "configs/staging.yaml",
        ]
    )

    assert grade_args.config == "configs/staging.yaml"
    assert publish_args.config == "configs/staging.yaml"


def test_model_failure_preserves_usage_and_trace_metadata(tmp_path):
    class ModelFailure(TimeoutError):
        rag_metadata = {
            "trace_id": "trace-error",
            "usage": {
                "input_tokens": 21,
                "output_tokens": 3,
                "status": "complete",
            },
            "diagnostics": {"planner_calls": 1},
        }

    service = FakeService(error=ModelFailure())
    records = asyncio.run(
        run_questions(
            [QUESTION],
            service,
            experiment_id="table-a-test",
            dataset_id="advanced",
            config_hash="snapshot",
            output_path=tmp_path / "runs.jsonl",
        )
    )

    assert records[0]["langfuse_trace_id"] == "trace-error"
    assert records[0]["usage"]["input_tokens"] == 21
    assert records[0]["usage"]["output_tokens"] == 3
    assert records[0]["diagnostics"]["planner_calls"] == 1


def test_run_command_persists_questions_without_loading_references(
    tmp_path, monkeypatch
):
    from app.evaluation.table_rag_a import _run_command
    from app.rag.config import load_table_rag_settings as resolve_settings

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps([QUESTION], ensure_ascii=False),
        encoding="utf-8",
    )
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    (data_directory / "corpus.json").write_text("{}", encoding="utf-8")
    service = FakeService(result=rag_result())

    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        "connections:\n  langfuse_base_url: https://trace.example.test\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=fake-openai-key\n"
        "LANGFUSE_PUBLIC_KEY=fake-public-key\n"
        "LANGFUSE_SECRET_KEY=fake-secret-key\n",
        encoding="utf-8",
    )

    def resolve_test_settings(**kwargs):
        return resolve_settings(
            **kwargs,
            env={},
            env_file=env_file,
            project_root=tmp_path,
        )

    monkeypatch.setattr(
        "app.rag.config.load_table_rag_settings", resolve_test_settings
    )
    async def load_fake_service(*, settings):
        assert settings.data_directory == data_directory.resolve()
        return service

    monkeypatch.setattr("app.rag.bootstrap.load_service", load_fake_service)

    args = parse_args(
        [
            "run",
            "--questions",
            str(questions_path),
            "--experiment-id",
            "table-a-cli-test",
            "--config",
            str(config_path),
            "--data-directory",
            str(data_directory),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
        ]
    )
    _run_command(args)

    experiment_directory = tmp_path / "artifacts" / "table-a-cli-test"
    manifest = json.loads((experiment_directory / "manifest.json").read_text())
    runs = json.loads((experiment_directory / "runs.jsonl").read_text().splitlines()[0])
    assert manifest["table_rag"]["run_status"] == "complete"
    assert manifest["table_rag"]["question_ids"] == ["q-1"]
    assert manifest["table_rag"]["langfuse_base_url"] == "https://trace.example.test"
    serialized_manifest = json.dumps(manifest)
    assert "fake-openai-key" not in serialized_manifest
    assert "fake-public-key" not in serialized_manifest
    assert "fake-secret-key" not in serialized_manifest
    assert runs["question"] == QUESTION["question"]
    assert runs["retrieval_mode"] == "table_rag"
    assert service.closed


def test_publication_uses_resolved_settings_and_rejects_endpoint_mismatch(
    monkeypatch,
):
    import app.evaluation.table_rag_a as evaluation
    from app.observability.tracing import Tracer
    from app.rag.config import RuntimeCredentials, TableRagSettings

    settings = TableRagSettings(
        credentials=RuntimeCredentials(
            langfuse_public_key="fake-public-key",
            langfuse_secret_key="fake-secret-key",
            langfuse_base_url="https://trace.example.invalid",
        )
    )
    current = {"settings": settings}
    config_paths = []
    tracer_args = []

    def resolve_settings(*, config_path=None, **_kwargs):
        config_paths.append(config_path)
        return current["settings"]

    def create_tracer(cls, *, public_key, secret_key, base_url=None):
        tracer_args.append((public_key, secret_key, base_url))
        return "fake-tracer"

    monkeypatch.setattr("app.rag.config.load_table_rag_settings", resolve_settings)
    monkeypatch.setattr(
        evaluation,
        "load_env",
        lambda: pytest.fail("publishing settings must resolve before dotenv injection"),
    )
    monkeypatch.setattr(Tracer, "from_credentials", classmethod(create_tracer))

    run_manifest = {
        "table_rag": {"langfuse_base_url": "https://trace.example.invalid"}
    }
    resolved = evaluation._resolve_publication_settings(
        run_manifest, config_path=None
    )
    assert evaluation._publication_tracer(resolved) == "fake-tracer"
    assert tracer_args[-1] == (
        "fake-public-key",
        "fake-secret-key",
        "https://trace.example.invalid",
    )

    current["settings"] = TableRagSettings(
        credentials=RuntimeCredentials(
            langfuse_public_key="fake-public-key",
            langfuse_secret_key="fake-secret-key",
            langfuse_base_url="https://default.example.invalid",
        )
    )
    with pytest.raises(SystemExit, match="differs from the saved run"):
        evaluation._resolve_publication_settings(
            run_manifest, config_path=None
        )
    assert len(tracer_args) == 1

    current["settings"] = settings
    resolved = evaluation._resolve_publication_settings(
        run_manifest, config_path="configs/staging.yaml"
    )
    assert evaluation._publication_tracer(resolved) == "fake-tracer"

    # Saved Swagger/older CLI runs predate endpoint metadata.
    legacy_manifest = {"table_rag_a": {"run_status": "complete"}}
    resolved = evaluation._resolve_publication_settings(
        legacy_manifest, config_path="configs/staging.yaml"
    )
    assert evaluation._publication_tracer(resolved) == "fake-tracer"
    assert tracer_args[-1][2] == "https://trace.example.invalid"
    assert config_paths == [None, None, "configs/staging.yaml", "configs/staging.yaml"]


def test_grade_publish_resolves_process_alias_before_dotenv_is_loaded(
    tmp_path, monkeypatch
):
    import app.evaluation.table_rag_a as evaluation
    import app.rag.config as rag_config

    process_url = "https://process.example.invalid"
    dotenv_url = "https://dotenv.example.invalid"
    config_path = tmp_path / "selected.yaml"
    config_path.write_text(
        "connections: {langfuse_base_url: 'https://yaml.example.invalid'}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"LANGFUSE_BASE_URL={dotenv_url}\n", encoding="utf-8"
    )
    monkeypatch.setenv("LANGFUSE_HOST", process_url)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)

    original_loader = rag_config.load_table_rag_settings

    def load_with_test_sources(**kwargs):
        return original_loader(
            **kwargs,
            env=dict(os.environ),
            env_file=env_file,
            project_root=tmp_path,
        )

    monkeypatch.setattr(rag_config, "load_table_rag_settings", load_with_test_sources)

    run_directory = tmp_path / "saved-run"
    run_directory.mkdir()
    (run_directory / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": "saved-run",
                "dataset_id": "advanced",
                "config_hash": "snapshot",
                "sample": False,
                "table_rag": {
                    "run_status": "complete",
                    "question_ids": [],
                    "langfuse_base_url": process_url,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_directory / "runs.jsonl").write_text("", encoding="utf-8")

    published_targets = []

    def inject_dotenv_for_grader():
        os.environ["LANGFUSE_BASE_URL"] = dotenv_url

    def capture_publish(*_args, settings, **_kwargs):
        published_targets.append(settings.credentials.langfuse_base_url)

    monkeypatch.setattr(evaluation, "load_env", inject_dotenv_for_grader)
    monkeypatch.setattr(
        evaluation, "make_ragas_faithfulness", lambda _model, **_kwargs: lambda *_args: {}
    )
    monkeypatch.setattr(evaluation, "grade_saved_runs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(evaluation, "write_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evaluation, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evaluation, "_publish_grade", capture_publish)

    args = evaluation.parse_args(
        [
            "grade",
            "--run-directory",
            str(run_directory),
            "--grade-id",
            "grade-1",
            "--config",
            str(config_path),
            "--ragas-model",
            "gpt-6-luna",
            "--publish-langfuse-scores",
        ]
    )
    evaluation._grade_command(args)

    assert published_targets == [process_url]
    assert os.environ["LANGFUSE_BASE_URL"] == dotenv_url



@pytest.mark.parametrize("tamper", [False, True])
def test_grade_command_resolves_gold_pages_from_saved_bundle(tmp_path, monkeypatch, tamper):
    import app.evaluation.table_rag_a as evaluation

    gold = "gold-only page contents"
    gold_page = {"book_id": "AS001", "page": 2, "text": gold}
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    page_bytes = json.dumps(
        [{"file_name": "AS001.pdf", "page": 2, "text": gold}]
    ).encode()
    pages_hash = sha256(page_bytes).hexdigest()
    (bundle / "pages.json").write_bytes(page_bytes if not tamper else b"[]")
    corpus_bytes = json.dumps({"files": {"pages.json": pages_hash}}).encode()
    (bundle / "corpus.json").write_bytes(corpus_bytes)

    def judge(prompt):
        actual, reference = prompt.split("[참고 정답·채점 기준]\n", 1)
        assert gold not in actual
        assert PAGE["text"] in actual
        assert "{{reference}}" in actual
        assert json.loads(reference)["reference_pages"] == [gold_page]
        assert "verified excerpt" in reference
        return {"text": json.dumps({
            "has_factual_claims": True,
            "criteria": {
                key: {"score": 1, "status": "scored", "reason": "verified"}
                for key in ("fact", "condition", "evidence", "decision")
            },
        })}

    def faithfulness(_question, _response, contexts):
        assert gold not in " ".join(contexts)
        assert PAGE["text"] in " ".join(contexts)
        return {"value": 1.0}

    monkeypatch.setattr(evaluation, "load_env", lambda: None)
    monkeypatch.setattr(evaluation, "make_openai_judge", lambda _model, **_kwargs: judge)
    monkeypatch.setattr(evaluation, "make_ragas_faithfulness", lambda _model, **_kwargs: faithfulness)

    record = _evaluation_record(
        rag_result(),
        experiment_id="table-a-grade-test",
        question=QUESTION,
        request_id="request-grade",
        dataset_id="advanced",
        config_hash="snapshot",
        latency_ms=5,
    )
    record["answer"] += " {{reference}}"
    experiment_directory = tmp_path / "artifacts" / "table-a-grade-test"
    experiment_directory.mkdir(parents=True)
    (experiment_directory / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": "table-a-grade-test",
                "dataset_id": "advanced",
                "config_hash": "snapshot",
                "sample": False,
                "table_rag": {
                    "run_status": "complete",
                    "question_ids": ["q-1"],
                    "data_directory": str(bundle),
                    "corpus_manifest_sha256": "sha256:" + sha256(corpus_bytes).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    (experiment_directory / "runs.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    references_path = tmp_path / "references.json"
    references_path.write_text(
        json.dumps(
            [
                {
                    "id": "q-1",
                    "reference_answer": "예약 번호",
                    "evidence_groups": [[
                        {"book_id": "AS001", "page": 2},
                        {"book_id": "AS001", "page": 3, "quote": "verified excerpt"},
                    ]],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = parse_args(
        [
            "grade",
            "--run-directory",
            str(experiment_directory),
            "--references",
            str(references_path),
            "--grade-id",
            "first",
            "--judge-model",
            "gpt-6-luna",
            "--ragas-model",
            "gpt-6-luna",
        ]
    )

    original_run = (experiment_directory / "runs.jsonl").read_bytes()
    original_references = references_path.read_bytes()
    if tamper:
        with pytest.raises(SystemExit, match="Reference pages do not match"):
            evaluation._grade_command(args)
        assert not (experiment_directory / "grades").exists()
        return

    evaluation._grade_command(args)
    assert (experiment_directory / "runs.jsonl").read_bytes() == original_run
    assert references_path.read_bytes() == original_references

    grade_directory = experiment_directory / "grades" / "first"
    summary = json.loads((grade_directory / "summary.json").read_text())
    scores = json.loads((grade_directory / "scores.jsonl").read_text())
    grade_manifest = json.loads((grade_directory / "manifest.json").read_text())
    assert "table_rag" in summary["modes"]
    assert scores["question_id"] == "q-1"
    assert scores["retrieval"]["recall_at_k"]["value"] == 1.0
    assert scores["judge"]["fact"]["value"] == 1.0
    assert scores["faithfulness"]["value"] == 1.0
    assert grade_manifest["benchmark"]["judge_schema"] == "judge-output-v3-fact-trace"
    assert grade_manifest["benchmark"]["reference_pages_sha256"] == f"sha256:{pages_hash}"


def test_table_rag_metrics_separate_exact_pages_alternatives_and_sql():
    from app.evaluation.table_rag_metrics import table_rag_metrics

    page_a = {"book_id": "AS001", "page": 5}
    alternative = {"book_id": "AS001", "page": 7}
    page_b = {"book_id": "AS002", "page": 5}
    wrong_book = {"book_id": "AS003", "page": 5}
    expectation = {"columns": ["amount"], "rows": [["12.00"]]}
    results = table_rag_metrics(
        {
            "evidence_groups": [[page_a, alternative], [page_b]],
            "expected_sql_results": [expectation],
        },
        {
            "retrieved_candidates": [wrong_book, alternative, page_b],
            "final_context_chunks": [alternative],
            "included_sql_results": [{**expectation, "status": "ok"}],
        },
        k=8,
    )
    assert results["candidate_page_recall"]["value"] == 2 / 3
    assert results["candidate_required_evidence_all"]["value"] == 1
    assert results["candidate_mrr"]["value"] == 0.5
    assert results["final_required_evidence_all"]["value"] == 0
    assert results["final_sql_expected"]["value"] == 1


def test_republishing_table_rag_scores_keeps_id_timestamp_and_payload():
    from app.evaluation.metrics import score
    from app.evaluation.publishing import plan_publication, publish_evaluation_scores

    class Tracer:
        enabled = True

        def __init__(self):
            self.writes = []

        def record_score(self, *args, **kwargs):
            self.writes.append((args, kwargs))
            return {"status": "recorded_unverified"}

        def flush(self):
            return {"status": "flushed_unverified"}

    result = score(1.0, "scored", "local review note")
    manifest = {
        "experiment_id": "trial",
        "grade_id": "grade-one",
        "dataset_id": "advanced",
        "config_hash": "config",
        "langfuse_score_timestamp": "2026-11-12T12:00:00+00:00",
    }
    manifest["langfuse_scores"] = plan_publication(
        manifest, evaluation_set="trial-grade-one", groups=["retrieval"]
    )
    runs = [{"request_id": "r1", "langfuse_trace_id": "trace-1"}]
    scores = [
        {
            "request_id": "r1",
            "retrieval": {"k": 8, "hit_at_k": result, "recall_at_k": result},
            "faithfulness": result,
            "judge": {},
        }
    ]
    tracer = Tracer()
    for _ in range(2):
        status = publish_evaluation_scores(tracer, runs, scores, manifest)
        assert status["status"] == "recorded_unverified"
    assert tracer.writes[:2] == tracer.writes[2:]
    assert (
        tracer.writes[0][1]["timestamp"].isoformat()
        == manifest["langfuse_score_timestamp"]
    )
    assert "local review note" not in tracer.writes[0][1]["comment"]


def test_grading_saved_request_without_reference_runs_only_faithfulness():
    record = _evaluation_record(
        rag_result(),
        experiment_id="table-rag-test",
        question=QUESTION,
        request_id="request-no-reference",
        dataset_id="advanced",
        config_hash="snapshot",
        latency_ms=5,
    )
    calls = []

    def faithfulness(question, response, contexts):
        calls.append((question, response, contexts))
        return {"value": 0.75, "input_tokens": 10, "output_tokens": 2}

    graded = grade_saved_runs(
        [record], None, k=8, faithfulness_fn=faithfulness
    )[0]

    assert len(calls) == 1
    assert calls[0][0] == QUESTION["question"]
    assert "12.00" in calls[0][2][-1]
    assert graded["faithfulness"]["value"] == 0.75
    assert graded["retrieval"]["hit_at_k"]["status"] == "not_applicable"
    assert graded["table_rag_metrics"]["final_page_recall"]["status"] == "not_applicable"
    assert all(item["status"] == "unavailable" for item in graded["judge"].values())


def test_saved_request_without_references_can_be_graded_without_rerunning(
    tmp_path, monkeypatch
):
    import app.evaluation.table_rag_a as evaluation

    record = _evaluation_record(
        rag_result(),
        experiment_id="table-rag",
        question=QUESTION,
        request_id="request-saved-only",
        dataset_id="advanced",
        config_hash="snapshot",
        latency_ms=5,
    )
    run_directory = tmp_path / "rag-runs" / "request-saved-only"
    run_directory.mkdir(parents=True)
    (run_directory / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": "table-rag",
                "dataset_id": "advanced",
                "config_hash": "snapshot",
                "sample": False,
                "table_rag": {
                    "origin": "api",
                    "request_id": "request-saved-only",
                    "question_ids": ["request-saved-only"],
                    "run_status": "complete",
                },
            }
        ),
        encoding="utf-8",
    )
    (run_directory / "runs.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    record["question_id"] = "request-saved-only"
    (run_directory / "runs.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    original_answer = record["answer"]
    monkeypatch.setattr(evaluation, "load_env", lambda: None)
    monkeypatch.setattr(
        evaluation,
        "make_ragas_faithfulness",
        lambda model, **_kwargs: lambda question, response, contexts: {"value": 1.0},
    )

    args = parse_args(
        [
            "grade",
            "--run-directory",
            str(run_directory),
            "--grade-id",
            "faith-only",
            "--ragas-model",
            "gpt-6-luna",
        ]
    )
    evaluation._grade_command(args)

    grade_directory = run_directory / "grades" / "faith-only"
    scores = json.loads((grade_directory / "scores.jsonl").read_text())
    manifest = json.loads((grade_directory / "manifest.json").read_text())
    stored_run = json.loads((run_directory / "runs.jsonl").read_text())
    assert scores["faithfulness"]["value"] == 1.0
    assert scores["judge"]["fact"]["status"] == "unavailable"
    assert scores["retrieval"]["recall_at_k"]["status"] == "not_applicable"
    assert manifest["benchmark"]["reference_status"] == "not_provided"
    assert stored_run["answer"] == original_answer


def test_reference_id_maps_one_saved_request_to_a_dataset_question(tmp_path):
    from app.evaluation.table_rag_a import parse_args

    args = parse_args(
        [
            "grade",
            "--run-directory",
            str(tmp_path / "request"),
            "--references",
            str(tmp_path / "references.json"),
            "--reference-id",
            "ADV-007",
        ]
    )

    assert args.reference_id == "ADV-007"


def test_trace_verification_names_role_specific_attempts_and_payloads():
    from app.evaluation.publishing import (
        expected_observations,
        expected_payload_observations,
    )

    run = {
        "retrieved_candidates": [],
        "final_context_chunks": [],
        "attempts": [
            {
                "attempt": 1,
                "role": "planner",
                "model_output": {"tool": "search_evidence"},
            },
            {
                "attempt": 2,
                "role": "answer",
                "model_output": {"answer": "확인"},
            },
            {
                "attempt": 3,
                "role": "planner",
                "error": "timeout",
                "model_output": None,
            },
        ],
    }

    assert expected_observations(run) == [
        "rag-query",
        "retrieval",
        "final-context",
        "planner-1",
        "answer-2",
        "planner-3",
    ]
    assert expected_payload_observations(run) == [
        "rag-query",
        "retrieval",
        "final-context",
        "planner-1",
        "answer-2",
    ]


def _write_local_grade_manifest(
    root, *, grade_id, request_id, evaluation_set, conditions_manifest
):
    from hashlib import sha256

    from app.evaluation.publishing import conditions

    grade_directory = root / request_id / "grades" / grade_id
    grade_directory.mkdir(parents=True)
    manifest = {
        "experiment_id": "table-rag",
        **conditions_manifest,
        "benchmark": conditions_manifest.get("benchmark", {}),
        "grade_id": grade_id,
        "langfuse_scores": {
            "evaluation_set": evaluation_set,
            "evaluation_hash": sha256(
                json.dumps(conditions(conditions_manifest), sort_keys=True).encode()
            ).hexdigest(),
        },
    }
    (grade_directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (grade_directory / "scores.jsonl").write_text(
        json.dumps({"request_id": request_id}) + "\n", encoding="utf-8"
    )
    return grade_directory / "manifest.json"


def _publication_manifest(request_id, *, config_hash="snapshot"):
    return {
        "experiment_id": "table-rag",
        "dataset_id": "advanced",
        "config_hash": config_hash,
        "sample": False,
        "benchmark": {
            "k": 8,
            "judge": "gpt-6-luna",
            "faithfulness": "gpt-6-luna",
            "judge_prompt": "judge-prompt-hash",
            "references_sha256": "references-hash",
            "question_ids": [request_id],
        },
    }


def test_table_rag_publication_preflight_allows_other_requests_in_same_set(tmp_path):
    from app.evaluation.publishing import plan_publication

    root = tmp_path / "rag-runs"
    existing = _publication_manifest("request-one")
    _write_local_grade_manifest(
        root,
        grade_id="grade-one",
        request_id="request-one",
        evaluation_set="shared-set",
        conditions_manifest=existing,
    )
    current = _publication_manifest("request-two")

    plan = plan_publication(
        current,
        evaluation_set="shared-set",
        groups=["faithfulness"],
        artifacts_dir=root,
        request_ids=["request-two"],
        skip_same_experiment=False,
    )

    assert plan["evaluation_set"] == "shared-set"
    assert plan["groups"] == ["faithfulness"]


def test_table_rag_publication_preflight_rejects_duplicate_request_and_condition(tmp_path):
    import pytest

    from app.evaluation.publishing import plan_publication

    root = tmp_path / "rag-runs"
    existing = _publication_manifest("request-one")
    _write_local_grade_manifest(
        root,
        grade_id="grade-one",
        request_id="request-one",
        evaluation_set="shared-set",
        conditions_manifest=existing,
    )

    with pytest.raises(ValueError, match="같은 요청"):
        plan_publication(
            _publication_manifest("request-one"),
            evaluation_set="shared-set",
            artifacts_dir=root,
            request_ids=["request-one"],
            skip_same_experiment=False,
        )
    with pytest.raises(ValueError, match="다른 채점 조건"):
        plan_publication(
            _publication_manifest("request-two", config_hash="other-snapshot"),
            evaluation_set="shared-set",
            artifacts_dir=root,
            request_ids=["request-two"],
            skip_same_experiment=False,
        )
