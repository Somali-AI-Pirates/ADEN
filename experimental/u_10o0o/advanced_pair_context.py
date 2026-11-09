"""Replay frozen Advanced page candidates with two-event context selection."""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from statistics import fmean

from app.evaluation.dataset import load_questions, load_references
from app.generation.context import build_context
from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import IDENTIFIER
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    RECORDS_DIR,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    USAGE,
    fingerprint,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    INVALID_CONSTRAINT,
    ROLE_CUES,
    _canonical_date,
    load_target_lookup,
    normalized,
)
from experimental.u_10o0o.advanced_target_metrics import score_run
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.prepare import CACHE_PATH

WORKFLOW = CACHE_PATH.parent / "advanced-workflow-20261109-data-checked"
WORKFLOW_RUN = CACHE_PATH.parent / "advanced-workflow-20261109-personal-checked"
BLIND_RUN = CACHE_PATH.parent / "advanced-target-retrieval-20261109-canonical"
WORKFLOW_RECORD = RECORDS_DIR / "advanced-workflow-20261109"
ARMS = ("baseline", "pair_context")

PAIR_CUE = re.compile(r"각각|건별|비교|대비|두\s*(?:건|사건|예약|장비|컨테이너)|양쪽")
SEPARATE = re.compile(r"건\s*[과와]|(?:[과와]|랑|하고)\s|,")
SHARED = re.compile(r"각각|건별")
ACTUAL_GATE = re.compile(r"(?:일)?(?:에)?\s*실제(?:로)?\s*(?:적입\s*)?반출")
OTHER_COUNT = re.compile(
    r"(?<!\w)(?:[3-9]|[1-9]\d+|세|네|다섯|여섯|일곱|여덟|아홉|열)"
    r"\s*(?:건|사건|예약|장비|컨테이너)"
)


def requested_roles(text):
    return tuple(role for role in ROLES if ROLE_CUES[role].search(text))


def unique_case(lookup, values):
    matches = [
        set(lookup.field_to_cases.get(field, {}).get(value, ()))
        for field, value in values.items()
    ]
    cases = set.intersection(*matches) if matches else set()
    return next(iter(cases)) if len(cases) == 1 else None


def explicit_pair(question, anchors, cases, lookup):
    profile = lookup._matched_profile_values(normalized(question))
    if DATE.search(question) or profile["shipper"] or profile["cargo"]:
        return {}
    left_roles = requested_roles(question[anchors[0].end() : anchors[1].start()])
    right_text = question[anchors[1].end() :]
    right_roles = requested_roles(right_text)
    if left_roles and right_roles and not SHARED.search(right_text):
        return dict(zip(cases, (left_roles, right_roles), strict=True))
    common = requested_roles(question)
    return dict.fromkeys(cases, common) if common else {}


def mixed_pair(question, anchor, first_case, lookup):
    text = normalized(question)
    names = list(lookup.profile_patterns[0].finditer(text))
    id_end = text.index(anchor.group().casefold()) + len(anchor.group())
    shared = list(SHARED.finditer(text))
    if (
        len(names) != 1
        or names[0].start() <= id_end
        or not SEPARATE.search(text[id_end : names[0].start()])
        or not shared
        or shared[-1].start() <= names[0].end()
    ):
        return {}
    subject = text[names[0].start() : shared[-1].start()]
    fields = lookup._matched_exact_fields(subject)
    wanted = {"shipper", "cargo", "actual_gate_out_date"}
    if INVALID_CONSTRAINT in fields or not wanted <= fields.keys():
        return {}
    if any(len(values) != 1 for values in fields.values()):
        return {}
    other = unique_case(
        lookup, {field: next(iter(values)) for field, values in fields.items()}
    )
    roles = requested_roles(text[shared[-1].end() :])
    if not other or other == first_case or not roles:
        return {}
    return {first_case: roles, other: tuple(dict.fromkeys((ROLES[1], *roles)))}


