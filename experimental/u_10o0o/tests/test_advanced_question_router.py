import re
from types import SimpleNamespace

from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_question_router import (
    fast_lookup,
    pack_decision,
    route_question,
)
from experimental.u_10o0o.advanced_target_lookup import INVALID_CONSTRAINT

CASE_A = "HG2028-00001"
CASE_B = "HG2028-00065"


def _pages(case_id, book_prefix):
    return [
        {
            "book_id": f"{book_prefix}{index:03}",
            "file_name": f"{book_prefix}{index:03}.pdf",
            "page": 1,
            "text": f"{role} 원문 내용",
        }
        for index, role in enumerate(ROLES, start=1)
    ]


class FakeLookup:
    def __init__(self):
        self.cases = {
            CASE_A: _pages(CASE_A, "ASA"),
            CASE_B: _pages(CASE_B, "ASB"),
        }
        self.id_to_cases = {case_id: {case_id} for case_id in self.cases}
        self.id_fields = {case_id: {"case_id"} for case_id in self.cases}
        self.field_to_cases = {
            "case_id": {case_id: {case_id} for case_id in self.cases},
            "contract_id": {"CT-200": {CASE_B}},
            "shipper": {
                "오로라 운송": {CASE_A},
                "한빛 물류": {CASE_B},
                "공통 업체": {CASE_A, CASE_B},
            },
            "spec": {
                "40gp": {CASE_A},
                "20gp": {CASE_B},
                "공통 규격": {CASE_A, CASE_B},
            },
            "scheduled_return_date": {"2028-04-30": {CASE_A}},
            "actual_designated_return_date": {"2028-04-30": {CASE_A}},
        }
        self.id_to_cases["CT-200"] = {CASE_B}
        self.id_fields["CT-200"] = {"contract_id"}
        self.exact_fields = {}
        self.page_roles = {
            (page["book_id"], page["page"]): (case_id, role)
            for case_id, pages in self.cases.items()
            for role, page in zip(ROLES, pages, strict=True)
        }

    @staticmethod
    def _requested_roles(question):
        return tuple(role for role in ROLES if role in question)

    def plan(self, question):
        identifiers_in_question = re.findall(r"HG20\d{2}-\d{5}", question)
        if len(identifiers_in_question) == 1:
            return SimpleNamespace(status="single_identifier")
        return SimpleNamespace(status="unresolved")

    def _matched_exact_fields(self, text):
        return self.exact_fields

    def _matched_profile_values(self, text):
        return {
            "shipper": {
                value for value in self.field_to_cases["shipper"] if value in text
            },
            "cargo": set(),
            "spec": {value for value in self.field_to_cases["spec"] if value in text},
        }


class FakeInterpreter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, question, *, timeout_seconds, rules_version):
        self.calls.append((question, timeout_seconds, rules_version))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _target(anchor, case_id, role):
    return {
        "anchor": anchor,
        "constraints": [{"field": "case_id", "raw": case_id, "polarity": "include"}],
        "roles": [role],
    }


def test_fast_lookup_only_returns_a_fully_packed_known_case_role(monkeypatch):
    lookup = FakeLookup()
    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router._count_tokens",
        lambda text: len(text.split()),
    )
    question = f"예약번호 {CASE_A}의 {ROLES[3]}를 보여줘"
    store_calls = []

    def page_lookup(case_id, roles):
        store_calls.append((case_id, roles))
        return [lookup.cases[case_id][ROLES.index(role)] for role in roles]

    result = fast_lookup(question, lookup, page_lookup)

    assert result.status == "ready"
    assert result.search_skipped
    assert result.case_id == CASE_A
    assert result.roles == (ROLES[3],)
    assert len(result.order) == len(result.context.final_chunks) == 1
    assert store_calls == [(CASE_A, (ROLES[3],))]


def test_fast_lookup_rejects_mixed_question_and_wrong_sql_source(monkeypatch):
    lookup = FakeLookup()
    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router._count_tokens",
        lambda text: len(text.split()),
    )
    mixed = f"예약번호 {CASE_A}의 예외와 {ROLES[3]}를 알려줘"
    assert fast_lookup(mixed, lookup, lambda case_id, roles: []).status == "ineligible"

    question = f"예약번호 {CASE_A}의 {ROLES[3]}를 보여줘"
    wrong_page = lookup.cases[CASE_A][0]
    result = fast_lookup(question, lookup, lambda case_id, roles: [wrong_page])
    assert result.status == "source_missing"
    assert result.order is None and result.context is None

    correct_page = lookup.cases[CASE_A][ROLES.index(ROLES[3])]
    altered_page = {**correct_page, "text": "다른 문서의 본문"}
    result = fast_lookup(question, lookup, lambda case_id, roles: [altered_page])
    assert result.status == "source_mismatch"
    assert result.order is None and result.context is None

    result = fast_lookup(
        question,
        lookup,
        lambda case_id, roles: [correct_page],
        context_tokens=1,
    )
    assert result.status == "packing_failed"
    assert result.reason == "source_did_not_fit"


