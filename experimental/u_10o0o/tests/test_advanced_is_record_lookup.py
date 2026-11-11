import sqlite3

import pytest

from experimental.u_10o0o import advanced_structured_store as schema
from experimental.u_10o0o.advanced_is_record_lookup import (
    lookup_record,
    record_codes,
    resolve_records,
)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    path = root / "advanced.sqlite3"
    db = sqlite3.connect(path)
    schema._create_schema(db)
    db.execute(
        "INSERT INTO source_documents VALUES (?,?,?,?)",
        ("AS001", "AS001.pdf", "a" * 64, 8),
    )
    for page, family in ((1, "IS"), (2, "IS"), (3, "IS"), (4, "HG")):
        db.execute(
            "INSERT INTO source_pages VALUES (?,?,?,?,?)",
            (f"p{page}", "AS001", page, family, "synthetic"),
        )
    entries = [
        ("si", "p1", "parsed", "SI기록", "IS03-003-01"),
        ("payment", "p2", "parsed", "입금 기록", "IS47-010-01 합성 주석"),
        ("notice", "p2", "parsed", "수정통지", "IS46-020-01 합성 주석"),
        ("bad_annotation", "p3", "parsed", "입금 기록", "IS47-010-02 IS47-010-03"),
        ("unsupported_annotation", "p3", "parsed", "SI기록", "IS03-003-06 합성 주석"),
        ("old", "p1", "parsed", "기록번호", "IS03-003-02"),
        ("other", "p2", "parsed", "VGM 대조", "IS04-007-01"),
        ("duplicate1", "p1", "parsed", "접수번호", "IS01-001-01"),
        ("duplicate2", "p2", "parsed", "접수번호", "IS01-001-01"),
        ("unreviewed", "p3", "needs_review", "SI기록", "IS03-003-03"),
        ("wrong_field", "p3", "parsed", "대상", "IS03-003-04"),
        ("wrong_family", "p4", "parsed", "SI기록", "IS03-003-05"),
        ("prefix_only", "p3", "parsed", "SI기록", "IS03-003-01-X"),
    ]
    for rid, sid, status, field, key in entries:
        db.execute(
            "INSERT INTO source_records VALUES (?,?,?,?,?,?,?)",
            (rid, sid, 10, 12, "synthetic", "table_row", status),
        )
        db.execute("INSERT INTO record_fields VALUES (?,?,?,?)", (rid, 0, field, key))
        db.execute(
            "INSERT INTO record_fields VALUES (?,?,?,?)", (rid, 1, "처리 상태", "미결")
        )
    db.commit()
    db.close()
    outputs = {"advanced.sqlite3": schema._sha_file(path)}
    inputs = {"fixture": "IS"}
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


@pytest.mark.parametrize(
    "text",
    [
        "IS03-003-01 처리 상태",
        "is03-003-01은?",
        "IS03 – 003 – 01 제출처",
    ],
)
def test_exact_key_and_variants_recover_row_and_source(store, text):
    result = resolve_records(text, store)
    assert result.status == "resolved"
    assert result.keys == ("IS03-003-01",)
    (row,) = result.records
    assert row.fields == {"SI기록": "IS03-003-01", "처리 상태": "미결"}
    assert (row.source.book_id, row.source.page) == ("AS001", 1)
    assert (row.source.line_start, row.source.line_end) == (10, 12)
    assert row.source.pdf_sha256 == "a" * 64
    assert row.case_id == ""


def test_old_scope_and_two_records_keep_request_order(store):
    assert resolve_records("IS03-003-01", store, legacy_only=True).status == "not_found"
    assert resolve_records("IS03-003-02", store, legacy_only=True).status == "resolved"
    result = resolve_records("IS04-007-01과 IS03-003-01 비교", store)
    assert [r.source.page for r in result.records] == [2, 1]


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("IS01-001-01", "ambiguous"),
        ("IS03-003-03", "not_found"),
        ("IS03-003-04", "not_found"),
        ("IS03-003-05", "not_found"),
        ("IS99-999-99", "not_found"),
        ("IS03-003-01 말고 IS04-007-01", "exclusion_unresolved"),
        ("IS03-003-01 IS04-007-01 IS03-003-02", "too_many_records"),
        ("IS03-003-01 IS99-999-99", "not_found"),
        ("처리 상태가 아직 미결인가요?", "no_supported_key"),
    ],
)
def test_unsupported_or_ambiguous_preserves_search_path(store, text, status):
    result = resolve_records(text, store)
    assert result.status == status
    assert not result.records


@pytest.mark.parametrize(
    "text",
    [
        "IS03",
        "IS03-003",
        "IS03-003-01-X",
        "XIS03-003-01",
        "_IS03-003-01",
        "IS03-003-01_",
        "IS03-\n003-01",
        "IS03-003-010",
    ],
)
def test_partial_and_embedded_identifiers_do_not_become_record_keys(text):
    assert record_codes(text) == ()


def test_invalid_key_rejected_and_db_remains_read_only(store):
    with pytest.raises(ValueError):
        lookup_record(store, "IS03-003-01' OR 1=1")
    with pytest.raises(sqlite3.OperationalError):
        store._execute("DELETE FROM record_fields")


def test_annotated_record_keys_keep_the_original_cell(store):
    result = resolve_records("IS47-010-01과 IS46-020-01 비교", store)
    assert result.status == "resolved"
    assert [r.role for r in result.records] == ["입금 기록", "수정통지"]
    assert result.records[0].fields["입금 기록"] == "IS47-010-01 합성 주석"
    assert resolve_records("IS47-010-02", store).status == "not_found"
    assert resolve_records("IS03-003-06", store).status == "not_found"


def test_resolved_original_page_is_added_with_existing_context_limits(store):
    from types import SimpleNamespace

    from app.generation.context import build_context
    from experimental.u_10o0o.advanced_source_lookup import apply_sources
    from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens

    ranked = [
        ({"file_name": "AS001.pdf", "page": p, "text": "합성 대조 문서"}, 1.0 / p)
        for p in range(20, 40)
    ]
    packed = build_context(_context_candidates(ranked), 8, 8000, _count_tokens)
    (record,) = resolve_records("IS03-003-01 처리 상태", store).records
    original = {
        "file_name": record.source.file_name,
        "page": record.source.page,
        "text": "IS03-003-01 합성 원본 설명과 표 및 예외",
    }
    current, context, _ = apply_sources(
        "IS03-003-01 처리 상태",
        ranked,
        SimpleNamespace(),
        8000,
        [original],
        (ranked, packed, "baseline"),
    )
    assert len(current) == 20
    assert len(context.final_chunks) == 8
    assert context.token_count <= 8000
    assert context.final_chunks[0].chunk.page == 1
    assert context.final_chunks[0].chunk.text == original["text"]
    assert len({(p["file_name"], p["page"]) for p, _ in current}) == 20
