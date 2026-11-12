import hashlib
import json
from types import SimpleNamespace

import pytest

from app.evaluation.samples import MockJudge
from app.evaluation.scripts import http_records, regrade
from app.evaluation.scripts._common import ROOT, sha256_file
from app.observability.records import compute_context_hash

QUESTION = "보관료는 얼마인가요?"
CHUNK = {"chunk_id": "B1-p1-c1", "book_id": "B1", "file_name": "B1.pdf", "page": 1,
         "text": "보관료는 USD 10이다.", "heading": "B1 요금표"}
CITATION = {"book_id": "B1", "file_name": "B1.pdf", "page": 1, "source_id": "S1"}
CONTEXT_TEXT = "[S1] B1.pdf p.1\nB1 요금표\n보관료는 USD 10이다."


class Obs:
    """Langfuse API 관측처럼 이름과 dict()를 가진다(입출력은 JSON 문자열, 메타데이터는 평탄)."""

    def __init__(self, name, **fields):
        self.name = name
        self._fields = {"name": name, **fields}

    def dict(self):
        return self._fields


def trace(answer="보관료는 USD 10입니다. [S1]", request_id="rid-1", level="DEFAULT"):
    final = [{**CHUNK, "source_id": "S1"}]
    output = {"status": "answered", "answer": answer, "reason": "요금표 확인", "citations": [CITATION]}
    return [
        Obs("rag-query", input=json.dumps({"question": QUESTION, "retrieval_mode": "hybrid"}),
            output=json.dumps(output),
            metadata={"request_id": request_id, "retrieval_mode": "hybrid", "dataset_id": "advanced",
                      "config_hash": "server-run-hash", "prompt_hash": "prompt-hash", "latency_ms": 900}),
        Obs("retrieval", output=json.dumps({"candidates": [{**CHUNK, "rank": 1, "score": 0.9,
                                                            "retriever": "hybrid"}]}),
            metadata={"provided_context": False, "candidate_count": 1}),
        Obs("final-context", metadata={"k": 8, "delivered_chunks": 1}, output=json.dumps({
            "final_context_chunks": final, "final_context_hash": compute_context_hash(final),
            "model_context_text": CONTEXT_TEXT,
            "model_context_hash": hashlib.sha256(CONTEXT_TEXT.encode()).hexdigest(),
            "model_context_token_count": 12, "budget_excluded_chunk_ids": []})),
        Obs("generation-attempt-1", model="gen-model", level=SimpleNamespace(value=level), statusMessage="",
            input=json.dumps([{"type": "human", "content": QUESTION}]), output=json.dumps({"content": "{}"}),
            usageDetails={"input": 100, "output": 20, "total": 120}, modelParameters={"max_output_tokens": 64},
            metadata={"attempt": 1, "latency_ms": 800, "prompt_variant": "base", "calendar_reference": "",
                      "usage_known.input": True, "usage_known.output": False}),
    ]


def http_row(**overrides):
    response = {"request_id": "rid-1", "status": "answered", "answer": "보관료는 USD 10입니다. [S1]",
                "reason": "요금표 확인", "citations": [CITATION]}
    row = {"experiment_id": "http-x", "question_id": "Q1", "retrieval_mode": "hybrid", "condition": "",
           "http_status": 200, "outcome": "success", "error": None, "latency_ms": 950,
           "latency_scope": "http_client", "request_id": "rid-1", "x_request_id": "rid-1",
           "response": response, "langfuse_trace_id": "trace-1",
           "langfuse_verification": {"status": "verified"}}
    return {**row, **overrides}


def test_record_keeps_http_answer_and_server_context():
    record = http_records.run_record(http_row(), QUESTION, trace(), experiment_id="http-x-records")
    assert record["request_id"] == "rid-1" and record["langfuse_trace_id"] == "trace-1"
    assert record["answer"] == "보관료는 USD 10입니다. [S1]" and record["citations"] == [CITATION]
    assert record["final_context_chunks"][0]["source_id"] == "S1" and record["k"] == 8
    assert record["attempts"][0]["input_tokens"] == 100 and record["attempts"][0]["output_tokens"] is None
    assert record["usage"]["status"] == "partial"  # 서버가 모른다고 기록한 출력 토큰을 추정하지 않음
    assert (record["latency_ms"], record["http_latency_ms"]) == (900, 950)
    assert record["experiment_id"] == "http-x-records" and record["http_experiment_id"] == "http-x"


