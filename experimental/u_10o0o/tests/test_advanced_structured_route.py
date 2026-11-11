import sqlite3
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from app.generation.context import build_context
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_structured_luna import LookupProposal
from experimental.u_10o0o.advanced_structured_route import Decision, assemble, verify
from experimental.u_10o0o.context_evidence import _count_tokens
from shared.contracts import Chunk, RetrievedChunk


@dataclass(frozen=True)
class Source:
    book_id: str = "AS001"
    file_name: str = "AS001.pdf"
    page: int = 101
    line_start: int = 15
    line_end: int = 15


FIRST = "HG2099-99991"
SECOND = "HG2099-99992"


class Lookup:
    cases = (FIRST, SECOND)
    id_fields = {case: {"case_id"} for case in cases}

    @staticmethod
    def _matched_exact_fields(anchor):
        return {}


class Store:
    def __init__(self):
        self.reads = []

    def profile_candidates(self, family, *, filters):
        self.reads.append((family, filters))
        return [filters["case_id"]] if filters["case_id"] in Lookup.cases else []

    def get_hg_facts(self, case, *, fact_code, role, cost_kind):
        self.reads.append((case, fact_code, role, cost_kind))
        received = fact_code == "received_amount"
        return [
            SimpleNamespace(
                source=Source(page=101 if case == FIRST else 102),
                column_label="날짜",
                state="reported",
                value_type="decimal" if received else "date",
                value_int=None,
                value_text="0.00" if received else "2028-01-01",
                unit=None,
                currency="USD" if received else None,
            )
        ]


def proposal(*spans):
    return LookupProposal.model_validate(
        {
            "route": "lookup",
            "targets": [
                {"family": "HG", "anchor": span, "constraints": [], "fields": [field]}
                for span, field in spans
            ],
        }
    )


def test_direct_and_mixed_sql_lookup():
    store = Store()
    question = f"{FIRST}의 실제 양하일은 언제인가요?"
    result = verify(question, Lookup(), store, rules=True)
    assert result.skip_search
    assert result.queries == 2
    assert result.targets == ((FIRST, (ROLES[1],)),)
    context, packed = assemble(result)
    assert len(context.final_chunks) == 1
    assert packed[0].field == "actual_discharge_date"
    assert packed[0].location == ("AS001", 101)

    mixed = question + " 그리고 관련 규정은 무엇인가요?"
    decision = verify(mixed, Lookup(), store, rules=True)
    assert not decision.skip_search
    assert decision.facts == result.facts


def test_model_keeps_two_roles_bound_to_their_own_spans():
    store = Store()
    left, right = f"{FIRST}의 실제 양하일", f"{SECOND}의 수납액"
    question = left + " 그리고 " + right + "은?"
    decision = verify(
        question,
        Lookup(),
        store,
        proposal=proposal((left, "실제 양하일"), (right, "수납액")),
    )
    assert decision.skip_search
    assert decision.targets == ((FIRST, (ROLES[1],)), (SECOND, (ROLES[4],)))
    assert [(fact.key, fact.field) for fact in decision.facts] == [
        (FIRST, "actual_discharge_date"),
        (SECOND, "received_amount"),
    ]


def test_unbound_second_key_and_unverified_exclusion_do_not_query():
    store = Store()
    anchor = f"{FIRST}의 실제 양하일"
    suggested = proposal((anchor, "실제 양하일"))
    for question in (anchor + f"과 {SECOND}의 반납일은?", anchor + " 빼고 알려줘"):
        decision = verify(question, Lookup(), store, proposal=suggested)
        assert decision.status == "conflict"
        assert decision.queries == 0
    assert not store.reads


def test_hallucinated_field_is_rejected_before_sql():
    store = Store()
    question = f"{FIRST}의 반납 예정일은?"
    decision = verify(
        question, Lookup(), store, proposal=proposal((question, "실제 반납일"))
    )
    assert decision.status == "invalid"
    assert not store.reads


def test_field_cannot_override_the_requested_record_role():
    store = Store()
    question = f"{FIRST}의 {ROLES[1]}에서 수납액은?"
    decision = verify(question, Lookup(), store, rules=True)
    assert decision.status == "conflict"
    assert not store.reads


def test_ambiguous_case_is_never_confirmed():
    class Ambiguous(Store):
        def profile_candidates(self, family, *, filters):
            return [FIRST, SECOND]

    decision = verify(f"{FIRST}의 실제 양하일은?", Lookup(), Ambiguous(), rules=True)
    assert decision.status == "ambiguous"
    assert not decision.facts
    assert not decision.targets


def test_verified_complete_lookup_does_not_invoke_search():
    from experimental.u_10o0o.advanced_structured_benchmark import _structured_result

    store = Store()
    direct = verify(f"{FIRST}의 실제 양하일은?", Lookup(), store, rules=True)

    def unexpected_search():
        raise AssertionError("A complete DB lookup must not search")

    retrieval, facts, ran_search = _structured_result(direct, unexpected_search)
    assert not ran_search
    assert not retrieval[0]
    assert facts == direct.facts
    assert retrieval[2].token_count <= 8000


def test_store_failure_is_visible_to_experiment():
    class FailedStore(Store):
        def profile_candidates(self, family, *, filters):
            raise sqlite3.OperationalError("unavailable")

    with pytest.raises(sqlite3.OperationalError):
        verify(f"{FIRST}의 실제 양하일은?", Lookup(), FailedStore(), rules=True)


def test_mixed_context_keeps_a_document_when_facts_fill_the_page_budget():
    first = verify(f"{FIRST}의 실제 양하일은?", Lookup(), Store(), rules=True).facts[0]
    facts = tuple(
        replace(first, source=replace(first.source, page=page)) for page in range(1, 9)
    )
    rule = RetrievedChunk(
        Chunk("rule", "AS002", "AS002.pdf", 99, "Original rule evidence"),
        1,
        1,
        "document",
    )
    original = build_context([rule], 8, 8000, _count_tokens)
    combined, packed = assemble(Decision("mixed", facts), original)
    assert len(combined.final_chunks) == 8
    assert len(packed) == 7
    assert rule in combined.final_chunks
