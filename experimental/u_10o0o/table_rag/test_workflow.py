"""Behavior checks for post-search SQL, persistent evidence and completion gates."""

import json
from types import SimpleNamespace as NS

import pytest

from experimental.u_10o0o.table_rag.evidence import Ledger, requirements_from
from experimental.u_10o0o.table_rag.workflow import (
    execute,
    Intermediate,
    SQLPlan,
    Model,
)
from experimental.u_10o0o.table_rag.retrieval import source_id

REQ = {"id": "r1", "subject": "사건", "request": "금액", "question_quote": "금액"}
QUESTION = "사건의 금액은?"
FINISH = ("finish", {"status": "sufficient", "reason": "verified"})


def proposal(subquery="세부 금액", requirements=None, ids=None):
    return (
        "solve_subquery",
        {
            "subquery": subquery,
            "requirements": [REQ] if requirements is None else requirements,
            "required_ids": ids or ["r1"],
        },
    )


def middle(eid="sql1:0:amount", quote="10.00", rid="r1", status="supported"):
    return Intermediate(
        intermediate_answer="확인된 금액",
        assessments=[
            {
                "requirement_id": rid,
                "status": status,
                "missing_detail": "",
                "claims": [
                    {
                        "statement": "금액 10.00",
                        "citations": [{"evidence_id": eid, "quote": quote}],
                    }
                ],
            }
        ],
    )


class FakeModel:
    def __init__(self, sequence):
        self.sequence = iter(sequence)
        self.calls = []
        self.payloads = []

    def request(self, stage, payload, **kwargs):
        wanted, value = next(self.sequence)
        assert stage == wanted
        self.calls.append({"stage": stage, "status": "complete", "seconds": 0})
        self.payloads.append((stage, payload))
        return value


class FakeRetriever:
    def __init__(self):
        self.pages = [
            {
                "file_name": "TEST.pdf",
                "book_id": "TEST",
                "page": i,
                "text": f"금액 10.00 확인 자료 {i}",
            }
            for i in (1, 2)
        ]
        self.page_map = {("TEST", p["page"]): p for p in self.pages}
        self.by_source = {source_id(p): p for p in self.pages}
        self.events = []
        self.store = NS(schema=self.schema, query=self.query)

    def schema(self, tids):
        return [{"table_id": t, "columns": [{"name": "amount"}]} for t in tids]

    def query(self, sql, allowed):
        self.events.append(("sql", allowed))
        return {
            "status": "ok",
            "rows": [{"amount": "10.00"}],
            "preview_truncated": False,
            "tables": allowed,
            "sources": [{"book_id": "TEST", "page": 2}],
            "source_scope": "whole table",
        }

    def search(self, q):
        i = 0 if q == QUESTION else 1
        self.events.append(("search", q))
        return {
            "query": q,
            "tables": ["t_original" if i == 0 else "t_new"],
            "ranked": [(self.pages[i], 1.0)],
            "seconds": 0,
            "work": {},
        }


def sql_plan(**overrides):
    data = {
        "sql": "SELECT amount FROM t_new",
        "table_ids": ["t_new"],
        "scope": "this record",
        "global_scope_verified": True,
        "requires_complete_population": False,
        "reason": "field lookup",
    }
    return SQLPlan(**(data | overrides))


def test_sql_is_generated_after_subquery_search_and_intermediate_is_returned():
    retriever = FakeRetriever()
    model = FakeModel(
        [
            ("plan", proposal()),
            ("sql", sql_plan()),
            ("intermediate", middle()),
            ("plan", FINISH),
        ]
    )
    result = execute(QUESTION, retriever, model)
    assert retriever.events == [
        ("search", QUESTION),
        ("search", "세부 금액"),
        ("sql", ["t_new"]),
    ]
    assert model.payloads[1][1]["schemas"][0]["table_id"] == "t_new"
    returned = model.payloads[-1][1]["verified_intermediate"]
    assert returned["intermediate_answers"][0]["statement"] == "금액 10.00"
    assert result["final"]["complete"] and result["finish"]["status"] == "sufficient"


