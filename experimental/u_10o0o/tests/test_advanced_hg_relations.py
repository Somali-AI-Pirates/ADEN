import sqlite3

import pytest

from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o import advanced_hg_relations as relation_module
from experimental.u_10o0o.advanced_hg_relations import (
    HGRelations,
    _code_type,
    _normalized_code,
)


def _relation_store():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(relation_module._SCHEMA)
    rows = []
    for case_id, book_id in (("HG2029-00001", "AS057"), ("HG2029-00002", "AS058")):
        for order, role in enumerate(ROLES):
            source_id = f"advanced:{book_id}:p{order + 1}"
            rows.append((case_id, role, order, source_id, book_id, order + 1))
    connection.executemany("INSERT INTO case_pages VALUES (?, ?, ?, ?, ?, ?)", rows)
    links = [
        (
            "reservation_id",
            "HG2029-00001",
            "exact",
            "HG2029-00001",
            ROLES[0],
            "advanced:AS057:p1",
            None,
            None,
        ),
        (
            "evidence_id",
            "EV-SH-00660-D",
            "exact",
            "HG2029-00001",
            ROLES[1],
            "advanced:AS057:p2",
            None,
            None,
        ),
        (
            "equipment_id",
            "HL-20GP-00660",
            "class_group",
            "HG2029-00001",
            ROLES[1],
            "advanced:AS057:p2",
            "equipment_class",
            "20GP",
        ),
        (
            "equipment_class",
            "20GP",
            "class_group",
            "HG2029-00001",
            ROLES[1],
            "advanced:AS057:p2",
            "equipment_id",
            "HL-20GP-00660",
        ),
        (
            "source_bundle",
            "HS-057",
            "source_group",
            "HG2029-00001",
            ROLES[0],
            "advanced:AS057:p1",
            None,
            None,
        ),
        (
            "source_bundle",
            "HS-057",
            "source_group",
            "HG2029-00002",
            ROLES[2],
            "advanced:AS058:p3",
            None,
            None,
        ),
    ]
    connection.executemany(
        "INSERT INTO code_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", links
    )
    relation = object.__new__(HGRelations)
    relation._connection = connection
    return relation, connection


def test_code_namespaces_are_typed_and_not_prefix_matched():
    assert _code_type("HG2029-00001") == ("reservation_id", "exact")
    assert _code_type("EV-SH-00660-D") == ("evidence_id", "exact")
    assert _code_type("HL-20GP-00660") == ("equipment_id", "exact")
    assert _code_type("HS-057") == ("source_bundle", "source_group")
    assert _normalized_code("２０ＧＰ") == "20GP"
    with pytest.raises(ValueError, match="전체 코드"):
        _normalized_code("HL-20GP-")


def test_exact_code_and_class_group_keep_provenance_and_ambiguity():
    relation, connection = _relation_store()
    try:
        evidence = relation.identify("EV-SH-00660-D")
        assert len(evidence) == 1
        assert evidence[0].code_type == "evidence_id"
        assert evidence[0].case_id == "HG2029-00001"
        assert evidence[0].role == ROLES[1]
        assert evidence[0].source_id == "advanced:AS057:p2"

        assert relation.code_candidates("20GP") == {"HG2029-00001"}
        equipment = relation.identify("HL-20GP-00660")
        assert len(equipment) == 1
        assert equipment[0].related_code_type == "equipment_class"
        assert equipment[0].related_code == "20GP"

        assert relation.code_candidates("HS-057") == {
            "HG2029-00001",
            "HG2029-00002",
        }
        assert relation.code_candidates("CT-006") == set()
        assert relation.identify("ZZ-006") == []
    finally:
        connection.close()


def test_effective_date_uses_allowlisted_read_only_fact_lookup():
    relation = object.__new__(HGRelations)

    class StructuredStub:
        @staticmethod
        def _execute(sql, parameters):
            assert "fact_code = ?" in sql
            assert parameters == ("recorded_amendment_effective_date", "2029-01-01")
            return [("HG2029-00001",), ("HG2029-00002",)]

        @staticmethod
        def identities_for(field, value):
            raise ValueError(f"unsupported identity: {field}")

    relation._structured = StructuredStub()
    relation._connection = sqlite3.connect(":memory:")
    assert relation.identities_for(
        "recorded_amendment_effective_date", "2029-01-01"
    ) == {
        "HG2029-00001",
        "HG2029-00002",
    }
    with pytest.raises(ValueError, match="unsupported identity"):
        relation.identities_for("not_a_date_fact", "2029-01-01")
    relation._connection.close()
