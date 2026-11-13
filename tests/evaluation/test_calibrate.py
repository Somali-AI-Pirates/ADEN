from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.calibrate import (
    DEFAULT_CASES,
    _checkpoint_path,
    _load_cli_cases,
    _schedule,
    parse_args,
    run_calibration,
)
from app.evaluation.calibration import CalibrationCase, HISTORICAL_CASE_IDS
from app.evaluation.model_runtime import EvaluationPolicy


def make_cases() -> tuple[CalibrationCase, ...]:
    return (
        CalibrationCase(
            case_id="SYN-1",
            category="date_scope",
            question="가상 문서의 실제 날짜는?",
            response="실제 날짜는 2031-01-02입니다.",
            contexts=({"source_id": "SYN-1-P1", "text": "실제 날짜: 2031-01-02"},),
            expected_supported=True,
            source="synthetic",
        ),
        CalibrationCase(
            case_id="SYN-2",
            category="date_scope",
            question="가상 문서의 실제 날짜는?",
            response="실제 날짜는 2031-01-03입니다.",
            contexts=({"source_id": "SYN-2-P1", "text": "실제 날짜: 2031-01-02"},),
            expected_supported=False,
            source="synthetic",
        ),
        CalibrationCase(
            case_id="HIST-1",
            category="historical_diagnostic",
            question="과거 질문",
            response="과거 답변",
            contexts=({"source_id": "HIST-P1", "text": "과거 원문"},),
            expected_supported=None,
            source="blind-review-1.jsonl",
        ),
    )


class FakeSession:
    def __init__(self, policy: EvaluationPolicy):
        self.policy = policy
        self.effective_reasoning_effort: str | None = None
        self.fallback_used = False
        self.closed = False
        self.restored: list[str] = []

    def restore_effective_effort(self, effort: str) -> None:
        self.effective_reasoning_effort = effort
        self.restored.append(effort)

    def to_manifest(self) -> dict[str, Any]:
        return {
            **self.policy.to_mapping(),
            "effective_reasoning_effort": self.effective_reasoning_effort,
            "fallback_used": self.fallback_used,
            "fallback_reason": None,
        }

    def close(self) -> None:
        self.closed = True


def fake_grader_factory(calls: list[tuple[str, str]], *, unavailable: bool = False):
    def factory(model: str, *, session: FakeSession, profile: str, policy: EvaluationPolicy):
        assert model == "test-model"
        assert policy.reasoning_effort == "max"

        def grade(question: str, response: str, contexts: list[str]) -> dict[str, Any]:
            calls.append((profile, response))
            if session.effective_reasoning_effort is None:
                session.effective_reasoning_effort = "max"
            if unavailable:
                return {
                    "value": None,
                    "status": "unavailable",
                    "details": {"reason": "synthetic_unavailable"},
                    "usage": {"logical_calls": 2, "input_tokens": 10, "output_tokens": 2},
                    "logical_calls": 2,
                    "input_tokens": 10,
                    "output_tokens": 2,
                }
            supported = "2031-01-02" in response
            value = 1.0 if supported else 0.0
            return {
                "value": value,
                "status": "scored",
                "details": {"profile": profile, "claims": []},
                "usage": {"logical_calls": 2, "input_tokens": 10, "output_tokens": 2},
                "logical_calls": 2,
                "input_tokens": 10,
                "output_tokens": 2,
            }

        return grade

    return factory


def run_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    kwargs = {
        "cases": make_cases(),
        "model": "test-model",
        "output_directory": tmp_path / "artifacts" / "calibration-run",
        "policy": EvaluationPolicy(),
        "input_fingerprints": {"cases": {"synthetic": "sha256:test"}},
        "profile_order": "alternating",
        "grader_factory": fake_grader_factory([]),
        "session_factory": FakeSession,
        "emit": lambda _message: None,
    }
    kwargs.update(overrides)
    return kwargs


def test_model_is_required_before_calibration_can_start():
    with pytest.raises(SystemExit) as error:
        parse_args([])

    assert error.value.code == 2


def test_profiles_cli_defaults_to_both_and_accepts_advanced_only():
    both = parse_args(["--model", "test-model"])
    advanced = parse_args(["--model", "test-model", "--profiles", "advanced"])

    assert both.profiles == ["default", "advanced"]
    assert advanced.profiles == ["advanced"]


