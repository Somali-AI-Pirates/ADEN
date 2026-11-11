from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from experimental.u_10o0o.fresh_record_hybrid import final_coverage as fc
from experimental.u_10o0o.fresh_record_hybrid import retrieval as r


def _relation_index(code_events: list[tuple[str, str]]) -> dict[str, Any]:
    code_to_events: dict[str, list[str]] = {}
    events: dict[str, dict[str, Any]] = {}
    for code, event_id in code_events:
        normalized = r.normalize_code(code)
        code_to_events.setdefault(normalized, []).append(event_id)
        event = events.setdefault(
            event_id,
            {"unit_ids": [], "source_ids": [], "line_spans": [], "relations": []},
        )
        event["relations"].append(
            {
                "code": code,
                "normalized_code": normalized,
                "role": "reservation_key",
                "source_id": f"AS001:{len(event['relations']) + 1:03d}",
                "start": 1,
                "end": 1,
            }
        )
    return {"code_to_events": code_to_events, "events": events}


def _plan_fixture(
    code_events: list[tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, list[re.Pattern[str]]]]:
    relation_index = _relation_index(code_events)
    return relation_index, r._code_patterns(relation_index)


def test_requested_roles_treats_each_exact_record_heading_as_one_role() -> None:
    assert fc.requested_roles("비용 조건 약정") == ["contract"]
    assert fc.requested_roles("계약 조건 대조 회신") == ["reconciliation"]
    assert fc.requested_roles("비용 청구 명세") == ["invoice"]


def test_requested_roles_keeps_a_separate_role_after_a_record_heading() -> None:
    assert fc.requested_roles("계약 조건 대조 회신과 입금") == [
        "reconciliation",
        "receipt",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("계약   조건\t대조   회신", ["reconciliation"]),
        ("수납\t및   미결 인계", ["receipt"]),
        ("계약-조건-대조-회신", ["reconciliation"]),
        ("계약-조건-대조-회신과 입금", ["reconciliation", "receipt"]),
    ],
)
def test_requested_roles_accepts_spacing_and_punctuation_of_record_headings(
    text: str, expected: list[str]
) -> None:
    assert fc.requested_roles(text) == expected


def test_plan_requests_binds_role_lists_to_the_correct_target_after_swap() -> None:
    first = "HG2029-00001"
    second = "HG2029-00002"
    relation_index, patterns = _plan_fixture(
        [(first, "event:first"), (second, "event:second")]
    )

    plan = fc.plan_requests(
        f"{first} 청구 항목과 {second} 약정 조건",
        relation_index,
        patterns,
    )
    swapped = fc.plan_requests(
        f"{first} 약정 조건과 {second} 청구 항목",
        relation_index,
        patterns,
    )

    assert plan["status"] == "planned"
    assert plan["binding"] == "target_local_clauses"
    assert {(slot["target"], slot["role"]) for slot in plan["slots"]} == {
        ("event:first", "invoice"),
        ("event:second", "contract"),
    }
    assert swapped["status"] == "planned"
    assert {(slot["target"], slot["role"]) for slot in swapped["slots"]} == {
        ("event:first", "contract"),
        ("event:second", "invoice"),
    }


def test_plan_requests_applies_explicit_common_roles_to_both_targets() -> None:
    first = "HG2029-00001"
    second = "HG2029-00002"
    relation_index, patterns = _plan_fixture(
        [(first, "event:first"), (second, "event:second")]
    )

    plan = fc.plan_requests(
        f"{first}와 {second} 각각 청구와 수납 잔액",
        relation_index,
        patterns,
    )

    assert plan["status"] == "planned"
    assert plan["binding"] == "explicit_shared_roles"
    assert {(slot["target"], slot["role"]) for slot in plan["slots"]} == {
        ("event:first", "invoice"),
        ("event:first", "receipt"),
        ("event:second", "invoice"),
        ("event:second", "receipt"),
    }


