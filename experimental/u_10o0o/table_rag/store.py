"""Original physical tables, source-grounded typed cells and read-only SELECT."""

import re
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter

from experimental.u_10o0o.advanced_schema_query import typed_value
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.pattern_retrieval.common import (
    file_sha,
    object_sha,
    read_json,
    write_json,
)

NULL_STATES = ("검토 필요", "미결", "해당 없음", "미정", "확인 필요", "미확정")
NUMBER_TOKEN = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MEASURE = re.compile(
    rf"(?:(?P<currency>KRW|USD|EUR)\s*(?P<prefix>{NUMBER_TOKEN})|(?P<suffix>{NUMBER_TOKEN})\s*(?P<unit>원|달러|KRW|USD|EUR|kg|KG|톤|일|대|개|%))"
)
DATE_TOKEN = re.compile(r"\d{4}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2}일?")
ID = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")
FUNCTIONS = {
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


def ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def canonical(text):
    return re.sub(r"\s+", "", text)


def typed_cell(raw, field):
    """Keep raw cells. Extract a measure only from an unambiguous value/label."""
    state = next((s for s in NULL_STATES if s in raw), "reported")
    result = {"value": None, "unit": None, "state": state, "type": "TEXT"}
    if state != "reported":
        return result
    text = raw.strip()
    # A repeated column label before its value is explicit, not a guessed role.
    if text.startswith(field):
        text = text[len(field) :].lstrip(" :：")
    parsed_date = typed_value(text, "date")
    if parsed_date:
        return {**result, "value": parsed_date[0], "unit": "date", "type": "DATE"}
    measure = MEASURE.fullmatch(text)
    if measure:
        raw_number = measure["prefix"] or measure["suffix"]
        unit = measure["currency"] or measure["unit"]
        unit = {"원": "KRW", "달러": "USD", "KG": "kg"}.get(unit, unit)
        return {
            **result,
            "value": str(Decimal(raw_number.replace(",", ""))),
            "unit": unit,
            "type": "DECIMAL",
        }
    if re.fullmatch(NUMBER_TOKEN, text) and not any(
        k in field for k in ("번호", "코드", "예약")
    ):
        return {
            **result,
            "value": str(Decimal(text.replace(",", ""))),
            "unit": "",
            "type": "DECIMAL",
        }
    return result


def split_tables(rows, layouts=None):
    """Never merge different pages/sections/header orders or separated blocks."""
    tables, current = [], []
    for row in sorted(
        rows, key=lambda r: (r["book_id"], r["page"], r["line_start"], r["record_id"])
    ):
        previous = current[-1] if current else None
        compatible = previous is not None and (
            (row["source_id"], row["section"], tuple(row["fields"]))
            == (previous["source_id"], previous["section"], tuple(previous["fields"]))
            and (
                all(
                    not re.search(r"[\w가-힣]", line)
                    for line in layouts[row["source_id"]][
                        previous["line_end"] : row["line_start"] - 1
                    ]
                )
                if layouts is not None
                else row["line_start"] <= previous["line_end"] + 1
            )
            and row["line_start"] > previous["line_start"]
        )
        if not compatible and current:
            tables.append(current)
            current = []
        current.append(row)
    if current:
        tables.append(current)
    return tables


def column_metadata(field, values, index):
    parsed = [typed_cell(v, field) for v in values]
    types = {v["type"] for v in parsed if v["value"] is not None}
    kinds = Counter(v["type"] for v in parsed)
    examples = sorted(set(values), key=lambda x: (len(x), x))[:3]
    examples = [v[:180] for v in examples]
    name = f"c{index}"
    columns = [
        {
            "name": name,
            "label": field,
            "type": "TEXT",
            "meaning": "원본 셀 전체",
            "examples": examples,
        }
    ]
    value_type = next(iter(types)) if len(types) == 1 else None
    if value_type:
        columns.extend(
            [
                {
                    "name": name + "_value",
                    "label": field,
                    "type": value_type,
                    "meaning": "원본에서 단일 값으로 확인한 값. 미확정/복합 값은 NULL.",
                    "examples": list(
                        dict.fromkeys(
                            p["value"] for p in parsed if p["value"] is not None
                        )
                    )[:3],
                },
                {
                    "name": name + "_unit",
                    "label": field,
                    "type": "TEXT",
                    "meaning": "값의 통화/단위. 서로 다른 단위를 합산하지 않는다.",
                    "examples": sorted(
                        {p["unit"] for p in parsed if p["unit"] is not None}
                    ),
                },
                {
                    "name": name + "_state",
                    "label": field,
                    "type": "TEXT",
                    "meaning": "reported 또는 원문 미확정 상태. NULL은 0이 아니다.",
                    "examples": sorted({p["state"] for p in parsed}),
                },
            ]
        )
    return columns, parsed, value_type, dict(kinds)


def markdown_table(table, rows):
    fields = list(rows[0]["fields"])

    def escape(x):
        return str(x).replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(escape(f) for f in fields) + " |"
    separator = "| " + " | ".join("---" for _ in fields) + " |"
    values = [
        "| " + " | ".join(escape(r["fields"][f]) for f in fields) + " |" for r in rows
    ]
    return (
        f"{table['heading']}\n{table['section']}\n{header}\n{separator}\n"
        + "\n".join(values)
    )


def build(directory):
    directory.mkdir(parents=True, exist_ok=False)
    rows = {}
    with StructuredStore() as store:
        provenance = store.provenance
        documents = [dict(r) for r in store._execute("SELECT * FROM source_documents")]
        for row in store._execute(
            "SELECT r.*,p.book_id,p.page,p.heading,p.page_family,f.field_name,f.raw_value "
            "FROM source_records r JOIN source_pages p USING(source_id) "
            "JOIN record_fields f USING(record_id) WHERE r.kind='table_row' AND r.parse_status='parsed' "
            "ORDER BY r.record_id,f.ordinal"
        ):
            r = dict(row)
            name, value = r.pop("field_name"), r.pop("raw_value")
            rows.setdefault(r["record_id"], {**r, "fields": {}})["fields"][name] = value
    layouts = {}
    from experimental.u_10o0o.advanced_structured_curation import LAYOUT_CACHE
    from experimental.u_10o0o.prepare import DATA_DIR

    for doc in documents:
        path = LAYOUT_CACHE / f"{doc['book_id']}-{doc['file_sha256'][:16]}.txt"
        meta = read_json(path.with_suffix(".json"))
        if (
            meta["text_sha256"] != file_sha(path)
            or meta["identity"]["pdf_sha256"] != doc["file_sha256"]
        ):
            raise ValueError("Original PDF/layout fingerprint differs")
        if file_sha(DATA_DIR / "documents" / doc["file_name"]) != doc["file_sha256"]:
            raise ValueError("Original PDF changed")
        pages = path.read_text().split("\f")
        for row in rows.values():
            if row["book_id"] == doc["book_id"]:
                layouts.setdefault(
                    row["source_id"], pages[row["page"] - 1].splitlines()
                )
    # Reparse column slices from original layout. Concatenating whole physical
    # lines interleaves wrapped cells and cannot validate a multi-line cell.
    from app.ingestion.layout import blocks, header_cells, looks_like_row, parse_table

    reparsed = {}
    for sid, lines in layouts.items():
        parts = [p for p in blocks("\n".join(lines)) if p["lines"]]
        index = 0
        while index < len(parts):
            header = parts[index]
            if header_cells(header) is None:
                index += 1
                continue
            end = index + 1
            while (
                end < len(parts)
                and looks_like_row(parts[end])
                and not header_cells(parts[end])
            ):
                end += 1
            parsed = (
                parse_table(header, parts[index + 1 : end]) if end > index + 1 else None
            )
            if parsed is not None:
                for block, cells in zip(parts[index + 1 : end], parsed, strict=True):
                    reparsed[(sid, block["start"], block["end"])] = cells
            index = end
    rejected = []
    for rid, row in list(rows.items()):
        actual = reparsed.get((row["source_id"], row["line_start"], row["line_end"]))
        if actual != row["fields"]:
            rejected.append(rid)
            rows.pop(rid)
    tables = split_tables(rows.values(), layouts)
    db_path = directory / "tables.sqlite3"
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        "CREATE TABLE lineage(table_id TEXT, row_id TEXT PRIMARY KEY, source_id TEXT, book_id TEXT, page INTEGER, line_start INTEGER, line_end INTEGER, fields_sha256 TEXT)"
    )
    catalog, chunks, typed_stats = [], [], Counter()
    for members in tables:
        first = members[0]
        table_id = "t_" + object_sha([r["record_id"] for r in members])[:16]
        table = {
            k: first[k]
            for k in (
                "source_id",
                "book_id",
                "page",
                "heading",
                "section",
                "page_family",
            )
        }
        table.update(
            table_id=table_id,
            line_start=first["line_start"],
            line_end=max(r["line_end"] for r in members),
            row_count=len(members),
            columns=[],
        )
        values_by_row = {r["record_id"]: [r["record_id"]] for r in members}
        definitions = ["row_id TEXT PRIMARY KEY"]
        for i, field in enumerate(first["fields"]):
            columns, parsed, kind, stats = column_metadata(
                field, [r["fields"][field] for r in members], i
            )
            table["columns"].extend(columns)
            typed_stats.update(stats)
            definitions.append(f"{ident(f'c{i}')} TEXT NOT NULL")
            if kind:
                definitions.extend(
                    f"{ident(f'c{i}_{suffix}')} TEXT"
                    for suffix in ("value", "unit", "state")
                )
            for row, value in zip(members, parsed, strict=True):
                values_by_row[row["record_id"]].append(row["fields"][field])
                if kind:
                    values_by_row[row["record_id"]].extend(
                        (value["value"], value["unit"], value["state"])
                    )
        db.execute(f"CREATE TABLE {ident(table_id)} ({','.join(definitions)}) STRICT")
        values = list(values_by_row.values())
        db.executemany(
            f"INSERT INTO {ident(table_id)} VALUES ({','.join('?' for _ in values[0])})",
            values,
        )
        db.executemany(
            "INSERT INTO lineage VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    table_id,
                    r["record_id"],
                    r["source_id"],
                    r["book_id"],
                    r["page"],
                    r["line_start"],
                    r["line_end"],
                    object_sha(r["fields"]),
                )
                for r in members
            ],
        )
        table["schema_sha256"] = object_sha(table["columns"])
        catalog.append(table)
        # Markdown contents, not metadata-only cards, are the new IS search unit.
        if first["page_family"] == "IS":
            chunks.append(
                {
                    "table_id": table_id,
                    "book_id": first["book_id"],
                    "file_name": first["book_id"] + ".pdf",
                    "page": first["page"],
                    "chunk_id": table_id,
                    "text": markdown_table(table, members),
                }
            )
    db.commit()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT count(*) FROM lineage").fetchone()[0] == len(rows)
    # Read every reconstructed raw cell back: row/column/source association must be exact.
    verified = 0
    for table in catalog:
        for sql_row in db.execute(f"SELECT * FROM {ident(table['table_id'])}"):
            columns = [
                r[1]
                for r in db.execute(f"PRAGMA table_info({ident(table['table_id'])})")
            ]
            got = dict(zip(columns, sql_row, strict=True))
            expected = rows[got["row_id"]]["fields"]
            actual = {
                c["label"]: got[c["name"]]
                for c in table["columns"]
                if re.fullmatch(r"c\d+", c["name"])
            }
            if actual != expected:
                raise ValueError("Raw table reconstruction changed a cell")
            verified += 1
    db.close()
    write_json(directory / "catalog.json", catalog)
    write_json(directory / "table-chunks.json", chunks)
    manifest = {
        "status": "complete",
        "source_snapshot": provenance,
        "rows": len(rows),
        "tables": len(catalog),
        "is_chunks": len(chunks),
        "raw_rows_roundtrip_verified": verified,
        "original_line_cell_checks": verified,
        "rejected_cell_source_rows": len(rejected),
        "rejected_record_ids": rejected,
        "typed_cell_counts": typed_stats,
        "family_tables": Counter(t["page_family"] for t in catalog),
        "boundaries": "source page + section + ordered columns + only blank/rule lines between records; no cross-page merge",
        "files": {
            p: file_sha(directory / p)
            for p in ("tables.sqlite3", "catalog.json", "table-chunks.json")
        },
    }
    write_json(directory / "store.json", manifest)
    print(
        "Physical table store complete:",
        manifest["tables"],
        "tables,",
        manifest["rows"],
        "rows,",
        len(chunks),
        "IS search chunks",
        flush=True,
    )