def test_structured_route_keeps_each_case_and_requested_role_separate():
    lookup = FakeLookup()
    first = f"첫째 예약번호 {CASE_A}의 {ROLES[3]}"
    second = f"둘째 예약번호 {CASE_B}의 {ROLES[1]}"
    question = f"{first}, {second}를 각각 확인해줘"
    interpreter = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [
                _target(first, CASE_A, ROLES[3]),
                _target(second, CASE_B, ROLES[1]),
            ],
        }
    )

    decision = route_question(question, lookup, interpreter)

    assert decision.status == "verified"
    assert decision.route == "lookup"
    assert not decision.query_required
    assert decision.targets_by_case == {CASE_A: (ROLES[3],), CASE_B: (ROLES[1],)}
    assert decision.targets[0].pages == (lookup.cases[CASE_A][3],)
    assert decision.targets[1].pages == (lookup.cases[CASE_B][1],)
    assert interpreter.calls == [(question, 10.0, None)]


def test_structured_route_merges_roles_for_repeated_case_across_clauses():
    lookup = FakeLookup()
    first = f"예약번호 {CASE_A}의 {ROLES[3]}"
    second = f"예약번호 {CASE_A}의 {ROLES[4]}"
    question = f"{first}, {second} 각각 확인해줘"
    interpreter = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [
                _target(first, CASE_A, ROLES[3]),
                _target(second, CASE_A, ROLES[4]),
            ],
        }
    )

    decision = route_question(question, lookup, interpreter)

    assert decision.status == "verified"
    assert decision.targets_by_case == {CASE_A: (ROLES[3], ROLES[4])}
    assert [target.roles for target in decision.targets] == [
        (ROLES[3],),
        (ROLES[4],),
    ]


def test_interpretation_timeout_and_unanchored_values_fall_back():
    lookup = FakeLookup()
    question = f"예약번호 {CASE_A}의 예외와 {ROLES[3]}를 보여줘"
    timed_out = FakeInterpreter(TimeoutError())
    timeout_decision = route_question(question, lookup, timed_out)
    assert timeout_decision.status == "timeout"
    assert timeout_decision.provider_called
    assert timeout_decision.query_required
    assert timed_out.calls == [(question, 10.0, None)]

    failed = route_question(question, lookup, FakeInterpreter(ValueError("offline")))
    assert failed.status == "interpretation_failure"
    assert failed.reason == "provider_error"

    interpreter = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [
                {
                    "anchor": question,
                    "constraints": [
                        {"field": "case_id", "raw": CASE_B, "polarity": "include"}
                    ],
                    "roles": [ROLES[3]],
                }
            ],
        }
    )
    conflict = route_question(question, lookup, interpreter)
    assert conflict.status == "conflict"
    assert conflict.query_required


def test_structured_route_rejects_overlapping_anchors_and_unstated_roles():
    lookup = FakeLookup()
    question = f"{CASE_A} {ROLES[3]} 예외"
    overlapping = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [
                _target(question, CASE_A, ROLES[3]),
                _target(question, CASE_A, ROLES[3]),
            ],
        }
    )
    assert route_question(question, lookup, overlapping).reason == "duplicate_anchor"

    invented_role = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [_target(f"{CASE_A} 비용", CASE_A, ROLES[3])],
        }
    )
    decision = route_question(question, lookup, invented_role)
    assert decision.status == "unresolved"
    assert decision.reason == "no_source"
    assert decision.query_required


def test_exclusion_cues_must_be_explicitly_and_validly_excluded():
    lookup = FakeLookup()
    valid_question = f"예약번호 {CASE_A} 말고 예약번호 {CASE_B}의 {ROLES[3]}"
    valid = route_question(
        valid_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": valid_question,
                        "constraints": [
                            {
                                "field": "case_id",
                                "raw": CASE_A,
                                "polarity": "exclude",
                            },
                            {
                                "field": "case_id",
                                "raw": CASE_B,
                                "polarity": "include",
                            },
                        ],
                        "roles": [ROLES[3]],
                    }
                ],
            }
        ),
    )
    assert valid.status == "verified"
    assert valid.targets_by_case == {CASE_B: (ROLES[3],)}

    omitted_question = f"예약번호 {CASE_A} 말고 {ROLES[3]}를 확인해줘"
    omitted = route_question(
        omitted_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [_target(omitted_question, CASE_A, ROLES[3])],
            }
        ),
    )
    assert omitted.status == "conflict"
    assert omitted.reason == "unverified_exclusion_cue"

    outside_anchor_question = (
        f"오로라 운송 건은 제외하고, 예약번호 {CASE_A}의 {ROLES[3]}"
    )
    outside_anchor = route_question(
        outside_anchor_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    _target(f"예약번호 {CASE_A}의 {ROLES[3]}", CASE_A, ROLES[3])
                ],
            }
        ),
    )
    assert outside_anchor.status == "conflict"
    assert outside_anchor.reason == "unverified_exclusion_cue"


