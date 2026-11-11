import json
from types import SimpleNamespace

import pytest

from experimental.u_10o0o.advanced_hg_luna import (
    DOMAIN_INSTRUCTIONS,
    FUNCTION_NAME,
    HGProposal,
    HG_ROLES,
    HGLuna,
    LOOKUP_HG_SOURCES_SCHEMA,
    PROMPT_SHA256,
    SCHEMA_SHA256,
    TIMEOUT_SECONDS,
)
from experimental.u_10o0o.advanced_luna_interpreter import (
    API_BASE_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
)


def _proposal(question: str | None = None):
    anchor = "HL-20GP-0066 (20GP) 화물의 실제 반출 기록"
    if question is not None:
        assert anchor in question
    return {
        "route": "lookup",
        "targets": [
            {
                "anchor": anchor,
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HL-20GP-0066",
                        "relation": "unspecified",
                    },
                    {"kind": "spec", "raw": "20GP", "relation": "unspecified"},
                    {"kind": "cargo", "raw": "화물", "relation": "unspecified"},
                ],
                "roles": ["현장 사건 수신대장"],
                "date_type": "actual",
            }
        ],
    }


def _response(*, output=None, status="completed", usage=None):
    return SimpleNamespace(
        output=output or [],
        status=status,
        usage=usage
        or SimpleNamespace(
            input_tokens=230,
            output_tokens=44,
            input_tokens_details=SimpleNamespace(cached_tokens=12),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
    )


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response, error)


class ConfigurableFakeClient(FakeClient):
    def __init__(self, response=None, error=None):
        super().__init__(response, error)
        self.options = []

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


def _function_call(arguments, name=FUNCTION_NAME):
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
    )


def test_hg_tool_proposal_is_strict_question_only_and_never_executes_results():
    question = "HL-20GP-0066 (20GP) 화물의 실제 반출 기록을 보여줘"
    client = ConfigurableFakeClient(
        _response(output=[_function_call(_proposal(question))])
    )

    result = HGLuna(client).invoke(question)

    assert result.status == "completed"
    assert result.proposal == HGProposal.model_validate(_proposal(question))
    assert result.tool_requested is True
    assert result.telemetry.attempted is True
    assert result.telemetry.model == MODEL_ID == "gpt-6-luna"
    assert result.telemetry.reasoning_effort == REASONING_EFFORT == "low"
    assert result.telemetry.input_tokens == 230
    assert result.telemetry.cached_input_tokens == 12
    assert result.telemetry.output_tokens == 44
    assert result.telemetry.reasoning_tokens == 5
    assert result.telemetry.latency_ms >= 0
    assert "question" not in result.telemetry.as_dict()
    assert "proposal" not in result.telemetry.as_dict()

    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == MODEL_ID
    assert request["input"] == question
    assert request["instructions"] == DOMAIN_INSTRUCTIONS
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert request["store"] is False
    assert request["timeout"] == TIMEOUT_SECONDS == 10.0
    assert request["tool_choice"] == "auto"
    assert request["parallel_tool_calls"] is False
    assert "max_tool_calls" not in request
    assert request["tools"] == [
        {
            "type": "function",
            "name": FUNCTION_NAME,
            "description": request["tools"][0]["description"],
            "parameters": LOOKUP_HG_SOURCES_SCHEMA,
            "strict": True,
        }
    ]
    assert "HL-20GP-0066" not in repr(request["tools"])
    assert "do not execute" in request["tools"][0]["description"]
    assert "20GP" in request["instructions"]
    assert "booking_status_as_of" in request["instructions"]
    assert "scheduled date into an actual date" in request["instructions"]
    assert "answer_as_of" in request["instructions"]
    assert "identity_filter" in request["instructions"]
    assert client.options == [{"base_url": API_BASE_URL, "max_retries": 0}]


def test_two_case_targets_keep_their_own_role_and_date_semantics():
    question = (
        "HG2030-00017의 실제 반출 기록과 HG2030-00018의 반납 예정일 기록을 비교해줘"
    )
    proposed = {
        "route": "lookup",
        "targets": [
            {
                "anchor": "HG2030-00017의 실제 반출 기록",
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HG2030-00017",
                        "relation": "unspecified",
                    }
                ],
                "roles": ["현장 사건 수신대장"],
                "date_type": "actual",
            },
            {
                "anchor": "HG2030-00018의 반납 예정일 기록",
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HG2030-00018",
                        "relation": "unspecified",
                    }
                ],
                "roles": ["현장 사건 수신대장"],
                "date_type": "scheduled",
            },
        ],
    }
    client = FakeClient(_response(output=[_function_call(proposed)]))

    result = HGLuna(client).invoke(question)

    assert result.status == "completed"
    assert [target.roles for target in result.proposal.targets] == [
        ["현장 사건 수신대장"],
        ["현장 사건 수신대장"],
    ]
    assert [target.date_type for target in result.proposal.targets] == [
        "actual",
        "scheduled",
    ]


