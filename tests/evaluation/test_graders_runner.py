from dataclasses import asdict
from hashlib import sha256
import json

from shared.contracts import Chunk, RetrievedChunk
from app.generation.context import build_context

import pytest

from app.observability.records import compute_context_hash, normalize_run_record
from app.evaluation.dataset import load_questions, load_references
from app.evaluation.graders import grade_record, parse_judge_output
from app.evaluation.runner import grade_all, run_comparison
from app.evaluation.samples import SAMPLE_EVAL, SampleServiceAdapter

REF = {"id": "q", "reference_answer": "정답 비밀문장", "expected_action": "answer",
       "evidence_groups": [[{"book_id": "A"}]]}
FINAL = [{"chunk_id": "c1", "book_id": "A", "file_name": "A.pdf", "page": 1,
          "source_id": "S1", "text": "문맥 문장"}]


def record(**kw):
    rec = {"experiment_id": "e", "question_id": "q", "request_id": "r", "dataset_id": "d",
           "retrieval_mode": "naive", "config_hash": "c", "question": "질문",
           "retrieved_candidates": FINAL, "final_context_chunks": FINAL,
           "final_context_hash": compute_context_hash(FINAL), "status": "answered",
           "answer": "답 [S1]", "reason": "사유", "citations": [{"source_id": "S1"}],
           "latency_ms": 10, "attempts": [], "langfuse_trace_id": None}
    rec.update(kw)
    return normalize_run_record(rec)


def judge_returning(has_claims, **crit):
    def judge(prompt):
        criteria = {k: {"score": 1, "status": "scored", "reason": "ok"}
                    for k in ("fact", "condition", "evidence", "decision")}
        criteria.update(crit)
        return {"text": json.dumps({"has_factual_claims": has_claims, "criteria": criteria}),
                "input_tokens": 7, "output_tokens": 3}
    return judge


def grade(rec, judge=None, faith=None):
    return grade_record(rec, REF, k=5, judge_fn=judge, faithfulness_fn=faith,
                        judge_template="{{question}}|{{reference}}|{{final_context}}")


def test_faithfulness_uses_only_question_answer_final_context():
    seen = {}

    def faith(q, resp, ctx):
        seen.update(q=q, resp=resp, ctx=ctx)
        return {"value": 0.8}

    s = grade(record(), judge_returning(True), faith)
    assert s["faithfulness"] == {"value": 0.8, "status": "scored", "reason": "RAGAS Faithfulness"}
    assert "문맥 문장" in seen["ctx"][0] and "[S1]" in seen["ctx"][0]
    assert "정답 비밀문장" not in json.dumps(seen, ensure_ascii=False)
    assert s["grading_usage"]["judge"]["input_tokens"] == 7  # 평가 사용량은 따로


def test_clarification_without_claims_is_not_applicable_with_claims_is_scored():
    clar = record(status="needs_clarification", answer="어느 항차인가요?", citations=[])
    s = grade(clar, judge_returning(False), lambda *a: pytest.fail("호출하면 안 됨"))
    assert s["faithfulness"]["status"] == "not_applicable"
    s2 = grade(record(status="needs_clarification"), judge_returning(True), lambda *a: {"value": 1.0})
    assert s2["faithfulness"]["status"] == "scored"  # 상태 이름만으로 제외하지 않음


def test_service_error_decision_zero_rest_unavailable():
    err = record(status="error", answer=None, reason=None, error_type="model_timeout")
    s = grade(err, lambda p: pytest.fail("Judge 호출 안 함"), lambda *a: pytest.fail())
    assert s["judge"]["decision"]["value"] == 0.0
    assert all(s["judge"][k]["status"] == "unavailable" for k in ("fact", "condition", "evidence"))
    assert s["faithfulness"]["status"] == "unavailable"


def test_grader_call_failures_are_unavailable_not_scores():
    def boom(*a):
        raise TimeoutError

    s = grade(record(), boom, boom)
    assert all(v["status"] == "unavailable" and v["value"] is None for v in s["judge"].values())
    assert s["faithfulness"]["status"] == "unavailable"  # 주장 여부 미확인 → RAGAS 실패도 평가 불가
    nan = grade(record(), judge_returning(True), lambda *a: {"value": float("nan")})
    assert nan["faithfulness"]["status"] == "unavailable"
    bad = grade(record(), lambda p: {"text": "not json"}, None)
    assert bad["judge"]["fact"]["reason"].startswith("Judge 출력 파싱 실패")


def test_missing_fact_stays_zero_and_bad_items_unavailable():
    crit, _ = parse_judge_output(json.dumps({"criteria": {
        "fact": {"score": 0, "status": "scored", "reason": "필수 사실 누락"},
        "condition": {"score": 0.5, "status": "scored"},
        "evidence": {"score": None, "status": "not_applicable", "reason": "인용 요구 없음"}}}))
    assert crit["fact"]["value"] == 0.0
    assert crit["condition"]["status"] == "unavailable"
    assert crit["evidence"]["status"] == "not_applicable"
    assert crit["decision"]["status"] == "unavailable"


