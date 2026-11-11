"""Synthetic row/query tests; no evaluation gold or external API calls."""

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from experimental.u_10o0o import advanced_structured_store as schema
from experimental.u_10o0o.advanced_schema_benchmark import invoke
from experimental.u_10o0o.advanced_schema_query import (
    TablePlan,
    build_catalog,
    cell_match,
    execute_plan,
    public_card,
    validate_plan,
)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    path = root / "advanced.sqlite3"
    db = sqlite3.connect(path)
    schema._create_schema(db)
    for book in ("AS001", "AS002"):
        db.execute(
            "INSERT INTO source_documents VALUES (?,?,?,?)",
            (book, book + ".pdf", "a" * 64, 20),
        )
    entries = [
        (
            "r1",
            "AS001",
            1,
            {
                "입금 기록": "IS47-001-01 합성 주석",
                "미배분": "1,000원",
                "연결청구": "INV-ONE",
                "실제 양하일": "2029-01-01",
            },
        ),
        (
            "r2",
            "AS001",
            2,
            {
                "입금 기록": "IS47-001-02",
                "미배분": "0원",
                "연결청구": "INV-TWO",
                "실제 양하일": "2029-02-01",
            },
        ),
        (
            "r3",
            "AS001",
            3,
            {
                "입금 기록": "IS47-001-03",
                "미배분": "미결",
                "연결청구": "INV-DUP",
                "실제 양하일": "검토 필요",
            },
        ),
        (
            "r4",
            "AS002",
            4,
            {
                "입금 기록": "IS48-001-01",
                "미배분": "5USD",
                "연결청구": "INV-FOREIGN",
                "실제 양하일": "2029-01-01",
            },
        ),
        ("i1", "AS001", 5, {"청구번호": "INV-ONE", "청구·상태": "검토 필요"}),
        ("i2", "AS001", 6, {"청구번호": "INV-DUP", "청구·상태": "미결"}),
        ("i3", "AS002", 7, {"청구번호": "INV-DUP", "청구·상태": "확정"}),
    ]
    for rid, book, page, fields in entries:
        db.execute(
            "INSERT INTO source_pages VALUES (?,?,?,?,?)",
            (rid, book, page, "IS", book + " > 합성 입금업무 > 표"),
        )
        db.execute(
            "INSERT INTO source_records VALUES (?,?,?,?,?,?,?)",
            (rid, rid, 10, 12, "합성", "table_row", "parsed"),
        )
        db.executemany(
            "INSERT INTO record_fields VALUES (?,?,?,?)",
            [(rid, n, k, v) for n, (k, v) in enumerate(fields.items())],
        )
    db.commit()
    db.close()
    outputs, inputs = (
        {"advanced.sqlite3": schema._sha_file(path)},
        {"fixture": "schema"},
    )
    manifest = {
        "schema_version": schema.SCHEMA_VERSION,
        "snapshot_type": schema.SNAPSHOT_TYPE,
        "snapshot_id": schema._sha_bytes(
            schema._json_bytes({"inputs": inputs, "outputs": outputs})
        ),
        "inputs": inputs,
        "outputs": outputs,
    }
    (root / "manifest.json").write_bytes(schema._json_bytes(manifest))
    with schema.StructuredStore(root) as opened:
        yield opened


def plan_for(
    card,
    question,
    *,
    field="입금 기록",
    raw="IS47-001-01",
    operator="text_contains",
    cardinality="one",
    follow=False,
):
    return TablePlan.model_validate(
        {
            "route": "lookup",
            "requests": [
                {
                    "schema_id": card["schema_id"],
                    "anchor": question,
                    "requested_columns": ["미배분"],
                    "filters": [
                        {
                            "field": field,
                            "operator": operator,
                            "raw": raw,
                            "quote": question,
                        }
                    ],
                    "cardinality": cardinality,
                    "follow_invoice": follow,
                }
            ],
        }
    )


def payments(store):
    return next(
        c
        for c in build_catalog(store)["cards"]
        if c["book_id"] == "AS001" and "r1" in c["member_ids"]
    )


def test_business_schema_separation_and_payload_allowlist(store):
    catalog = build_catalog(store)
    assert catalog["rows"] == 7
    assert len(catalog["cards"]) == 4
    card = payments(store)
    assert set(card["member_ids"]) == {"r1", "r2", "r3"}
    public = json.dumps(public_card(card), ensure_ascii=False)
    assert "IS47-001-01" not in public
    assert "INV-ONE" not in public
    assert "member_ids" not in public
    assert "1,000원" not in public
    assert "미배분" in public


def test_whole_record_key_source_and_unique_invoice_join(store):
    card = payments(store)
    question = "IS47-001-01의 미배분과 연결 청구도 보여줘"
    plan = plan_for(card, question, follow=True)
    result = execute_plan(store, question, plan, [card])
    assert result["status"] == "resolved"
    assert [r.record_id for r in result["records"]] == ["r1", "i1"]
    assert result["records"][0].fields["미배분"] == "1,000원"
    assert result["records"][1].fields["청구·상태"] == "검토 필요"
    assert result["records"][0].source.line_start == 10
    with pytest.raises(sqlite3.OperationalError):
        store._execute("DELETE FROM record_fields")


