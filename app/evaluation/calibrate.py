"""Run resumable, local Faithfulness calibration for the synthetic and audit cases.

Examples::

    uv run --locked --extra eval python -m app.evaluation.calibrate \
        --model gpt-6-luna --run-id faithfulness-v1 --ids SYN-FTH-01
    uv run --locked --extra eval python -m app.evaluation.calibrate \
        --model gpt-6-luna --run-id faithfulness-v1 --resume

The first command evaluates the selected profile(s) for the requested IDs. It pins
the session to max, or to xhigh only after an explicit account/permission denial.
The second command reuses matching case/profile checkpoints for the frozen profile set.
Raw inputs and evaluator outputs are written only under the local artifacts tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.evaluation.calibration import (
    DEFAULT_LOCAL_AUDIT_DIRECTORY,
    CalibrationCase,
    load_local_historical_cases,
    load_synthetic_cases,
    summarize_verdicts,
)
from app.evaluation.checkpoints import (
    atomic_json,
    digest_json,
    evaluation_lock,
    evaluator_files,
    fingerprint_files,
)
from app.evaluation.faithfulness import make_faithfulness
from app.evaluation.model_runtime import EvaluationPolicy, EvaluationSession
from shared.settings import PROJECT_ROOT


DEFAULT_CASES = PROJECT_ROOT / "tests/fixtures/faithfulness_calibration_synthetic.json"
DEFAULT_ARTIFACTS_DIRECTORY = PROJECT_ROOT / "artifacts/u_10o0o/faithfulness-calibration"
RUN_SCHEMA_VERSION = 1
PROFILES = ("default", "advanced")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="평가에 사용할 모델. 유료 호출 전 명시 필요")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="12개 합성 보정 사례 JSON")
    parser.add_argument(
        "--historical-dir", type=Path, default=DEFAULT_LOCAL_AUDIT_DIRECTORY,
        help="라벨을 추가하지 않고 불러올 로컬 과거 감사 자료 디렉터리",
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIRECTORY,
        help="로컬 artifacts 하위 출력 폴더",
    )
    parser.add_argument("--run-id", default=_new_run_id(), help="재개에 사용할 실행 ID")
    parser.add_argument(
        "--profile-order", choices=("alternating", "default-first", "advanced-first"),
        default="alternating", help="사례별 평가 프로필 실행 순서",
    )
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES, default=list(PROFILES),
        help="실행할 프로필. 새 프롬프트는 --profiles advanced로 해당 조건만 다시 평가",
    )
    parser.add_argument(
        "--ids", nargs="+", help="첫 연결 점검 등 이번 호출에서 실행할 사례 ID. 전체 입력은 고정됨",
    )
    parser.add_argument("--resume", action="store_true", help="기존 실행의 일치하는 checkpoint부터 재개")
    parser.add_argument(
        "--retry-incomplete", action="store_true",
        help="완료 checkpoint 없이 시작 표시만 남은 호출을 다시 실행할 수 있음(중복 과금 가능)",
    )
    parser.add_argument(
        "--continue-after-unavailable", action="store_true",
        help="첫 사례 두 프로필이 모두 unavailable인 뒤 원인을 확인하고 계속 진행",
    )
    return parser.parse_args(argv)


def _new_run_id() -> str:
    return "faithfulness-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "to_mapping"):
        return _jsonable(value.to_mapping())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _case_mapping(case: CalibrationCase) -> dict[str, Any]:
    return _jsonable(asdict(case))


def _schedule(
    cases: Sequence[CalibrationCase],
    order: str,
    profiles: Sequence[str] = PROFILES,
) -> list[tuple[CalibrationCase, str]]:
    selected_profiles = tuple(profile for profile in PROFILES if profile in set(profiles))
    schedule: list[tuple[CalibrationCase, str]] = []
    for index, case in enumerate(cases):
        if order == "alternating":
            case_profiles = (
                selected_profiles
                if index % 2 == 0
                else tuple(reversed(selected_profiles))
            )
        elif order == "default-first":
            case_profiles = selected_profiles
        elif order == "advanced-first":
            case_profiles = tuple(reversed(selected_profiles))
        else:
            raise ValueError(f"지원하지 않는 profile 순서: {order}")
        schedule.extend((case, profile) for profile in case_profiles)
    return schedule


def _case_profile_key(case_id: str, profile: str) -> str:
    return hashlib.sha256(f"{case_id}\0{profile}".encode("utf-8")).hexdigest()


def _checkpoint_path(output_directory: Path, case_id: str, profile: str) -> Path:
    return output_directory / "checkpoints" / f"{_case_profile_key(case_id, profile)}.json"


def _started_path(output_directory: Path, case_id: str, profile: str) -> Path:
    return output_directory / "started" / f"{_case_profile_key(case_id, profile)}.json"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"실행 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"실행 파일 형식이 잘못됐습니다: {path}")
    return value


def _validate_artifact_directory(path: Path) -> Path:
    resolved = path.resolve()
    artifacts_root = (PROJECT_ROOT / "artifacts").resolve()
    if not resolved.is_relative_to(artifacts_root):
        raise ValueError("원문 입력과 평가 출력을 저장소의 artifacts/ 안에만 저장할 수 있습니다.")
    return resolved


def _input_fingerprints(
    cases_path: Path,
    historical_directory: Path,
    *,
    evaluator_path_list: Sequence[Path] | None = None,
) -> dict[str, Any]:
    cases_path = cases_path.resolve()
    historical_directory = historical_directory.resolve()
    historical_paths = sorted(historical_directory.glob("blind-review-*.jsonl"))
    if not historical_paths:
        raise ValueError("로컬 감사 디렉터리에 blind-review-*.jsonl 파일이 없습니다.")
    paths = [cases_path, *historical_paths]
    case_and_audit = fingerprint_files(paths)
    evaluator_hashes = fingerprint_files(
        evaluator_path_list if evaluator_path_list is not None else evaluator_files()
    )
    dependency_paths = [
        path for path in (PROJECT_ROOT / "pyproject.toml", PROJECT_ROOT / "uv.lock")
        if path.is_file()
    ]
    return {
        "cases_and_historical_audit": case_and_audit,
        "evaluator": evaluator_hashes,
        "dependency_lock": fingerprint_files(dependency_paths),
    }


def _signature(
    *,
    cases: Sequence[CalibrationCase],
    model: str,
    policy: EvaluationPolicy,
    profile_order: str,
    input_fingerprints: Mapping[str, Any],
    profiles: Sequence[str] = PROFILES,
) -> dict[str, Any]:
    frozen = {
        "schema_version": RUN_SCHEMA_VERSION,
        "model": model,
        "profiles": list(profiles),
        "profile_order": profile_order,
        "policy": policy.to_mapping(),
        "input_fingerprints": _jsonable(input_fingerprints),
        "cases": [_case_mapping(case) for case in cases],
    }
    return {"run_signature": digest_json(frozen), "frozen_inputs": frozen}


def _read_checkpoints(
    output_directory: Path,
    cases: Sequence[CalibrationCase],
    signature: str,
    profiles: Sequence[str] = PROFILES,
) -> dict[tuple[str, str], dict[str, Any]]:
    by_id = {case.case_id: case for case in cases}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    checkpoint_directory = output_directory / "checkpoints"
    if not checkpoint_directory.exists():
        return rows
    expected_paths = {
        _checkpoint_path(output_directory, case.case_id, profile): (case, profile)
        for case in cases
        for profile in profiles
    }
    for path in checkpoint_directory.glob("*.json"):
        identity = expected_paths.get(path)
        if identity is None:
            raise ValueError(f"실행 입력에 없는 checkpoint가 있습니다: {path.name}")
        case, profile = identity
        saved = _load_json(path)
        if (
            saved.get("run_signature") != signature
            or saved.get("case_id") != case.case_id
            or saved.get("profile") != profile
            or saved.get("case_sha256") != digest_json(_case_mapping(by_id[case.case_id]))
        ):
            raise ValueError(f"case/profile checkpoint가 현재 입력과 다릅니다: {case.case_id}/{profile}")
        rows[(case.case_id, profile)] = saved
    return rows


def _all_effective_efforts(
    checkpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> set[str]:
    efforts: set[str] = set()
    saved_effort = manifest.get("effective_reasoning_effort")
    if saved_effort:
        efforts.add(saved_effort)
    for checkpoint in checkpoints.values():
        session = checkpoint.get("session")
        if isinstance(session, Mapping) and session.get("effective_reasoning_effort"):
            efforts.add(str(session["effective_reasoning_effort"]))
    return efforts


def _predicted_supported(value: Any, status: str) -> bool | None:
    if status != "scored" or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    # Faithfulness is claim-average; the calibration label means every factual
    # claim is grounded, so binary support requires a perfect score.
    return math.isclose(float(value), 1.0, rel_tol=0.0, abs_tol=1e-12)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _usage_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    token_fields = ("input_tokens", "output_tokens", "reasoning_tokens")
    totals: dict[str, Any] = {field: 0 for field in token_fields}
    totals["logical_calls"] = 0
    totals["known_token_calls"] = {field: 0 for field in token_fields}
    for row in rows:
        result = row.get("result") or {}
        usage = result.get("usage") or {}
        calls = usage.get("logical_calls")
        if isinstance(calls, int):
            totals["logical_calls"] += calls
        for field in token_fields:
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[field] += value
                totals["known_token_calls"][field] += 1
    for field in token_fields:
        if totals["known_token_calls"][field] == 0:
            totals[field] = None
    return totals


def _summarize(
    cases: Sequence[CalibrationCase],
    checkpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    model: str,
    policy: EvaluationPolicy,
    session_manifest: Mapping[str, Any],
    selected_profiles: Sequence[str] = PROFILES,
) -> dict[str, Any]:
    profile_summaries: dict[str, Any] = {}
    for profile in selected_profiles:
        rows = [
            checkpoints[(case.case_id, profile)]
            for case in cases
            if (case.case_id, profile) in checkpoints
        ]
        values = [
            float(row["result"]["value"])
            for row in rows
            if row.get("result", {}).get("status") == "scored"
            and isinstance(row.get("result", {}).get("value"), (int, float))
            and not isinstance(row.get("result", {}).get("value"), bool)
        ]
        verdicts = {
            row["case_id"]: _predicted_supported(
                row.get("result", {}).get("value"), row.get("result", {}).get("status", "")
            )
            for row in rows
        }
        synthetic = [
            case for case in cases
            if case.expected_supported is not None and (case.case_id, profile) in checkpoints
        ]
        historical = [case for case in cases if case.expected_supported is None]
        historical_rows = [
            checkpoints[(case.case_id, profile)]
            for case in historical
            if (case.case_id, profile) in checkpoints
        ]
        category_summary: dict[str, Any] = {}
        for category in sorted({case.category for case in cases}):
            category_cases = [case for case in cases if case.category == category]
            category_rows = [
                checkpoints[(case.case_id, profile)]
                for case in category_cases
                if (case.case_id, profile) in checkpoints
            ]
            category_verdicts = {
                row["case_id"]: _predicted_supported(
                    row.get("result", {}).get("value"), row.get("result", {}).get("status", "")
                )
                for row in category_rows
            }
            category_labeled = [
                case for case in category_cases
                if case.expected_supported is not None and (case.case_id, profile) in checkpoints
            ]
            category_values = [
                float(row["result"]["value"])
                for row in category_rows
                if row.get("result", {}).get("status") == "scored"
                and isinstance(row.get("result", {}).get("value"), (int, float))
                and not isinstance(row.get("result", {}).get("value"), bool)
            ]
            category_summary[category] = {
                "case_count": len(category_cases),
                "completed_count": len(category_rows),
                "mean_faithfulness": _mean(category_values),
                "labeled_metrics": summarize_verdicts(category_labeled, category_verdicts),
            }

        status_counts = Counter(row.get("result", {}).get("status", "unknown") for row in rows)
        history_values = [
            float(row["result"]["value"])
            for row in historical_rows
            if row.get("result", {}).get("status") == "scored"
            and isinstance(row.get("result", {}).get("value"), (int, float))
            and not isinstance(row.get("result", {}).get("value"), bool)
        ]
        profile_summaries[profile] = {
            "completed_count": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "mean_faithfulness_all_scored": _mean(values),
            "labeled_synthetic_metrics": summarize_verdicts(synthetic, verdicts),
            "historical_diagnostic": {
                "count": len(historical),
                "completed_count": len(historical_rows),
                "expected_labels": None,
                "mean_faithfulness_descriptive_only": _mean(history_values),
            },
            "categories": category_summary,
            "usage": _usage_totals(rows),
        }

    case_rows = []
    for case in cases:
        for profile in selected_profiles:
            saved = checkpoints.get((case.case_id, profile))
            if saved is None:
                continue
            result = saved.get("result", {})
            case_rows.append({
                "case_id": case.case_id,
                "category": case.category,
                "source": case.source,
                "profile": profile,
                "expected_supported": case.expected_supported,
                "value": result.get("value"),
                "status": result.get("status"),
                "predicted_supported": _predicted_supported(
                    result.get("value"), result.get("status", "")
                ),
                "logical_calls": result.get("logical_calls"),
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
            })
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "model": model,
        "requested_policy": policy.to_mapping(),
        "effective_policy": _jsonable(session_manifest),
        "binary_support_rule": "scored Faithfulness value == 1.0 means all claims supported",
        "historical_labels": "unlabeled; descriptive score only; excluded from confusion matrices",
        "profiles": profile_summaries,
        "cases": case_rows,
    }


def _is_first_case_unavailable(
    checkpoints: Mapping[tuple[str, str], Mapping[str, Any]],
    first_case: CalibrationCase,
    profiles: Sequence[str] = PROFILES,
) -> bool:
    pair = [checkpoints.get((first_case.case_id, profile)) for profile in profiles]
    return all(
        row is not None and row.get("result", {}).get("status") == "unavailable"
        for row in pair
    )


def run_calibration(
    cases: Sequence[CalibrationCase],
    *,
    model: str,
    output_directory: Path,
    policy: EvaluationPolicy,
    input_fingerprints: Mapping[str, Any],
    profile_order: str = "alternating",
    profiles: Sequence[str] = PROFILES,
    selected_ids: Sequence[str] | None = None,
    resume: bool = False,
    retry_incomplete: bool = False,
    continue_after_unavailable: bool = False,
    grader_factory: Callable[..., Any] = make_faithfulness,
    session_factory: Callable[..., Any] = EvaluationSession,
    emit: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Evaluate selected case/profile pairs in strict sequence with local checkpoints."""
    if not model.strip():
        raise ValueError("모델을 명시해야 합니다.")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("보정 표본의 case_id가 중복됩니다.")
    if not cases:
        raise ValueError("평가할 보정 표본이 없습니다.")
    if not profiles or len(set(profiles)) != len(profiles):
        raise ValueError("실행할 프로필은 중복 없이 하나 이상 선택해야 합니다.")
    unknown_profiles = set(profiles) - set(PROFILES)
    if unknown_profiles:
        raise ValueError(f"지원하지 않는 프로필입니다: {sorted(unknown_profiles)}")
    selected_profiles = tuple(profile for profile in PROFILES if profile in set(profiles))
    if selected_ids:
        unknown = set(selected_ids) - {case.case_id for case in cases}
        if unknown:
            raise ValueError(f"입력 세트에 없는 ID입니다: {sorted(unknown)}")

    output_directory = output_directory.resolve()
    manifest_path = output_directory / "manifest.json"
    output_directory.mkdir(parents=True, exist_ok=True)
    signature_info = _signature(
        cases=cases,
        model=model,
        policy=policy,
        profile_order=profile_order,
        input_fingerprints=input_fingerprints,
        profiles=selected_profiles,
    )
    signature = signature_info["run_signature"]

    with evaluation_lock(output_directory, "faithfulness-calibration"):
        if manifest_path.exists():
            if not resume:
                raise ValueError("실행 경로가 이미 있습니다. 같은 입력이면 --resume을 사용하세요.")
            manifest = _load_json(manifest_path)
            if manifest.get("run_signature") != signature:
                raise ValueError("입력·모델·평가 설정이 기존 실행과 다릅니다. 새 --run-id를 사용하세요.")
        else:
            if resume:
                raise ValueError("--resume 실행에 필요한 manifest.json이 없습니다.")
            manifest = {
                "schema_version": RUN_SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_signature": signature,
                **signature_info["frozen_inputs"],
                "status": "in_progress",
                "effective_reasoning_effort": None,
                "fallback_used": False,
            }
            atomic_json(manifest_path, manifest)

        manifest["profiles"] = list(selected_profiles)
        checkpoints = _read_checkpoints(output_directory, cases, signature, selected_profiles)
        for case in cases:
            for profile in selected_profiles:
                if (case.case_id, profile) in checkpoints:
                    _started_path(output_directory, case.case_id, profile).unlink(missing_ok=True)
        efforts = _all_effective_efforts(checkpoints, manifest)
        if len(efforts) > 1:
            raise ValueError("저장된 checkpoint의 reasoning effort가 서로 다릅니다.")
        if efforts:
            pinned_effort = next(iter(efforts))
            if manifest.get("effective_reasoning_effort") not in (None, pinned_effort):
                raise ValueError("manifest와 case checkpoint의 effective effort가 다릅니다.")
        else:
            pinned_effort = None

        schedule = _schedule(cases, profile_order, selected_profiles)
        chosen_ids = set(selected_ids or [case.case_id for case in cases])
        schedule = [(case, profile) for case, profile in schedule if case.case_id in chosen_ids]
        if not schedule:
            raise ValueError("선택한 ID에 실행 항목이 없습니다.")

        first_case = schedule[0][0]
        if manifest.get("status") == "paused_first_case_unavailable" and not continue_after_unavailable:
            raise ValueError(
                "첫 사례에서 선택 프로필 결과가 모두 unavailable입니다. 원인을 확인한 뒤 "
                "--continue-after-unavailable를 명시해야 이어갈 수 있습니다."
            )

        active_tasks = [(case, profile) for case, profile in schedule if (case.case_id, profile) not in checkpoints]
        for case, profile in active_tasks:
            started = _started_path(output_directory, case.case_id, profile)
            checkpoint = _checkpoint_path(output_directory, case.case_id, profile)
            if checkpoint.exists():
                continue
            if started.exists():
                start_row = _load_json(started)
                if start_row.get("run_signature") != signature or start_row.get("case_sha256") != digest_json(_case_mapping(case)):
                    raise ValueError(f"불완전 표시가 현재 입력과 다릅니다: {case.case_id}/{profile}")
                if not retry_incomplete:
                    raise ValueError(
                        f"{case.case_id}/{profile}는 호출 후 checkpoint가 없습니다. "
                        "이미 API 요청이 처리됐을 수 있으므로 중복 과금 가능성을 확인하고 "
                        "--retry-incomplete를 명시하세요."
                    )

        session = session_factory(policy)
        try:
            if pinned_effort:
                session.restore_effective_effort(pinned_effort)

            for index, (case, profile) in enumerate(schedule, start=1):
                if (case.case_id, profile) in checkpoints:
                    continue
                started = _started_path(output_directory, case.case_id, profile)
                checkpoint_path = _checkpoint_path(output_directory, case.case_id, profile)
                if started.exists():
                    started.unlink()
                atomic_json(
                    started,
                    {
                        "run_signature": signature,
                        "case_id": case.case_id,
                        "profile": profile,
                        "case_sha256": digest_json(_case_mapping(case)),
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                case_contexts = [
                    f"[{context['source_id']}]\n{context['text']}"
                    for context in case.contexts
                ]
                grader = grader_factory(model, session=session, profile=profile, policy=policy)
                try:
                    result = _jsonable(grader(case.question, case.response, case_contexts))
                except Exception as exc:
                    if getattr(exc, "abort_evaluation", False):
                        manifest.update({
                            "status": "aborted_reasoning_policy_error",
                            "effective_reasoning_effort": getattr(session, "effective_reasoning_effort", None),
                            "safe_error_type": type(exc).__name__,
                        })
                        atomic_json(manifest_path, manifest)
                        atomic_json(
                            output_directory / "summary.json",
                            _summarize(
                                cases,
                                checkpoints,
                                model=model,
                                policy=policy,
                                session_manifest=session.to_manifest(),
                                selected_profiles=selected_profiles,
                            ),
                        )
                        raise
                    raise
                row = {
                    "run_signature": signature,
                    "case_id": case.case_id,
                    "case_sha256": digest_json(_case_mapping(case)),
                    "case": _case_mapping(case),
                    "profile": profile,
                    "result": result,
                    "session": _jsonable(session.to_manifest()),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                atomic_json(checkpoint_path, row)
                started.unlink(missing_ok=True)
                checkpoints[(case.case_id, profile)] = row

                effective = getattr(session, "effective_reasoning_effort", None)
                manifest.update({
                    "status": "in_progress",
                    "effective_reasoning_effort": effective,
                    "fallback_used": bool(session.to_manifest().get("fallback_used")),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                atomic_json(manifest_path, manifest)
                atomic_json(
                    output_directory / "summary.json",
                    _summarize(
                        cases,
                        checkpoints,
                        model=model,
                        policy=policy,
                        session_manifest=session.to_manifest(),
                        selected_profiles=selected_profiles,
                    ),
                )
                value = result.get("value")
                usage = result.get("usage") or {}
                selected_completed = sum(
                    (scheduled_case.case_id, scheduled_profile) in checkpoints
                    for scheduled_case, scheduled_profile in schedule
                )
                emit(
                    f"[{selected_completed}/{len(schedule)}] "
                    f"{case.case_id} {profile}: {result.get('status')} value={value} "
                    f"calls={usage.get('logical_calls')} tokens="
                    f"{usage.get('input_tokens')}/{usage.get('output_tokens')} "
                    f"effort={effective or 'not-pinned'}"
                )

                if case.case_id == first_case.case_id and _is_first_case_unavailable(
                    checkpoints, first_case, selected_profiles
                ):
                    manifest.update({
                        "status": "paused_first_case_unavailable",
                        "pause_reason": "selected_profiles_unavailable_on_first_case",
                    })
                    atomic_json(manifest_path, manifest)
                    emit(
                        "첫 사례의 선택 프로필 결과가 모두 unavailable여서 추가 API 호출을 멈췄습니다. "
                        "원인을 확인하기 전까지 자동으로 다음 사례를 평가하지 않습니다."
                    )
                    break

            manifest["status"] = (
                "complete"
                if all((case.case_id, profile) in checkpoints for case in cases for profile in selected_profiles)
                else manifest.get("status", "in_progress")
            )
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["effective_reasoning_effort"] = getattr(session, "effective_reasoning_effort", None) or pinned_effort
            manifest["session_policy"] = _jsonable(session.to_manifest())
            atomic_json(manifest_path, manifest)

            summary = _summarize(
                cases,
                checkpoints,
                model=model,
                policy=policy,
                session_manifest=session.to_manifest(),
                selected_profiles=selected_profiles,
            )
            atomic_json(output_directory / "summary.json", summary)
            if manifest["status"] == "complete":
                result_rows = [
                    checkpoints[(case.case_id, profile)]
                    for case in cases
                    for profile in selected_profiles
                ]
                atomic_json(output_directory / "results.json", result_rows)
            _print_summary(summary, emit)
            return summary
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()


def _print_summary(summary: Mapping[str, Any], emit: Callable[[str], None]) -> None:
    emit("Faithfulness calibration summary (historical cases are unlabeled)")
    for profile, row in summary["profiles"].items():
        metrics = row["labeled_synthetic_metrics"]
        usage = row["usage"]
        emit(
            f"{profile}: synthetic scored={metrics['scored_count']}/12 "
            f"accuracy={metrics['accuracy']} precision={metrics['precision']} "
            f"recall={metrics['recall']} historical descriptive mean="
            f"{row['historical_diagnostic']['mean_faithfulness_descriptive_only']} "
            f"calls={usage['logical_calls']} tokens={usage['input_tokens']}/{usage['output_tokens']}"
        )
        for category, category_row in row["categories"].items():
            category_metrics = category_row["labeled_metrics"]
            emit(
                f"  {category}: labeled n={category_metrics['scored_count']} "
                f"accuracy={category_metrics['accuracy']} "
                f"mean={category_row['mean_faithfulness']}"
            )


def _load_cli_cases(args: argparse.Namespace) -> tuple[tuple[CalibrationCase, ...], dict[str, Any]]:
    cases_path = args.cases.resolve()
    audit_directory = args.historical_dir.resolve()
    synthetic = load_synthetic_cases(cases_path)
    historical = load_local_historical_cases(audit_directory)
    cases = (*synthetic, *historical)
    fingerprints = _input_fingerprints(cases_path, audit_directory)
    if len(cases) != 20:
        raise ValueError("보정 실행은 합성 12건과 무라벨 과거 감사 8건, 총 20건이어야 합니다.")
    return cases, fingerprints


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if not _SAFE_RUN_ID.fullmatch(args.run_id):
        raise SystemExit("--run-id에는 영문·숫자·점·밑줄·하이픈만 사용할 수 있습니다.")
    try:
        output_base = _validate_artifact_directory(args.artifacts_dir)
        output_directory = _validate_artifact_directory(output_base / args.run_id)
        cases, fingerprints = _load_cli_cases(args)
        selected = set(args.ids or [case.case_id for case in cases])
        unknown = selected - {case.case_id for case in cases}
        if unknown:
            raise ValueError(f"입력 세트에 없는 ID입니다: {sorted(unknown)}")
        if args.retry_incomplete and not args.resume:
            raise ValueError("--retry-incomplete에는 --resume이 필요합니다.")
        if args.continue_after_unavailable and not args.resume:
            raise ValueError("--continue-after-unavailable에는 --resume이 필요합니다.")

        from app.evaluation.scripts._common import load_env

        load_env()
        policy = EvaluationPolicy()
        summary = run_calibration(
            cases,
            model=args.model,
            output_directory=output_directory,
            policy=policy,
            input_fingerprints=fingerprints,
            profile_order=args.profile_order,
            profiles=args.profiles,
            selected_ids=args.ids,
            resume=args.resume,
            retry_incomplete=args.retry_incomplete,
            continue_after_unavailable=args.continue_after_unavailable,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"실행 기록: {output_directory}")
    expected_pairs = len(cases) * len(args.profiles)
    print(f"완료 사례/프로필: {len(summary['cases'])}/{expected_pairs}")


if __name__ == "__main__":
    main()