def test_judge_prompt_gets_reference_but_service_does_not():
    prompts = []
    s = grade(record(), lambda p: prompts.append(p) or judge_returning(True)(p), None)
    assert "정답 비밀문장" in prompts[0] and s["judge"]["fact"]["value"] == 1.0


def test_runner_passes_only_question_and_saves_failures(tmp_path):
    questions = load_questions(SAMPLE_EVAL)
    assert all(set(q) == {"id", "question"} for q in questions)
    adapter = SampleServiceAdapter()
    runs = run_comparison(questions, adapter, experiment_id="sample-t", dataset_id="d",
                          config_hash="c")
    assert len(runs) == len(questions) * 3
    refs = load_references(SAMPLE_EVAL)
    leaked = [v for r in refs.values() for v in (r["reference_answer"], r["rubric"])]
    assert not any(v in str(adapter.seen_inputs) for v in leaked)
    errors = [r for r in runs if r["status"] == "error"]
    assert [(r["question_id"], r["retrieval_mode"]) for r in errors] == [("SAMPLE-Q4", "hybrid_rerank")]
    assert sum(r["status"] == "needs_clarification" for r in runs) == 6  # 정상 추가 확인


def test_runner_records_adapter_exception_and_invalid_record():
    def adapter(question, mode, rid):
        if mode == "naive":
            raise ConnectionError("down")
        return {"request_id": rid}  # 계약 위반

    runs = run_comparison([{"id": "q1", "question": "x"}], adapter, experiment_id="e",
                          dataset_id="d", config_hash="c", modes=("naive", "hybrid"))
    assert [r["error_type"] for r in runs] == ["adapter_exception", "invalid_run_record"]
    assert all(r["usage"]["input_tokens"] is None for r in runs)
    with pytest.raises(ValueError, match="참고 정답"):
        grade_all(runs, {}, k=5, judge_fn=None, faithfulness_fn=None)


@pytest.mark.parametrize("saved_context", [True, False])
def test_graders_receive_generation_heading_and_sources(saved_context):
    # 승인일과 예약번호가 본문이 아닌 제목에만 있는 경우를 재현한다.
    chunk = Chunk("A-p1-c1", "A", "A.pdf", 1, "보관료 무료 2일 연장 승인",
                  "예약 DEMO-01207 / 승인 원본 서명일 2030-11-22")
    bundle = build_context([RetrievedChunk(chunk, 1, 1.0, "dense")], 1, 4000, len)
    final = [dict(asdict(chunk), source_id="S1")]
    kwargs = dict(final_context_chunks=final, final_context_hash=compute_context_hash(final))
    if saved_context:
        kwargs["model_context_text"] = bundle.text
        kwargs["model_context_hash"] = bundle.context_hash
    rec = record(**kwargs)
    seen = {}

    def judge(prompt):
        seen["judge"] = prompt
        return judge_returning(True)(prompt)

    def faith(question, response, contexts):
        seen["faith"] = contexts
        return {"value": 1.0}

    grade_record(rec, REF, k=5, judge_fn=judge, faithfulness_fn=faith,
                 judge_template="{{final_context}}")
    assert seen["judge"] == bundle.text
    assert seen["faith"] == [bundle.text]
    assert "DEMO-01207" in seen["judge"] and "2030-11-22" in seen["judge"]
    assert "정답 비밀문장" not in str(seen)


def test_saved_context_is_preserved_instead_of_rebuilt_from_chunks():
    seen = {}
    actual = "[S1]\n제목: 당시 제목\n내용: 실제 모델에게 전달한 문맥  \n"
    rec = record(model_context_text=actual, model_context_hash=sha256(actual.encode()).hexdigest())

    def faith(question, response, contexts):
        seen["faith"] = contexts
        return {"value": 1.0}

    def judge(prompt):
        seen["judge"] = prompt
        return judge_returning(True)(prompt)

    grade_record(rec, REF, k=5, judge_fn=judge, faithfulness_fn=faith,
                 judge_template="{{final_context}}")
    assert seen["judge"] == actual
    assert seen["faith"] == [actual]
    assert "문맥 문장" not in str(seen)


def test_explicit_empty_model_context_does_not_use_retrieved_text():
    prompts = []
    rec = record(model_context_text="", model_context_hash=sha256(b"").hexdigest())
    result = grade_record(
        rec, REF, k=5,
        judge_fn=lambda prompt: prompts.append(prompt) or judge_returning(True)(prompt),
        faithfulness_fn=lambda *args: pytest.fail("빈 실제 문맥으로 RAGAS를 호출하지 않음"),
        judge_template="{{final_context}}")
    assert prompts == ["(최종 문맥 없음)"]
    assert result["faithfulness"]["value"] == 0.0
