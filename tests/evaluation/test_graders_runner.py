import json

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
    assert seen["ctx"] == ["[S1]\n문맥 문장"] and "정답 비밀문장" not in json.dumps(seen, ensure_ascii=False)
    assert s["grading_usage"]["judge"]["input_tokens"] == 7  # 평가 사용량은 따로


def test_clarification_without_claims_is_not_applicable_with_claims_is_scored():
    clar = record(status="needs_clarification", answer="어느 항차인가요?", citations=[])
    seen = []
    s = grade(clar, judge_returning(False), lambda *a: seen.append(a) or {
        "status": "not_applicable", "value": None, "reason": "사실 주장 없음"})
    assert len(seen) == 1  # Judge 판정과 무관하게 자체 주장 추출
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


def judge_json(claims=True, checks=(), **crit):
    criteria = {k: {"score": 1, "status": "scored", "reason": "ok"}
                for k in ("fact", "condition", "evidence", "decision")}
    criteria.update(crit)
    return json.dumps({"has_factual_claims": claims, "criteria": criteria,
                       "calculation_checks": list(checks)})


def test_valid_output_keeps_zero_and_not_applicable():
    crit, claims, calc = parse_judge_output(judge_json(
        fact={"score": 0, "status": "scored", "reason": "필수 사실 누락"},
        evidence={"score": None, "status": "not_applicable", "reason": "인용 요구 없음"}))
    assert crit["fact"] == {"value": 0.0, "status": "scored", "reason": "필수 사실 누락"}
    assert crit["evidence"]["status"] == "not_applicable" and claims is True
    assert calc["status"] == "not_applicable"  # 검산 요청 없음은 빈 배열


@pytest.mark.parametrize("bad, message", [
    ({"condition": {"score": 0.5, "status": "scored", "reason": "x"}}, "condition.score"),
    ({"fact": {"score": True, "status": "scored", "reason": "x"}}, "fact.score"),  # bool은 점수가 아니다
    ({"fact": {"score": None, "status": "scored", "reason": "x"}}, "fact.score"),
    ({"evidence": {"score": 1, "status": "not_applicable", "reason": "x"}}, "evidence.score"),
    ({"decision": {"score": 1, "status": "done", "reason": "x"}}, "decision.status"),
    ({"fact": {"score": 1, "status": "scored", "reason": " "}}, "fact.reason"),
    ({"decision": "1"}, "decision 항목 없음"),
])
def test_structure_errors_make_all_four_unavailable_with_reason(bad, message):
    crit, claims, _ = parse_judge_output(judge_json(**bad))
    assert claims is None
    for item in crit.values():
        assert item["status"] == "unavailable" and item["value"] is None
        assert item["reason"].startswith("Judge 출력 구조 오류") and message in item["reason"]


def test_has_factual_claims_and_criteria_are_required():
    no_claims = json.loads(judge_json())
    del no_claims["has_factual_claims"]
    crit, claims, _ = parse_judge_output(json.dumps(no_claims))
    assert claims is None and "has_factual_claims" in crit["fact"]["reason"]
    crit, _, _ = parse_judge_output(json.dumps({"has_factual_claims": "true"}))
    assert "criteria 객체 없음" in crit["decision"]["reason"]


CHECK = {"target": "보관료", "status": "complete", "missing_evidence": [],
         "supporting_source_ids": ["S1"], "reason": "규칙·사건·요율로 계산"}


def test_calculation_checks_are_validated_separately_from_scores():
    crit, _, calc = parse_judge_output(judge_json(checks=[CHECK]), {"S1"})
    assert calc["status"] == "complete" and calc["items"][0]["supporting_source_ids"] == ["S1"]
    # 문맥에 없는 근거 ID는 진단만 평가 불가로 두고 네 점수는 그대로 둔다
    crit, _, calc = parse_judge_output(judge_json(checks=[CHECK]), {"S2"})
    assert crit["fact"]["value"] == 1.0
    assert calc["status"] == "unavailable" and "실제 제공 문맥에 없음" in calc["reason"]
    missing = {**CHECK, "status": "incomplete", "missing_evidence": ["산입 규칙"], "supporting_source_ids": []}
    _, _, calc = parse_judge_output(judge_json(checks=[CHECK, missing]), {"S1"})
    assert calc["status"] == "incomplete" and calc["items"][1]["missing_evidence"] == ["산입 규칙"]


@pytest.mark.parametrize("check, message", [
    ({**CHECK, "missing_evidence": ["요율"]}, "complete는"),
    ({**CHECK, "supporting_source_ids": []}, "complete는"),
    ({**CHECK, "status": "incomplete"}, "incomplete는"),
    ({**CHECK, "status": "not_applicable"}, "not_applicable에는"),
    ({**CHECK, "status": "done"}, ".status"),
    ({**CHECK, "missing_evidence": "요율"}, "문자열 배열"),
    ({**CHECK, "target": ""}, ".target"),
])
def test_inconsistent_calculation_checks_are_unavailable(check, message):
    _, _, calc = parse_judge_output(judge_json(checks=[check]), {"S1"})
    assert calc["status"] == "unavailable" and message in calc["reason"]


def test_missing_calculation_checks_do_not_invalidate_scores():
    old = json.loads(judge_json())
    del old["calculation_checks"]  # 이전 기준(v2) 출력
    crit, claims, calc = parse_judge_output(json.dumps(old))
    assert crit["fact"]["value"] == 1.0 and claims is True
    assert calc == {"status": "unavailable", "reason": "Judge 출력에 calculation_checks 없음", "items": []}


def test_grade_record_checks_source_ids_against_final_context():
    def judge(prompt):
        return {"text": judge_json(checks=[CHECK])}

    s = grade(record(), judge, None)
    assert s["calculation_checks"]["status"] == "complete"  # FINAL의 S1
    err = grade(record(status="error", answer=None, reason=None, error_type="t"), None, None)
    assert err["calculation_checks"]["status"] == "unavailable"


def test_judge_prompt_gets_reference_but_service_does_not():
    prompts = []
    s = grade(record(), lambda p: prompts.append(p) or judge_returning(True)(p), None)
    assert "정답 비밀문장" in prompts[0] and s["judge"]["fact"]["value"] == 1.0


def test_chunk_heading_reaches_judge_and_faithfulness_context():
    # 고급 자료는 화주·예약번호가 제목에만 있어 생성 모델처럼 채점기도 제목을 봐야 한다
    final = [{**FINAL[0], "heading": "화주 ACME 예약 BK-1"}]
    prompts, seen = [], {}

    def faith(q, resp, ctx):
        seen["ctx"] = ctx
        return {"value": 1.0}

    grade(record(retrieved_candidates=final, final_context_chunks=final,
                 final_context_hash=compute_context_hash(final)),
          lambda p: prompts.append(p) or judge_returning(True)(p), faith)
    assert "제목: 화주 ACME 예약 BK-1\n문맥 문장" in prompts[0]
    assert seen["ctx"] == ["[S1]\n제목: 화주 ACME 예약 BK-1\n문맥 문장"]


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