def test_plan_requests_collapses_multiple_codes_for_one_event() -> None:
    reservation = "HG2029-00001"
    contract = "CT-10001"
    relation_index, patterns = _plan_fixture(
        [(reservation, "event:one"), (contract, "event:one")]
    )

    plan = fc.plan_requests(
        f"{reservation} / {contract} 청구와 수납 잔액",
        relation_index,
        patterns,
    )

    assert plan["status"] == "planned"
    assert plan["binding"] == "single_event"
    assert plan["event_ids"] == ["event:one"]
    assert {(slot["target"], slot["role"]) for slot in plan["slots"]} == {
        ("event:one", "invoice"),
        ("event:one", "receipt"),
    }


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("HG2029-00001 청구 제외하고 수납", "negation_or_uncertainty"),
        ("HG2029-00001 청구 규정과 예외", "rule_or_reason_request"),
        ("HG2029-00001 청구와 EV-99-0001 수납", "unresolved_code_in_question"),
        ("HG2029-00001 청구", "fewer_than_two_requests"),
    ],
)
def test_plan_requests_falls_back_for_unsafe_or_incomplete_interpretation(
    query: str, status: str
) -> None:
    relation_index, patterns = _plan_fixture([("HG2029-00001", "event:one")])

    plan = fc.plan_requests(query, relation_index, patterns)

    assert plan == {"status": status, "slots": []}


@pytest.mark.parametrize(
    "revision_term",
    ["승인", "효력", "판본", "버전", "정정", "변경", "취소", "예정", "전후", "최신"],
)
def test_plan_requests_falls_back_for_revision_or_effect_questions(
    revision_term: str,
) -> None:
    relation_index, patterns = _plan_fixture([("HG2029-00001", "event:one")])

    plan = fc.plan_requests(
        f"HG2029-00001 청구와 수납 {revision_term}",
        relation_index,
        patterns,
    )

    assert plan == {"status": "revision_or_effect_request", "slots": []}


def _record_link_fixture(
    kind: str, *, edge_role: str = "contract_reference"
) -> tuple[list[r.Unit], dict[str, Any], dict[str, dict[str, Any]]]:
    event_id = "event:HG202900001"
    source_id = "AS001:001"
    code = "CT-10001"
    unit = r.Unit(
        unit_id="B:record",
        text=code,
        sources=(source_id,),
        corpus="B",
        kind=kind,
        event_ids=(event_id,),
        line_spans=({"source_id": source_id, "start": 1, "end": 1},),
    )
    relation_index = {
        "code_to_events": {r.normalize_code(code): [event_id]},
        "events": {
            event_id: {
                "unit_ids": [unit.unit_id],
                "relations": [
                    {
                        "code": code,
                        "normalized_code": r.normalize_code(code),
                        "role": edge_role,
                        "source_id": source_id,
                        "start": 1,
                        "end": 1,
                    }
                ],
            }
        },
    }
    pages = {source_id: {"lines": [code], "text": code}}
    return [unit], relation_index, pages


@pytest.mark.parametrize(
    ("kind", "expected_role"),
    [
        ("비용 조건 약정", "contract"),
        ("현장 사건 수신대장", "event"),
        ("계약 조건 대조 회신", "reconciliation"),
        ("비용 청구 명세", "invoice"),
        ("수납 및 미결 인계", "receipt"),
    ],
)
def test_build_record_links_uses_exact_record_kind_not_relation_edge_role(
    kind: str, expected_role: str
) -> None:
    units, relation_index, pages = _record_link_fixture(kind)

    links = fc.build_record_links(units, relation_index, pages)

    assert list(links) == [("event:HG202900001", expected_role)]
    assert (
        links[("event:HG202900001", expected_role)]["AS001:001"]["record_kind"] == kind
    )


def _record_links_with_proofs(
    page_lines: list[str],
    proofs: list[tuple[str, str, int, int]],
    *,
    unit_span_end: int | None = None,
) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    event_id = "event:HG202900001"
    source_id = "AS001:001"
    unit_id = "B:record"
    unit = r.Unit(
        unit_id=unit_id,
        text="\\n".join(page_lines),
        sources=(source_id,),
        corpus="B",
        kind="비용 청구 명세",
        event_ids=(event_id,),
        line_spans=(
            {
                "source_id": source_id,
                "start": 1,
                "end": unit_span_end or len(page_lines),
            },
        ),
    )
    relations = []
    code_to_events: dict[str, list[str]] = {}
    for code, edge_role, start, end in proofs:
        normalized = r.normalize_code(code)
        code_to_events.setdefault(normalized, []).append(event_id)
        relations.append(
            {
                "code": code,
                "normalized_code": normalized,
                "role": edge_role,
                "source_id": source_id,
                "start": start,
                "end": end,
            }
        )
    relation_index = {
        "code_to_events": code_to_events,
        "events": {
            event_id: {
                "unit_ids": [unit_id],
                "relations": relations,
            }
        },
    }
    pages = {
        source_id: {
            "lines": page_lines,
            "text": "\\n".join(page_lines),
        }
    }
    return fc.build_record_links([unit], relation_index, pages)


