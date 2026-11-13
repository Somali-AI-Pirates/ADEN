from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

from app.evaluation.faithfulness import (
    ClaimVerdict,
    DetailedVerdicts,
    SupportingQuote,
    _source_bodies,
    _validate_advanced_verdicts,
    make_faithfulness,
)


@dataclass
class FakeMetadata:
    stage: str
    input_tokens: int = 12
    output_tokens: int = 7
    reasoning_tokens: int = 2
    requested_effort: str = "max"
    effective_effort: str = "max"
    latency_ms: int = 25
    response_id: str = "resp-test"
    status: str = "completed"
    attempts: int = 1
    fallbacks: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class FakeCall:
    output: BaseModel
    metadata: FakeMetadata

    def to_mapping(self) -> dict[str, Any]:
        return {"output": self.output.model_dump(), "metadata": self.metadata.to_mapping()}


class FakeSession:
    def __init__(
        self,
        statements: list[str],
        verdicts: list[dict[str, Any]],
        excluded_nonfactual: list[dict[str, str]] | None = None,
    ):
        self.statements = statements
        self.verdicts = verdicts
        self.excluded_nonfactual = excluded_nonfactual or []
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    async def parse_json(
        self,
        prompt: str,
        response_model: type[BaseModel],
        *,
        model: str,
        stage: str,
    ) -> FakeCall:
        assert model == "test-model"
        self.calls.append((stage, prompt, response_model))
        if stage.endswith("extraction"):
            payload = {"statements": self.statements}
            if "excluded_nonfactual" in response_model.model_fields:
                payload["excluded_nonfactual"] = self.excluded_nonfactual
        else:
            payload = {"statements": self.verdicts}
        return FakeCall(response_model(**payload), FakeMetadata(stage=stage))


CONTEXTS = [
    "[BOOK-HG-2026-P14]\n예약 DEMO-17 | 실제 반환일: 2026-03-02\n[FAKE-ID]\n이 줄은 본문이다.",
    "[SQL:1]\n{\"예약\":\"DEMO-17\",\"청구액\":\"120 USD\"}",
]


