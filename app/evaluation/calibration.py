"""Offline loading and scoring helpers for Faithfulness calibration cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


HISTORICAL_CASE_IDS = tuple(
    f"AS-ADD{number:02}" for number in (2, 7, 11, 15, 21, 25, 27, 29)
)
DEFAULT_LOCAL_AUDIT_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "artifacts/u_10o0o/advanced-faithfulness-audit-20261113"
)


@dataclass(frozen=True)
class CalibrationCase:
    case_id: str
    category: str
    question: str
    response: str
    contexts: tuple[Mapping[str, str], ...]
    expected_supported: bool | None
    source: str


def load_synthetic_cases(path: Path) -> tuple[CalibrationCase, ...]:
    """Load checked-in synthetic examples containing no project source excerpts."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"보정 표본 파일을 읽을 수 없습니다: {path}") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise ValueError("보정 표본 파일에 cases 배열이 필요합니다.")
    result = tuple(_parse_case(row, source="synthetic") for row in cases)
    ids = [case.case_id for case in result]
    if len(ids) != len(set(ids)):
        raise ValueError("synthetic 보정 표본에 중복 id가 있습니다.")
    if any(case.expected_supported is None for case in result):
        raise ValueError("synthetic 보정 표본에는 expected_supported label이 필요합니다.")
    if len(result) != 12:
        raise ValueError("synthetic 보정 표본은 12개여야 합니다.")
    return result


def load_local_historical_cases(
    audit_directory: Path = DEFAULT_LOCAL_AUDIT_DIRECTORY,
) -> tuple[CalibrationCase, ...]:
    """Load only the eight prior audit examples from local, ignored audit files.

    These records are diagnostics, not human-adjudicated labels; no expected
    verdict is assigned here. Raw answers and contexts stay in the local folder.
    """
    if not audit_directory.is_dir():
        raise ValueError(f"로컬 Faithfulness 감사 자료가 없습니다: {audit_directory}")
    requested = set(HISTORICAL_CASE_IDS)
    found: dict[str, CalibrationCase] = {}
    for path in sorted(audit_directory.glob("blind-review-*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"로컬 감사 자료를 읽을 수 없습니다: {path.name}") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"로컬 감사 자료 JSON이 잘못됐습니다: {path.name}") from exc
            case_id = row.get("question_id")
            if case_id not in requested:
                continue
            if case_id in found:
                raise ValueError(f"로컬 감사 자료에서 id가 중복됩니다: {case_id}")
            contexts = row.get("contexts")
            response_parts = [
                row.get("answer") or "",
                row.get("reason") or "",
            ]
            response = "\n\n".join(part for part in response_parts if part.strip())
            found[case_id] = CalibrationCase(
                case_id=case_id,
                category="historical_diagnostic",
                question=str(row.get("question") or ""),
                response=response,
                contexts=tuple(
                    {"source_id": str(item.get("source_id", "")), "text": str(item.get("text", ""))}
                    for item in contexts or []
                    if isinstance(item, dict)
                ),
                expected_supported=None,
                source=path.name,
            )
    missing = sorted(requested - found.keys())
    if missing:
        raise ValueError(f"로컬 감사 자료에 필요한 historical id가 없습니다: {missing}")
    return tuple(found[case_id] for case_id in HISTORICAL_CASE_IDS)


def _parse_case(row: Any, *, source: str) -> CalibrationCase:
    if not isinstance(row, dict):
        raise ValueError("보정 표본 항목은 객체여야 합니다.")
    required = ("id", "category", "question", "response", "contexts", "expected_supported")
    if any(key not in row for key in required):
        raise ValueError("보정 표본에 필수 필드가 없습니다.")
    contexts = row["contexts"]
    if not isinstance(contexts, list) or not contexts:
        raise ValueError(f"{row['id']}에 하나 이상의 context가 필요합니다.")
    if any(
        not isinstance(context, dict)
        or not isinstance(context.get("source_id"), str)
        or not isinstance(context.get("text"), str)
        for context in contexts
    ):
        raise ValueError(f"{row['id']}의 context 형식이 잘못됐습니다.")
    if not isinstance(row["expected_supported"], bool):
        raise ValueError(f"{row['id']}의 expected_supported는 boolean이어야 합니다.")
    return CalibrationCase(
        case_id=str(row["id"]),
        category=str(row["category"]),
        question=str(row["question"]),
        response=str(row["response"]),
        contexts=tuple(
            {"source_id": context["source_id"], "text": context["text"]}
            for context in contexts
        ),
        expected_supported=row["expected_supported"],
        source=source,
    )


def summarize_verdicts(
    cases: Sequence[CalibrationCase],
    verdicts: Mapping[str, bool | None],
) -> dict[str, Any]:
    """Compare judged verdicts with labels, excluding unlabeled/failed cases."""
    tp = tn = fp = fn = unavailable = unlabeled = 0
    for case in cases:
        expected = case.expected_supported
        if expected is None:
            unlabeled += 1
            continue
        actual = verdicts.get(case.case_id)
        if actual is None:
            unavailable += 1
        elif not isinstance(actual, bool):
            raise ValueError(f"{case.case_id}의 verdict는 boolean 또는 None이어야 합니다.")
        elif expected and actual:
            tp += 1
        elif not expected and not actual:
            tn += 1
        elif not expected and actual:
            fp += 1
        else:
            fn += 1
    scored = tp + tn + fp + fn
    return {
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "scored_count": scored,
        "unavailable_count": unavailable,
        "unlabeled_count": unlabeled,
        "accuracy": (tp + tn) / scored if scored else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }
