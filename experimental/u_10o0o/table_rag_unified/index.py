"""Read-only common identity/record/source index built from verified local inputs."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from time import perf_counter
from unicodedata import normalize

from experimental.u_10o0o.advanced_is_record_lookup import source_record_key
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.bm25 import tokenize_bigram_id_normalized
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)

DEFAULT = ROOT / "artifacts/u_10o0o/advanced-snapshots/unified-records-20261112-v2"
RELATIONS = ROOT / "artifacts/u_10o0o/advanced-snapshots/hg-code-relations-v1"
CODE = re.compile(
    r"(?<![A-Z0-9_])([A-Z][A-Z0-9]*(?:\s*[-‐‑–—−－]\s*[A-Z0-9]+)+)(?![A-Z0-9_])", re.I
)
ROLE_MAP = {
    "비용 조건 약정": "contract",
    "계약 조건 확인": "contract",
    "현장 사건 수신대장": "events",
    "사건·증빙 접수 대조": "events",
    "계약 조건 대조 회신": "approval",
    "비용 청구 명세": "invoice",
    "수납 및 미결 인계": "receipt",
    "비용별 정산 확인": "settlement",
}
DATE_FIELDS = {
    "양하 원시기록": "actual_discharge_date",
    "적입 반출기록": "actual_gate_out_date",
    "반출 전표": "system_entry_date",
    "공장비 인수증": "actual_designated_return_date",
}
ROLES = ("contract", "events", "approval", "invoice", "receipt", "settlement", "record")
FIELDS = (
    "shipper",
    "cargo",
    "spec",
    "contract_id",
    "actual_discharge_date",
    "actual_gate_out_date",
    "actual_designated_return_date",
    "scheduled_return_date",
    "system_entry_date",
    "recorded_amendment_effective_date",
)
SCHEMA = """
CREATE TABLE entities(id TEXT PRIMARY KEY, family TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL);
CREATE TABLE sources(entity TEXT REFERENCES entities(id), source TEXT NOT NULL,
 role TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(entity,source,role));
CREATE TABLE keys(value TEXT NOT NULL, kind TEXT NOT NULL, entity TEXT REFERENCES entities(id),
 source TEXT NOT NULL, relation TEXT NOT NULL, PRIMARY KEY(value,entity,source,relation));
CREATE INDEX key_value ON keys(value);
CREATE TABLE attributes(entity TEXT REFERENCES entities(id), field TEXT NOT NULL,
 value TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(entity,field,value,source));
