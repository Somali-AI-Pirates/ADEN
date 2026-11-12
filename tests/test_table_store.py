import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.rag.table_store import TableStore


def write_snapshot(directory, *, include_chunks=False):
    database = directory / "tables.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE rates (code TEXT, amount TEXT);
            INSERT INTO rates VALUES ('A', '12.50'), ('B', '20.00');
            CREATE TABLE hidden (value TEXT);
            INSERT INTO hidden VALUES ('private');
            """
        )
        connection.commit()

    catalog = [
        {
            "table_id": "rates",
            "book_id": "fixture-book",
            "page": 7,
            "heading": "Rates",
            "section": "Rates",
            "row_count": 2,
            "columns": ["code", "amount"],
        }
    ]
    (directory / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    if include_chunks:
        (directory / "table-chunks.json").write_text("[]", encoding="utf-8")

    names = ["catalog.json", "tables.sqlite3"]
    if include_chunks:
        names.append("table-chunks.json")
    hashes = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }
    (directory / "store.json").write_text(
        json.dumps({"files": hashes}), encoding="utf-8"
    )


@pytest.fixture
def snapshot(tmp_path):
    write_snapshot(tmp_path)
    return tmp_path


def test_allowed_query_has_table_provenance_even_when_result_is_empty(snapshot):
    store = TableStore(snapshot)

    result = store.query("SELECT code FROM rates WHERE code = 'missing'", ["rates"])

    assert result["status"] == "ok"
    assert result["tables"] == ["rates"]
    assert result["rows"] == []
    assert result["sources"] == [{"book_id": "fixture-book", "page": 7}]
    assert result["source_scope"]
    assert result["seconds"] >= 0


def test_allowed_query_returns_rows_and_preview_information(snapshot):
    store = TableStore(snapshot)

    result = store.query(
        "SELECT code, amount FROM rates ORDER BY code",
        ["rates"],
        preview_rows=1,
    )

    assert result["status"] == "ok"
    assert result["columns"] == ["code", "amount"]
    assert result["rows"] == [{"code": "A", "amount": "12.50"}]
    assert result["sql"] == "SELECT code, amount FROM rates ORDER BY code"
    assert result["preview_truncated"] is True
    assert result["sources"] == [{"book_id": "fixture-book", "page": 7}]


@pytest.mark.parametrize(
    ("sql", "allowed_tables"),
    [
        ("SELECT code FROM rates", []),
        ("SELECT code FROM rates", ["missing"]),
        ("DELETE FROM rates", ["rates"]),
    ],
)
def test_rejected_queries_have_no_rows_or_sources(snapshot, sql, allowed_tables):
    result = TableStore(snapshot).query(sql, allowed_tables)

    assert result["status"] == "rejected"
    assert result["rows"] == []
    assert result["sources"] == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT value FROM hidden",
        "WITH selected AS (SELECT code FROM rates) DELETE FROM rates",
        "SELECT code, code FROM rates",
        "SELECT 1",
        "SELECT code FROM missing_table",
    ],
)
def test_sql_errors_have_no_rows_or_sources(snapshot, sql):
    result = TableStore(snapshot).query(sql, ["rates"])

    assert result["status"] == "sql_error"
    assert result["rows"] == []
    assert result["sources"] == []


@pytest.mark.parametrize("filename", ["catalog.json", "tables.sqlite3"])
def test_changed_required_snapshot_file_is_rejected(tmp_path, filename):
    write_snapshot(tmp_path)
    with (tmp_path / filename).open("ab") as file:
        file.write(b"tampered")

    with pytest.raises(ValueError, match="fingerprint"):
        TableStore(tmp_path)


def test_changed_declared_table_chunks_file_is_rejected(tmp_path):
    write_snapshot(tmp_path, include_chunks=True)
    with (tmp_path / "table-chunks.json").open("ab") as file:
        file.write(b"tampered")

    with pytest.raises(ValueError, match="fingerprint"):
        TableStore(tmp_path)


@pytest.mark.parametrize("seconds", [-0.1, float("inf"), float("nan")])
def test_invalid_query_timeout_configuration_raises(snapshot, seconds):
    with pytest.raises(ValueError):
        TableStore(snapshot).query("SELECT code FROM rates", ["rates"], seconds=seconds)



def test_decimal_arithmetic_keeps_large_values_and_mixed_scales():
    from decimal import Decimal, localcontext

    from app.rag.table_store import DecimalAvg, DecimalSum, decimal_add, decimal_sub

    large = "12345678901234567890123456789"
    # A caller's global precision must not alter SQL calculations.
    with localcontext() as context:
        context.prec = 4
        assert decimal_add(large, "2") == "12345678901234567890123456791"
        assert decimal_sub(large, "2") == "12345678901234567890123456787"
        assert Decimal(decimal_add(large, "0.0001")) == Decimal(large + ".0001")
        total = DecimalSum()
        average = DecimalAvg()
        for value in (large, "12345678901234567890123456791"):
            total.step(value)
            average.step(value)
        assert total.finalize() == "24691357802469135780246913580"
        assert Decimal(average.finalize()) == Decimal("12345678901234567890123456790")


def test_decimal_functions_reject_nonfinite_or_unbounded_work():
    import pytest

    from app.rag.table_store import decimal_add, decimal_cmp

    for value in ("NaN", "Infinity", "1E1000000"):
        with pytest.raises(ValueError):
            decimal_add(value, "1")
        with pytest.raises(ValueError):
            decimal_cmp(value, "1")


def test_decimal_average_repeating_values_has_explicit_rounding():
    from decimal import Decimal

    from app.rag.table_store import DecimalAvg

    average = DecimalAvg()
    for value in ("1", "0", "0"):
        average.step(value)
    assert Decimal(average.finalize()) == Decimal("0." + "3" * 28)



def test_sql_decimal_aggregates_preserve_large_source_values(snapshot):
    with closing(sqlite3.connect(snapshot / "tables.sqlite3")) as connection:
        connection.executemany(
            "UPDATE rates SET amount=? WHERE code=?",
            [
                ("12345678901234567890123456789", "A"),
                ("12345678901234567890123456791", "B"),
            ],
        )
        connection.commit()
    manifest_path = snapshot / "store.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["tables.sqlite3"] = hashlib.sha256(
        (snapshot / "tables.sqlite3").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    store = TableStore(snapshot)
    result = store.query(
        "SELECT decimal_sum(amount) AS total, decimal_avg(amount) AS mean FROM rates",
        ["rates"],
    )
    assert result["status"] == "ok"
    assert result["rows"] == [{
        "total": "24691357802469135780246913580",
        "mean": "12345678901234567890123456790",
    }]
    invalid = store.query("SELECT decimal_add(amount, 'NaN') AS value FROM rates", ["rates"])
    assert invalid["status"] == "sql_error"
    assert invalid["sources"] == []
