"""Bind frozen work scenarios to verified Advanced HG evidence and compare retrieval."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import torch

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, normalized
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MODELS,
    PEER_DATA,
    RECORDS_DIR,
    RERANKED_HEAD,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    RetrievalBenchmark,
    fingerprint,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_target_metrics import score_run
from experimental.u_10o0o.advanced_usage_blind import (
    EVAL,
    FACTS,
    Fact,
    hg_fact,
    load_inputs,
    make_reference,
    prior_evaluation,
    reference_locations,
    source_evidence,
)
from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index

SCENARIOS = RECORDS_DIR / "advanced-workflow-20261109/scenarios.json"
SEED = "advanced-workflow-20261109-v1"
REQUESTS = {
    "W01": (("free", "rate"),),
    "W02": (("gate", "paid"),),
    "W03": (("approval", "charged"),),
    "W04": (("charged", "received"),),
    "W05": (("scheduled", "returned"),),
    "W06": (("discharge", "gate"),),
    "W07": (("paid", "gate"),),
    "W08": (("gate", "charged"),),
    "W09": (("approval", "charged"),),
    "W10": (("gate", "charged", "received"),),
    "W11": (("free", "rate"), ("free", "rate")),
    "W12": (("gate", "charged"), ("gate", "charged")),
    "W13": (("paid", "charged"), ("gate", "paid", "charged")),
    "W14": (("gate", "charged", "received"), ("gate", "charged", "received")),
    "W15": (("approval",), ("received", "balance")),
    "W16": (("free", "gate", "charged"), ("free", "gate", "charged")),
    "W17": (("gate", "charged"), ("gate", "charged")),
    "W18": (("gate", "charged"), ("gate", "charged")),
    "W19": (("returned", "late_charge"),),
    "W20": (("returned", "late_charge"),),
}
ID_FIELDS = {
    ("W07", 0): ("shipper", "cargo", "spec"),
    ("W08", 0): ("shipper", "spec", "gate"),
    ("W09", 0): ("shipper", "cargo", "spec"),
    ("W10", 0): ("shipper", "cargo", "gate"),
    ("W13", 1): ("shipper", "cargo", "gate"),
    ("W14", 0): ("shipper", "cargo", "gate"),
    ("W14", 1): ("shipper", "cargo", "gate"),
}


def local_output(path):
    path = path.resolve()
    if not path.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError(
            "원문 자료를 저장하는 출력은 저장소 artifacts 아래를 사용하세요."
        )
    return path


def source_order(qid, side, case, seed=SEED):
    return sha256(f"{seed}/{qid}/{side}/{case}".encode()).digest()


def storage_amount_matches_record(record):
    fields = record["fields"]
    _, equals, result = fields["구간 계산"].rpartition("=")
    number = r"[+-]?\d[\d,]*(?:\.\d+)?"
    computed = re.findall(number, result)
    billed = re.findall(number, fields["확정 금액"])
    return (
        bool(equals)
        and len(computed) == len(billed) == 1
        and Decimal(computed[0].replace(",", "")) == Decimal(billed[0].replace(",", ""))
    )


def additional_exclusions():
    folder = EVAL / "advanced_usage_blind/local"
    files = [
        *(folder / f"{split}_references.json" for split in ("blind", "pilot")),
        *(
            PEER_DATA / f"{name}_adv.json"
            for name in ("false_premise", "synth", "synth_multi")
        ),
    ]
    refs = [row for path in files for row in load_references(path).values()]
    return (
        set().union(*(reference_locations(row) for row in refs)),
        {case for row in refs for case in row.get("case_keys", ())},
    )


def build_dataset(output, *, scenarios_path=SCENARIOS, seed=SEED, excluded=()):
    scenarios = json.loads(scenarios_path.read_bytes())
    if scenarios_path == SCENARIOS:
        requests, id_fields = REQUESTS, ID_FIELDS
        if [row["id"] for row in scenarios] != list(requests):
            raise ValueError("고정 질문과 근거 요청 표가 다릅니다.")
    else:
        requests = {row["id"]: row["requests"] for row in scenarios}
        id_fields = {
            (row["id"], side): tuple(fields)
            for row in scenarios
            for side, (fields, _) in enumerate(
                zip(row["identify"], row["requests"], strict=True)
            )
            if fields
        }
        if len(requests) != len(scenarios) or any(
            not 1 <= len(sides) <= 2 or any(not facts for facts in sides)
            for sides in requests.values()
        ):
            raise ValueError("고정 질문에는 사건별 필수 근거가 필요합니다.")
    restored, cases, display, sources, records, typed, *_ = load_inputs()
    file = FACTS / "role_facts.jsonl"
    rates = {
        row["case_id"]: row
        for row in read_rows(file)
        if row["fact"] == "first_rate_tier" and row["cost_kind"] == "storage"
    }
    reserved_pages, reserved_cases, previous_questions = prior_evaluation()
    extra_pages, extra_cases = additional_exclusions()
    reserved_pages.update(extra_pages)
    reserved_cases.update(extra_cases)
    for path in excluded:
        for ref in load_references(path).values():
            reserved_pages.update(reference_locations(ref))
            reserved_cases.update(ref.get("case_keys", ()))
    eligible = {
        case
        for case, pages in cases.items()
        if case not in reserved_cases
        and not reserved_pages.intersection(location(page) for page in pages)
    }
    attrs = {
        case: {**profile, "gate": hg_fact(typed, records, case, "gate").value}
        for case, profile in display.items()
    }
    patterns = set(id_fields.values()) | {("equipment",), ("shipper", "spec", "gate")}
    by_fields = {pattern: defaultdict(list) for pattern in patterns}

    def field_values(case, pattern):
        return tuple(normalized(attrs[case][field]) for field in pattern)

    for case in cases:
        for pattern in patterns:
            by_fields[pattern][field_values(case, pattern)].append(case)

    def facts_for(qid, side, case):
        facts = []
        for name in requests[qid][side]:
            if name == "rate":
                rate = rates[case]
                fact = Fact(
                    "원문 첫 유료 구간 요율",
                    rate["raw_value"],
                    records[rate["source"]["record_id"]],
                )
            else:
                states = ("reported",)
                if qid in {"W19", "W20"}:
                    states = {
                        "returned": ("receipt_missing",)
                        if qid == "W19"
                        else ("non_designated_yard_only",),
                        "late_charge": ("unconfirmed",),
                    }[name]
                fact = hg_fact(typed, records, case, name, states=states)
            if fact is None:
                return ()
            if name == "charged" and not storage_amount_matches_record(fact.record):
                return ()
            citations = source_evidence(fact, sources, restored)
            if not citations:
                return ()
            if len(requests[qid]) > 1:
                fact = Fact(
                    f"건 {'AB'[side]}의 {fact.ask}",
                    fact.value,
                    fact.record,
                    fact.support,
                )
            facts.append((fact, citations))
        return facts

    def choose(qid, side, template, unavailable, different_shipper=None):
        keys = [
            *([id_fields[qid, side]] if (qid, side) in id_fields else []),
            *([("equipment",)] if f"{{equipment_{'ab'[side]}}}" in template else []),
        ]
        for case in sorted(
            eligible - unavailable, key=lambda key: source_order(qid, side, key, seed)
        ):
            if different_shipper == normalized(attrs[case]["shipper"]):
                continue
            if any(len(by_fields[key][field_values(case, key)]) != 1 for key in keys):
                continue
            if facts := facts_for(qid, side, case):
                return case, facts
        raise ValueError(f"{qid}의 요청을 원문과 대조할 수 있는 사건이 없습니다.")

    def choose_ambiguous(qid, unavailable):
        groups = by_fields["shipper", "spec", "gate"]
        for group in sorted(
            groups.values(), key=lambda keys: source_order(qid, 0, min(keys), seed)
        ):
            if len(group) != 2 or not set(group) <= eligible - unavailable:
                continue
            left, right = sorted(group)
            if normalized(attrs[left]["cargo"]) == normalized(attrs[right]["cargo"]):
                continue
            a, b = facts_for(qid, 0, left), facts_for(qid, 1, right)
            if a and b and a[-1][0].value != b[-1][0].value:
                return [(left, a), (right, b)]
        raise ValueError(f"{qid}의 두 사건을 확인할 수 없습니다.")

    rows, used = [], set()
    order = sorted(scenarios, key=lambda row: (row["type"] != "clarify", row["id"]))
    for row in order:
        qid, kind, template = row["id"], row["type"], row["question"]
        if kind == "clarify":
            selected = choose_ambiguous(qid, used)
        else:
            first = choose(qid, 0, template, used)
            selected = [first]
            if len(requests[qid]) == 2:
                selected.append(
                    choose(
                        qid,
                        1,
                        template,
                        used | {first[0]},
                        normalized(attrs[first[0]]["shipper"]),
                    )
                )
        slots = {}
        for side, (case, _) in zip("ab", selected):
            profile = attrs[case]
            values = {
                field: profile[field]
                for field in ("equipment", "shipper", "cargo", "gate")
            }
            spec = profile["spec"]
            spec_phrase = f"{spec[:2]}피트짜리 일반 컨테이너"
            values.update(case=case, spec_phrase=spec_phrase)
            slots.update({f"{field}_{side}": value for field, value in values.items()})
        question = template.format_map(slots)
        if normalized(question) in previous_questions:
            raise ValueError(f"{qid}가 이전 질문과 동일합니다.")
        previous_questions.add(normalized(question))
        facts, evidence = zip(
            *(pair for _, pairs in selected for pair in pairs), strict=True
        )
        case_keys = [case for case, _ in selected]
        used.update(case_keys)
        action = kind if kind in {"clarify", "abstain"} else "answer"
        reference = make_reference(
            qid,
            {
                "family": "hg",
                "case_keys": case_keys,
                "action": action,
                "intent": kind,
                "style": kind,
                "facts": facts,
            },
            evidence,
        )
        # Each fact is required; citations within its bundle jointly establish it.
        reference["evidence_groups"] = [
            [citation]
            for citation in {
                tuple(e.items()): e for bundle in evidence for e in bundle
            }.values()
        ]
        gold_pages = [restored[key] for key in sorted(reference_locations(reference))]
        context = packed_context([(page, 0.0) for page in gold_pages])
        if fact_coverage(reference, context)["all_facts"] != 1:
            raise ValueError(f"{qid}의 정답 근거가 문맥 예산에서 일치하지 않습니다.")
        rows.append((qid, question, reference, context.token_count))

    rows.sort()
    output.mkdir(parents=True, exist_ok=False)
    question_path = output / "questions.json"
    reference_path = output / "references.json"
    write_json(
        question_path,
        [{"id": qid, "question": question} for qid, question, _, _ in rows],
    )
    write_json(reference_path, [ref for _, _, ref, _ in rows])
    manifest = {
        "seed": seed,
        "scenarios": fingerprint(scenarios_path),
        "questions": fingerprint(question_path),
        "references": fingerprint(reference_path),
        "pages": fingerprint(CACHE_PATH),
        "case_links": fingerprint(CASE_INDEX / "case_sources.jsonl"),
        "role_facts": fingerprint(file),
        "builder": fingerprint(Path(__file__)),
        "eligible_hg_cases": len(eligible),
        "selected_cases": len(used),
        "questions_by_type": dict(
            Counter(ref["question_type"] for _, _, ref, _ in rows)
        ),
        "gold_pages": len(
            set().union(*(reference_locations(ref) for _, _, ref, _ in rows))
        ),
        "gold_required_facts": sum(
            len(ref["evidence_requirements"]) for _, _, ref, _ in rows
        ),
        "max_gold_context_tokens": max(tokens for _, _, _, tokens in rows),
    }
    if excluded:
        manifest["excluded_references"] = [fingerprint(path) for path in excluded]
    write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))


def run_benchmark(data, output):
    manifest = json.loads((data / "manifest.json").read_bytes())
    questions_path, references_path = data / "questions.json", data / "references.json"
    for key, path in (
        ("questions", questions_path),
        ("references", references_path),
        ("pages", CACHE_PATH),
        ("case_links", CASE_INDEX / "case_sources.jsonl"),
        ("role_facts", FACTS / "role_facts.jsonl"),
    ):
        if manifest[key]["sha256"] != fingerprint(path)["sha256"]:
            raise ValueError("평가 입력 지문이 다릅니다.")
    questions = [{**row, "split": "workflow"} for row in load_questions(questions_path)]
    if len(questions) != len(REQUESTS):
        raise ValueError("업무 평가 질문 수가 다릅니다.")
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    started = perf_counter()
    log(f"업무 질문 검색 비교 시작 · {len(questions)}문항")
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    plans = {q["id"]: lookup.plan(q["question"]) for q in questions}
    benchmark = RetrievalBenchmark(pages, vectors, lookup)
    retrievals = benchmark.batch(questions, plans, log)
    latency = benchmark.latency(questions, plans, len(questions))
    save_rankings(output, retrievals)
    log("검색 종료 · 원본 근거 채점 시작")

    page_cases = {
        location(p): case for case, group in lookup.cases.items() for p in group
    }
    score = score_run(
        questions,
        retrievals,
        load_references(references_path),
        page_cases=page_cases,
    )
    write_rows(output / "questions.jsonl", score["records"])
    write_json(
        output / "results.json",
        {
            "dataset": manifest,
            "config": {
                "candidates": CANDIDATES,
                "reranked_head": RERANKED_HEAD,
                "final_k": FINAL_K,
                "context_tokens": CONTEXT_TOKENS,
                "reranker": {
                    "name": KURE,
                    "revision": MODELS[KURE],
                    "gpu": torch.cuda.get_device_name(),
                },
                "embedding": {
                    "name": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "vectors": fingerprint(VECTORS_PATH),
                },
                "benchmark": fingerprint(Path(__file__)),
                "generation_calls": 0,
            },
            "routes": dict(Counter(plan.status for plan in plans.values())),
            "dense_eligible": sum(
                bool(plan.candidate_queries) for plan in plans.values()
            ),
            "summary": score["summary"],
            "latency": latency,
            "elapsed_seconds": perf_counter() - started,
        },
    )
    log(f"평가 완료 · {perf_counter() - started:.1f}초")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build").add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--data", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = local_output(args.output)
    if args.command == "build":
        build_dataset(output)
    else:
        run_benchmark(args.data.resolve(), output)


if __name__ == "__main__":
    main()