CREATE INDEX attribute_value ON attributes(field,value);
CREATE TABLE tables(id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE VIRTUAL TABLE table_terms USING fts5(table_id UNINDEXED, tokens);
"""


def norm(value):
    return " ".join(normalize("NFKC", value).casefold().split())


SPEC_FORMS = {
    "20gp": (
        "20gp",
        "20피트일반형",
        "20피트일반형컨테이너",
        "20피트일반컨테이너",
        "20피트짜리일반컨테이너",
    ),
    "40gp": (
        "40gp",
        "40피트일반형",
        "40피트일반형컨테이너",
        "40피트일반컨테이너",
        "40피트짜리일반컨테이너",
    ),
}


def canonical_spec(value):
    compact = "".join(norm(value).split())
    return next(
        (key for key, forms in SPEC_FORMS.items() if compact in forms), norm(value)
    )


def codes(text):
    normalized = normalize("NFKC", text)
    return tuple(
        dict.fromkeys(
            re.sub(r"\s*[-‐‑–—−－]\s*", "-", m[1]).upper()
            for m in CODE.finditer(normalized)
        )
    )


def sid(book, page):
    return f"{book}:p{int(page):04d}"


def connect(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def build(directory, table_directory):
    from experimental.u_10o0o.table_rag.store import TableStore

    TableStore(table_directory)  # Verify physical-table files before trusting lineage.
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("Use a new snapshot directory")
    directory.mkdir(parents=True)
    output = directory / "index.sqlite3"
    with (
        StructuredStore() as src,
        closing(connect(RELATIONS / "advanced.sqlite3")) as rel,
    ):
        rm = read_json(RELATIONS / "manifest.json")
        if (
            file_sha(RELATIONS / "advanced.sqlite3")
            != rm["outputs"]["advanced.sqlite3"]
        ):
            raise ValueError("HG relationship snapshot changed")
        pages = {
            r["source_id"]: sid(r["book_id"], r["page"])
            for r in src._execute("SELECT * FROM source_pages")
        }
        raw_pages = read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
        raw = {
            sid(p.get("book_id") or Path(p["file_name"]).stem, p["page"]): p["text"]
            for p in raw_pages
        }
        with closing(sqlite3.connect(output)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)

            def add_key(
                value, kind, entity, source, relation="identity", verified_cell=False
            ):
                if not verified_cell and value not in codes(raw[source]):
                    raise ValueError("Identifier absent from restored source")
                db.execute(
                    "INSERT OR IGNORE INTO keys VALUES(?,?,?,?,?)",
                    (value, kind, entity, source, relation),
                )

            def attr(entity, field, value, source):
                db.execute(
                    "INSERT OR IGNORE INTO attributes VALUES(?,?,?,?)",
                    (entity, field, norm(value), source),
                )

            for row in src._execute(
                "SELECT * FROM case_records ORDER BY family,case_id,role"
            ):
                entity = row["family"] + ":" + row["case_id"]
                source = pages[row["source_id"]]
                db.execute(
                    "INSERT OR IGNORE INTO entities VALUES(?,?,?,?)",
                    (entity, row["family"], "case", row["case_id"]),
                )
                role = ROLE_MAP[row["role"]]
                db.execute(
                    "INSERT INTO sources VALUES(?,?,?,?)",
                    (entity, source, role, ROLES.index(role)),
                )
                add_key(row["case_id"], "reservation_id", entity, source)
            for row in rel.execute(
                "SELECT * FROM code_relations WHERE relation_kind='exact'"
            ):
                source = pages[row["source_id"]]
                add_key(row["code"], row["code_type"], "HG:" + row["case_id"], source)
            for row in src._execute(
                "SELECT p.*,r.source_id FROM case_profiles p JOIN source_records r ON r.record_id=p.source_record_id"
            ):
                for field, key in (
                    ("shipper", "shipper_key"),
                    ("cargo", "cargo_key"),
                    ("spec", "spec_key"),
                    ("contract_id", "contract_key"),
                ):
                    attr(
                        "HG:" + row["case_id"], field, row[key], pages[row["source_id"]]
                    )
            for row in src._execute(
                "SELECT * FROM role_facts WHERE state='reported' AND value_type='date'"
            ):
                if row["fact_code"] in FIELDS:
                    attr(
                        "HG:" + row["case_id"],
                        row["fact_code"],
                        row["value_text"],
                        pages[row["source_id"]],
                    )
            with closing(connect(Path(table_directory) / "tables.sqlite3")) as physical:
                valid_records = {
                    r[0] for r in physical.execute("SELECT row_id FROM lineage")
                }
            records = {}
            query = """SELECT r.*,p.page_family,c.case_id,f.field_name,f.raw_value
              FROM source_records r JOIN source_pages p USING(source_id)
              LEFT JOIN case_records c USING(source_id)
              JOIN record_fields f USING(record_id)
              WHERE r.kind='table_row' AND r.parse_status='parsed' AND p.page_family IN ('HA','IS')
              ORDER BY r.record_id,f.ordinal"""
            for row in src._execute(query):
                records.setdefault(row["record_id"], {"row": dict(row), "fields": {}})[
                    "fields"
                ][row["field_name"]] = row["raw_value"]
            for record_id, item in records.items():
                if record_id not in valid_records:
                    continue
                row, fields = item["row"], item["fields"]
                source = pages[row["source_id"]]
                if row["page_family"] == "HA":
                    entity = "HA:" + row["case_id"]
                    label = fields.get("항목", fields.get("확인 항목", ""))
                    value = fields.get("기재 내용", "")
                    if label in {"예약·장비", "대상 연결"}:
                        for key in codes(value):
                            if key.startswith(("HA28-", "HL-")):
                                add_key(
                                    key,
                                    "equipment_id"
                                    if key.startswith("HL-")
                                    else "reservation_id",
                                    entity,
                                    source,
                                )
                    if label in {"화주·화물", "적용 대상"} and " / " in value:
                        parts = value.split(" / ")
                        if len(parts) == 2:
                            attr(entity, "shipper", parts[0], source)
                            attr(
                                entity,
                                "cargo" if label == "화주·화물" else "spec",
                                parts[1],
                                source,
                            )
                    date_field = DATE_FIELDS.get(fields.get("기록"))
                    date_value = fields.get("날짜·접수값", "")
                    if date_field and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                        attr(entity, date_field, date_value, source)
                    continue
                record_keys = [
                    key
                    for field, value in fields.items()
                    if (key := source_record_key(field, value))
                ]
                if not record_keys:
                    continue
                entity = "IS:" + record_id
                db.execute(
                    "INSERT INTO entities VALUES(?,?,?,?)",
                    (entity, "IS", "record", record_keys[0]),
                )
                db.execute(
                    "INSERT INTO sources VALUES(?,?,?,?)", (entity, source, "record", 0)
                )
                for key in record_keys:
                    add_key(key, "record_id", entity, source, verified_cell=True)
                for field, value in fields.items():
                    if any(
                        x in field
                        for x in ("대상", "예약", "장비", "근거", "청구", "승인")
                    ):
                        for key in codes(value):
                            add_key(
                                key,
                                "record_reference",
                                entity,
                                source,
                                "record_reference",
                                verified_cell=True,
                            )
            # HA contracts are explicitly labelled in the source introduction.
            for entity, source in db.execute(
                "SELECT entity,source FROM sources WHERE entity LIKE 'HA:%' AND role='contract'"
            ).fetchall():
                for key in re.findall(r"원계약\s+(CT-[A-Z0-9-]+)", raw[source]):
                    add_key(key, "contract_id", entity, source)
                    attr(entity, "contract_id", key, source)
            catalog = read_json(Path(table_directory) / "catalog.json")
            for table in catalog:
                source = sid(table["book_id"], table["page"])
                db.execute(
                    "INSERT INTO tables VALUES(?,?,?)",
                    (table["table_id"], source, json.dumps(table, ensure_ascii=False)),
                )
                content = " ".join(
                    [
                        table["heading"],
                        table["section"],
                        *[str(c) for c in table["columns"]],
                    ]
                )
                db.execute(
                    "INSERT INTO table_terms VALUES(?,?)",
                    (
                        table["table_id"],
                        " ".join(tokenize_bigram_id_normalized(content)),
                    ),
                )
            db.commit()
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("Unified index integrity failed")
            counts = {
                name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("entities", "sources", "keys", "attributes", "tables")
            }
            groups = dict(
                db.execute("SELECT family,COUNT(*) FROM entities GROUP BY family")
            )
            assert groups["HG"] == 2730 and groups["HA"] == 198
            inputs = {
                "structured": src.provenance,
                "relations": file_sha(RELATIONS / "manifest.json"),
                "restored_pages": file_sha(
                    ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
                ),
                "table_catalog": file_sha(Path(table_directory) / "catalog.json"),
            }
    write_json(
        directory / "manifest.json",
        {
            "version": 1,
            "database_sha256": file_sha(output),
            "inputs": inputs,
            "counts": counts,
            "families": groups,
            "builder_sha256": file_sha(__file__),
        },
    )
    output.chmod(0o444)
    return counts


class RecordIndex:
    def __init__(self, directory=DEFAULT):
        self.directory = Path(directory)
        self.path = self.directory / "index.sqlite3"
        self.manifest = read_json(self.directory / "manifest.json")
        if file_sha(self.path) != self.manifest["database_sha256"]:
            raise ValueError("Unified index fingerprint mismatch")

    def validate_inputs(self, table_directory):
        inputs = self.manifest["inputs"]
        if file_sha(Path(table_directory) / "catalog.json") != inputs["table_catalog"]:
            raise ValueError(
                "Unified index table catalog differs from the retrieval store"
            )
        raw = ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
        if file_sha(raw) != inputs["restored_pages"]:
            raise ValueError("Unified index original-source snapshot differs")

    def lookup(self, targets, roles=()):
        """Each target is an AND of exact identifiers/typed constraints; targets remain separate."""
        started = perf_counter()
        output = []
        with closing(connect(self.path)) as db:
            for target in targets:
                sets = []
                for key in target.get("identifiers", []):
                    values = codes(key)
                    if (
                        len(values) != 1
                        or values[0]
                        != re.sub(r"\s*-\s*", "-", normalize("NFKC", key)).upper()
                    ):
                        raise ValueError("Use one complete identifier")
                    sets.append(
                        {
                            r[0]
                            for r in db.execute(
                                "SELECT DISTINCT entity FROM keys WHERE value=?",
                                (values[0],),
                            )
                        }
                    )
                for constraint in target.get("constraints", []):
                    field, value = constraint["field"], norm(constraint["value"])
                    if field == "spec":
                        value = canonical_spec(value)
                    if field not in FIELDS:
                        raise ValueError("Unsupported typed constraint")
                    sets.append(
                        {
                            r[0]
                            for r in db.execute(
                                "SELECT DISTINCT entity FROM attributes WHERE field=? AND value=?",
                                (field, value),
                            )
                        }
                    )
                candidates = set.intersection(*sets) if sets else set()
                status = (
                    "matched"
                    if len(candidates) == 1
                    else "ambiguous"
                    if candidates
                    else "conflict"
                    if len(sets) > 1 and all(sets)
                    else "insufficient_constraints"
                    if not sets
                    else "not_found"
                )
                # Do not silently pick a single event when exact conditions match several.
                entities = []
                for entity in sorted(candidates)[:8]:
                    metadata = dict(
                        db.execute(
                            "SELECT * FROM entities WHERE id=?", (entity,)
                        ).fetchone()
                    )
                    source_rows = [
                        dict(r)
                        for r in db.execute(
                            "SELECT source,role,ordinal FROM sources WHERE entity=? ORDER BY ordinal,source",
                            (entity,),
                        )
                    ]
                    selected_roles = target.get("roles", roles)
                    if selected_roles:
                        source_rows = [
                            r for r in source_rows if r["role"] in selected_roles
                        ]
                    metadata["sources"] = source_rows
                    metadata["attributes"] = [
                        dict(r)
                        for r in db.execute(
                            "SELECT field,value,source FROM attributes WHERE entity=? ORDER BY field,value",
                            (entity,),
                        )
                    ]
                    metadata["identifiers"] = [
                        dict(r)
                        for r in db.execute(
                            "SELECT DISTINCT value,kind,relation FROM keys WHERE entity=? ORDER BY kind,value",
                            (entity,),
                        )
                    ]
                    entities.append(metadata)
                output.append(
                    {
                        "status": status,
                        "candidate_count": len(candidates),
                        "truncated": len(candidates) > 8,
                        "entities": entities,
                    }
                )
        return {"targets": output, "seconds": perf_counter() - started}

    def discover(self, query, limit=6):
        terms = list(dict.fromkeys(tokenize_bigram_id_normalized(query)))[:80]
        if not terms:
            return []
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with closing(connect(self.path)) as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT table_id FROM table_terms WHERE table_terms MATCH ? ORDER BY bm25(table_terms),table_id LIMIT ?",
                    (expression, limit),
                )
            ]