class DecimalSum:
    def __init__(self):
        self.total, self.count = Decimal(0), 0

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))
            self.count += 1

    def finalize(self):
        return str(self.total) if self.count else None


class DecimalAvg(DecimalSum):
    def finalize(self):
        return str(self.total / self.count) if self.count else None


class TableStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        manifest = read_json(self.directory / "store.json")
        for name, sha in manifest["files"].items():
            if file_sha(self.directory / name) != sha:
                raise ValueError("Physical table snapshot fingerprint changed")
        self.catalog = {
            t["table_id"]: t for t in read_json(self.directory / "catalog.json")
        }
        self.by_page = defaultdict(list)
        for t in self.catalog.values():
            self.by_page[(t["book_id"], t["page"])].append(t["table_id"])

    def schema(self, table_ids):
        return [
            {
                k: self.catalog[t][k]
                for k in (
                    "table_id",
                    "book_id",
                    "page",
                    "heading",
                    "section",
                    "row_count",
                    "columns",
                )
            }
            for t in table_ids
        ]

    def query(self, sql, allowed_tables, *, preview_rows=40, seconds=2):
        started = perf_counter()
        allowed = set(allowed_tables)
        if (
            not allowed
            or not allowed <= self.catalog.keys()
            or not re.match(r"\s*(SELECT|WITH)\b", sql, re.I)
        ):
            return {
                "status": "rejected",
                "reason": "SELECT and visible source tables required",
                "rows": [],
                "sources": [],
            }
        db = sqlite3.connect(
            f"file:{self.directory / 'tables.sqlite3'}?mode=ro", uri=True
        )
        db.execute("PRAGMA query_only=ON")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
        db.create_aggregate("decimal_sum", 1, DecimalSum)
        db.create_aggregate("decimal_avg", 1, DecimalAvg)
        db.create_function(
            "decimal_cmp",
            2,
            lambda a, b: (
                None
                if a is None or b is None
                else (Decimal(str(a)) > Decimal(str(b)))
                - (Decimal(str(a)) < Decimal(str(b)))
            ),
        )
        db.create_function(
            "decimal_add",
            2,
            lambda a, b: (
                None
                if a is None or b is None
                else str(Decimal(str(a)) + Decimal(str(b)))
            ),
        )
        db.create_function(
            "decimal_sub",
            2,
            lambda a, b: (
                None
                if a is None or b is None
                else str(Decimal(str(a)) - Decimal(str(b)))
            ),
        )
        touched = set()

        def authorize(action, first, second, database, origin):
            if action == sqlite3.SQLITE_READ:
                if first not in allowed:
                    return sqlite3.SQLITE_DENY
                touched.add(first)
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if (
                action == sqlite3.SQLITE_FUNCTION
                and (second or first or "").lower() in FUNCTIONS
            ):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)
        db.set_progress_handler(lambda: int(perf_counter() - started > seconds), 1000)
        try:
            cursor = db.execute(sql)
            names = [c[0] for c in cursor.description]
            if len(names) != len(set(names)):
                raise ValueError(
                    "Duplicate result column names; use explicit SQL aliases"
                )
            fetched = cursor.fetchmany(preview_rows + 1)
            if not touched:
                raise ValueError("SQL result has no original source table")
            truncated = len(fetched) > preview_rows
            values = [
                dict(zip(names, row, strict=True)) for row in fetched[:preview_rows]
            ]
            result = {
                "status": "ok",
                "columns": names,
                "rows": values,
                "preview_truncated": truncated,
                "tables": sorted(touched),
                "sources": [
                    {
                        "book_id": self.catalog[t]["book_id"],
                        "page": self.catalog[t]["page"],
                    }
                    for t in sorted(touched)
                ],
                "source_scope": "whole original physical tables read by SELECT; aggregates run before preview limit",
            }
        except (sqlite3.Error, InvalidOperation, ValueError) as exc:
            result = {
                "status": "sql_error",
                "error_class": type(exc).__name__,
                "message": str(exc)[:180],
                "rows": [],
                "sources": [],
            }
        finally:
            db.close()
        result["seconds"] = perf_counter() - started
        return result
