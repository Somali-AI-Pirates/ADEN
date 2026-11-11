"""Offline scoring for the real Advanced question-routing benchmark.

References are loaded only by :func:`score_luna`, which the runner calls after
persisting its retrieval traces. The module never reads question text into its
output and never invokes a model or retrieval component.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

AMBIGUOUS_ACTIONS = frozenset({"clarify", "needs_clarification"})
OFFICIAL_SPLITS = frozenset({"development", "additional"})


def _items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        lists = [
            value
            for key, value in data.items()
            if isinstance(value, list) and not key.startswith("_")
        ]
        if len(lists) != 1:
            raise ValueError(f"{path}: 참고 항목 목록을 하나 찾을 수 없습니다.")
        data = lists[0]
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"{path}: 항목은 객체 목록이어야 합니다.")
    return data


def _source_key(citation: Mapping[str, Any]) -> tuple[str, int] | None:
    book_id = citation.get("book_id")
    if not isinstance(book_id, str) or not book_id:
        file_name = citation.get("file_name")
        if isinstance(file_name, str) and file_name:
            book_id = Path(file_name).stem
    page = citation.get("page")
    if not isinstance(book_id, str) or not book_id or type(page) is not int:
        return None
    return book_id, page


def _walk_citations(value: Any):
    if isinstance(value, Mapping):
        if _source_key(value) is not None:
            yield value
        else:
            for item in value.values():
                yield from _walk_citations(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk_citations(item)


def _gold(reference: Mapping[str, Any], lookup: Any) -> dict[str, Any]:
    action = reference.get("expected_action")
    ambiguous = (
        action in AMBIGUOUS_ACTIONS or reference.get("question_type") == "clarify"
    )
    cited_pairs: set[tuple[str, str]] = set()
    requirements = reference.get("evidence_requirements", ())
    if isinstance(requirements, Sequence) and not isinstance(
        requirements, (str, bytes)
    ):
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            for citation in _walk_citations(requirement.get("primary", ())):
                pair = lookup.page_roles.get(_source_key(citation))
                if (
                    isinstance(pair, Sequence)
                    and not isinstance(pair, (str, bytes))
                    and len(pair) == 2
                    and all(isinstance(value, str) and value for value in pair)
                ):
                    cited_pairs.add((pair[0], pair[1]))

    case_keys = reference.get("case_keys", ())
    if isinstance(case_keys, str):
        case_keys = [case_keys]
    cases = (
        {value for value in case_keys if isinstance(value, str) and value}
        if isinstance(case_keys, Sequence)
        else set()
    )
    if not cases:
        cases = {case for case, _ in cited_pairs}

    # A clarify reference can list the plausible cases, but none is confirmed.
    if ambiguous:
        cases.clear()
        cited_pairs.clear()
        label = "clarify"
    elif cases:
        label = "target"
    else:
        label = "no_target"
    return {"cases": cases, "roles": cited_pairs, "label": label}


def _verified_prediction(
    trace: Mapping[str, Any], lookup: Any
) -> tuple[set[str], set[tuple[str, str]]]:
    targets = trace.get("targets", ())
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
        raise ValueError("trace.targets must be a list of verified targets")
    known_cases = set(lookup.cases)
    known_roles = set(lookup.page_roles.values())
    cases: set[str] = set()
    roles: set[tuple[str, str]] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            raise ValueError("trace target must be an object")
        case = target.get("case")
        if not isinstance(case, str) or case not in known_cases:
            raise ValueError("trace contains an unverified case target")
        cases.add(case)
        requested_roles = target.get("roles", ())
        if not isinstance(requested_roles, Sequence) or isinstance(
            requested_roles, (str, bytes)
        ):
            raise ValueError("verified target roles must be a list")
        for role in requested_roles:
            if not isinstance(role, str) or (case, role) not in known_roles:
                raise ValueError("trace contains an unverified case-role target")
            roles.add((case, role))
    return cases, roles


def _safe_model_target_count(trace: Mapping[str, Any]) -> tuple[int, int]:
    """Count validated provider proposals before rules fallback, not confirmations."""
    targets = trace.get("model_targets", ())
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
        return 0, 0
    roles = sum(
        len(target.get("roles", ()))
        for target in targets
        if isinstance(target, Mapping)
        and isinstance(target.get("roles", ()), Sequence)
        and not isinstance(target.get("roles", ()), (str, bytes))
    )
    return len(targets), roles


def _ratio(numerator: int | float, denominator: int | float) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _sum_facts(rows: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("required_facts", 0) > 0]
    required = sum(row["required_facts"] for row in eligible)
    hits = sum(row.get(f"{stage}_facts", 0) for row in eligible)
    complete = sum(row.get(f"{stage}_complete", 0) == 1 for row in eligible)
    return {
        "question_denominator": len(eligible),
        "required_facts": required,
        "facts_hit": hits,
        "fact_recall": _ratio(hits, required),
        "all_facts_complete": _ratio(complete, len(eligible)),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    target_rows = [row for row in rows if row["gold_label"] == "target"]
    ambiguous_rows = [row for row in rows if row["gold_label"] == "clarify"]
    no_target_rows = [row for row in rows if row["gold_label"] == "no_target"]
    role_rows = [row for row in rows if row["gold_roles"]]
    target_tp = sum(len(row["predicted_cases"] & row["gold_cases"]) for row in rows)
    target_fp = sum(len(row["predicted_cases"] - row["gold_cases"]) for row in rows)
    target_fn = sum(len(row["gold_cases"] - row["predicted_cases"]) for row in rows)
    role_tp = sum(len(row["predicted_roles"] & row["gold_roles"]) for row in rows)
    role_fp = sum(len(row["predicted_roles"] - row["gold_roles"]) for row in rows)
    role_fn = sum(len(row["gold_roles"] - row["predicted_roles"]) for row in rows)
    target_exact = sum(row["predicted_cases"] == row["gold_cases"] for row in rows)
    role_exact = sum(row["predicted_roles"] == row["gold_roles"] for row in rows)
    auto_questions = sum(bool(row["predicted_cases"]) for row in rows)
    auto_cases = sum(len(row["predicted_cases"]) for row in rows)
    wrong_confirm_questions = sum(
        bool(row["predicted_cases"] - row["gold_cases"]) for row in rows
    )
    no_target_confirmed = sum(bool(row["predicted_cases"]) for row in no_target_rows)
    ambiguous_confirmed = sum(bool(row["predicted_cases"]) for row in ambiguous_rows)
    return {
        "questions": len(rows),
        "target_labeled_questions": len(target_rows),
        "role_labeled_questions": len(role_rows),
        "clarify_questions": len(ambiguous_rows),
        "no_target_questions": len(no_target_rows),
        "target": {
            "true_positive": target_tp,
            "false_positive": target_fp,
            "false_negative": target_fn,
            "precision": _ratio(target_tp, target_tp + target_fp),
            "recall": _ratio(target_tp, target_tp + target_fn),
            "exact": _ratio(target_exact, len(rows)),
            "exact_on_target_questions": _ratio(
                sum(row["predicted_cases"] == row["gold_cases"] for row in target_rows),
                len(target_rows),
            ),
        },
        "role": {
            "true_positive": role_tp,
            "false_positive": role_fp,
            "false_negative": role_fn,
            "precision": _ratio(role_tp, role_tp + role_fp),
            "recall": _ratio(role_tp, role_tp + role_fn),
            "exact": _ratio(role_exact, len(rows)),
            "exact_on_role_questions": _ratio(
                sum(row["predicted_roles"] == row["gold_roles"] for row in role_rows),
                len(role_rows),
            ),
        },
        "wrong_confirmation": {
            "questions_with_wrong_case": _ratio(
                wrong_confirm_questions, auto_questions
            ),
            "wrong_case_predictions": _ratio(target_fp, auto_cases),
            "no_target": _ratio(no_target_confirmed, len(no_target_rows)),
            "clarify": _ratio(ambiguous_confirmed, len(ambiguous_rows)),
        },
        "partial_unresolved": {
            "count": sum(row.get("status") == "partial" for row in rows),
            "denominator": len(rows),
        },
        "rules_fallback_count": sum(
            row.get("used_rules_fallback", False) for row in rows
        ),
        "validated_model_suggestions": {
            "targets": sum(row.get("model_target_count", 0) for row in rows),
            "roles": sum(row.get("model_role_count", 0) for row in rows),
        },
        "facts": {
            "candidate": _sum_facts(rows, "candidate"),
            "final": _sum_facts(rows, "final"),
        },
        "macro_page_recall": {
            stage: _ratio(sum(values), len(values))
            for stage, field in (
                ("candidate", "page_recall_at_20"),
                ("final", "page_recall_at_8"),
            )
            for values in [[row[field] for row in rows if field in row]]
        },
        "provider": {
            "calls": sum(row.get("provider_called", False) for row in rows),
            "latency_ms": {
                "count": sum(
                    row.get("telemetry", {}).get("latency_ms") is not None
                    for row in rows
                ),
                "p50": _percentile(
                    [
                        float(row["telemetry"]["latency_ms"])
                        for row in rows
                        if row.get("telemetry", {}).get("latency_ms") is not None
                    ],
                    0.5,
                ),
                "p95": _percentile(
                    [
                        float(row["telemetry"]["latency_ms"])
                        for row in rows
                        if row.get("telemetry", {}).get("latency_ms") is not None
                    ],
                    0.95,
                ),
            },
            "input_tokens": sum(
                row.get("telemetry", {}).get("input_tokens", 0) or 0 for row in rows
            ),
            "output_tokens": sum(
                row.get("telemetry", {}).get("output_tokens", 0) or 0 for row in rows
            ),
            "reasoning_tokens": sum(
                row.get("telemetry", {}).get("reasoning_tokens", 0) or 0 for row in rows
            ),
        },
        "decision_and_pack_ms": {
            "count": sum(row.get("decision_and_pack_ms") is not None for row in rows),
            "p50": _percentile(
                [
                    float(row["decision_and_pack_ms"])
                    for row in rows
                    if row.get("decision_and_pack_ms") is not None
                ],
                0.5,
            ),
            "p95": _percentile(
                [
                    float(row["decision_and_pack_ms"])
                    for row in rows
                    if row.get("decision_and_pack_ms") is not None
                ],
                0.95,
            ),
        },
    }


def _category(question: Mapping[str, Any], reference: Mapping[str, Any]) -> str:
    split = question["split"]
    if split in OFFICIAL_SPLITS:
        return "official"
    if split == "scenarios":
        return f"S{reference['scenario']:02}"
    variant = reference.get("variant")
    return f"{split}/{variant}" if variant is not None else split


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def score_luna(
    output,
    questions,
    files,
    retrievals,
    traces,
    lookup,
    *,
    comparison_arm="luna",
    retrieval_scorer=None,
) -> dict[str, Any]:
    """Score verified routing output and packed evidence after traces are saved.

    ``files`` follows ``advanced_experiment_eval.score``: split to a
    ``(questions_path, references_path)`` pair. ``retrievals`` and ``traces``
    are keyed by ``(question_id, arm)``; arms are expected to include ``rules``
    and ``luna``. Only post-validation ``trace.targets`` count as predictions.
    Raw ``model_targets`` are retained as counts for diagnosis, never as
    confirmed selections.
    """
    from experimental.u_10o0o.advanced_experiment_eval import score_one

    output = Path(output)
    if not output.is_dir():
        raise ValueError("평가 결과 디렉터리가 먼저 만들어져야 합니다.")
    references = {
        split: {row["id"]: row for row in _items(paths[1])}
        for split, paths in files.items()
    }
    arms = tuple(dict.fromkeys(arm for _, arm in traces))
    if not {"rules", comparison_arm} <= set(arms):
        raise ValueError(f"비교에는 rules와 {comparison_arm} trace가 필요합니다.")
    rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {
        "split": defaultdict(lambda: defaultdict(list)),
        "category": defaultdict(lambda: defaultdict(list)),
        "overall": defaultdict(lambda: defaultdict(list)),
    }
    for question in questions:
        qid, split = question["id"], question["split"]
        reference = references[split][qid]
        gold = _gold(reference, lookup)
        per_arm: dict[str, dict[str, Any]] = {}
        for arm in arms:
            trace = traces[qid, arm]
            predicted_cases, predicted_roles = _verified_prediction(trace, lookup)
            retrieval_score = (retrieval_scorer or score_one)(
                reference, retrievals[qid, arm]
            )
            model_target_count, model_role_count = _safe_model_target_count(trace)
            row = {
                **retrieval_score,
                "status": trace.get("status"),
                "provider_called": bool(trace.get("provider_called", False)),
                "telemetry": trace.get("telemetry")
                if isinstance(trace.get("telemetry"), Mapping)
                else {},
                "decision_and_pack_ms": trace.get("decision_and_pack_ms"),
                "used_rules_fallback": bool(trace.get("used_rules_fallback", False)),
                "model_target_count": model_target_count,
                "model_role_count": model_role_count,
                "predicted_cases": predicted_cases,
                "predicted_roles": predicted_roles,
                "gold_cases": gold["cases"],
                "gold_roles": gold["roles"],
            }
            per_arm[arm] = row
        category = _category(question, reference)
        arms_output = {
            arm: {
                key: value
                for key, value in metrics.items()
                if key
                not in {
                    "predicted_cases",
                    "predicted_roles",
                    "gold_cases",
                    "gold_roles",
                }
            }
            for arm, metrics in per_arm.items()
        }
        row_output = {
            "id": qid,
            "split": split,
            "category": category,
            "gold_label": gold["label"],
            "gold_target_count": len(gold["cases"]),
            "gold_role_count": len(gold["roles"]),
            "arms": arms_output,
        }
        for arm, metrics in per_arm.items():
            case_tp = len(metrics["predicted_cases"] & metrics["gold_cases"])
            case_fp = len(metrics["predicted_cases"] - metrics["gold_cases"])
            case_fn = len(metrics["gold_cases"] - metrics["predicted_cases"])
            role_tp = len(metrics["predicted_roles"] & metrics["gold_roles"])
            role_fp = len(metrics["predicted_roles"] - metrics["gold_roles"])
            role_fn = len(metrics["gold_roles"] - metrics["predicted_roles"])
            arms_output[arm].update(
                target={
                    "true_positive": case_tp,
                    "false_positive": case_fp,
                    "false_negative": case_fn,
                    "exact": metrics["predicted_cases"] == metrics["gold_cases"],
                    "exact_on_target_question": (
                        metrics["predicted_cases"] == metrics["gold_cases"]
                        if gold["label"] == "target"
                        else None
                    ),
                    "matches_gold_targets": metrics["predicted_cases"]
                    == metrics["gold_cases"],
                },
                role={
                    "true_positive": role_tp,
                    "false_positive": role_fp,
                    "false_negative": role_fn,
                    "exact": metrics["predicted_roles"] == metrics["gold_roles"],
                    "exact_on_role_question": (
                        metrics["predicted_roles"] == metrics["gold_roles"]
                        if gold["roles"]
                        else None
                    ),
                    "matches_gold_roles": metrics["predicted_roles"]
                    == metrics["gold_roles"],
                },
                wrong_confirmation={
                    "automatic_confirmation": bool(metrics["predicted_cases"]),
                    "wrong_case_count": case_fp,
                    "no_target_false_confirmation": gold["label"] == "no_target"
                    and bool(metrics["predicted_cases"]),
                    "clarify_false_confirmation": gold["label"] == "clarify"
                    and bool(metrics["predicted_cases"]),
                },
            )
        if (
            "final_complete" in per_arm["rules"]
            and "final_complete" in per_arm[comparison_arm]
        ):
            before = int(per_arm["rules"]["final_complete"])
            after = int(per_arm[comparison_arm]["final_complete"])
            row_output["final_fact_change"] = (
                "gain" if after > before else "loss" if after < before else "same"
            )
            row_output["final_fact_hits_delta"] = per_arm[comparison_arm].get(
                "final_facts", 0
            ) - per_arm["rules"].get("final_facts", 0)
        rows.append(row_output)
        for group_kind, group_key in (
            ("split", split),
            ("category", category),
            ("overall", "all"),
        ):
            for arm in arms:
                grouped[group_kind][group_key][arm].append(
                    {"gold_label": gold["label"], **per_arm[arm]}
                )

    # Build arm-specific aggregates and paired gains/losses without exposing text.
    summaries = {
        group_kind: {
            group_name: {
                arm: _summarize(grouped[group_kind][group_name][arm]) for arm in arms
            }
            for group_name in sorted(grouped[group_kind])
        }
        for group_kind in ("overall", "split", "category")
    }
    comparable = [row for row in rows if "final_fact_change" in row]
    summaries["paired_final_facts"] = {
        "denominator": len(comparable),
        "gains": sum(row["final_fact_change"] == "gain" for row in comparable),
        "losses": sum(row["final_fact_change"] == "loss" for row in comparable),
        "unchanged": sum(row["final_fact_change"] == "same" for row in comparable),
        "fact_hit_delta": sum(
            row.get("final_fact_hits_delta", 0) for row in comparable
        ),
    }
    for group_kind, output_key in (
        ("split", "paired_by_split"),
        ("category", "paired_by_category"),
    ):
        summaries[output_key] = {
            group_name: {
                "denominator": len(members),
                "gains": sum(row.get("final_fact_change") == "gain" for row in members),
                "losses": sum(
                    row.get("final_fact_change") == "loss" for row in members
                ),
                "unchanged": sum(
                    row.get("final_fact_change") == "same" for row in members
                ),
                "fact_hit_delta": sum(
                    row.get("final_fact_hits_delta", 0) for row in members
                ),
            }
            for group_name in sorted(grouped[group_kind])
            for members in [
                [
                    row
                    for row in rows
                    if row["split" if group_kind == "split" else "category"]
                    == group_name
                    and "final_fact_change" in row
                ]
            ]
        }
    _write_jsonl(output / "luna-scores.jsonl", rows)
    return summaries
