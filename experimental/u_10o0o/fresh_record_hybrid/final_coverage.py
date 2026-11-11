"""Select verified target/record coverage from frozen E/F candidates.

This module never loads evaluation references or invokes a retrieval/model API.
Unsupported question interpretations retain the exact saved KURE context.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import retrieval as r

ROOT = Path("artifacts/u_10o0o/advanced-fresh-final-coverage-20261111")
SOURCES = {
    "E": (
        Path(
            "artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/run-E-v1/results.jsonl"
        ),
        "13cc7c1579b8d218fbbd9b73e08a05167a267d29448749910e1eb0da22b5f762",
    ),
    "F": (
        Path(
            "artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/run-F-v1/results.jsonl"
        ),
        "36fdbf675beb5cc998adfc81faf7fddd62d4e880fea95f3dc8ae97198328b123",
    ),
}
# Exact original HG record headings, not the roles of identifier edges.
RECORD_ROLES = {
    "비용 조건 약정": "contract",
    "현장 사건 수신대장": "event",
    "계약 조건 대조 회신": "reconciliation",
    "비용 청구 명세": "invoice",
    "수납 및 미결 인계": "receipt",
}
ROLE_TERMS = {
    "contract": r"약정|계약|요율|단가|무료일|공제일",
    "event": r"증빙|전표|양하|반출|반납",
    "reconciliation": r"대조|정정|회신",
    "invoice": r"청구|산식",
    "receipt": r"수납|입금|미결|미수|잔액",
}
NEGATION = re.compile(r"제외|말고|아니|않|없|빼고|미정|불명|모호")
RULE_REQUEST = re.compile(r"규정|절차|예외|정책|공통|일반|왜|이유|조항|방침")
REVISION_REQUEST = re.compile(r"승인|효력|판본|버전|정정|변경|취소|예정|전후|최신")
SHARED = re.compile(r"각각|각\s*건|두\s*(?:건|사건)\s*모두|둘\s*다")
LIST_GAP = re.compile(r"[\s,·/]*(?:(?:와|과|및|그리고|하고)[\s,·/]*)?")
COVERAGE_POLICY = "exact-HG-role-coverage-v1"


def requested_roles(text: str) -> list[str]:
    # A record title is one request, even when its words name other roles.
    # For example, "계약 조건 대조 회신" is reconciliation, not two records.
    found = set()
    remaining = text
    for title, role in sorted(RECORD_ROLES.items(), key=lambda item: -len(item[0])):
        pattern = r"[\W_]*".join(re.escape(part) for part in title.split())
        if re.search(pattern, remaining):
            found.add(role)
            remaining = re.sub(pattern, " ", remaining)
    found.update(
        role for role, pattern in ROLE_TERMS.items() if re.search(pattern, remaining)
    )
    return [role for role in ROLE_TERMS if role in found]


def plan_requests(
    query: str,
    relation_index: Mapping[str, Any],
    patterns: Mapping[str, Sequence[re.Pattern[str]]],
) -> dict[str, Any]:
    """Bind roles only in a shared target list or clear target-local clauses."""

    def stop(reason: str) -> dict[str, Any]:
        return {"status": reason, "slots": []}

    if NEGATION.search(query):
        return stop("negation_or_uncertainty")
    if RULE_REQUEST.search(query):
        return stop("rule_or_reason_request")
    if REVISION_REQUEST.search(query):
        return stop("revision_or_effect_request")
    explicit = r.find_explicit_codes(query, relation_index, patterns=patterns)
    if not explicit:
        return stop("no_explicit_target")
    known = {code for code, _ in explicit}
    for match in r._CODE_RE.finditer(query):
        token = match.group()
        if any(c.isascii() and c.isalpha() for c in token):
            if r.normalize_code(token) not in known:
                return stop("unresolved_code_in_question")

    mentions = []
    for code, offset in explicit:
        events = relation_index["code_to_events"].get(code, [])
        if len(set(events)) != 1:
            return stop("ambiguous_code")
        matches = [
            match
            for pattern in patterns[code]
            for match in pattern.finditer(query)
            if not r._has_code_continuation(query, match.start(), match.end())
        ]
        distinct_spans = {(m.start(), m.end()) for m in matches}
        starts = {start for start, _ in distinct_spans}
        if len(starts) != 1:
            return stop("repeated_target_clause")
        end = max(end for start, end in distinct_spans if start == offset)
        mentions.append((offset, end, events[0]))
    mentions.sort()
    events = list(dict.fromkeys(event for _, _, event in mentions))
    if len(events) > 2:
        return stop("too_many_targets")
    # Several identifiers for one event are safe only when all refer to it.
    if len(events) == 1:
        slots = [(events[0], role) for role in requested_roles(query)]
        binding = "single_event"
    else:
        if len(mentions) != 2:
            return stop("mixed_target_and_supporting_codes")
        first, second = mentions
        prefix = query[: first[0]]
        gap = query[first[1] : second[0]]
        suffix = query[second[1] :]
        if requested_roles(prefix):
            return stop("unbound_prefix_roles")
        if LIST_GAP.fullmatch(gap) and SHARED.search(suffix):
            roles = requested_roles(suffix)
            slots = [(event, role) for event in events for role in roles]
            binding = "explicit_shared_roles"
        else:
            left = requested_roles(gap)
            right = requested_roles(suffix)
            if not left or not right or SHARED.search(query):
                return stop("uncertain_clause_binding")
            slots = [(first[2], role) for role in left] + [
                (second[2], role) for role in right
            ]
            binding = "target_local_clauses"
    if len(slots) < 2:
        return stop("fewer_than_two_requests")
    return {
        "status": "planned",
        "binding": binding,
        "event_ids": events,
        "slots": [
            {"target": event, "role": role, "slot_id": f"{event}|{role}"}
            for event, role in slots
        ],
    }


def build_record_links(
    units: Sequence[r.Unit],
    relation_index: Mapping[str, Any],
    pages: Mapping[str, Mapping[str, Any]],
    patterns: Mapping[str, Sequence[re.Pattern[str]]] | None = None,
) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """Require a record heading and a complete code proof, not a code prefix."""
    if patterns is None:
        patterns = r._code_patterns(relation_index)
    docs = {unit.unit_id: unit for unit in units}
    links: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for event_id, event in relation_index["events"].items():
        for unit_id in event["unit_ids"]:
            unit = docs.get(unit_id)
            if unit is None or unit.kind not in RECORD_ROLES:
                continue
            if event_id not in unit.event_ids:
                continue
            for proof in event["relations"]:
                source = proof["source_id"]
                start, end = int(proof["start"]), int(proof["end"])
                if not any(
                    r._span_overlaps(span, source, start, end)
                    for span in unit.line_spans
                ):
                    continue
                lines = pages[source]["lines"]
                if not 1 <= start <= end <= len(lines):
                    raise ValueError("Relation proof is outside the original page")
                literal = r.normalize_code(str(proof["code"]))
                proof_text = "\n".join(lines[start - 1 : end])
                complete_code = any(
                    not r._has_code_continuation(proof_text, match.start(), match.end())
                    for pattern in patterns.get(literal, ())
                    for match in pattern.finditer(proof_text)
                )
                if not complete_code:
                    # An unverified edge cannot make a page eligible. Another
                    # complete-code edge for the same record may still prove it.
                    continue
                key = (event_id, RECORD_ROLES[unit.kind])
                links.setdefault(key, {}).setdefault(
                    source,
                    {
                        "source_id": source,
                        "record_unit_id": unit.unit_id,
                        "record_kind": unit.kind,
                        "start": start,
                        "end": end,
                    },
                )
    return links


def coverage_order(
    ranked: Sequence[str],
    slots_by_unit: Mapping[str, set[str]],
    requested: set[str],
    limit: int = r.MAX_FINAL_SOURCES,
) -> tuple[list[str], list[str], str]:
    """Greedy uncovered-request count, then frozen KURE rank; fill by KURE."""
    available = set().union(*(slots_by_unit.get(uid, set()) for uid in ranked))
    if not requested <= available:
        return list(ranked), [], "missing_requested_record_in_candidates"
    selected = []
    uncovered = set(requested)
    while uncovered and len(selected) < limit:
        best = max(
            (uid for uid in ranked if uid not in selected),
            key=lambda uid: (
                len(slots_by_unit.get(uid, set()) & uncovered),
                -ranked.index(uid),
            ),
        )
        selected.append(best)
        uncovered -= slots_by_unit.get(best, set())
    if uncovered:
        return list(ranked), [], "requested_records_exceed_page_budget"
    protected = list(selected)
    for uid in ranked:
        if len(selected) >= limit:
            break
        if uid not in selected:
            selected.append(uid)
    selected_set = set(selected)
    return [uid for uid in ranked if uid in selected_set], protected, "eligible"


def select_context(
    row: Mapping[str, Any],
    plan: Mapping[str, Any],
    links: Mapping[tuple[str, str], Mapping[str, Any]],
    docs: Mapping[str, r.Unit],
    pages: Mapping[str, Mapping[str, Any]],
    encoding: Any,
) -> dict[str, Any]:
    """Change only final selection; preserve all upstream arrays and scores."""
    started = time.perf_counter()
    result = copy.deepcopy(row)
    result["condition"] = row["condition"] + "_coverage"
    metadata: dict[str, Any] = {
        "policy": COVERAGE_POLICY,
        "plan": plan,
        "status": plan["status"],
        "applied": False,
        "links": [],
        "protected_unit_ids": [],
    }
    ranked = row["reranked_unit_ids"]
    if plan["status"] == "planned":
        if set(plan["event_ids"]) != set(row["relation"].get("event_ids", [])):
            metadata["status"] = "target_disagrees_with_frozen_resolution"
        else:
            by_unit: dict[str, set[str]] = {uid: set() for uid in ranked}
            for slot in plan["slots"]:
                record_sources = links.get((slot["target"], slot["role"]), {})
                for uid in ranked:
                    for source in docs[uid].sources:
                        if source in record_sources:
                            by_unit[uid].add(slot["slot_id"])
                            metadata["links"].append(
                                {
                                    "unit_id": uid,
                                    "slot_id": slot["slot_id"],
                                    **record_sources[source],
                                }
                            )
            ordered, protected, status = coverage_order(
                ranked, by_unit, {slot["slot_id"] for slot in plan["slots"]}
            )
            metadata["status"] = status
            metadata["protected_unit_ids"] = protected
            if status == "eligible":
                selected, tokens, digest = r._restore_context(
                    ordered, docs, pages, encoding
                )
                saved = {part["source_id"]: part["text"] for part in selected}
                complete = all(
                    saved.get(source) == pages[source]["text"]
                    for uid in protected
                    for source in docs[uid].sources
                )
                if not complete:
                    metadata["status"] = "protected_record_exceeds_token_budget"
                else:
                    result.update(
                        final_sources=selected,
                        context_tokens=tokens,
                        context_sha256=digest,
                    )
                    metadata["applied"] = True
    metadata["context_changed"] = result["context_sha256"] != row["context_sha256"]
    metadata["selection_ms"] = (time.perf_counter() - started) * 1000
    metadata["selected_unit_ids"] = [
        uid
        for uid in ranked
        if any(
            source in {p["source_id"] for p in result["final_sources"]}
            for source in docs[uid].sources
        )
    ]
    result["final_selection"] = metadata
    result["reused_timings_ms"] = result.pop("timings_ms", {})
    result["timings_ms"] = {"selection_and_restore": metadata["selection_ms"]}
    result["upstream_reused"] = {
        "candidate_order": True,
        "kure_order_and_scores": True,
        "new_model_calls": 0,
        "new_search_calls": 0,
    }
    return result


def replay(output: Path) -> dict[str, Any]:
    manifest_path = output.parent / "run-manifest.json"
    partial = output.with_suffix(".partial")
    if output.suffix != ".jsonl":
        raise ValueError("Results path must have .jsonl suffix")
    if any(p.exists() for p in (output, manifest_path, partial)):
        raise ValueError("Use a fresh output path; saved runs are never overwritten")
    started = time.perf_counter()
    questions = r.load_questions(r.DEFAULT_QUESTIONS)
    queries = {q.question_id: q.text for q in questions}
    fingerprints = r._input_fingerprints(
        r.DEFAULT_PAGES, r.DEFAULT_UNITS, r.DEFAULT_RELATIONS, questions
    )
    all_rows = []
    source_hashes = {}
    source_manifests = {}
    source_models = {}
    for condition, (path, digest) in SOURCES.items():
        if r.sha256_file(path) != digest:
            raise ValueError(f"Frozen {condition} result fingerprint mismatch")
        manifest = json.loads((path.parent / "run-manifest.json").read_text())
        if (
            manifest["inputs"] != fingerprints
            or manifest["conditions"] != [condition]
            or manifest["summary"]["results_sha256"] != digest
            or manifest["context"]
            != {"max_sources": 8, "max_tokens": 8000, "tokenizer": "o200k_base"}
        ):
            raise ValueError("Source run/input/budget mismatch")
        rows = r.jsonl_rows(path)
        if len(rows) != len(queries) or {row["id"] for row in rows} != set(queries):
            raise ValueError("Incomplete frozen result IDs")
        for row in rows:
            if (
                row["condition"] != condition
                or row["question_sha256"] != r.sha256_text(queries[row["id"]])
                or len(row["candidate_unit_ids"]) != 20
                or len(set(row["candidate_unit_ids"])) != 20
                or len(row["reranked_unit_ids"]) != 20
                or set(row["candidate_unit_ids"]) != set(row["reranked_unit_ids"])
                or [x["unit_id"] for x in row["rerank_order"]]
                != row["reranked_unit_ids"]
            ):
                raise ValueError("Invalid frozen candidate/rerank/question row")
        all_rows.extend(rows)
        source_hashes[condition] = digest
        source_manifests[condition] = r.sha256_file(path.parent / "run-manifest.json")
        source_models[condition] = manifest["reranker_model"]
    raw_units, pages, _ = r.load_pages(r.DEFAULT_PAGES)
    docs = {unit.unit_id: unit for unit in raw_units}
    units = r.load_record_units(r.DEFAULT_UNITS)
    relations = json.loads(r.DEFAULT_RELATIONS.read_text())
    encoding = r._context_encoding()
    # Reconstruct KURE contexts before applying any new selection.
    for row in all_rows:
        if row["candidate_source_ids"] != r._candidate_source_ids(
            row["candidate_unit_ids"], docs
        ):
            raise ValueError("Candidate source chain mismatch")
        final, tokens, digest = r._restore_context(
            row["reranked_unit_ids"], docs, pages, encoding
        )
        if (final, tokens, digest) != (
            row["final_sources"],
            row["context_tokens"],
            row["context_sha256"],
        ):
            raise ValueError("Frozen KURE context cannot be reproduced")

    index_started = time.perf_counter()
    patterns = r._code_patterns(relations)
    links = build_record_links(units, relations, pages, patterns)
    index_ms = (time.perf_counter() - index_started) * 1000
    plans, plan_times = {}, {}
    for q in questions:
        t0 = time.perf_counter()
        plans[q.question_id] = plan_requests(q.text, relations, patterns)
        plan_times[q.question_id] = (time.perf_counter() - t0) * 1000

    output.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with partial.open("x", encoding="utf-8") as handle:
        for row in all_rows:
            changed = select_context(
                row, plans[row["id"]], links, docs, pages, encoding
            )
            changed["final_selection"]["planning_ms"] = plan_times[row["id"]]
            changed["timings_ms"]["planning"] = plan_times[row["id"]]
            for value in (row, changed):
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")
                rows_written += 1
    partial.rename(output)
    manifest = {
        "format": "fresh-final-coverage-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "conditions": ["E", "E_coverage", "F", "F_coverage"],
        "questions": len(queries),
        "rows": rows_written,
        "inputs": fingerprints,
        "source_results_sha256": source_hashes,
        "source_manifests_sha256": source_manifests,
        "source_reranker_models": source_models,
        "results_sha256": r.sha256_file(output),
        "policy": COVERAGE_POLICY,
        "code_sha256": r.sha256_file(__file__),
        "candidate_limit": 20,
        "context": {"max_sources": 8, "max_tokens": 8000},
        "index_build_ms": index_ms,
        "elapsed_seconds": time.perf_counter() - started,
        "new_model_calls": 0,
        "new_search_calls": 0,
        "new_api_calls": 0,
        "gold_read": False,
        "timing_boundary": (
            "CPU query plan and final selection/restore only; shared index load "
            "separate. Frozen BM25/vector/KURE timings are not new measurements."
        ),
    }
    r.write_json_atomic(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "run-v1/results.jsonl")
    args = parser.parse_args()
    print(json.dumps(replay(args.results), indent=2))


if __name__ == "__main__":
    main()