def indirect_pair(question, lookup):
    text = normalized(question)
    names = list(lookup.profile_patterns[0].finditer(text))
    dates = list(DATE.finditer(text))
    if (
        len(names) != 2
        or len(dates) != 2
        or names[-1].end() > dates[0].start()
        or not SHARED.search(text[names[-1].end() : dates[0].start()])
        or not re.fullmatch(
            r"\s*(?:,|와|과|및)\s*", text[dates[0].end() : dates[1].start()]
        )
        or not ACTUAL_GATE.match(text[dates[1].end() :])
    ):
        return {}
    roles = requested_roles(text[dates[1].end() :])
    targets = {}
    ends = (names[1].start(), dates[0].start())
    for name, end, day in zip(names, ends, dates, strict=True):
        cargo = lookup._matched_profile_values(text[name.start() : end])["cargo"]
        value = _canonical_date(day.group())
        if len(cargo) != 1 or not value:
            return {}
        case = unique_case(
            lookup,
            {
                "shipper": name.group(1),
                "cargo": next(iter(cargo)),
                "actual_gate_out_date": value,
            },
        )
        if not case or case in targets or not roles:
            return {}
        targets[case] = roles
    return targets


def pair_requirements(question, lookup):
    if EXCLUSION.search(question) or OTHER_COUNT.search(IDENTIFIER.sub("", question)):
        return {}
    if not PAIR_CUE.search(question) and question.count("건은") < 2:
        return {}
    anchors = list(IDENTIFIER.finditer(question))
    if not anchors:
        return indirect_pair(question, lookup)
    if len(anchors) > 2:
        return {}
    cases = []
    for anchor in anchors:
        key = anchor.group()
        matches = lookup.id_to_cases.get(key, ())
        if len(matches) != 1 or not {"case_id", "equipment_id"} & lookup.id_fields[key]:
            return {}
        cases.append(next(iter(matches)))
    if len(cases) == 1:
        return mixed_pair(question, anchors[0], cases[0], lookup)
    if cases[0] == cases[1]:
        return {}
    return explicit_pair(question, anchors, cases, lookup)


def pack_pair_context(question, ranked, lookup, baseline=None):
    """Order only existing pages, preserving the old context unless coverage grows."""
    if baseline is None:
        baseline = packed_context(ranked)
    targets = pair_requirements(question, lookup)
    if not targets:
        return ranked, baseline, "no_verified_pair"

    requested = {(case, role) for case, roles in targets.items() for role in roles}
    page_by_role = {}
    for row in ranked:
        key = lookup.page_roles.get(location(row[0]))
        if key in requested:
            page_by_role.setdefault(key, row)

    def coverage(context):
        return {
            lookup.page_roles.get((item.chunk.book_id, item.chunk.page))
            for item in context.final_chunks
        } & requested

    current = coverage(baseline)
    if current == requested:
        return ranked, baseline, "covered"
    priority = [
        [page_by_role[case, role] for role in roles if (case, role) in page_by_role]
        for case, roles in targets.items()
    ]
    if not all(priority):
        return ranked, baseline, "candidate_role_missing"
    first = [
        row for group in zip_longest(*priority) for row in group if row is not None
    ]
    first_pages = {location(row[0]) for row in first}
    ordered = first + [row for row in ranked if location(row[0]) not in first_pages]
    packed = build_context(
        _context_candidates(ordered), FINAL_K, CONTEXT_TOKENS, _count_tokens
    )
    selected = coverage(packed)
    if current < selected and {case for case, _ in selected} == targets.keys():
        route = "repacked" if selected == requested else "repacked_partial"
        return ordered, packed, route
    return ranked, baseline, "no_role_gain"