@pytest.mark.parametrize(
    "page_text",
    [
        "HG2029-000010",
        "ABC-HG2029-00001",
        "HG2029-00001-ABC",
    ],
)
def test_build_record_links_rejects_prefix_and_suffix_code_edges(
    page_text: str,
) -> None:
    links = _record_links_with_proofs(
        [page_text],
        [("HG2029-00001", "reservation_key", 1, 1)],
    )

    assert links == {}


def test_build_record_links_skips_bad_edge_then_keeps_later_complete_proof() -> None:
    links = _record_links_with_proofs(
        ["HG2029-000010", "CT-10001"],
        [
            ("HG2029-00001", "reservation_key", 1, 1),
            ("CT-10001", "contract_reference", 2, 2),
        ],
    )

    assert list(links) == [("event:HG202900001", "invoice")]
    assert links[("event:HG202900001", "invoice")]["AS001:001"] == {
        "source_id": "AS001:001",
        "record_unit_id": "B:record",
        "record_kind": "비용 청구 명세",
        "start": 2,
        "end": 2,
    }


def test_build_record_links_still_rejects_out_of_range_relation_proof() -> None:
    with pytest.raises(ValueError, match="outside the original page"):
        _record_links_with_proofs(
            ["HG2029-00001"],
            [("HG2029-00001", "reservation_key", 2, 2)],
            unit_span_end=2,
        )


def test_build_record_links_ignores_unlisted_kind_even_with_literal_edge_proof() -> (
    None
):
    units, relation_index, pages = _record_link_fixture("비용 청구 명세 — 정정 원문")

    assert fc.build_record_links(units, relation_index, pages) == {}


def test_coverage_order_greedily_covers_slots_then_restores_kure_order_and_fills() -> (
    None
):
    ranked = ["filler", "p1", "p2", "last"]
    slots_by_unit = {
        "filler": set(),
        "p1": {"a", "b"},
        "p2": {"b", "c"},
        "last": {"c"},
    }

    ordered, protected, status = fc.coverage_order(
        ranked,
        slots_by_unit,
        {"a", "b", "c"},
        limit=3,
    )

    assert status == "eligible"
    # p1 and p2 cover the same number at the first step; frozen KURE rank breaks the tie.
    assert protected == ["p1", "p2"]
    # One slot is filled from the original KURE order, then the final list is KURE ordered.
    assert ordered == ["filler", "p1", "p2"]


def test_coverage_order_falls_back_when_a_slot_is_missing_or_exceeds_eight_pages() -> (
    None
):
    ranked = [f"p{i}" for i in range(10)]

    missing, protected, status = fc.coverage_order(
        ranked,
        {"p0": {"a"}, "p1": {"b"}},
        {"a", "missing"},
    )
    too_many_slots, too_many_protected, too_many_status = fc.coverage_order(
        ranked,
        {f"p{i}": {f"slot-{i}"} for i in range(9)},
        {f"slot-{i}" for i in range(9)},
        limit=8,
    )

    assert (missing, protected, status) == (
        ranked,
        [],
        "missing_requested_record_in_candidates",
    )
    assert (too_many_slots, too_many_protected, too_many_status) == (
        ranked,
        [],
        "requested_records_exceed_page_budget",
    )


class _CharacterEncoding:
    @staticmethod
    def encode(text: str, *, disallowed_special: tuple[()] = ()) -> list[str]:
        del disallowed_special
        return list(text)

    @staticmethod
    def decode(tokens: list[str], *, errors: str = "strict") -> str:
        del errors
        return "".join(tokens)


