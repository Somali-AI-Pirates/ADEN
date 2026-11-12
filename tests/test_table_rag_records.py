import json
import sqlite3
from hashlib import sha256

import pytest

from app.rag.records import RecordIndex, canonical_spec, codes, grounded, norm
from app.rag.keyword_retriever import tokenize_for_bm25


def _digest(path):
    return sha256(path.read_bytes()).hexdigest()


def _refresh_database_hash(directory):
    manifest_path = directory / "records.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["database_sha256"] = _digest(directory / "records.sqlite3")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _snapshot(directory):
    directory.mkdir()
    pages = [
        {"file_name": "AS001.pdf", "page": number}
        for number in (1, 2, 4, 8)
    ] + [
        {"file_name": "AS055.pdf", "page": 1},
        {"file_name": "IS.pdf", "page": 7},
    ]
    table = {
        "table_id": "t_cost",
        "source_id": "AS001:p0008",
        "book_id": "AS001",
        "file_name": "AS001.pdf",
        "page": 8,
        "heading": "비용 청구",
        "section": "비용",
        "page_family": "HG",
        "line_start": 1,
        "line_end": 2,
        "row_count": 1,
        "columns": [],
        "schema_sha256": "0" * 64,
    }
    (directory / "pages.json").write_text(
        json.dumps(pages), encoding="utf-8"
    )
    (directory / "catalog.json").write_text(
        json.dumps([table], ensure_ascii=False), encoding="utf-8"
    )
    database_path = directory / "records.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE entities(id TEXT PRIMARY KEY, family TEXT NOT NULL,
                kind TEXT NOT NULL, label TEXT NOT NULL);
            CREATE TABLE sources(entity TEXT REFERENCES entities(id), source TEXT NOT NULL,
                role TEXT NOT NULL, ordinal INTEGER NOT NULL,
                PRIMARY KEY(entity,source,role));
            CREATE TABLE keys(value TEXT NOT NULL, kind TEXT NOT NULL,
                entity TEXT REFERENCES entities(id), source TEXT NOT NULL,
                relation TEXT NOT NULL, PRIMARY KEY(value,entity,source,relation));
            CREATE INDEX key_value ON keys(value);
            CREATE INDEX keys_by_entity ON keys(entity,kind,value);
            CREATE TABLE attributes(entity TEXT REFERENCES entities(id), field TEXT NOT NULL,
                value TEXT NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY(entity,field,value,source));
            CREATE INDEX attribute_value ON attributes(field,value);
            CREATE TABLE tables(id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE VIRTUAL TABLE table_terms USING fts5(table_id UNINDEXED, tokens);
            INSERT INTO entities VALUES('HG:HG2029-00660','HG','case','HG2029-00660');
            INSERT INTO entities VALUES('HA:HA28-100','HA','case','HA28-100');
            INSERT INTO entities VALUES('IS:IS-ADV-021','IS','record','IS-ADV-021');
            INSERT INTO sources VALUES('HG:HG2029-00660','AS001:p0004','invoice',3);
            INSERT INTO sources VALUES('HG:HG2029-00660','AS001:p0001','contract',0);
            INSERT INTO sources VALUES('HG:HG2029-00660','AS001:p0002','events',1);
            INSERT INTO sources VALUES('HA:HA28-100','AS055:p0001','record',0);
            INSERT INTO sources VALUES('IS:IS-ADV-021','IS:p0007','record',0);
            INSERT INTO keys VALUES('HG2029-00660','reservation_id','HG:HG2029-00660',
                'AS001:p0001','identity');
            INSERT INTO keys VALUES('HL-20GP-0066','equipment_id','HG:HG2029-00660',
                'AS001:p0002','identity');
            INSERT INTO keys VALUES('HA28-100','reservation_id','HA:HA28-100',
                'AS055:p0001','identity');
            INSERT INTO keys VALUES('IS-ADV-021','record_id','IS:IS-ADV-021',
                'IS:p0007','identity');
            INSERT INTO attributes VALUES('HG:HG2029-00660','spec','20gp','AS001:p0002');
            INSERT INTO attributes VALUES('HG:HG2029-00660','actual_gate_out_date',
                '2029-02-03','AS001:p0002');
            INSERT INTO attributes VALUES('HA:HA28-100','actual_gate_out_date',
                '2029-02-04','AS055:p0001');
            """
        )
        db.execute(
            "INSERT INTO tables VALUES(?,?,?)",
            (
                "t_cost",
                "AS001:p0008",
                json.dumps(table, ensure_ascii=False, sort_keys=True),
            ),
        )
        db.execute(
            "INSERT INTO table_terms VALUES(?,?)",
            (
                "t_cost",
                " ".join(
                    tokenize_for_bm25("비용 청구 invoice amount HL-20GP-0066")
                ),
            ),
        )
    manifest = {
        "schema_version": 1,
        "version": 1,
        "database_sha256": _digest(database_path),
        "inputs": {
            "pages_sha256": _digest(directory / "pages.json"),
            "catalog_sha256": _digest(directory / "catalog.json"),
        },
        "counts": {
            "entities": 3,
            "keys": 4,
            "attributes": 3,
            "sources": 5,
            "tables": 1,
        },
        "families": {
            "HG": {"entities": 1, "sources": 3},
            "HA": {"entities": 1, "sources": 1},
            "IS": {"entities": 1, "sources": 1},
        },
    }
    (directory / "records.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return database_path


@pytest.fixture
def records_directory(tmp_path):
    directory = tmp_path / "bundle"
    _snapshot(directory)
    return directory


def test_normalization_preserves_complete_ids_and_korean_spec_aliases():
    assert codes("장비 HL－20GP－0066, 20GP") == ("HL-20GP-0066",)
    assert norm("  해강　기재 ") == "해강 기재"
    assert canonical_spec("20피트 일반형 컨테이너") == "20gp"


def test_lookup_keeps_entities_and_roles_separate(records_directory):
    index = RecordIndex(records_directory)

    result = index.lookup(
        [
            {"identifiers": ["HG2029-00660"], "constraints": [], "roles": ["invoice"]},
            {"identifiers": ["HA28-100"], "constraints": [], "roles": []},
            {"identifiers": ["IS-ADV-021"], "constraints": [], "roles": []},
        ]
    )

    assert [target["status"] for target in result["targets"]] == [
        "matched",
        "matched",
        "matched",
    ]
    first = result["targets"][0]["entities"][0]
    assert first["id"] == "HG:HG2029-00660"
    assert first["sources"] == [
        {"source": "AS001:p0004", "role": "invoice", "ordinal": 3}
    ]
    all_roles = index.lookup(
        [{"identifiers": ["HG2029-00660"], "constraints": [], "roles": []}]
    )["targets"][0]["entities"][0]["sources"]
    assert [source["role"] for source in all_roles] == [
        "contract",
        "events",
        "invoice",
    ]
    assert result["targets"][1]["entities"][0]["family"] == "HA"
    assert result["targets"][2]["entities"][0]["family"] == "IS"


def test_lookup_intersects_typed_constraints_and_detects_conflicts(records_directory):
    index = RecordIndex(records_directory)

    result = index.lookup(
        [
            {
                "identifiers": ["HL-20GP-0066"],
                "constraints": [
                    {"field": "spec", "value": "20피트 일반형"},
                    {"field": "actual_gate_out_date", "value": "2029-02-03"},
                ],
                "roles": ["events"],
            },
            {
                "identifiers": ["HL-20GP-0066"],
                "constraints": [
                    {"field": "actual_gate_out_date", "value": "2029-02-04"}
                ],
                "roles": [],
            },
        ]
    )

    assert [target["status"] for target in result["targets"]] == ["matched", "conflict"]
    assert result["targets"][0]["entities"][0]["sources"][0]["role"] == "events"


def test_lookup_requires_complete_identifier_and_supported_fields(records_directory):
    index = RecordIndex(records_directory)

    with pytest.raises(ValueError, match="complete identifier"):
        index.lookup([{"identifiers": ["HL20GP0066"], "constraints": [], "roles": []}])
    partial = index.lookup(
        [{"identifiers": ["HL-20GP"], "constraints": [], "roles": []}]
    )
    assert partial["targets"][0]["status"] == "not_found"
    with pytest.raises(ValueError, match="typed constraint"):
        index.lookup([{"identifiers": [], "constraints": [], "roles": []}])
    with pytest.raises(ValueError, match="typed constraint"):
        index.lookup(
            [
                {
                    "identifiers": [],
                    "constraints": [{"field": "unknown", "value": "x"}],
                    "roles": [],
                }
            ]
        )


def test_table_discovery_uses_read_only_fts_catalog(records_directory):
    index = RecordIndex(records_directory)

    assert index.discover("청구 비용 invoice") == ["t_cost"]
    assert index.discover("HL－20GP－0066") == ["t_cost"]

    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(f"file:{index.path}?mode=ro", uri=True) as db:
            db.execute("DELETE FROM entities")


def test_record_snapshot_fingerprints_are_enforced(tmp_path):
    database = _snapshot(tmp_path / "bundle")
    (tmp_path / "bundle" / "pages.json").write_text("[{}]", encoding="utf-8")

    with pytest.raises(ValueError, match="does not match pages.json"):
        RecordIndex(tmp_path / "bundle")

    assert database.is_file()


def test_record_manifest_does_not_accept_boolean_schema_version(records_directory):
    manifest_path = records_directory / "records.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        RecordIndex(records_directory)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE sources SET source='AS099:p0001' WHERE role='contract'",
            "original page",
        ),
        (
            "UPDATE keys SET entity='missing-entity' WHERE value='HL-20GP-0066'",
            "foreign-key violation",
        ),
        (
            "UPDATE keys SET source='AS001:p0008' WHERE value='HL-20GP-0066'",
            "keys source",
        ),
        (
            "UPDATE attributes SET source='AS001:p0008' "
            "WHERE field='actual_gate_out_date'",
            "attributes source",
        ),
        (
            "UPDATE tables SET source='AS001:p0001' WHERE id='t_cost'",
            "does not match the catalog",
        ),
    ],
)
def test_updated_database_fingerprint_does_not_hide_invalid_source_links(
    records_directory, statement, message
):
    with sqlite3.connect(records_directory / "records.sqlite3") as db:
        db.execute(statement)
    _refresh_database_hash(records_directory)

    with pytest.raises(ValueError, match=message):
        RecordIndex(records_directory)


def test_lookup_grounding_rejects_excluded_or_invented_values():
    class Request:
        class Target:
            identifiers = ["HG2029-00660"]
            constraints = []

        targets = [Target()]

    with pytest.raises(ValueError, match="explicitly excluded"):
        grounded(Request(), "HG2029-00660은 제외하고 조회", [])

    Request.Target.identifiers = ["HG2029-00000"]
    with pytest.raises(ValueError, match="not grounded"):
        grounded(Request(), "HG2029-00660을 조회", [])