@pytest.mark.parametrize("row, observations, message", [
    (http_row(), trace(answer="다른 답변"), "answer"),
    (http_row(), trace(request_id="rid-2"), "request_id"),
    (http_row(retrieval_mode="naive"), trace(), "검색 방식"),
    (http_row(), trace()[1:], "rag-query"),
])
def test_record_rejects_trace_that_does_not_match_http_response(row, observations, message):
    with pytest.raises(ValueError, match=message):
        http_records.run_record(row, QUESTION, observations, experiment_id="http-x-records")


def test_failed_attempt_keeps_error():
    record = http_records.run_record(http_row(), QUESTION, trace(level="ERROR"), experiment_id="e")
    assert record["attempts"][0]["error"] == "error"


def test_links_http_runs_and_regrade_scores_same_request(tmp_path, monkeypatch):
    artifacts = tmp_path / "a"
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "Q1", "question": QUESTION}], ensure_ascii=False), encoding="utf-8")
    references = tmp_path / "refs.json"
    references.write_text(json.dumps([{"id": "Q1", "question": QUESTION, "expected_action": "answer",
                                       "evidence_groups": [[{"book_id": "B1", "page": 1}]]}],
                                     ensure_ascii=False), encoding="utf-8")
    src = artifacts / "http-x"
    src.mkdir(parents=True)
    timeout = http_row(outcome="timeout", http_status=None, request_id=None, response=None,
                       langfuse_trace_id=None, langfuse_verification=None)
    (src / "http_runs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (http_row(), timeout)) + "\n", encoding="utf-8")
    (src / "manifest.json").write_text(json.dumps({
        "experiment_id": "http-x", "kind": "http_client", "question_ids": ["Q1"], "modes": ["hybrid"],
        "questions_file": "questions.json", "questions_sha256": sha256_file(questions),
        "git_commit": "abc", "git_dirty": False}), encoding="utf-8")
    fake = SimpleNamespace(enabled=True, observations=lambda trace_id: trace(), shutdown=lambda: None)
    monkeypatch.setattr(http_records.Tracer, "from_env", classmethod(lambda cls: fake))
    monkeypatch.setattr(http_records, "load_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["http_records", "--source", "http-x", "--questions", str(questions),
                                     "--config", str(ROOT / "configs" / "advanced.yaml"),
                                     "--artifacts-dir", str(artifacts), "--wait", "0"])
    http_records.main()

    out = artifacts / "http-x-records"
    runs = [json.loads(line) for line in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [run["request_id"] for run in runs] == ["rid-1"]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["http_records"]["linked"] == 1 and manifest["benchmark"]["k"] == 8
    assert manifest["http_records"]["unlinked"][0]["outcome"] == "timeout"
    assert manifest["config_hash"] == sha256_file(ROOT / "configs" / "advanced.yaml")

    judge = MockJudge()
    monkeypatch.setattr(regrade, "make_openai_judge", lambda model: judge)
    monkeypatch.setattr(regrade, "load_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["regrade", "--source", "http-x-records", "--references", str(references),
                                     "--judge-model", "mock", "--experiment-id", "http-x-records-e1",
                                     "--artifacts-dir", str(artifacts), "--reports-dir", str(tmp_path / "r")])
    regrade.main()
    graded = artifacts / "http-x-records-e1"
    scores = [json.loads(line) for line in (graded / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [score["request_id"] for score in scores] == ["rid-1"] and judge.calls == 1
    assert not (graded / "runs.jsonl").exists()  # HTTP 답변을 그대로 채점하고 새 답변을 만들지 않음
    assert scores[0]["judge"]["decision"]["status"] == "scored"