def _selection_fixture(
    *, condition: str = "E", target_text: str = "target page"
) -> tuple[
    dict[str, Any],
    dict[str, r.Unit],
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, dict[str, Any]]],
    dict[str, Any],
    _CharacterEncoding,
    str,
]:
    docs: dict[str, r.Unit] = {}
    pages: dict[str, dict[str, Any]] = {}
    ranked = []
    for index in range(20):
        source_id = f"AS001:{index + 1:03d}"
        unit_id = f"A:{source_id}:raw"
        text = target_text if index == 10 else f"ordinary page {index}"
        docs[unit_id] = r.Unit(
            unit_id=unit_id,
            text=text,
            sources=(source_id,),
            corpus="A",
            kind="raw_page",
            line_spans=({"source_id": source_id, "start": 1, "end": 1},),
        )
        pages[source_id] = {"source_id": source_id, "text": text, "lines": [text]}
        ranked.append(unit_id)

    event_id = "event:HG202900001"
    target_source = docs[ranked[10]].sources[0]
    slot_roles = ["invoice", "receipt"]
    plan = {
        "status": "planned",
        "binding": "single_event",
        "event_ids": [event_id],
        "slots": [
            {
                "target": event_id,
                "role": role,
                "slot_id": f"{event_id}|{role}",
            }
            for role in slot_roles
        ],
    }
    links = {
        (event_id, role): {
            target_source: {
                "source_id": target_source,
                "record_unit_id": f"B:{role}",
                "record_kind": role,
                "start": 1,
                "end": 1,
            }
        }
        for role in slot_roles
    }
    encoding = _CharacterEncoding()
    baseline_sources, tokens, digest = r._restore_context(ranked, docs, pages, encoding)
    row = {
        "id": "synthetic",
        "condition": condition,
        "candidate_unit_ids": ranked.copy(),
        "reranked_unit_ids": ranked.copy(),
        "rerank_order": [
            {"unit_id": unit_id, "score": float(20 - index)}
            for index, unit_id in enumerate(ranked)
        ],
        "relation": {"event_ids": [event_id]},
        "final_sources": baseline_sources,
        "context_tokens": tokens,
        "context_sha256": digest,
        "timings_ms": {"rerank": 1.0},
    }
    return row, docs, pages, links, plan, encoding, ranked[10]


@pytest.mark.parametrize("condition", ["E", "F"])
def test_select_context_preserves_frozen_arrays_and_returns_kure_ordered_coverage(
    condition: str,
) -> None:
    row, docs, pages, links, plan, encoding, target_unit = _selection_fixture(
        condition=condition
    )
    original = copy.deepcopy(row)

    result = fc.select_context(row, plan, links, docs, pages, encoding)

    assert result["condition"] == f"{condition}_coverage"
    assert result["final_selection"]["status"] == "eligible"
    assert result["final_selection"]["applied"] is True
    assert result["final_selection"]["protected_unit_ids"] == [target_unit]
    assert [part["source_id"] for part in result["final_sources"]] == [
        docs[uid].sources[0] for uid in [*row["reranked_unit_ids"][:7], target_unit]
    ]
    assert result["candidate_unit_ids"] == row["candidate_unit_ids"]
    assert result["reranked_unit_ids"] == row["reranked_unit_ids"]
    assert result["rerank_order"] == row["rerank_order"]
    assert result["upstream_reused"]["new_model_calls"] == 0
    assert row == original


def test_select_context_falls_back_for_missing_candidate_and_does_not_mutate_row() -> (
    None
):
    row, docs, pages, _links, plan, encoding, _target_unit = _selection_fixture()
    original = copy.deepcopy(row)
    event_id = plan["event_ids"][0]
    missing_links = {
        (event_id, role): {
            "AS001:999": {
                "source_id": "AS001:999",
                "record_unit_id": f"B:{role}",
                "record_kind": role,
                "start": 1,
                "end": 1,
            }
        }
        for role in ("invoice", "receipt")
    }

    result = fc.select_context(row, plan, missing_links, docs, pages, encoding)

    assert (
        result["final_selection"]["status"] == "missing_requested_record_in_candidates"
    )
    assert result["final_selection"]["applied"] is False
    assert result["final_sources"] == row["final_sources"]
    assert result["context_sha256"] == row["context_sha256"]
    assert row == original


def test_select_context_falls_back_when_a_protected_page_is_token_clipped() -> None:
    row, docs, pages, links, plan, encoding, target_unit = _selection_fixture(
        target_text="x" * (r.MAX_CONTEXT_TOKENS + 100)
    )
    original = copy.deepcopy(row)
    baseline_sources = copy.deepcopy(row["final_sources"])

    result = fc.select_context(row, plan, links, docs, pages, encoding)

    assert target_unit in result["final_selection"]["protected_unit_ids"]
    assert (
        result["final_selection"]["status"] == "protected_record_exceeds_token_budget"
    )
    assert result["final_selection"]["applied"] is False
    assert result["final_sources"] == baseline_sources
    assert result["context_sha256"] == row["context_sha256"]
    assert row == original