def test_advanced_profile_emits_verified_per_claim_quotes_and_usage():
    statements = [
        "예약 DEMO-17의 실제 반환일은 2026-03-02이다.",
        "예약 DEMO-17의 예정 반환일은 2026-03-02이다.",
    ]
    session = FakeSession(
        statements,
        [
            {
                "statement": statements[0],
                "verdict": 1,
                "reason": "실제 반환일이 원문에 적혀 있다.",
                "supporting_evidence": [
                    {"source_id": "BOOK-HG-2026-P14", "quote": "실제 반환일: 2026-03-02"}
                ],
            },
            {
                "statement": statements[1],
                "verdict": 0,
                "reason": "예정 반환일을 확인할 수 없다.",
                "supporting_evidence": [],
            },
        ],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("예약 DEMO-17 실제 반환일은?", "실제 반환일은 2026-03-02입니다.", CONTEXTS)

    assert result["value"] == 0.5
    assert result["status"] == "scored"
    assert result["details"]["claims"][0]["supporting_evidence"] == [
        {"source_id": "BOOK-HG-2026-P14", "quote": "실제 반환일: 2026-03-02"}
    ]
    assert result["details"]["claims"][1]["supporting_evidence"] == []
    assert result["input_tokens"] == 24
    assert result["reasoning_tokens"] == 4
    assert [call[0] for call in session.calls] == [
        "faithfulness_claim_extraction",
        "faithfulness_claim_verification",
    ]
    assert "가상 예약 DEMO-17" in session.calls[0][1]
    assert "BOOK-HG-2026-P14" in session.calls[1][1]
    assert "FAKE-ID" in session.calls[1][1]


def test_default_profile_uses_stock_ragas_schema_without_claim_source_mapping():
    statement = "예약 DEMO-17의 청구액은 120 USD이다."
    session = FakeSession(
        [statement],
        [{"statement": statement, "verdict": 1, "reason": "Ragas stock NLI result."}],
    )
    grader = make_faithfulness("test-model", session=session, profile="default")

    result = grader("청구액은?", "120 USD입니다.", CONTEXTS)

    assert result["value"] == 1.0
    assert result["details"]["source_attribution"] == "not_collected_by_stock_schema"
    assert result["details"]["claims"][0]["supporting_evidence"] is None
    assert session.calls[0][2].__name__ == "StatementGeneratorOutput"
    assert session.calls[1][2].__name__ == "NLIStatementOutput"


def test_empty_or_boilerplate_only_claims_are_not_applicable_without_second_call():
    session = FakeSession(["안녕하세요.", "추가 확인이 필요합니다."], [])
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("예약 코드는?", "안녕하세요. 추가 확인이 필요합니다.", CONTEXTS)

    assert result["value"] is None
    assert result["status"] == "not_applicable"
    assert result["details"]["claims"] == []
    assert result["details"]["excluded_claims"] == [
        {"text": "안녕하세요.", "reason": "generic_conversational_filler"},
        {"text": "추가 확인이 필요합니다.", "reason": "generic_conversational_filler"},
    ]
    assert len(session.calls) == 1


def test_advanced_extraction_records_exact_nonfactual_request_for_more_information():
    response = "예약번호를 알려 주세요."
    session = FakeSession(
        [],
        [],
        excluded_nonfactual=[
            {"text": response, "reason": "request_for_more_information"}
        ],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("예약번호는?", response, [])

    assert result["value"] is None
    assert result["status"] == "not_applicable"
    assert result["details"]["excluded_claims"] == [
        {"text": response, "reason": "request_for_more_information"}
    ]
    assert len(session.calls) == 1


def test_advanced_exclusion_not_found_verbatim_makes_metric_unavailable():
    response = "예약번호를 알려 주세요."
    session = FakeSession(
        [],
        [],
        excluded_nonfactual=[
            {"text": "필요한 예약번호를 알려 주세요.", "reason": "request_for_more_information"}
        ],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("예약번호는?", response, [])

    assert result["value"] is None
    assert result["status"] == "unavailable"
    assert result["details"]["reason"] == "invalid_excluded_nonfactual"
    assert result["details"]["failed_validation"] == [
        {"index": 0, "code": "excluded_text_not_present_in_response"}
    ]
    assert len(session.calls) == 1


def test_default_profile_keeps_stock_extraction_schema_and_logs_program_filtered_filler():
    session = FakeSession(["안녕하세요."], [])
    grader = make_faithfulness("test-model", session=session, profile="default")

    result = grader("예약번호는?", "안녕하세요.", [])

    assert result["value"] is None
    assert result["status"] == "not_applicable"
    assert result["details"]["excluded_claims"] == [
        {"text": "안녕하세요.", "reason": "generic_conversational_filler"}
    ]
    assert session.calls[0][2].__name__ == "StatementGeneratorOutput"
    assert len(session.calls) == 1


@pytest.mark.parametrize("profile", ["default", "advanced"])
def test_program_filtered_exclusion_must_also_match_the_response(profile):
    session = FakeSession(["안녕하세요."], [])
    grader = make_faithfulness("test-model", session=session, profile=profile)

    result = grader("예약번호는?", "예약번호는 DEMO-17입니다.", [])

    assert result["value"] is None
    assert result["status"] == "unavailable"
    assert result["details"]["reason"] == "invalid_excluded_nonfactual"
    assert result["details"]["failed_validation"] == [
        {"index": 0, "code": "excluded_text_not_present_in_response"}
    ]
    assert len(session.calls) == 1


@pytest.mark.parametrize("profile", ["default", "advanced"])
def test_empty_contexts_are_scored_as_unsupported_instead_of_rejected(profile):
    statement = "예약 DEMO-17의 청구액은 120 USD이다."
    if profile == "advanced":
        verdict = {
            "statement": statement,
            "verdict": 0,
            "reason": "제공된 근거가 없다.",
            "supporting_evidence": [],
        }
    else:
        verdict = {"statement": statement, "verdict": 0, "reason": "제공된 근거가 없다."}
    session = FakeSession([statement], [verdict])
    grader = make_faithfulness("test-model", session=session, profile=profile)

    result = grader("청구액은?", "청구액은 120 USD입니다.", [])

    assert result["value"] == 0.0
    assert result["status"] == "scored"
    assert result["details"]["claims"][0]["verdict"] == 0
    assert len(session.calls) == 2


def test_runtime_output_failure_preserves_safe_verdicts_prompts_and_usage():
    statement = "예약 DEMO-17의 청구액은 120 USD이다."

    class EvaluationOutputError(Exception):
        def __init__(self):
            self.evaluation_details = {
                "status": "output_error",
                "stage": "faithfulness_claim_verification",
                "input_tokens": 31,
                "output_tokens": 4,
                "reasoning_tokens": 1,
                "error_type": "ValidationError",
                "response_status": "incomplete",
                "incomplete_reason": "max_output_tokens",
            }
            self.raw_verdicts = [{"verdict": 2}]
            self.validation_errors = [{"location": ["statements", 0, "verdict"], "error_type": "literal_error"}]

    class FailingVerificationSession(FakeSession):
        async def parse_json(self, prompt, response_model, *, model, stage):
            if stage == "faithfulness_claim_verification":
                self.calls.append((stage, prompt, response_model))
                raise EvaluationOutputError()
            return await super().parse_json(prompt, response_model, model=model, stage=stage)

    session = FailingVerificationSession(
        ["안녕하세요.", statement],
        [],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("청구액은?", "안녕하세요. 120 USD입니다.", CONTEXTS)

    assert result["value"] is None
    assert result["status"] == "unavailable"
    assert result["error"] == "EvaluationOutputError"
    assert result["details"]["raw_verdicts"] == [{"verdict": 2}]
    assert result["details"]["excluded_claims"] == [
        {"text": "안녕하세요.", "reason": "generic_conversational_filler"}
    ]
    assert len(result["details"]["model_calls"]) == 2
    assert all(call["prompt"] and len(call["prompt_sha256"]) == 64 for call in result["details"]["model_calls"])
    assert result["details"]["usage"]["input_tokens"] == 43
    assert result["details"]["usage"]["logical_calls"] == 2
    verification_stage = result["details"]["usage"]["stages"][1]
    assert verification_stage["stage"] == "faithfulness_claim_verification"
    assert verification_stage["response_status"] == "incomplete"
    assert verification_stage["incomplete_reason"] == "max_output_tokens"


def test_invalid_quote_makes_metric_unavailable_instead_of_scoring_zero():
    statement = "예약 DEMO-17의 실제 반환일은 2026-03-02이다."
    session = FakeSession(
        [statement],
        [
            {
                "statement": statement,
                "verdict": 1,
                "reason": "모델이 든 인용이 실제 원문에 없다.",
                "supporting_evidence": [
                    {"source_id": "FAKE-ID", "quote": "실제 반환일: 2026-03-02"}
                ],
            }
        ],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("실제 반환일은?", "2026-03-02입니다.", CONTEXTS)

    assert result["value"] is None
    assert result["status"] == "unavailable"
    assert result["details"]["reason"] == "invalid_faithfulness_output"
    assert result["details"]["raw_verdicts"] == [{"claim_index": 0, "verdict": 1}]
    assert result["details"]["failed_validation"] == [
        {"claim_index": 0, "code": "unknown_source_id", "source_id": "FAKE-ID"}
    ]
    assert result["logical_calls"] == 2
    assert len(result["details"]["model_calls"]) == 2
    assert len(result["details"]["model_calls"][1]["prompt_sha256"]) == 64
    assert result["details"]["model_calls"][1]["prompt"]


def test_bad_literal_verdict_is_unavailable_and_raw_numeric_value_is_diagnostic_only():
    statement = "예약 DEMO-17의 실제 반환일은 2026-03-02이다."
    session = FakeSession(
        [statement],
        [
            {
                "statement": statement,
                "verdict": 2,
                "reason": "이 문장은 절대 저장하지 않아야 한다.",
                "supporting_evidence": [],
            }
        ],
    )
    grader = make_faithfulness("test-model", session=session, profile="advanced")

    result = grader("실제 반환일은?", "2026-03-02입니다.", CONTEXTS)

    assert result["value"] is None
    assert result["status"] == "unavailable"
    assert result["logical_calls"] == 2
    assert result["details"]["raw_verdicts"] == [{"verdict": 2}]
    assert result["details"]["failed_validation"][0]["stage"] == "faithfulness_claim_verification"
    assert "reason" not in str(result["details"]["failed_validation"])


def test_source_parser_only_treats_each_contexts_first_line_as_an_id():
    bodies = _source_bodies(CONTEXTS)
    assert set(bodies) == {"BOOK-HG-2026-P14", "SQL:1"}
    assert "FAKE-ID" in bodies["BOOK-HG-2026-P14"]
    assert _source_bodies(["예약 DEMO-17\n[FAKE-ID]\n실제 반환일: 2026-03-02"]) == {}


def test_quotes_use_nfc_and_whitespace_matching_but_not_loose_semantic_matching():
    source = "[S1]\n금액: 1,200 USD\n효력일: 2026-03-02"
    claim = "청구액은 1,200 USD이다."
    valid = DetailedVerdicts(
        statements=[
            ClaimVerdict(
                statement=claim,
                verdict=1,
                reason="금액과 통화가 문맥에 있다.",
                supporting_evidence=[SupportingQuote(source_id="S1", quote="금액:\n 1,200 USD")],
            )
        ]
    )

    claims, scores = _validate_advanced_verdicts([claim], valid, _source_bodies([source]))

    assert scores == [1]
    assert claims[0]["evidence_validation"] == "verified"

    invalid = DetailedVerdicts(
        statements=[
            ClaimVerdict(
                statement=claim,
                verdict=1,
                reason="다른 통화는 원문에 없다.",
                supporting_evidence=[SupportingQuote(source_id="S1", quote="금액: 1,200 KRW")],
            )
        ]
    )
    try:
        _validate_advanced_verdicts([claim], invalid, _source_bodies([source]))
    except ValueError as exc:
        assert str(exc) == "invalid_faithfulness_output"
        assert exc.failures == [  # type: ignore[attr-defined]
            {"claim_index": 0, "code": "quote_not_present_in_source", "source_id": "S1"}
        ]
    else:
        raise AssertionError("A paraphrased quote must fail exact source validation")


def test_advanced_verdict_rejects_boolean_and_float_coercion():
    from app.evaluation.faithfulness import ClaimVerdict
    from pydantic import ValidationError
    import pytest
    for value in (True, False, 1.0, 0.0):
        with pytest.raises(ValidationError):
            ClaimVerdict(statement="fact", verdict=value, reason="reason",
                         supporting_evidence=[])
