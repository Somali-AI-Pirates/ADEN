"""공통 저장 근거 공급기를 평가 어댑터에 연결해 검증한다."""
import json
from unittest.mock import patch

import pytest

from app.service.adapters import make_query_adapter
from app.service.resources import load_resources
from shared.settings import configured_path, load_settings
from tests.service.test_prepared_context import generation_setup as _generation_setup

generation_setup = _generation_setup


@pytest.fixture
def stored_runs(tmp_path, monkeypatch):
    candidates = [dict(chunk_id=f"DEMO-{i}", book_id=f"DEMO{i}", file_name=f"DEMO{i}.pdf",
                       page=1, text=f"접수 기록 {i}", heading=f"예약 DEMO / 자료 {i}",
                       rank=i, score=1.0/i, retriever="fixed") for i in (1, 2)]
    run = dict(question="예약 DEMO의 접수 상태", retrieval_mode="naive", retrieved_candidates=candidates,
               final_context_chunks=[{k:v for k,v in c.items() if k not in ("rank", "score", "retriever")}
                                     for c in reversed(candidates)])
    path = tmp_path / "runs.jsonl"
    def save():
        path.write_text(json.dumps(run, ensure_ascii=False)+"\n", encoding="utf-8-sig")
    save()
    monkeypatch.setenv("ADEN_EVIDENCE_PROVIDER", "app.service.evidence:stored_evidence")
    monkeypatch.setenv("ADEN_EVIDENCE_RUNS", str(path))
    monkeypatch.setenv("ADEN_EVIDENCE_CONTEXT", "1")
    return run, save, path


def run_saved_evidence(question, mode):
    settings = load_settings(configured_path())
    resources = load_resources(settings)
    try:
        return make_query_adapter(resources, settings)(question, mode, "stored-evidence-test")
    finally:
        resources.close()


@pytest.mark.parametrize("fixed,book", [(False, "DEMO1"), (True, "DEMO2")])
def test_common_provider_evaluation_preserves_selected_order(generation_setup, stored_runs, fixed, book):
    stack, model = generation_setup
    run, _, _ = stored_runs
    stack.enter_context(patch.dict("os.environ", {"ADEN_EVIDENCE_CONTEXT":"1" if fixed else ""}))
    embedder = stack.enter_context(patch("app.service.resources.Embedder"))
    qdrant = stack.enter_context(patch("app.service.resources.QdrantClient"))

    record = run_saved_evidence(run["question"], "naive")

    assert record["status"] == "answered"
    assert record["citations"][0]["book_id"] == book
    assert record["request_id"] == "stored-evidence-test"
    assert "예약 DEMO / 자료" in model.messages[-1].content
    embedder.assert_not_called()
    qdrant.assert_not_called()


@pytest.mark.parametrize("kind", ["changed_text", "missing_candidate", "duplicate_final", "k_limit", "token_limit"])
def test_bad_fixed_evidence_is_rejected_before_generation(generation_setup, stored_runs, kind):
    stack, model = generation_setup
    run, save, path = stored_runs
    if kind == "changed_text":
        run["final_context_chunks"][0]["text"] = "후보에 없는 금액"
    elif kind == "missing_candidate":
        run["final_context_chunks"][0]["chunk_id"] = "UNKNOWN"
    elif kind == "duplicate_final":
        run["final_context_chunks"].append(dict(run["final_context_chunks"][0]))
    save()
    settings = load_settings()
    if kind == "k_limit":
        settings.retrieval.k = 1
    elif kind == "token_limit":
        settings.retrieval.context_token_budget = 1
    config = path.parent / "config.json"
    config.write_text(settings.model_dump_json(), encoding="utf-8")
    stack.enter_context(patch.dict("os.environ", {"ADEN_CONFIG":str(config)}))

    record = run_saved_evidence(run["question"], "naive")

    assert record["status"] == "error"
    assert record["attempts"] == []
    assert model.messages == []


@pytest.mark.parametrize("question,mode,error_type", [
    ("미등록 질문", "naive", "KeyError"),
    ("예약 DEMO의 접수 상태", "hybrid", "NotImplementedError"),
])
def test_unavailable_evidence_does_not_generate(generation_setup, stored_runs, question, mode, error_type):
    stack, model = generation_setup

    record = run_saved_evidence(question, mode)

    assert record["status"] == "error"
    assert record["error_type"] == error_type
    assert model.messages == []