def read_frozen():
    workflow_cfg = json.loads((WORKFLOW_RUN / "results.json").read_bytes())["dataset"]
    blind_cfg = json.loads((BLIND_RUN / "summary.json").read_bytes())["config"]
    definitions = (
        (
            "workflow",
            WORKFLOW / "questions.json",
            WORKFLOW / "references.json",
            WORKFLOW_RUN / "rankings.jsonl",
            workflow_cfg,
            workflow_cfg["pages"],
        ),
        (
            "blind",
            USAGE / "blind_questions.json",
            USAGE / "blind_references.json",
            BLIND_RUN / "rankings.jsonl",
            blind_cfg["inputs"]["usage_blind"],
            blind_cfg["pages"],
        ),
    )
    questions, saved, references, inputs = [], {}, {}, {}
    pages_sha = fingerprint(CACHE_PATH)["sha256"]
    for split, qpath, rpath, ranks, cfg, snapshot in definitions:
        stamps = {"questions": fingerprint(qpath), "references": fingerprint(rpath)}
        if snapshot["sha256"] != pages_sha or any(
            stamp["sha256"] != cfg[key]["sha256"] for key, stamp in stamps.items()
        ):
            raise ValueError(f"{split} 기존 순위의 평가 입력 지문이 다릅니다.")
        current = load_questions(qpath)
        ids = {row["id"] for row in current}
        rows = [
            row
            for row in read_rows(ranks)
            if row["arm"] == "baseline" and row["id"] in ids
        ]
        if (
            len(rows) != len(ids)
            or {row["id"] for row in rows} != ids
            or ids & saved.keys()
        ):
            raise ValueError(f"{split} 기존 기준선 문항과 질문 ID가 다릅니다.")
        questions.extend({**row, "split": split} for row in current)
        saved.update({row["id"]: row for row in rows})
        references[split] = rpath
        inputs[split] = stamps | {"rankings": fingerprint(ranks)}
    if Counter(row["split"] for row in questions) != {"workflow": 20, "blind": 300}:
        raise ValueError("업무형 20문항과 블라인드 300문항이 필요합니다.")
    return questions, saved, references, inputs


def summarize(rows, arms):
    return {
        arm: {
            "full_evidence": sum(
                row["arms"][arm]["context"]["personal_all_facts"] for row in rows
            ),
            "facts_found": sum(
                row["arms"][arm]["context"]["personal_required_facts_found"]
                for row in rows
            ),
            "facts_expected": sum(
                row["arms"][arm]["context"]["personal_required_facts"] for row in rows
            ),
            "context_doc_recall": fmean(
                row["arms"][arm]["context"]["document_recall"] for row in rows
            ),
        }
        for arm in arms
    }