def test_sql_cannot_use_only_previously_observed_schema():
    model = FakeModel(
        [
            ("plan", proposal()),
            ("sql", sql_plan(table_ids=["t_original"])),
            ("intermediate", middle(eid="sql1:0:amount")),
            ("plan", FINISH),
        ]
    )
    r = FakeRetriever()
    result = execute(QUESTION, r, model)
    assert not any(e[0] == "sql" for e in r.events)
    assert result["steps"][0]["sql"]["status"] == "rejected"
    assert not result["final"]["complete"]
    assert result["finish"]["status"] == "insufficient"


def test_invalid_quote_is_not_accepted_as_supported():
    ledger = Ledger([REQ])
    doc = {
        "id": "TEST:p0001",
        "kind": "document",
        "text": "금액 10.00",
        "source_ids": ["TEST:p0001"],
    }
    ledger.ingest(middle("TEST:p0001", "금액 999.00").model_dump(), {"TEST:p0001": doc})
    assert not ledger.complete()
    assert ledger.claims == []
    assert ledger.invalid


def test_requirement_must_quote_original_question():
    with pytest.raises(ValueError):
        requirements_from(QUESTION, [{**REQ, "question_quote": "없는 대상"}])


def test_missing_request_can_trigger_followup_and_be_filled():
    r2 = {**REQ, "id": "r2", "request": "자료", "question_quote": "금액"}
    first = proposal(requirements=[REQ, r2])
    second = proposal("추가 자료", requirements=[], ids=["r2"])
    model = FakeModel(
        [
            ("plan", first),
            ("sql", sql_plan()),
            ("intermediate", middle()),
            ("plan", second),
            ("sql", sql_plan()),
            ("intermediate", middle("sql2:0:amount", rid="r2")),
            ("plan", FINISH),
        ]
    )
    result = execute(QUESTION, FakeRetriever(), model)
    assert len(result["steps"]) == 2
    assert model.payloads[3][1]["coverage"][1]["status"] == "missing"
    assert result["final"]["complete"]
    assert len(result["final"]["payload"]["intermediate_answers"]) == 2


def test_global_population_not_verified_cannot_claim_full_coverage():
    model = FakeModel(
        [
            ("plan", proposal()),
            (
                "sql",
                sql_plan(
                    requires_complete_population=True, global_scope_verified=False
                ),
            ),
            ("intermediate", middle()),
            ("plan", FINISH),
        ]
    )
    result = execute(QUESTION, FakeRetriever(), model)
    assert not result["final"]["complete"]
    assert result["final"]["payload"]["requirements"][0]["status"] == "partial"


def test_sql_failure_can_be_resolved_from_actual_document():
    r = FakeRetriever()
    r.store.query = lambda *args: {
        "status": "sql_error",
        "rows": [],
        "sources": [],
        "sql": "broken",
    }
    model = FakeModel(
        [
            ("plan", proposal()),
            ("sql", sql_plan()),
            ("intermediate", middle("TEST:p0002", "금액 10.00")),
            ("plan", FINISH),
        ]
    )
    result = execute(QUESTION, r, model)
    assert result["final"]["complete"]
    assert result["steps"][0]["sql"]["status"] == "sql_error"


def test_sql_provenance_over_eight_pages_survives_raw_page_limit():
    ledger = Ledger([REQ])
    ids = [f"T:p{i:04d}" for i in range(1, 13)]
    proof = {
        "id": "sql1:0:total",
        "kind": "sql",
        "text": "120",
        "source_ids": ids,
        "query_id": "sql1",
        "row": 0,
        "column": "total",
        "value": "120",
        "preview_truncated": False,
    }
    sql = {
        "status": "ok",
        "sql": "SELECT SUM(amount) FROM full_table",
        "tables": ["full_table"],
        "source_scope": "whole table",
        "preview_truncated": False,
    }
    ledger.ingest(
        middle("sql1:0:total", "120").model_dump(), {"sql1:0:total": proof}, sql
    )
    pages = [
        {"file_name": "T.pdf", "book_id": "T", "page": i, "text": "본문 " * 100}
        for i in range(1, 13)
    ]
    result = ledger.pack([[(p, 1) for p in pages]], {source_id(p): p for p in pages})
    assert result["raw_pages"] == 8
    assert len(result["provenance_sources"]) == 12
    assert result["payload"]["sql_operations"]
    assert result["complete"] and result["tokens"] <= 8000