def test_conflicting_positive_values_and_explicit_identifier_are_rejected():
    lookup = FakeLookup()
    question = f"예약번호 {CASE_A}와 예약번호 {CASE_B}의 {ROLES[3]}"
    same_field_conflict = route_question(
        question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": question,
                        "constraints": [
                            {
                                "field": "case_id",
                                "raw": CASE_A,
                                "polarity": "include",
                            },
                            {
                                "field": "case_id",
                                "raw": CASE_B,
                                "polarity": "include",
                            },
                        ],
                        "roles": [ROLES[3]],
                    }
                ],
            }
        ),
    )
    assert same_field_conflict.status == "conflict"
    assert same_field_conflict.reason == "conflicting_positive_constraints"

    id_question = f"예약번호 {CASE_A} 계약번호 CT-200의 {ROLES[3]}"
    explicit_id_conflict = route_question(
        id_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": id_question,
                        "constraints": [
                            {
                                "field": "contract_id",
                                "raw": "CT-200",
                                "polarity": "include",
                            }
                        ],
                        "roles": [ROLES[3]],
                    }
                ],
            }
        ),
    )
    assert explicit_id_conflict.status == "conflict"
    assert explicit_id_conflict.reason == "positive_constraints_disagree"


def test_date_field_rejects_ambiguous_same_date_types():
    lookup = FakeLookup()
    date_value = "2028-04-30"

    valid_question = f"예약번호 {CASE_A}의 예정 반납일 {date_value} {ROLES[1]}"
    lookup.exact_fields = {"scheduled_return_date": {date_value}}
    valid = route_question(
        valid_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": valid_question,
                        "constraints": [
                            {
                                "field": "case_id",
                                "raw": CASE_A,
                                "polarity": "include",
                            },
                            {
                                "field": "scheduled_return_date",
                                "raw": date_value,
                                "polarity": "include",
                            },
                        ],
                        "roles": [ROLES[1]],
                    }
                ],
            }
        ),
    )
    assert valid.status == "verified"

    ambiguous_question = (
        f"예약번호 {CASE_A}의 예정 반납일 {date_value}와 "
        f"실제 지정 반납일 {date_value} {ROLES[1]}"
    )
    lookup.exact_fields = {
        "scheduled_return_date": {date_value},
        "actual_designated_return_date": {date_value},
    }
    ambiguous = route_question(
        ambiguous_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": ambiguous_question,
                        "constraints": [
                            {
                                "field": "case_id",
                                "raw": CASE_A,
                                "polarity": "include",
                            },
                            {
                                "field": "scheduled_return_date",
                                "raw": date_value,
                                "polarity": "include",
                            },
                        ],
                        "roles": [ROLES[1]],
                    }
                ],
            }
        ),
    )
    assert ambiguous.status == "unresolved"
    assert ambiguous.reason == "no_source"

    lookup.exact_fields = {
        "scheduled_return_date": {date_value},
        INVALID_CONSTRAINT: {"invalid"},
    }
    invalid_type = route_question(
        valid_question,
        lookup,
        FakeInterpreter(
            {
                "route": "lookup",
                "targets": [
                    {
                        "anchor": valid_question,
                        "constraints": [
                            {
                                "field": "case_id",
                                "raw": CASE_A,
                                "polarity": "include",
                            },
                            {
                                "field": "scheduled_return_date",
                                "raw": date_value,
                                "polarity": "include",
                            },
                        ],
                        "roles": [ROLES[1]],
                    }
                ],
            }
        ),
    )
    assert invalid_type.status == "unresolved"


