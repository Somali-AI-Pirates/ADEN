"""Source-derived arithmetic checks, distinct from semantic question accuracy."""

from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import sqlite3

from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.pattern_retrieval.common import write_json, object_sha
from experimental.u_10o0o.table_rag.store import TableStore, ident


def source_value(raw, label, kind):
    """Independent expectation from the original record cell, not _value columns."""
    text = raw.strip()
    if text.startswith(label):
        text = text[len(label) :].lstrip(" :：")
    if kind == "DATE":
        m = re.fullmatch(r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})일?", text)
        if m:
            return date(*map(int, m.groups())).isoformat(), "date"
        return None
    # Only unambiguous whole-cell measures enter the arithmetic oracle.
    m = re.fullmatch(
        r"(?:(USD|KRW|EUR)\s*)?([+-]?[0-9,]+(?:\.[0-9]+)?)\s*(원|달러|USD|KRW|EUR|kg|KG|톤|일|대|개|%)?",
        text,
    )
    if not m or (m[1] and m[3]):
        return None
    unit = m[1] or m[3] or ""
    unit = {"원": "KRW", "달러": "USD", "KG": "kg"}.get(unit, unit)
    return str(Decimal(m[2].replace(",", ""))), unit


def audit(directory):
    directory = Path(directory)
    store = TableStore(directory)
    selected, counts = [], Counter()
    for table in sorted(
        store.catalog.values(), key=lambda t: object_sha(t["table_id"])
    ):
        family = table.get("page_family")
        if (
            family not in ("HG", "HA", "IS")
            or counts[family] >= 10
            or table["row_count"] < 2
        ):
            continue
        columns = [c for c in table["columns"] if c["name"].endswith("_value")]
        if columns:
            selected.append((table, columns[0]))
            counts[family] += 1
    results = []
    with (
        StructuredStore() as original,
        sqlite3.connect(f"file:{directory / 'tables.sqlite3'}?mode=ro", uri=True) as db,
    ):
        for table, column in selected:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT row_id FROM lineage WHERE table_id=? ORDER BY row_id",
                    (table["table_id"],),
                )
            ]
            original_values = [
                original._execute(
                    "SELECT raw_value FROM record_fields WHERE record_id=? AND field_name=?",
                    (rid, column["label"]),
                ).fetchone()[0]
                for rid in ids
            ]
            values = [
                source_value(v, column["label"], column["type"])
                for v in original_values
            ]
            expected = {
                rid: v for rid, v in zip(ids, values, strict=True) if v is not None
            }
            raw_name = column["name"][:-6]
            actual = db.execute(
                f"SELECT row_id,{ident(column['name'])},{ident(raw_name + '_unit')} FROM {ident(table['table_id'])} WHERE {ident(column['name'])} IS NOT NULL ORDER BY row_id"
            ).fetchall()
            equal = {r[0]: (r[1], r[2]) for r in actual} == expected
            unit_groups = {}
            for value, unit in expected.values():
                unit_groups.setdefault(unit, []).append(value)
            value_col, unit_col = ident(column["name"]), ident(raw_name + "_unit")
            if column["type"] == "DECIMAL":
                sql = f"SELECT {unit_col} unit, count({value_col}) n, decimal_sum({value_col}) value FROM {ident(table['table_id'])} WHERE {value_col} IS NOT NULL GROUP BY {unit_col} ORDER BY {unit_col}"
                expected_rows = [
                    {
                        "unit": u,
                        "n": len(vs),
                        "value": str(sum(map(Decimal, vs), Decimal(0))),
                    }
                    for u, vs in sorted(unit_groups.items())
                ]
            else:
                sql = f"SELECT {unit_col} unit, count({value_col}) n, min({value_col}) value FROM {ident(table['table_id'])} WHERE {value_col} IS NOT NULL GROUP BY {unit_col} ORDER BY {unit_col}"
                expected_rows = [
                    {"unit": u, "n": len(vs), "value": min(vs)}
                    for u, vs in sorted(unit_groups.items())
                ]
            result = store.query(sql, [table["table_id"]])
            results.append(
                {
                    "table_id": table["table_id"],
                    "family": table["page_family"],
                    "book_id": table["book_id"],
                    "page": table["page"],
                    "type": column["type"],
                    "original_cells": len(original_values),
                    "typed_cells": len(expected),
                    "raw_value_and_unit_equal": equal,
                    "aggregation_equal": result["status"] == "ok"
                    and result["rows"] == expected_rows,
                }
            )
    output = {
        "scope": "source-derived SQL/value checks; not natural-language query accuracy or human validation",
        "count": len(results),
        "family_counts": dict(counts),
        "passed": sum(
            r["raw_value_and_unit_equal"] and r["aggregation_equal"] for r in results
        ),
        "rows": results,
    }
    write_json(directory / "source-oracle.json", output)
    print({k: v for k, v in output.items() if k != "rows"})
    if output["passed"] != output["count"] or output["count"] != 30:
        raise ValueError("Source arithmetic checks failed or lacked family coverage")
