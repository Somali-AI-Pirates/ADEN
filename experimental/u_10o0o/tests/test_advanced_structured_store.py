import sqlite3

import pytest

from experimental.u_10o0o import advanced_structured_store as store_module
from experimental.u_10o0o.advanced_case_sources import ROLES


def _fixture_snapshot(root):
    root.mkdir()
    database = root / "advanced.sqlite3"
    connection = sqlite3.connect(database)
    store_module._create_schema(connection)
    connection.executemany(
        "INSERT INTO source_documents VALUES (?, ?, ?, ?)",
        [
            ("AS001", "AS001.pdf", "a" * 64, 1),
            ("AS002", "AS002.pdf", "b" * 64, 1),
            ("AS003", "AS003.pdf", "c" * 64, 1),
        ],
    )
    source_rows = [
        ("advanced:AS001:p1", "AS001", 1, "HG", "HG page"),
        ("advanced:AS002:p1", "AS002", 1, "HA", "HA page"),
        ("advanced:AS003:p1", "AS003", 1, "IS", "IS page"),
    ]
    connection.executemany(
        "INSERT INTO source_pages VALUES (?, ?, ?, ?, ?)", source_rows
    )
    case_id = "HG000000000001"
    ha_case_id = "HA28-0001"
    connection.executemany(
        "INSERT INTO cases VALUES (?, ?)", [("HG", case_id), ("HA", ha_case_id)]
    )
    connection.executemany(
        "INSERT INTO case_records VALUES (?, ?, ?, ?, ?)",
        [
            ("HG", case_id, ROLES[0], "advanced:AS001:p1", "page_heading_case_id"),
            (
                "HA",
                ha_case_id,
                store_module.HA_ROLES[0],
                "advanced:AS002:p1",
                "page_heading_case_id",
            ),
        ],
    )
    records = [
        ("hg-record-1", "advanced:AS001:p1", 1, 3, "HG section", "table_row", "parsed"),
        ("ha-row-1", "advanced:AS002:p1", 4, 5, "HA section", "table_row", "parsed"),
        ("is-row-1", "advanced:AS003:p1", 6, 7, "IS section", "table_row", "parsed"),
        ("is-row-2", "advanced:AS003:p1", 8, 9, "IS section", "table_row", "parsed"),
    ]
    connection.executemany(
        "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?)", records
    )
    fields = [
        ("ha-row-1", 0, "장비", "AB-123"),
        ("ha-row-1", 1, "금액", "0"),
        ("is-row-1", 0, "기록번호", "IS-0001"),
        ("is-row-2", 0, "기록번호", "IS-0001"),
    ]
    connection.executemany("INSERT INTO record_fields VALUES (?, ?, ?, ?)", fields)
    connection.execute(
        "INSERT INTO case_profiles VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("HG", case_id, "hg-record-1", "acme", "steel coils", "20gp", "CT-0001"),
    )
    fact_rows = [
        (
            "hg-record-1",
            "HG",
            case_id,
            ROLES[0],
            "actual_discharge_date",
            "none",
            "날짜",
            "2025-05-01",
            "reported",
            "date",
            "2025-05-01",
            None,
            '"2025-05-01"',
            None,
            None,
            None,
            "advanced:AS001:p1",
        ),
        (
            "hg-record-1",
            "HG",
            case_id,
            ROLES[0],
            "comparison_equipment",
            "none",
            "장비",
            "AB-123",
            "reported",
            "text",
            "AB-123",
            None,
            '"AB-123"',
            None,
            None,
            None,
            "advanced:AS001:p1",
        ),
    ]
    connection.executemany(
        "INSERT INTO role_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        fact_rows,
    )
    connection.executemany(
        "INSERT INTO handbook_records VALUES (?, ?, ?)",
        [("기록번호", "IS-0001", "is-row-1"), ("기록번호", "IS-0001", "is-row-2")],
    )
    connection.commit()
    connection.close()

    outputs = {"advanced.sqlite3": store_module._sha_file(database)}
    inputs = {"fixture": "structured-store"}
    manifest = {
        "schema_version": store_module.SCHEMA_VERSION,
        "snapshot_type": store_module.SNAPSHOT_TYPE,
        "snapshot_id": store_module._sha_bytes(
            store_module._json_bytes({"inputs": inputs, "outputs": outputs})
        ),
        "inputs": inputs,
        "outputs": outputs,
        "counts": {},
        "config": {},
    }
    (root / "manifest.json").write_bytes(store_module._json_bytes(manifest))
    return root, case_id, ha_case_id


def test_hg_fact_and_identity_lookups_preserve_state_and_source(tmp_path):
    root, case_id, _ = _fixture_snapshot(tmp_path / "snapshot")
    with store_module.StructuredStore(root) as store:
        facts = store.get_hg_facts(case_id, fact_code="actual_discharge_date")
        assert len(facts) == 1
        assert facts[0].value == "2025-05-01"
        assert facts[0].state == "reported"
        assert facts[0].source.page == 1
        assert facts[0].source.pdf_sha256 == "a" * 64
        assert facts[0].source.line_start == 1
        assert store.query_count == 1

        assert store.identities_for("case_id", case_id) == {case_id}
        assert store.identities_for("actual_discharge_date", "2025-05-01") == {case_id}
        assert store.identities_for("equipment_id", "ab-123") == {case_id}
        assert store.profile_candidates(
            filters={"shipper": "ACME", "spec": "20GP"}
        ) == {case_id}


def test_ha_rows_use_exact_role_and_qualifiers(tmp_path):
    root, _, ha_case_id = _fixture_snapshot(tmp_path / "snapshot")
    with store_module.StructuredStore(root) as store:
        rows = store.get_role_rows(
            "HA", ha_case_id, qualifiers={"장비": "AB-123", "금액": "0"}
        )
        assert len(rows) == 1
        assert rows[0].role == store_module.HA_ROLES[0]
        assert rows[0].fields == {"장비": "AB-123", "금액": "0"}
        assert rows[0].source.line_end == 5
        assert store.query_count == 2

        with pytest.raises(ValueError, match="역할"):
            store.get_role_rows("HA", ha_case_id, "지원 안 되는 역할")


def test_handbook_lookup_preserves_duplicate_key_candidates(tmp_path):
    root, _, _ = _fixture_snapshot(tmp_path / "snapshot")
    with store_module.StructuredStore(root) as store:
        rows = store.lookup_handbook("기록번호", "IS-0001")
        assert [row.record_id for row in rows] == ["is-row-1", "is-row-2"]
        assert all(row.family == "IS" for row in rows)
        assert all(row.fields == {"기록번호": "IS-0001"} for row in rows)
        assert store.query_count == 3
        with pytest.raises(ValueError, match="키 종류"):
            store.lookup_handbook("unsupported", "IS-0001")


def test_store_is_read_only_and_requires_context_manager(tmp_path):
    root, case_id, _ = _fixture_snapshot(tmp_path / "snapshot")
    store = store_module.StructuredStore(root)
    with pytest.raises(RuntimeError, match="열어야"):
        store.get_hg_facts(case_id)
    with store:
        with pytest.raises(sqlite3.OperationalError):
            store._db().execute("DELETE FROM cases")
        with pytest.raises(ValueError, match="식별 필드"):
            store.identities_for("raw_sql", "anything")
