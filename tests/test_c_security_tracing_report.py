import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.observability.tracing import Tracer
from evaluation.dataset import load_questions, load_references
from evaluation.report import write_report
from evaluation.samples import (SAMPLE_SECURITY, FakeLangfuseClient, make_sample_generate,
                                mock_security_judge, sample_retrieve)
from evaluation.security import analyze_security, check_same_context, run_security_comparison

ROOT = Path(__file__).resolve().parent.parent


def run_sample_security(generate=None, judge=mock_security_judge):
    calls = []

    def retrieve(question, mode):
        calls.append(question)
        return sample_retrieve(question, mode)

    questions = load_questions(SAMPLE_SECURITY)
    runs = run_security_comparison(questions, retrieve, generate or make_sample_generate("방어"),
                                   experiment_id="sample-s", retrieval_mode="hybrid")
    return calls, analyze_security(runs, load_references(SAMPLE_SECURITY), judge)


def test_security_reuses_one_context_per_question():
    calls, rows = run_sample_security()
    assert len(calls) == 2 and len(rows) == 4
    attacks = [i["attack_text"] for i in load_references(SAMPLE_SECURITY).values()]
    assert not any(a in q for a in attacks for q in calls)  # attack_text로 검색하지 않음
    checks = check_same_context(rows)
    assert all(c["context_identical"] and c["generation_settings_identical"]
               for c in checks.values())
    outcomes = {(r["question_id"], r["prompt_variant"]): r["outcome"] for r in rows}
    assert outcomes[("SAMPLE-SEC-1", "base")] == "attack_followed"
    assert outcomes[("SAMPLE-SEC-1", "defense")] == "defended"
    assert outcomes[("SAMPLE-SEC-2", "base")] == "not_exposed"
    assert sum(r["defense_success"] for r in rows) == 1  # 미노출은 방어 성공 아님


def test_generation_mutation_detected_and_failure_not_success():
    base = make_sample_generate("방어")

    def generate(question, chunks, variant):
        if variant == "defense":
            chunks.reverse()  # 순서 변경 → 동일 문맥 확인 실패
            raise RuntimeError("model down")
        return base(question, chunks, variant)

    _, rows = run_sample_security(generate)
    failed = [r for r in rows if r["prompt_variant"] == "defense"]
    assert all(r["outcome"] == "generation_failed" and not r["defense_success"] for r in failed)
    checks = check_same_context(rows)
    assert checks["SAMPLE-SEC-1"]["context_identical"] is False  # 2개 청크 순서가 바뀜


def test_exposure_unknown_without_model_input_and_unjudged():
    base = make_sample_generate("방어")
    _, rows = run_sample_security(lambda q, c, v: {**base(q, c, v), "model_input": None},
                                  judge=None)
    assert {r["outcome"] for r in rows} == {"exposure_unknown"}
    _, rows = run_sample_security(judge=None)
    sec1 = [r for r in rows if r["question_id"] == "SAMPLE-SEC-1"]
    assert {r["outcome"] for r in sec1} == {"unjudged"}


def test_tracer_records_full_flow_by_request_id():
    client = FakeLangfuseClient()
    tracer = Tracer(client)
    t = tracer.start_request("req-1", question="q", retrieval_mode="hybrid",
                             dataset_id="d", config_hash="c")
    t.record_retrieval(retrieved_candidates=[{"chunk_id": "a"}],
                       final_context_chunks=[{"chunk_id": "a", "text": "x"}],
                       final_context_hash="h", k=5)
    t.record_attempt(attempt=1, model="m", model_input=[{"role": "user"}], input_tokens=10,
                     output_tokens=None, error="timeout")
    done = t.finish(status="answered", answer="a", reason=None, citations=[], latency_ms=5)
    assert done["langfuse_trace_id"] == client.create_trace_id(seed="req-1")
    assert done["langfuse_status"] == "recorded_unverified"
    names = [o["name"] for o in client.observations]
    assert names == ["rag-query", "retrieval", "final-context", "generation-attempt-1"]
    gen = client.observations[-1]
    assert gen["usage_details"] == {"input": 10} and gen["level"] == "ERROR"
    assert all(o["ended"] for o in client.observations)
    assert tracer.verify_trace(done["langfuse_trace_id"], timeout_s=0)["status"] == "not_found"
    assert tracer.flush() == {"status": "flushed_unverified"}
    assert tracer.verify_trace(done["langfuse_trace_id"], timeout_s=0)["status"] == "verified"


@pytest.mark.parametrize("fail_on", ["rag-query", "generation", "update"])
def test_tracer_sdk_failures_are_not_success(fail_on):
    t = Tracer(FakeLangfuseClient(fail_on=fail_on)).start_request(
        "r", question="q", retrieval_mode="naive", dataset_id="d", config_hash="c")
    t.record_attempt(attempt=1, model="m", model_input="x")
    done = t.finish(status="answered", answer="a", reason=None, citations=[], latency_ms=1)
    assert done["langfuse_status"] == "sdk_error" and done["langfuse_errors"]


def test_flush_failure_and_disabled(monkeypatch):
    assert Tracer(FakeLangfuseClient(fail_on="flush")).flush()["status"] == "failed"
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    tracer = Tracer.from_env()
    assert not tracer.enabled and tracer.flush() == {"status": "disabled"}
    done = tracer.start_request("r", question="q", retrieval_mode="naive", dataset_id="d",
                                config_hash="c").finish(status="answered", answer="a",
                                                        reason=None, citations=[], latency_ms=1)
    assert done == {"langfuse_trace_id": None, "langfuse_status": "disabled", "langfuse_errors": []}


def test_import_does_not_load_sdks():
    code = ("import sys, app.observability, evaluation.runner, evaluation.security, "
            "evaluation.report; print(any(m in sys.modules for m in ('langfuse','ragas','openai')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=ROOT)
    assert out.stdout.strip() == "False"


def test_sample_scripts_write_labeled_reports(tmp_path):
    for script in ("run_benchmark.py", "run_security.py"):
        subprocess.run([sys.executable, f"scripts/{script}", "--sample", "--experiment-id",
                        "sample-t", "--artifacts-dir", str(tmp_path / "a"),
                        "--reports-dir", str(tmp_path / "r")], check=True, capture_output=True, cwd=ROOT)
    rep = tmp_path / "r" / "sample-t"
    for name in ("README.md", "manifest.json", "comparison.csv", "answers.jsonl", "scores.jsonl",
                 "summary.json", "security_comparison.csv"):
        assert (rep / name).exists(), name
    assert "가상 샘플 결과" in (rep / "README.md").read_text(encoding="utf-8")
    assert "보안 비교" in (rep / "README.md").read_text(encoding="utf-8")
    manifest = json.loads((rep / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sample"] is True and {"benchmark", "security"} <= set(manifest)
    assert (rep / "comparison.csv").read_text(encoding="utf-8-sig").count("\nSAMPLE,") == 15
    assert len((tmp_path / "a" / "sample-t" / "runs.jsonl").read_text(
        encoding="utf-8").splitlines()) == 15
    with pytest.raises(ValueError, match="샘플"):  # 샘플과 실제 결과를 섞지 않음
        write_report(rep, {**manifest, "sample": False})
    rerun = subprocess.run([sys.executable, "scripts/run_benchmark.py", "--sample",
                            "--experiment-id", "sample-t", "--artifacts-dir", str(tmp_path / "a"),
                            "--reports-dir", str(tmp_path / "r")], capture_output=True, cwd=ROOT)
    assert rerun.returncode != 0  # 기존 결과 덮어쓰기 방지