def test_schedule_can_run_only_advanced():
    schedule = _schedule(make_cases(), "alternating", ("advanced",))

    assert [(case.case_id, profile) for case, profile in schedule] == [
        ("SYN-1", "advanced"),
        ("SYN-2", "advanced"),
        ("HIST-1", "advanced"),
    ]


def test_advanced_only_run_freezes_profile_and_does_not_call_default(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    kwargs = run_kwargs(
        tmp_path,
        profiles=("advanced",),
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory(calls),
    )

    summary = run_calibration(**kwargs)
    manifest = json.loads((kwargs["output_directory"] / "manifest.json").read_text())

    assert calls == [("advanced", "실제 날짜는 2031-01-02입니다.")]
    assert tuple(summary["profiles"]) == ("advanced",)
    assert manifest["profiles"] == ["advanced"]
    assert _checkpoint_path(kwargs["output_directory"], "SYN-1", "advanced").exists()
    assert not _checkpoint_path(kwargs["output_directory"], "SYN-1", "default").exists()


def test_resume_rejects_changed_profile_selection(tmp_path: Path):
    kwargs = run_kwargs(
        tmp_path,
        profiles=("advanced",),
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory([]),
    )
    run_calibration(**kwargs)

    with pytest.raises(ValueError, match="입력·모델·평가 설정"):
        run_calibration(
            **{
                **kwargs,
                "profiles": ("default", "advanced"),
                "resume": True,
            }
        )


def test_first_probe_gate_pauses_single_advanced_profile_when_unavailable(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    kwargs = run_kwargs(
        tmp_path,
        profiles=("advanced",),
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory(calls, unavailable=True),
    )

    summary = run_calibration(**kwargs)
    manifest = json.loads((kwargs["output_directory"] / "manifest.json").read_text())

    assert calls == [("advanced", "실제 날짜는 2031-01-02입니다.")]
    assert manifest["status"] == "paused_first_case_unavailable"
    assert manifest["pause_reason"] == "selected_profiles_unavailable_on_first_case"
    assert tuple(summary["profiles"]) == ("advanced",)


def test_cli_loader_combines_12_synthetic_and_8_unlabeled_local_cases(tmp_path: Path):
    audit_directory = tmp_path / "audit"
    audit_directory.mkdir()
    rows = [
        {
            "question_id": case_id,
            "question": f"{case_id} 질문",
            "answer": f"{case_id} 답변",
            "reason": "",
            "contexts": [{"source_id": f"{case_id}-P1", "text": "검수용 원문"}],
        }
        for case_id in HISTORICAL_CASE_IDS
    ]
    (audit_directory / "blind-review-test.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    cases, fingerprints = _load_cli_cases(
        Namespace(cases=DEFAULT_CASES, historical_dir=audit_directory)
    )

    assert len(cases) == 20
    assert sum(case.expected_supported is not None for case in cases) == 12
    assert sum(case.expected_supported is None for case in cases) == 8
    assert fingerprints["cases_and_historical_audit"]


def test_profile_order_alternates_by_case():
    cases = make_cases()

    schedule = _schedule(cases, "alternating")

    assert [(case.case_id, profile) for case, profile in schedule] == [
        ("SYN-1", "default"),
        ("SYN-1", "advanced"),
        ("SYN-2", "advanced"),
        ("SYN-2", "default"),
        ("HIST-1", "default"),
        ("HIST-1", "advanced"),
    ]


def test_probe_and_resume_reuse_checkpoints_and_pin_one_session_effort(tmp_path: Path):
    cases = make_cases()
    calls: list[tuple[str, str]] = []
    sessions: list[FakeSession] = []

    def session_factory(policy: EvaluationPolicy) -> FakeSession:
        session = FakeSession(policy)
        sessions.append(session)
        return session

    kwargs = run_kwargs(
        tmp_path,
        cases=cases,
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory(calls),
        session_factory=session_factory,
    )
    first = run_calibration(**kwargs)

    assert len(calls) == 2
    assert sessions[0].closed
    assert first["profiles"]["default"]["completed_count"] == 1
    assert first["profiles"]["advanced"]["completed_count"] == 1

    resumed = run_calibration(
        **{
            **kwargs,
            "selected_ids": None,
            "resume": True,
        }
    )

    assert len(calls) == 6
    assert sessions[1].restored == ["max"]
    assert sessions[1].closed
    assert resumed["profiles"]["default"]["completed_count"] == 3
    assert resumed["profiles"]["advanced"]["completed_count"] == 3
    assert resumed["profiles"]["default"]["labeled_synthetic_metrics"]["scored_count"] == 2
    assert resumed["profiles"]["default"]["historical_diagnostic"]["expected_labels"] is None
    assert resumed["profiles"]["default"]["labeled_synthetic_metrics"]["unlabeled_count"] == 0
    assert resumed["profiles"]["default"]["usage"]["logical_calls"] == 6
    assert (kwargs["output_directory"] / "results.json").is_file()


def test_resume_rejects_changed_input_fingerprint(tmp_path: Path):
    kwargs = run_kwargs(
        tmp_path,
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory([]),
    )
    run_calibration(**kwargs)

    with pytest.raises(ValueError, match="다릅니다"):
        run_calibration(
            **{
                **kwargs,
                "resume": True,
                "input_fingerprints": {"cases": {"synthetic": "sha256:changed"}},
            }
        )


def test_incomplete_case_is_not_retried_without_explicit_override(tmp_path: Path):
    kwargs = run_kwargs(tmp_path, selected_ids=["SYN-1"])
    run_calibration(**kwargs)
    output = kwargs["output_directory"]
    case = make_cases()[1]
    from app.evaluation.checkpoints import atomic_json, digest_json
    from app.evaluation.calibrate import _case_mapping, _started_path

    manifest = json.loads((output / "manifest.json").read_text())
    atomic_json(
        _started_path(output, case.case_id, "advanced"),
        {
            "run_signature": manifest["run_signature"],
            "case_sha256": digest_json(_case_mapping(case)),
        },
    )

    with pytest.raises(ValueError, match="중복 과금"):
        run_calibration(**{**kwargs, "selected_ids": None, "resume": True})


def test_first_case_unavailable_for_both_profiles_pauses_additional_calls(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    kwargs = run_kwargs(
        tmp_path,
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory(calls, unavailable=True),
    )

    summary = run_calibration(**kwargs)
    manifest = json.loads((kwargs["output_directory"] / "manifest.json").read_text())

    assert len(calls) == 2
    assert manifest["status"] == "paused_first_case_unavailable"
    assert summary["profiles"]["default"]["labeled_synthetic_metrics"]["unavailable_count"] == 1


def test_started_marker_requires_explicit_retry_after_manifest_matches(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    kwargs = run_kwargs(
        tmp_path,
        selected_ids=["SYN-1"],
        grader_factory=fake_grader_factory(calls),
    )
    run_calibration(**kwargs)
    output = kwargs["output_directory"]
    # Model an interrupted second case/profile after its request started but before its result was saved.
    case = make_cases()[1]
    from app.evaluation.checkpoints import atomic_json, digest_json
    from app.evaluation.calibrate import _case_mapping, _started_path

    manifest = json.loads((output / "manifest.json").read_text())
    atomic_json(
        _started_path(output, case.case_id, "advanced"),
        {
            "run_signature": manifest["run_signature"],
            "case_sha256": digest_json(_case_mapping(case)),
        },
    )
    run_calibration(
        **{
            **kwargs,
            "selected_ids": None,
            "resume": True,
            "retry_incomplete": True,
        }
    )

    assert len(calls) == 6
    assert not _started_path(output, case.case_id, "advanced").exists()
    assert _checkpoint_path(output, case.case_id, "advanced").exists()


def test_raw_checkpoint_contains_case_and_model_details_locally(tmp_path: Path):
    kwargs = run_kwargs(tmp_path, selected_ids=["SYN-1"])
    run_calibration(**kwargs)

    output = kwargs["output_directory"]
    path = _checkpoint_path(output, "SYN-1", "default")
    saved = json.loads(path.read_text())

    assert saved["case"]["question"] == "가상 문서의 실제 날짜는?"
    assert saved["result"]["details"]["profile"] == "default"
    assert saved["session"]["effective_reasoning_effort"] == "max"
