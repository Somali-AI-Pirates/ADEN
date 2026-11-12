import hashlib
import json
from uuid import uuid4

import pytest

from app.observability.records import compute_context_hash
from app.observability.run_store import (
    RequestRunStoreError,
    save_request_failure,
    save_request_run,
)


def _run_record(request_id: str) -> dict:
    chunk = {
        "chunk_id": "AS001:p0001:page",
        "source_id": "AS001:p0001",
        "book_id": "AS001",
        "file_name": "AS001.pdf",
        "page": 1,
        "text": "원문 예약 기록",
    }
    return {
        "request_id": request_id,
        "question": "예약 상태를 알려줘",
        "dataset_id": "advanced",
        "retrieval_mode": "table_rag",
        "config_hash": "snapshot-hash",
        "status": "answered",
        "answer": "예약 기록을 확인했습니다.",
        "reason": "원문을 확인했습니다.",
        "citations": [{"source_id": chunk["source_id"]}],
        "retrieved_candidates": [chunk.copy()],
        "final_context_chunks": [chunk],
        "final_context_hash": compute_context_hash([chunk]),
        "model_context_text": '{"question":"예약 상태","evidence":[]}',
        "model_context_hash": hashlib.sha256('{"question":"예약 상태","evidence":[]}'.encode()).hexdigest(),
        "attempts": [],
        "usage": {
            "logical_calls": 1,
            "provider_attempts": 1,
            "missing_usage": 0,
            "attempts": [
                {
                    "attempt": 1,
                    "role": "answer",
                    "model": "gpt-6-luna",
                    "model_input": {"question": "예약 상태를 알려줘"},
                    "model_output": {"answer": "예약 기록을 확인했습니다."},
                    "input_tokens": 12,
                    "output_tokens": 4,
                }
            ]
        },
        "latency_ms": 27,
        "trace_id": "trace-example",
        "langfuse_trace_id": "trace-example",
        "sql_results": [],
        "included_sql_results": [],
        "diagnostics": {"finish_status": "sufficient"},
    }


def test_save_request_run_writes_grader_compatible_private_artifacts(tmp_path):
    request_id = uuid4().hex

    directory = save_request_run(
        _run_record(request_id),
        service_manifest={"model": "gpt-6-luna"},
        output_root=tmp_path,
    )

    assert directory == tmp_path / request_id
    assert directory.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    run = json.loads((directory / "runs.jsonl").read_text(encoding="utf-8"))
    assert manifest["table_rag"]["run_status"] == "complete"
    assert manifest["table_rag"]["question_ids"] == [request_id]
    assert run["question_id"] == request_id
    assert run["origin"] == "service"
    assert run["answer"] == "예약 기록을 확인했습니다."
    assert run["final_context_chunks"][0]["text"] == "원문 예약 기록"
    assert run["langfuse_trace_id"] == "trace-example"
    assert run["usage"]["input_tokens"] == 12
    assert run["usage"]["output_tokens"] == 4
    assert run["attempts"][0]["role"] == "answer"
    assert run["usage"]["logical_calls"] == 1
    assert run["usage"]["provider_attempts"] == 1


def test_request_run_does_not_overwrite_same_request_id(tmp_path):
    request_id = uuid4().hex
    first = save_request_run(_run_record(request_id), output_root=tmp_path)
    saved = (first / "runs.jsonl").read_text(encoding="utf-8")
    changed = _run_record(request_id)
    changed["answer"] = "changed"

    with pytest.raises(RequestRunStoreError, match="already exists"):
        save_request_run(changed, output_root=tmp_path)

    assert (first / "runs.jsonl").read_text(encoding="utf-8") == saved


def test_save_request_failure_records_error_without_exception_text(tmp_path):
    request_id = uuid4().hex
    error = RuntimeError("private provider payload")
    directory = save_request_failure(
        "예약 상태를 알려줘",
        request_id,
        error,
        output_root=tmp_path,
    )

    run = json.loads((directory / "runs.jsonl").read_text(encoding="utf-8"))
    assert run["status"] == "error"
    assert run["error_type"] == "RuntimeError"
    assert "private" not in (directory / "runs.jsonl").read_text(encoding="utf-8")
    assert run["question"] == "예약 상태를 알려줘"


def test_request_run_rejects_path_like_ids(tmp_path):
    with pytest.raises(RequestRunStoreError, match="Invalid request ID"):
        save_request_run(_run_record("../outside"), output_root=tmp_path)



def test_planner_failure_without_final_context_is_still_saved(tmp_path):
    request_id = uuid4().hex

    class PlannerFailure(RuntimeError):
        rag_metadata = {
            "usage": {
                "attempts": [
                    {
                        "attempt": 1,
                        "role": "planner",
                        "model": "gpt-6-luna",
                        "model_input": {"question": "예약 상태를 알려줘"},
                        "model_output": None,
                        "input_tokens": 8,
                        "output_tokens": None,
                        "error": "timeout",
                    }
                ]
            }
        }

    directory = save_request_failure(
        "예약 상태를 알려줘",
        request_id,
        PlannerFailure("private details"),
        output_root=tmp_path,
    )

    run = json.loads((directory / "runs.jsonl").read_text(encoding="utf-8"))
    assert run["status"] == "error"
    assert run["attempts"][0]["role"] == "planner"
    assert run["usage"]["input_tokens"] == 8
    assert run["model_context_text"] is None


def test_concurrent_save_publishes_exactly_one_complete_request_folder(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    request_id = uuid4().hex

    def save():
        try:
            return save_request_run(_run_record(request_id), output_root=tmp_path)
        except RequestRunStoreError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))

    assert sum(result is not None for result in results) == 1
    directory = tmp_path / request_id
    assert json.loads((directory / "manifest.json").read_text())[
        "table_rag"
    ]["run_status"] == "complete"
    rows = (directory / "runs.jsonl").read_text().splitlines()
    assert len(rows) == 1