def peer_metrics(workflow_inputs, pair_ids):
    cfg_path = WORKFLOW_RECORD / "peer-config.json"
    results_path = WORKFLOW_RECORD / "peer-results.json"
    config = json.loads(cfg_path.read_bytes())
    if any(
        config[key]["sha256"] != workflow_inputs[key]["sha256"]
        for key in ("questions", "references")
    ):
        raise ValueError("동료 결과의 평가 입력 지문이 다릅니다.")
    if config["restored_pages"]["sha256"] != fingerprint(CACHE_PATH)["sha256"]:
        raise ValueError("동료 결과의 복원 페이지 지문이 다릅니다.")
    if (
        config["retrieval"]["context_page_limit"],
        config["retrieval"]["context_token_limit"],
    ) != (FINAL_K, CONTEXT_TOKENS):
        raise ValueError("동료 결과의 최종 문맥 상한이 다릅니다.")
    rows = json.loads(results_path.read_bytes())["question_results"]
    metrics = {row["id"]: row["metrics"] for row in rows}
    ids = {row["id"] for row in load_questions(WORKFLOW / "questions.json")}
    if len(rows) != len(ids) or metrics.keys() != ids or not pair_ids <= ids:
        raise ValueError("동료 결과의 업무형 문항 ID가 다릅니다.")
    return {
        "config": fingerprint(cfg_path),
        "results": fingerprint(results_path),
        "full_evidence": sum(row["all_facts"] for row in metrics.values()),
        "parallel_full_evidence": sum(metrics[qid]["all_facts"] for qid in pair_ids),
        "facts_found": sum(row["required_facts_hit"] for row in metrics.values()),
        "context_doc_recall": fmean(row["doc_recall@8"] for row in metrics.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = local_output(args.output)
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line)

    questions, saved, ref_paths, inputs = read_frozen()
    log("문항과 보관 순위 지문 검증 · 업무형 20문항 · 블라인드 300문항")
    pages = json.loads(CACHE_PATH.read_bytes())
    lookup = load_target_lookup(pages)
    pages_by_key = {location(page): page for page in pages}
    retrievals = {}
    changes = Counter()
    routes = {"workflow": Counter(), "blind": Counter()}

    for q in questions:
        qid, split = q["id"], q["split"]
        row = saved[qid]
        pool, expanded = (
            [(pages_by_key[item["book_id"], item["page"]], 0.0) for item in row[stage]]
            for stage in ("pool", "expanded")
        )
        keys = [location(page) for page, _ in expanded]
        if len(keys) != CANDIDATES or len(set(keys)) != CANDIDATES:
            raise ValueError(f"{qid}의 기존 문맥 후보 20쪽이 고정되지 않았습니다.")
        baseline = packed_context(expanded)
        actual = [
            (item.chunk.book_id, item.chunk.page) for item in baseline.final_chunks
        ]
        expected = [(item["book_id"], item["page"]) for item in row["packed"]]
        if (
            baseline.context_hash != row["context_sha256"]
            or baseline.token_count != row["context_tokens"]
            or actual != expected
        ):
            raise ValueError(f"{qid}의 기준선 문맥 재현에 실패했습니다.")
        ordered, proposed, route = pack_pair_context(
            q["question"], expanded, lookup, baseline
        )
        if (
            set(keys) != {location(page) for page, _ in ordered}
            or len(ordered) != CANDIDATES
        ):
            raise ValueError(f"{qid}의 고정 후보 집합이 변경됐습니다.")
        routes[split][route] += 1
        changes[split] += proposed.context_hash != baseline.context_hash
        retrievals[qid, ARMS[0]] = pool, expanded, baseline, (), row["route"]
        retrievals[qid, ARMS[1]] = pool, ordered, proposed, (), route

    save_rankings(output, retrievals)
    log("문맥 조립 완료 · 기준선 320문항 재현 · 후보 20쪽 변경 0건")
    refs = {
        qid: ref
        for path in ref_paths.values()
        for qid, ref in load_references(path).items()
    }
    scored = score_run(
        questions,
        retrievals,
        refs,
        page_cases={key: case for key, (case, _) in lookup.page_roles.items()},
    )["records"]
    write_rows(output / "questions.jsonl", scored)
    groups = {
        "workflow": [row for row in scored if row["split"] == "workflow"],
        "blind": [row for row in scored if row["split"] == "blind"],
        "parallel": [
            row
            for row in scored
            if refs[row["id"]].get("question_type") == "parallel"
            and row["split"] == "workflow"
        ],
    }
    summary = {}
    for name, rows in groups.items():
        paired = Counter(
            tuple(row["arms"][arm]["context"]["personal_all_facts"] for arm in ARMS)
            for row in rows
        )
        summary[name] = summarize(rows, ARMS) | {
            "questions": len(rows),
            "gained": paired[0, 1],
            "lost": paired[1, 0],
        }
    peer = peer_metrics(inputs["workflow"], {row["id"] for row in groups["parallel"]})
    write_json(
        output / "results.json",
        {
            "inputs": inputs,
            "pages": fingerprint(CACHE_PATH),
            "code": fingerprint(Path(__file__)),
            "limits": {
                "candidates": CANDIDATES,
                "final_pages": FINAL_K,
                "tokens": CONTEXT_TOKENS,
            },
            "calls": {"search": 0, "rerank": 0, "external_api": 0},
            "baseline_context_reproduced": len(questions),
            "candidate_membership_changes": 0,
            "context_changes": dict(changes),
            "routes": {key: dict(value) for key, value in routes.items()},
            "summary": summary,
            "peer": peer,
        },
    )
    log(
        f"채점 완료 · 업무형 개선 {summary['workflow']['gained']} · 블라인드 하락 {summary['blind']['lost']}"
    )


if __name__ == "__main__":
    main()
