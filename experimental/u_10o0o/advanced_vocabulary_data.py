"""Bind frozen vocabulary scenarios to source-verified Advanced HG facts."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from string import Formatter

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import normalized
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import CONTEXT_TOKENS, RECORDS_DIR
from experimental.u_10o0o.advanced_usage_blind import (
    FACTS,
    HG_FIELDS,
    SPECS,
    Fact,
    hg_fact,
    load_inputs,
    make_reference,
    prior_evaluation,
    reference_locations,
    source_evidence,
)
from experimental.u_10o0o.advanced_target_benchmark import fingerprint
from experimental.u_10o0o.advanced_workflow_eval import (
    additional_exclusions,
    storage_amount_matches_record,
)
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

SCENARIOS = RECORDS_DIR / "advanced-vocabulary-20261109/scenarios.json"
OUTPUT = PROJECT_ROOT / "artifacts/u_10o0o/advanced-vocabulary-20261109-data"
SEED = "advanced-vocabulary-20261109-v1"
EXTRA_REFERENCE_FILES = (
    PROJECT_ROOT
    / "artifacts/u_10o0o/advanced-workflow-20261109-data-checked/references.json",
    PROJECT_ROOT
    / "artifacts/u_10o0o/advanced-pair-holdout-20261109-data/references.json",
)
ALLOWED_SLOTS = {"shipper", "cargo", "spec", "spec_spoken", "spec_feet"}
FACT_KEYS = {*HG_FIELDS, "rate"}

# These are different operational controls. Equal values make the contrast
# uninformative even when the source rows happen to be separate.
INTERCHANGEABLE_CONTROLS = (
    frozenset(("scheduled", "returned")),
    frozenset(("charged", "received")),
)


def _artifact_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("질문 원문과 근거는 로컬 artifacts 아래에만 저장합니다.")
    if resolved.exists():
        raise FileExistsError(f"출력 폴더가 이미 있습니다: {resolved}")
    return resolved


def _scenario_rows(path: Path) -> list[dict]:
    rows = json.loads(path.read_bytes())
    if not isinstance(rows, list) or len(rows) != 36:
        raise ValueError("시나리오 파일은 고정된 36개 항목 목록이어야 합니다.")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("각 시나리오는 JSON 객체여야 합니다.")
        qid = row.get("id")
        if not isinstance(qid, str) or not re.fullmatch(r"V\d{2}", qid) or qid in ids:
            raise ValueError("시나리오 ID는 중복 없는 V01 형식이어야 합니다.")
        ids.add(qid)
        if row.get("type") not in {"spec", "record", "combined"}:
            raise ValueError(
                f"{qid}: type은 spec, record, combined 중 하나여야 합니다."
            )
        for key in ("source", "plain", "contrast"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{qid}: {key} 질문이 비어 있습니다.")
            _template_slots(value, qid)
        for key in ("requests", "contrast_requests"):
            values = row.get(key)
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(value, str) or value not in FACT_KEYS
                    for value in values
                )
                or len(set(values)) != len(values)
            ):
                raise ValueError(
                    f"{qid}: {key}에는 중복 없는 유효한 HG 사실 코드가 필요합니다."
                )
        if row["requests"] == row["contrast_requests"]:
            raise ValueError(f"{qid}: 대조 질문은 다른 근거를 요청해야 합니다.")
    if ids != {f"V{i:02d}" for i in range(1, 37)}:
        raise ValueError("시나리오 ID는 V01부터 V36까지 빠짐없이 있어야 합니다.")
    return rows


def _template_slots(template: str, qid: str) -> set[str]:
    slots = set()
    try:
        for _, name, format_spec, conversion in Formatter().parse(template):
            if name is None:
                continue
            if name not in ALLOWED_SLOTS or format_spec or conversion:
                raise ValueError(f"{qid}: 지원하지 않는 템플릿 슬롯입니다.")
            slots.add(name)
    except ValueError as exc:
        if str(exc).startswith(f"{qid}:"):
            raise
        raise ValueError(f"{qid}: 질문 템플릿을 해석할 수 없습니다.") from exc
    return slots


def _reserved_inputs():
    prior_pages, prior_cases, prior_questions = prior_evaluation()
    workflow_pages, workflow_cases = additional_exclusions()
    references = []
    for path in EXTRA_REFERENCE_FILES:
        if not path.is_file():
            raise FileNotFoundError(f"예약 대상 평가 자료가 없습니다: {path}")
        references.extend(load_references(path).values())
    extra_pages = set().union(*(reference_locations(row) for row in references))
    extra_cases = {case for row in references for case in row.get("case_keys", ())}
    all_pages = prior_pages | workflow_pages | extra_pages
    all_cases = prior_cases | workflow_cases | extra_cases

    question_files = (
        EXTRA_REFERENCE_FILES[0].with_name("questions.json"),
        EXTRA_REFERENCE_FILES[1].with_name("questions.json"),
    )
    for path in question_files:
        if path.is_file():
            prior_questions.update(
                normalized(row["question"]) for row in load_questions(path)
            )
    detail = {
        "prior_evaluation": {
            "reserved_cases": len(prior_cases),
            "reserved_pages": len(prior_pages),
        },
        "workflow_evaluation": {
            "reserved_cases": len(workflow_cases),
            "reserved_pages": len(workflow_pages),
        },
        "workflow_checked": {
            "reserved_cases": len(extra_cases),
            "reserved_pages": len(extra_pages),
            "references": len(references),
        },
    }
    return all_pages, all_cases, prior_questions, detail


def _fact_data(case, code, typed, records, rates, sources, restored, cache):
    key = (case, code)
    if key in cache:
        return cache[key]
    if code == "rate":
        row = rates.get(case)
        fact = (
            Fact(
                "원문 첫 유료 구간 요율",
                row["raw_value"],
                records[row["source"]["record_id"]],
            )
            if row
            else None
        )
    elif code == "balance":
        case_facts = typed[case]
        balance = case_facts.get(("balance_amount", None))
        total = case_facts.get(("receipt_total", None))
        received = case_facts.get(("received_amount", None))
        rows = (balance, total, received)
        if not all(
            row and row["state"] == "reported" and row.get("value") is not None
            for row in rows
        ):
            cache[key] = None
            return None
        if len({row.get("currency") for row in rows}) != 1:
            cache[key] = None
            return None
        try:
            balance_value, total_value, received_value = (
                Decimal(str(row["value"])) for row in rows
            )
        except (InvalidOperation, ValueError):
            cache[key] = None
            return None
        if (
            total_value <= received_value
            or balance_value != total_value - received_value
        ):
            cache[key] = None
            return None
        fact = hg_fact(typed, records, case, code)
        if fact is not None:
            fact = Fact(
                fact.ask,
                fact.value,
                fact.record,
                (
                    records[total["source"]["record_id"]],
                    records[received["source"]["record_id"]],
                ),
            )
    else:
        fact = hg_fact(typed, records, case, code)
    if fact is None:
        cache[key] = None
        return None
    if code == "charged" and not storage_amount_matches_record(fact.record):
        cache[key] = None
        return None
    citations = source_evidence(fact, sources, restored)
    result = (fact, citations) if citations else None
    cache[key] = result
    return result


def _signature(code, fact, citations):
    return (
        code,
        normalized(fact.value),
        tuple(
            (citation["book_id"], citation["page"], normalized(citation["quote"]))
            for citation in citations
        ),
    )


def _has_distinct_contrast(requests, contrast_requests, facts):
    left = [_signature(code, *facts[code]) for code in requests]
    right = [_signature(code, *facts[code]) for code in contrast_requests]
    if set(left) == set(right):
        return False
    values = {code: normalized(facts[code][0].value) for code in facts}
    for pair in INTERCHANGEABLE_CONTROLS:
        if pair <= set(requests) | set(contrast_requests):
            first, second = tuple(pair)
            if first in facts and second in facts and values[first] == values[second]:
                return False
    return True


def _render(template, profile):
    spec = profile["spec"]
    feet = re.match(r"\d+", spec)
    if not feet or spec not in SPECS:
        raise ValueError("HG 사건의 컨테이너 규격 표기를 확인할 수 없습니다.")
    values = {
        "shipper": profile["shipper"],
        "cargo": profile["cargo"],
        "spec": spec,
        "spec_spoken": f"{feet[0]}피트짜리 일반 컨테이너",
        "spec_feet": f"{feet[0]}피트",
    }
    return template.format_map(values)


def _build_reference(qid, scenario, case, variant, codes, facts, restored):
    selected = [facts[code] for code in codes]
    reference = make_reference(
        qid,
        {
            "family": "hg",
            "case_keys": [case],
            "action": "answer",
            "intent": f"vocabulary_{scenario['type']}",
            "style": variant,
            "facts": [fact for fact, _ in selected],
        },
        [citations for _, citations in selected],
    )
    all_citations = [cite for _, citations in selected for cite in citations]
    reference["evidence_groups"] = [
        [cite]
        for cite in {tuple(cite.items()): cite for cite in all_citations}.values()
    ]
    reference.update(
        pair_id=scenario["id"],
        variant=variant,
        scenario_type=scenario["type"],
        fact_keys=codes,
        question_type=scenario["type"],
        identifier_mode=variant,
    )
    pages = [restored[key] for key in sorted(reference_locations(reference))]
    context = packed_context([(page, 0.0) for page in pages])
    coverage = fact_coverage(reference, context)
    if coverage["all_facts"] != 1 or context.token_count > CONTEXT_TOKENS:
        return None
    return reference, context.token_count


def build_dataset(output: Path = OUTPUT, *, scenarios_path: Path = SCENARIOS) -> dict:
    """Create fixed source/plain/contrast questions with verified gold evidence."""
    output = _artifact_output(Path(output))
    scenarios_path = Path(scenarios_path).resolve()
    scenarios = _scenario_rows(scenarios_path)
    reserved_pages, reserved_cases, prior_questions, exclusion_detail = (
        _reserved_inputs()
    )
    restored, cases, display, sources, records, typed, *_ = load_inputs()
    role_fact_file = FACTS / "role_facts.jsonl"
    rates = {
        row["case_id"]: row
        for row in read_rows(role_fact_file)
        if row["fact"] == "first_rate_tier" and row["cost_kind"] == "storage"
    }

    eligible = {
        case
        for case, case_pages in cases.items()
        if case not in reserved_cases
        and not reserved_pages.intersection(location(page) for page in case_pages)
    }
    profile_keys = {
        case: tuple(
            normalized(display[case][key]) for key in ("shipper", "cargo", "spec")
        )
        for case in cases
    }
    key_counts = Counter(profile_keys.values())
    eligible = {case for case in eligible if key_counts[profile_keys[case]] == 1}

    fact_cache = {}
    used_cases = set()
    used_profile_keys = set()
    questions = []
    references = []
    token_counts = []
    exclusion_counts = Counter()
    chosen_rows = []
    all_question_keys = set(prior_questions)

    for scenario in scenarios:
        sid = scenario["id"]
        required_codes = list(
            dict.fromkeys([*scenario["requests"], *scenario["contrast_requests"]])
        )
        chosen = None
        candidates = sorted(
            eligible - used_cases,
            key=lambda case: sha256(f"{SEED}/{sid}/{case}".encode()).digest(),
        )
        for case in candidates:
            if profile_keys[case] in used_profile_keys:
                exclusion_counts["duplicate_profile_tuple"] += 1
                continue
            facts = {}
            missing = False
            for code in required_codes:
                value = _fact_data(
                    case,
                    code,
                    typed,
                    records,
                    rates,
                    sources,
                    restored,
                    fact_cache,
                )
                if value is None:
                    missing = True
                    exclusion_counts["fact_missing_or_unverified"] += 1
                    break
                facts[code] = value
            if missing:
                continue
            if not _has_distinct_contrast(
                scenario["requests"], scenario["contrast_requests"], facts
            ):
                exclusion_counts["degenerate_contrast"] += 1
                continue
            profile = display[case]
            rendered = {
                variant: _render(scenario[variant], profile)
                for variant in ("source", "plain", "contrast")
            }
            if any(not text.strip() for text in rendered.values()):
                exclusion_counts["empty_rendered_question"] += 1
                continue
            if len({normalized(text) for text in rendered.values()}) != 3:
                exclusion_counts["duplicate_variant_question"] += 1
                continue
            if any(normalized(text) in all_question_keys for text in rendered.values()):
                exclusion_counts["duplicate_question"] += 1
                continue

            built = {}
            for variant, codes in (
                ("source", scenario["requests"]),
                ("plain", scenario["requests"]),
                ("contrast", scenario["contrast_requests"]),
            ):
                qid = f"{sid}-{variant}"
                ref_result = _build_reference(
                    qid, scenario, case, variant, codes, facts, restored
                )
                if ref_result is None:
                    exclusion_counts["gold_context_does_not_fit"] += 1
                    built = {}
                    break
                built[variant] = (rendered[variant], *ref_result)
            if len(built) != 3:
                continue

            # Both phrasings describe the same event and must have identical gold.
            if (
                built["source"][1]["fact_keys"] != built["plain"][1]["fact_keys"]
                or built["source"][1]["evidence_requirements"]
                != built["plain"][1]["evidence_requirements"]
            ):
                raise AssertionError("원문식과 일상식 질문의 정답 근거가 다릅니다.")
            chosen = (case, profile_keys[case], built)
            break

        if chosen is None:
            raise ValueError(
                f"{sid}: 예약 제외·원문 검증·대조 조건을 만족하는 사건이 없습니다."
            )

        case, profile_key, built = chosen
        used_cases.add(case)
        used_profile_keys.add(profile_key)
        chosen_rows.append({"scenario_id": sid, "case": case})
        for variant in ("source", "plain", "contrast"):
            question, reference, tokens = built[variant]
            qid = f"{sid}-{variant}"
            questions.append({"id": qid, "question": question})
            references.append(reference)
            token_counts.append(tokens)
            all_question_keys.add(normalized(question))

    questions_path = output / "questions.json"
    references_path = output / "references.json"
    # Validate the same public loaders used by the evaluation pipeline.
    if len(questions) != 108 or len(references) != 108:
        raise AssertionError("질문·참고자료는 시나리오당 세 문항이어야 합니다.")
    output.mkdir(parents=True, exist_ok=False)
    write_json(questions_path, questions)
    write_json(references_path, references)
    loaded_questions = load_questions(questions_path)
    loaded_references = load_references(references_path)
    if len(loaded_questions) != len(loaded_references) or set(loaded_references) != {
        row["id"] for row in loaded_questions
    }:
        raise AssertionError("질문과 참고자료의 ID가 맞지 않습니다.")

    ref_by_variant = Counter(row["variant"] for row in references)
    ref_by_type = Counter(row["scenario_type"] for row in references)
    fact_distribution = Counter(
        code for reference in references for code in reference["fact_keys"]
    )
    unique_pages = set().union(*(reference_locations(row) for row in references))
    manifest = {
        "seed": SEED,
        "scenarios": fingerprint(scenarios_path),
        "questions": fingerprint(questions_path),
        "references": fingerprint(references_path),
        "pages": fingerprint(CACHE_PATH),
        "role_facts": fingerprint(role_fact_file),
        "case_links": fingerprint(
            Path(
                "artifacts/u_10o0o/advanced-curation/hg-case-sources-20261109/case_sources.jsonl"
            )
        ),
        "builder": fingerprint(Path(__file__).resolve()),
        "counts": {
            "scenarios": len(scenarios),
            "questions": len(questions),
            "questions_by_variant": dict(ref_by_variant),
            "questions_by_type": dict(Counter(row["type"] for row in scenarios)),
            "references_by_type": dict(ref_by_type),
            "selected_cases": len(used_cases),
            "unique_profile_tuples": len(used_profile_keys),
            "unique_gold_pages": len(unique_pages),
            "required_fact_instances": sum(len(row["fact_keys"]) for row in references),
            "fact_key_instances": dict(fact_distribution),
        },
        "token_budget": {
            "max_context_tokens": CONTEXT_TOKENS,
            "max_gold_context_tokens": max(token_counts),
            "mean_gold_context_tokens": sum(token_counts) / len(token_counts),
            "gold_contexts_verified": len(token_counts),
        },
        "reserved_evidence": exclusion_detail,
        "selection_exclusions": dict(exclusion_counts),
        "reserved_reference_fingerprints": [
            fingerprint(path) for path in EXTRA_REFERENCE_FILES
        ],
        "scenario_seeded_case_links": len(chosen_rows),
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS)
    args = parser.parse_args()
    print(
        json.dumps(
            build_dataset(args.output, scenarios_path=args.scenarios),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
