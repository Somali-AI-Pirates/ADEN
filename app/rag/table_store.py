import json
import re
import sqlite3
from contextlib import closing
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import file_digest
from math import isfinite
from pathlib import Path
from time import perf_counter

ALLOWED_SQL_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "total",
    "abs",
    "round",
    "coalesce",
    "ifnull",
    "nullif",
    "length",
    "lower",
    "upper",
    "trim",
    "ltrim",
    "rtrim",
    "substr",
    "substring",
    "replace",
    "instr",
    "like",
    "glob",
    "date",
    "datetime",
    "strftime",
    "julianday",
    "unixepoch",
    "group_concat",
    "decimal_sum",
    "decimal_avg",
    "decimal_cmp",
    "decimal_sub",
    "decimal_add",
}


# Bound arithmetic work independently of SQLite's VM progress callback.
# Exact add/subtract/sum support up to this many aligned decimal digits.
_MAX_DECIMAL_DIGITS = 10_000


def _number(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Decimal operands must be finite")
    parts = number.as_tuple()
    if len(parts.digits) > _MAX_DECIMAL_DIGITS or abs(parts.exponent) > _MAX_DECIMAL_DIGITS:
        raise ValueError("Decimal operand exceeds the supported precision")
    return number


def _context(precision):
    if precision > _MAX_DECIMAL_DIGITS:
        raise ValueError("Decimal result exceeds the supported precision")
    return localcontext(Context(prec=max(28, precision), rounding=ROUND_HALF_EVEN))


def _add(left, right):
    precision = (
        max(left.adjusted(), right.adjusted())
        - min(left.as_tuple().exponent, right.as_tuple().exponent)
        + 2
    )
    with _context(precision):
        return left + right


class DecimalSum:
    def __init__(self):
        self.total = Decimal("0")
        self.count = 0

    def step(self, value):
        if value is None:
            return
        self.total = _add(self.total, _number(value))
        self.count += 1

    def finalize(self):
        return str(self.total) if self.count else None


class DecimalAvg(DecimalSum):
    def finalize(self):
        if not self.count:
            return None
        # A terminating division by count needs at most 3 * digit_count(count)
        # extra coefficient digits. Recurring decimals use half-even rounding;
        # preserve at least 28 significant digits and 28 fractional digits for
        # large integral results.
        integer_digits = (
            0 if self.total.copy_abs() < self.count
            else max(1, self.total.adjusted() - len(str(self.count)) + 2)
        )
        precision = max(
            len(self.total.as_tuple().digits) + 3 * len(str(self.count)),
            integer_digits + 28,
        )
        with _context(precision):
            return str(self.total / self.count)


def decimal_cmp(a, b):
    if a is None or b is None:
        return None
    left, right = _number(a), _number(b)
    return (left > right) - (left < right)


def decimal_add(a, b) -> str | None:
    if a is None or b is None:
        return None
    return str(_add(_number(a), _number(b)))


def decimal_sub(a, b) -> str | None:
    if a is None or b is None:
        return None
    # copy_negate avoids applying the ambient Decimal precision.
    return str(_add(_number(a), _number(b).copy_negate()))


class TableStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        manifest_path = self.directory / "store.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hashes = manifest["files"]
        required_files = {"catalog.json", "tables.sqlite3"}

        if not required_files.issubset(hashes):
            raise ValueError(
                "Snapshot manifest is missing the catalog or database hash"
            )

        for name in ("catalog.json", "tables.sqlite3", "table-chunks.json"):
            if name not in hashes:
                continue
            with (self.directory / name).open("rb") as file:
                actual = file_digest(file, "sha256").hexdigest()
            if actual != hashes[name]:
                raise ValueError(f"Physical table snapshot fingerprint changed: {name}")

        catalog_path = self.directory / "catalog.json"
        catalogs = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.catalog = {}
        self.by_page = {}

        for catalog in catalogs:
            self.catalog[catalog["table_id"]] = catalog
            page_key = (catalog["book_id"], catalog["page"])
            self.by_page.setdefault(page_key, []).append(catalog["table_id"])

    def schema(self, table_ids):
        ret = []

        for table_id in table_ids:
            table = self.catalog.get(table_id)

            if table is None:
                continue

            ret.append(
                {
                    "table_id": table["table_id"],
                    "book_id": table["book_id"],
                    "page": table["page"],
                    "heading": table["heading"],
                    "section": table["section"],
                    "row_count": table["row_count"],
                    "columns": table["columns"],
                }
            )

        return ret

    def query(self, sql, allowed_tables, *, preview_rows=40, seconds=2):
        allowed_tables = set(allowed_tables)

        if preview_rows < 1:
            raise ValueError("미리보기 행 수는 1 이상이어야 합니다.")
        if not isfinite(seconds) or seconds < 0:
            raise ValueError("조회 시간 제한은 0 이상의 유한한 값이어야 합니다.")

        if (
            not allowed_tables
            or not allowed_tables.issubset(self.catalog)
            or not re.match(r"\s*(SELECT|WITH)\b", sql, re.IGNORECASE)
        ):
            return {
                "status": "rejected",
                "sql": sql,
                "reason": "SELECT and visible source tables required",
                "rows": [],
                "sources": [],
            }

        started = perf_counter()
        db_path = self.directory / "tables.sqlite3"
        touched = set()

        def authorize(action, first, second, database, origin):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK

            if action == sqlite3.SQLITE_READ and first in allowed_tables:
                touched.add(first)
                return sqlite3.SQLITE_OK

            if action == sqlite3.SQLITE_FUNCTION:
                f_name = (second or first or "").lower()

                if f_name in ALLOWED_SQL_FUNCTIONS:
                    return sqlite3.SQLITE_OK

            return sqlite3.SQLITE_DENY

        try:
            with closing(
                sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
            ) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
                conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
                conn.create_function("decimal_cmp", 2, decimal_cmp)
                conn.create_function("decimal_add", 2, decimal_add)
                conn.create_function("decimal_sub", 2, decimal_sub)
                conn.create_aggregate("decimal_sum", 1, DecimalSum)  # type: ignore
                conn.create_aggregate("decimal_avg", 1, DecimalAvg)  # type: ignore
                conn.set_authorizer(authorize)
                conn.set_progress_handler(
                    lambda: int(perf_counter() - started > seconds),
                    1000,
                )
                cursor = conn.execute(sql)
                columns = [column[0] for column in cursor.description]
                if len(columns) != len(set(columns)):
                    raise ValueError(
                        "Duplicate result column names; use explicit SQL aliases"
                    )
                fetched = cursor.fetchmany(preview_rows + 1)
                if not touched:
                    raise ValueError("SQL result has no original source table")

                tables = sorted(touched)
                sources = []
                for table_id in tables:
                    table = self.catalog[table_id]
                    sources.append({"book_id": table["book_id"], "page": table["page"]})

                result = {
                    "status": "ok",
                    "sql": sql,
                    "columns": columns,
                    "rows": [
                        dict(zip(columns, row, strict=True))
                        for row in fetched[:preview_rows]
                    ],
                    "preview_truncated": len(fetched) > preview_rows,
                    "tables": tables,
                    "sources": sources,
                    "source_scope": (
                        "whole original physical tables read by SELECT; "
                        "aggregates run before preview limit"
                    ),
                }
        except (sqlite3.Error, InvalidOperation, ValueError) as exc:
            result = {
                "status": "sql_error",
                "sql": sql,
                "error_class": type(exc).__name__,
                "message": str(exc)[:180],
                "rows": [],
                "sources": [],
            }
        result["seconds"] = perf_counter() - started
        return result