def test_ambiguous_link_rejects_all_records(store):
    card = payments(store)
    question = "IS47-001-03의 미배분과 연결 청구도 보여줘"
    result = execute_plan(
        store,
        question,
        plan_for(card, question, raw="IS47-001-03", follow=True),
        [card],
    )
    assert result["status"] == "ambiguous_invoice"
    assert not result["records"]


@pytest.mark.parametrize(
    ("cell", "operator", "raw", "kind", "match"),
    [
        ("1,000원", "gt", "0원", "number", 1),
        ("KRW 1,000.25", "gt", "1,000.24원", "number", 1),
        ("0원", "eq", "0원", "number", 1),
        ("미결", "eq", "0원", "number", 0),
        ("검토 필요", "ne", "0원", "number", 0),
        ("1USD", "gt", "0원", "number", 0),
        ("100원 예정", "gt", "0원", "number", 0),
        ("1,000원", "gt", "0", "number", 0),
        ("2029-01-01", "eq", "2029년 1월 1일", "date", 1),
        ("2029-02-30", "eq", "2029-02-30", "date", 0),
        ("미결", "text_contains", "미결", None, 1),
    ],
)
def test_typed_comparisons_preserve_units_and_unknown_states(
    cell, operator, raw, kind, match
):
    assert cell_match(cell, operator, raw, kind) == match


def test_numeric_filter_returns_only_verified_amount(store):
    card = payments(store)
    q = "미배분이 0원 초과인 기록들을 보여줘"
    result = execute_plan(
        store,
        q,
        plan_for(card, q, field="미배분", raw="0원", operator="gt", cardinality="many"),
        [card],
    )
    assert result["status"] == "resolved"
    assert [r.record_id for r in result["records"]] == ["r1"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda r: r.update(schema_id="not-discovered"), "unknown_schema"),
        (lambda r: r.update(requested_columns=["숨긴필드"]), "unknown_requested_field"),
        (lambda r: r["filters"][0].update(raw="invented"), "unanchored_value"),
        (lambda r: r["filters"][0].update(field="unknown"), "unsupported_operator"),
        (lambda r: r.update(filters=[]), "missing_positive_condition"),
    ],
)
def test_invalid_plan_does_not_execute_sql(store, mutation, expected):
    card = payments(store)
    q = "IS47-001-01 미배분"
    raw = plan_for(card, q).model_dump()
    mutation(raw["requests"][0])
    before = store.query_count
    result = execute_plan(store, q, TablePlan.model_validate(raw), [card])
    assert result["status"] == expected
    assert store.query_count == before
    assert result["records"] == []


def test_date_role_and_exclusion_are_not_silently_lost(store):
    card = payments(store)
    q = "예정 양하일 2029-01-01인 기록의 미배분"
    plan = plan_for(card, q, field="실제 양하일", raw="2029-01-01", operator="eq")
    assert validate_plan(q, plan, [card]) == "unverified_date_role"
    q = "IS47-001-01 말고 IS47-001-02 미배분"
    plan = plan_for(card, q, raw="IS47-001-02")
    assert validate_plan(q, plan, [card]) == "unhandled_exclusion"


def test_ambiguous_single_record_is_not_first_row(store):
    card = payments(store)
    q = "IS47 기록 미배분"
    result = execute_plan(store, q, plan_for(card, q, raw="IS47"), [card])
    assert result["status"] == "ambiguous_rows"
    assert result["records"] == []


def test_overlapping_targets_and_pure_documents(store):
    card = payments(store)
    q = "IS47-001-01 미배분"
    plan = plan_for(card, q)
    plan.requests.append(plan.requests[0])
    assert validate_plan(q, plan, [card]) == "overlapping_targets"
    result = execute_plan(
        store, "일반 절차", TablePlan(route="documents", requests=[]), [card]
    )
    assert result["status"] == "documents"
    assert result["sql_statements"] == 0


def test_api_plan_has_no_rows_and_timeout_has_no_retry(store):
    card = payments(store)

    class FakeResponses:
        calls = 0

        def parse(self, **kwargs):
            self.calls += 1
            assert "member_ids" not in kwargs["input"]
            assert "INV-ONE" not in kwargs["input"]
            assert kwargs["reasoning"] == {"effort": "low"}
            assert kwargs["store"] is False
            raise APITimeoutError(
                request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            )

    responses = FakeResponses()
    result = invoke(
        SimpleNamespace(responses=responses), "일반 업무 질문", [card], "low"
    )
    assert result["plan"] is None
    assert result["telemetry"]["status"] == "timeout"
    assert responses.calls == 1