def test_pack_decision_uses_verified_target_map_and_preserves_fallback(monkeypatch):
    lookup = FakeLookup()
    question = f"예약번호 {CASE_A}의 예외와 {ROLES[3]}를 보여줘"
    interpreter = FakeInterpreter(
        {
            "route": "lookup",
            "targets": [_target(question, CASE_A, ROLES[3])],
        }
    )
    decision = route_question(question, lookup, interpreter)
    ranked = [(page, 1.0) for page in lookup.cases[CASE_B]]
    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router._count_tokens",
        lambda text: len(text.split()),
    )
    calls = []

    def pack_pair_context(
        question,
        ordered,
        lookup,
        baseline,
        *,
        targets,
        context_tokens,
        reserve_general,
    ):
        calls.append((targets, ordered, reserve_general))
        return ordered, baseline, "repacked"

    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router.pack_pair_context",
        pack_pair_context,
    )
    ordered, context, route = pack_decision(question, ranked, lookup, decision)
    assert ordered[0][0] == lookup.cases[CASE_A][3]
    assert ordered[1:] == ranked
    assert context.final_chunks
    assert route == "repacked"
    assert calls[0][0] == {CASE_A: (ROLES[3],)}
    assert calls[0][2] is False

    mixed_interpreter = FakeInterpreter(
        {
            "route": "mixed",
            "targets": [_target(question, CASE_A, ROLES[3])],
        }
    )
    mixed = route_question(question, lookup, mixed_interpreter)
    pack_decision(question, ranked, lookup, mixed)
    assert calls[-1][2] is True

    unresolved = route_question(question, lookup, None)
    fallback_order, fallback_context, fallback_route = pack_decision(
        question, ranked, lookup, unresolved
    )
    assert fallback_order == ranked
    assert [item.chunk.page for item in fallback_context.final_chunks] == [
        page["page"] for page in lookup.cases[CASE_A]
    ]
    assert fallback_route == "interpreter_not_configured"


def test_partial_route_is_reported_after_pack(monkeypatch):
    lookup = FakeLookup()
    question = f"{CASE_A} {ROLES[3]}, 공통 업체 공통 규격 {ROLES[1]}"
    first_anchor = f"{CASE_A} {ROLES[3]}"
    second_anchor = f"공통 업체 공통 규격 {ROLES[1]}"
    interpreter = FakeInterpreter(
        {
            "route": "mixed",
            "targets": [
                _target(first_anchor, CASE_A, ROLES[3]),
                {
                    "anchor": second_anchor,
                    "constraints": [
                        {
                            "field": "shipper",
                            "raw": "공통 업체",
                            "polarity": "include",
                        },
                        {
                            "field": "spec",
                            "raw": "공통 규격",
                            "polarity": "include",
                        },
                    ],
                    "roles": [ROLES[1]],
                },
            ],
        }
    )
    decision = route_question(question, lookup, interpreter)
    assert decision.status == "ambiguous"
    assert decision.query_required
    assert [target.case_id for target in decision.targets] == [CASE_A]
    assert decision.candidate_case_ids == (CASE_A, CASE_B)

    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router._count_tokens",
        lambda text: len(text.split()),
    )
    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router.pack_pair_context",
        lambda question, ordered, lookup, baseline, **kwargs: (
            ordered,
            baseline,
            "repacked_with_general",
        ),
    )
    ranked = [(page, 1.0) for page in lookup.cases[CASE_B]]
    _, _, route = pack_decision(question, ranked, lookup, decision)
    assert route == "repacked_with_general_partial_target_resolution"


def test_pack_decision_caps_unique_candidates_at_twenty(monkeypatch):
    lookup = FakeLookup()
    question = f"예약번호 {CASE_A}의 예외와 {ROLES[3]}를 보여줘"
    decision = route_question(
        question,
        lookup,
        FakeInterpreter(
            {"route": "lookup", "targets": [_target(question, CASE_A, ROLES[3])]}
        ),
    )
    ranked = [
        (
            {
                "book_id": f"OTHER{index:03}",
                "file_name": f"OTHER{index:03}.pdf",
                "page": 1,
                "text": f"비대상 원문 {index}",
            },
            100.0 - index,
        )
        for index in range(25)
    ]
    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router._count_tokens",
        lambda text: len(text.split()),
    )
    captured = {}

    def pack(question, ordered, lookup, baseline, **kwargs):
        captured.update(kwargs)
        return ordered, baseline, "repacked"

    monkeypatch.setattr(
        "experimental.u_10o0o.advanced_question_router.pack_pair_context", pack
    )
    ordered, _, _ = pack_decision(question, ranked, lookup, decision)
    sources = [(page["book_id"], page["page"]) for page, _ in ordered]
    assert len(sources) == 20
    assert len(set(sources)) == 20
    assert sources[0] == (lookup.cases[CASE_A][3]["book_id"], 1)
    assert sources[1] == ("OTHER000", 1)
    assert captured["reserve_general"] is False