def test_as_of_timestamp_is_distinct_from_actual_event_identity_date():
    question = "2026-10-15 10:30 KST 기준 2026-10-03 양하 컨테이너 건의 실제 비용 청구 명세를 확인해줘"
    anchor = (
        "2026-10-15 10:30 KST 기준 2026-10-03 양하 컨테이너 건의 실제 비용 청구 명세"
    )
    proposed = {
        "route": "lookup",
        "targets": [
            {
                "anchor": anchor,
                "cues": [
                    {
                        "kind": "date",
                        "raw": "2026-10-15 10:30 KST",
                        "relation": "answer_as_of",
                    },
                    {
                        "kind": "date",
                        "raw": "2026-10-03",
                        "relation": "identity_filter",
                    },
                    {"kind": "cargo", "raw": "컨테이너", "relation": "unspecified"},
                ],
                "roles": ["비용 청구 명세"],
                "date_type": "actual",
            }
        ],
    }
    client = FakeClient(_response(output=[_function_call(proposed)]))

    result = HGLuna(client).invoke(question)

    assert result.status == "completed"
    target = result.proposal.targets[0]
    assert target.date_type == "actual"
    assert [(cue.raw, cue.relation) for cue in target.cues if cue.kind == "date"] == [
        ("2026-10-15 10:30 KST", "answer_as_of"),
        ("2026-10-03", "identity_filter"),
    ]


def test_general_question_can_skip_the_function_for_local_document_routing():
    client = FakeClient(_response(output=[]))

    result = HGLuna(client).invoke("무료기간 적용 규정과 예외를 설명해줘")

    assert result.status == "no_tool_call"
    assert result.proposal is None
    assert result.tool_requested is False
    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["tool_choice"] == "auto"


def test_invalid_literals_roles_extra_fields_and_non_unique_anchor_are_rejected():
    question = "HG2030-00017 비용 청구 명세를 확인해줘"
    invalid = {
        "route": "lookup",
        "targets": [
            {
                "anchor": question,
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HG2030-00018",
                        "relation": "unspecified",
                    }
                ],
                "roles": ["비용 청구 명세"],
                "date_type": "unspecified",
            }
        ],
    }
    result = HGLuna(FakeClient(_response(output=[_function_call(invalid)]))).invoke(
        question
    )
    assert result.status == "invalid_proposal"
    assert result.proposal is None

    repeated_anchor_question = (
        "HG2030-00017 비용 청구 명세를 확인하고 HG2030-00017 비용 청구 명세도 비교해줘"
    )
    repeated_anchor = {
        "route": "lookup",
        "targets": [
            {
                "anchor": "HG2030-00017 비용 청구 명세",
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HG2030-00017",
                        "relation": "unspecified",
                    }
                ],
                "roles": ["비용 청구 명세"],
                "date_type": "unspecified",
            }
        ],
    }
    result = HGLuna(
        FakeClient(_response(output=[_function_call(repeated_anchor)]))
    ).invoke(repeated_anchor_question)
    assert result.status == "invalid_proposal"

    valid = {
        "route": "lookup",
        "targets": [
            {
                "anchor": question,
                "cues": [
                    {
                        "kind": "identifier",
                        "raw": "HG2030-00017",
                        "relation": "unspecified",
                    }
                ],
                "roles": ["unknown role"],
                "date_type": "unspecified",
            }
        ],
    }
    result = HGLuna(FakeClient(_response(output=[_function_call(valid)]))).invoke(
        question
    )
    assert result.status == "invalid_proposal"

    valid["targets"][0]["roles"] = ["비용 청구 명세"]
    valid["targets"][0]["untrusted_result"] = "local result must not be echoed"
    result = HGLuna(FakeClient(_response(output=[_function_call(valid)]))).invoke(
        question
    )
    assert result.status == "invalid_proposal"


def test_route_target_invariants_and_supported_role_schema_are_enforced():
    with pytest.raises(ValueError):
        HGProposal.model_validate({"route": "lookup", "targets": []})
    with pytest.raises(ValueError):
        HGProposal.model_validate(
            {"route": "documents", "targets": [_proposal()["targets"][0]]}
        )
    assert LOOKUP_HG_SOURCES_SCHEMA["properties"]["targets"]["items"]["properties"][
        "roles"
    ]["items"]["enum"] == list(HG_ROLES)
    assert PROMPT_SHA256
    assert SCHEMA_SHA256


def test_timeout_is_one_sanitized_attempt_without_retry():
    secret_question = "private customer HS-057 question"
    client = FakeClient(error=TimeoutError(secret_question))

    result = HGLuna(client).invoke(secret_question)

    assert result.status == "timeout"
    assert result.proposal is None
    assert result.telemetry.attempted is True
    assert len(client.responses.calls) == 1
    assert secret_question not in repr(result)


def test_input_validation_rejects_empty_or_overlong_timeout_without_call():
    client = FakeClient(_response())
    interpreter = HGLuna(client)

    with pytest.raises(ValueError, match="non-empty"):
        interpreter.invoke("   ")
    with pytest.raises(ValueError, match="at most 10 seconds"):
        interpreter.invoke("lookup", timeout_seconds=10.1)
    assert client.responses.calls == []