def test_budget_loss_downgrades_coverage_instead_of_silently_dropping():
    ledger = Ledger([REQ])
    p = {
        "id": "T:p0001",
        "kind": "document",
        "text": "근거 " * 2000,
        "source_ids": ["T:p0001"],
    }
    body = middle("T:p0001", "근거").model_dump()
    body["assessments"][0]["claims"][0]["statement"] = "긴 내용 " * 3000
    ledger.ingest(body, {"T:p0001": p})
    assert ledger.complete()
    packed = ledger.pack([], {}, token_limit=500)
    assert packed["dropped_claims"] == 1
    assert not packed["complete"] and packed["tokens"] <= 500


def test_api_failure_ends_with_available_sources_not_false_success():
    model = FakeModel([("plan", None)])
    result = execute(QUESTION, FakeRetriever(), model)
    assert result["finish"]["status"] == "insufficient"
    assert result["final"]["payload"]["pages"]
    assert not result["final"]["complete"]


def test_actual_function_call_output_carries_intermediate_back_to_planner():
    sent = []
    responses = [
        NS(
            status="completed",
            usage=None,
            output=[
                NS(
                    type="function_call",
                    name="solve_subquery",
                    call_id="c1",
                    arguments=json.dumps(proposal()[1], ensure_ascii=False),
                )
            ],
        ),
        NS(
            status="completed",
            usage=None,
            output=[
                NS(
                    type="function_call",
                    name="finish",
                    call_id="c2",
                    arguments=json.dumps(FINISH[1]),
                )
            ],
        ),
    ]

    def create(**kwargs):
        sent.append(list(kwargs["input"]))
        return responses.pop(0)

    model = Model(client=NS(responses=NS(create=create)))
    assert (
        model.request("plan", {"question": QUESTION}, instructions="test", first=True)[
            0
        ]
        == "solve_subquery"
    )
    assert (
        model.request("plan", {"intermediate_answer": "verified"}, instructions="test")[
            0
        ]
        == "finish"
    )
    feedback = sent[-1][-1]
    assert feedback["type"] == "function_call_output"
    assert feedback["call_id"] == "c1"
    assert "verified" in feedback["output"]


def test_one_bad_reference_rejects_the_whole_claim():
    ledger = Ledger([REQ])
    body = middle("TEST:p0001", "금액 10.00").model_dump()
    body["assessments"][0]["claims"][0]["citations"].append(
        {"evidence_id": "missing", "quote": "USD"}
    )
    proof = {
        "id": "TEST:p0001",
        "kind": "document",
        "text": "금액 10.00",
        "source_ids": ["TEST:p0001"],
    }
    ledger.ingest(body, {"TEST:p0001": proof})
    assert ledger.claims == [] and not ledger.complete()


def test_rejected_claim_is_not_returned_in_the_planner_feedback():
    model = FakeModel(
        [
            ("plan", proposal()),
            ("sql", sql_plan()),
            ("intermediate", middle("TEST:p0002", "존재하지 않는 숫자 999")),
            ("plan", FINISH),
        ]
    )
    result = execute(QUESTION, FakeRetriever(), model)
    feedback = model.payloads[-1][1]
    assert "존재하지 않는 숫자 999" not in json.dumps(feedback, ensure_ascii=False)
    assert not result["final"]["complete"]


def test_answer_cannot_claim_answered_when_coverage_is_missing():
    from experimental.u_10o0o.table_rag.agent import Answer
    from experimental.u_10o0o.table_rag.workflow import answer

    model = FakeModel(
        [("answer", Answer(answer="부분 답변", citations=[], status="answered"))]
    )
    result = answer(
        model, QUESTION, {"pages": [], "requirements": [{"status": "missing"}]}
    )
    assert result["answer"]["status"] == "insufficient"
    assert result["coverage_guard_applied"]


def test_reference_gate_rejects_partial_experiment(tmp_path):
    from experimental.u_10o0o.table_rag.workflow_experiment import saved_results

    (tmp_path / "run.json").write_text(
        json.dumps({"status": "partial", "count": 1, "references_opened": False})
    )
    with pytest.raises(ValueError, match="280"):
        saved_results(tmp_path)
