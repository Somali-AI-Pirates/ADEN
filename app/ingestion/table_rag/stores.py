"""Build the raw table and shared record SQLite snapshots."""

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from unicodedata import normalize

from shared.bm25_tokens import tokenize_for_bm25
from shared.text_normalization import normalize_text as norm

from .common import digest, dump
from .constants import (
    CODE,
    DATE_FIELDS,
    HG_EVENT_FIELDS,
    HA_ROLES,
    IS_ANNOTATED_KEYS,
    IS_KEYS,
    ROLE_MAP,
    ROLE_ORDER,
)
from .sources import _read_curated, family, page_id, page_references, typed


def normalize_code(value):
    return re.sub(r"\s*[-‐‑–—−－]\s*", "-", normalize("NFKC", value)).upper()


def code_kind(value):
    for prefix, kind in (
        ("CT-", "contract_id"),
        ("HL-", "equipment_id"),
        ("EV-", "evidence_id"),
        ("AP-", "approval_id"),
        ("INV-", "invoice_id"),
        ("RCPT-", "receipt_id"),
    ):
        if value.startswith(prefix):
            return kind
    return None


def build_physical_store(
    curated,
    pages,
    directory,
    source_fingerprint=None,
    layout_cache=None,
    builder_sha256=None,
    curation_manifest_sha256=None,
):
    sources, records = _read_curated(curated)
    rows = [
        r
        for r in records
        if r["kind"] == "table_row" and r["review_status"] == "parsed" and r["fields"]
    ]
    rows.sort(
        key=lambda r: (
            sources[r["source_id"]]["book_id"],
            sources[r["source_id"]]["page"],
            r["line_start"],
            r["record_id"],
        )
    )
    groups = []
    cached_layouts = {}

    def blank_gap(previous, current):
        if layout_cache is None:
            return current["line_start"] <= previous["line_end"] + 2
        source = sources[current["source_id"]]
        key = source["book_id"]
        if key not in cached_layouts:
            path = Path(layout_cache) / f"{key}-{source['file_sha256'][:16]}.txt"
            pages_text = path.read_text(encoding="utf-8").split("\f")
            if pages_text and not pages_text[-1].strip():
                pages_text.pop()
            cached_layouts[key] = pages_text
        lines = cached_layouts[key][source["page"] - 1].splitlines()
        gap = lines[previous["line_end"] : current["line_start"] - 1]
        return all(not re.search(r"[\w가-힣]", line) for line in gap)

    for row in rows:
        prev = groups[-1][-1] if groups else None
        same_table = (
            prev is not None
            and (row["source_id"], row["section"], tuple(row["fields"]))
            == (prev["source_id"], prev["section"], tuple(prev["fields"]))
            and row["line_start"] > prev["line_start"]
            and blank_gap(prev, row)
        )
        if same_table:
            groups[-1].append(row)
        else:
            groups.append([row])
    dbpath = directory / "tables.sqlite3"
    if dbpath.exists():
        dbpath.unlink()
    catalog, chunks = [], []
    with sqlite3.connect(dbpath) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "CREATE TABLE lineage(table_id TEXT,row_id TEXT PRIMARY KEY,source_id TEXT,book_id TEXT,page INTEGER,line_start INTEGER,line_end INTEGER,fields_sha256 TEXT)"
        )
        for members in groups:
            first = members[0]
            src = sources[first["source_id"]]
            sid = page_id(src)
            fam = family(src)
            tableid = (
                "t_"
                + hashlib.sha256(
                    json.dumps(
                        [r["record_id"] for r in members], separators=(",", ":")
                    ).encode()
                ).hexdigest()[:16]
            )
            labels = list(first["fields"])
            columns = []
            definitions = ["row_id TEXT PRIMARY KEY"]
            data = [[r["record_id"]] for r in members]
            for i, label in enumerate(labels):
                parsed = [typed(r["fields"][label], label) for r in members]
                kinds = {v["type"] for v in parsed if v["value"] is not None}
                value_type = next(iter(kinds)) if len(kinds) == 1 else None
                columns.append(
                    {
                        "name": f"c{i}",
                        "label": label,
                        "type": "TEXT",
                        "meaning": "원본 셀 전체",
                        "examples": [
                            value[:180]
                            for value in sorted(
                                {r["fields"][label] for r in members},
                                key=lambda value: (len(value), value),
                            )[:3]
                        ],
                    }
                )
                definitions.append(f'"c{i}" TEXT NOT NULL')
                if value_type:
                    for suffix, sqltype, meaning in (
                        (
                            "value",
                            value_type,
                            "원본에서 단일 값으로 확인한 값. 미확정/복합 값은 NULL.",
                        ),
                        (
                            "unit",
                            "TEXT",
                            "값의 통화/단위. 서로 다른 단위를 합산하지 않는다.",
                        ),
                        (
                            "state",
                            "TEXT",
                            "reported 또는 원문 미확정 상태. NULL은 0이 아니다.",
                        ),
                    ):
                        columns.append(
                            {
                                "name": f"c{i}_{suffix}",
                                "label": label,
                                "type": sqltype,
                                "meaning": meaning,
                                "examples": (
                                    list(
                                        dict.fromkeys(
                                            v[suffix]
                                            for v in parsed
                                            if v[suffix] is not None
                                        )
                                    )[:3]
                                    if suffix == "value"
                                    else sorted(
                                        {
                                            v[suffix]
                                            for v in parsed
                                            if v[suffix] is not None
                                        }
                                    )
                                ),
                            }
                        )
                        definitions.append(f'"c{i}_{suffix}" TEXT')
                for data_row, r, v in zip(data, members, parsed, strict=True):
                    data_row.append(r["fields"][label])
                    if value_type:
                        data_row.extend((v["value"], v["unit"], v["state"]))
            db.execute(f'CREATE TABLE "{tableid}" ({",".join(definitions)}) STRICT')
            db.executemany(
                f'INSERT INTO "{tableid}" VALUES ({",".join("?" for _ in definitions)})',
                data,
            )
            db.executemany(
                "INSERT INTO lineage VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        tableid,
                        r["record_id"],
                        src["source_id"],
                        src["book_id"],
                        src["page"],
                        r["line_start"],
                        r["line_end"],
                        hashlib.sha256(
                            json.dumps(
                                r["fields"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                    )
                    for r in members
                ],
            )
            section = first["section"]
            heading = src["heading"]
            table = {
                "table_id": tableid,
                "source_id": sid,
                "book_id": src["book_id"],
                "file_name": src["file_name"],
                "page": src["page"],
                "heading": heading,
                "section": section,
                "page_family": fam,
                "line_start": first["line_start"],
                "line_end": max(r["line_end"] for r in members),
                "row_count": len(members),
                "columns": columns,
                "schema_sha256": hashlib.sha256(
                    json.dumps(
                        columns,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
            catalog.append(table)
            if fam == "IS":

                def esc(value):
                    return str(value).replace("|", "\\|").replace("\n", " ")

                head = "| " + " | ".join(map(esc, labels)) + " |"
                sep = "| " + " | ".join("---" for _ in labels) + " |"
                body = [
                    "| "
                    + " | ".join(esc(r["fields"].get(label, "")) for label in labels)
                    + " |"
                    for r in members
                ]
                chunks.append(
                    {
                        "table_id": tableid,
                        "chunk_id": tableid,
                        "book_id": src["book_id"],
                        "file_name": src["file_name"],
                        "page": src["page"],
                        "text": "\n".join([heading, section, head, sep, *body]),
                    }
                )
        catalog.sort(
            key=lambda row: (
                row["book_id"],
                row["page"],
                row["line_start"],
                row["table_id"],
            )
        )
        chunks.sort(key=lambda row: (row["book_id"], row["page"], row["chunk_id"]))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("tables.sqlite3 integrity_check failed")
        if db.execute("SELECT count(*) FROM lineage").fetchone()[0] != len(rows):
            raise ValueError("Physical table lineage row count mismatch")
        records_by_id = {r["record_id"]: r for r in rows}
        for table in catalog:
            cursor = db.execute(f'SELECT * FROM "{table["table_id"]}"')
            names = [column[0] for column in cursor.description]
            for sqlrow in cursor:
                raw = records_by_id[sqlrow[names.index("row_id")]]["fields"]
                if any(
                    sqlrow[names.index(f"c{index}")] != value
                    for index, value in enumerate(raw.values())
                ):
                    raise ValueError("Raw table cell roundtrip mismatch")
    (directory / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    dump(directory / "table-chunks.json", chunks)
    store = {
        "status": "complete",
        "rows": len(rows),
        "tables": len(catalog),
        "is_chunks": len(chunks),
        "inputs": {
            "source_fingerprint": source_fingerprint,
            "curation_manifest_sha256": curation_manifest_sha256,
            "builder_sha256": builder_sha256,
        },
        "raw_rows_roundtrip_verified": len(rows),
        "family_tables": dict(Counter(t["page_family"] for t in catalog)),
        "files": {
            n: digest(directory / n)
            for n in ("catalog.json", "tables.sqlite3", "table-chunks.json")
        },
    }
    dump(directory / "store.json", store)
    return store


def build_records_index(curated, pages_path, directory, builder_sha256=None):
    sources, records = _read_curated(curated)
    catalog = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
    _, page_text = page_references(pages_path)
    by_source = defaultdict(list)
    for record in records:
        by_source[record["source_id"]].append(record)
    path = directory / "records.sqlite3"
    if path.exists():
        path.unlink()
    schema = """
    CREATE TABLE entities(id TEXT PRIMARY KEY,family TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL);
    CREATE TABLE sources(entity TEXT NOT NULL REFERENCES entities(id),source TEXT NOT NULL,role TEXT NOT NULL,ordinal INTEGER NOT NULL,PRIMARY KEY(entity,source,role));
    CREATE INDEX sources_source ON sources(source);
    CREATE TABLE keys(value TEXT NOT NULL,kind TEXT NOT NULL,entity TEXT NOT NULL REFERENCES entities(id),source TEXT NOT NULL,relation TEXT NOT NULL,PRIMARY KEY(value,entity,source,relation));
    CREATE INDEX key_value ON keys(value);
    CREATE INDEX keys_by_entity ON keys(entity,kind,value);
    CREATE TABLE attributes(entity TEXT NOT NULL REFERENCES entities(id),field TEXT NOT NULL,value TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(entity,field,value,source));
    CREATE INDEX attribute_value ON attributes(field,value);
    CREATE TABLE tables(id TEXT PRIMARY KEY,source TEXT NOT NULL,metadata TEXT NOT NULL);
    CREATE VIRTUAL TABLE table_terms USING fts5(table_id UNINDEXED,tokens);
    """
    family_counts = {family_name: Counter() for family_name in ("HG", "HA", "IS")}

    def source_ref(source_id):
        try:
            source = sources[source_id]
        except KeyError as exc:
            raise ValueError(
                f"Curated record references unknown source: {source_id}"
            ) from exc
        return page_id(source)

    def add_entity(db, entity_id, family_name, kind, label):
        db.execute(
            "INSERT OR IGNORE INTO entities VALUES(?,?,?,?)",
            (entity_id, family_name, kind, label),
        )

    def add_key(db, value, kind, entity_id, source, relation="identity"):
        if value:
            db.execute(
                "INSERT OR IGNORE INTO keys VALUES(?,?,?,?,?)",
                (value, kind, entity_id, source, relation),
            )

    def add_attribute(db, entity_id, field, value, source):
        normalized = norm(value)
        if normalized:
            db.execute(
                "INSERT OR IGNORE INTO attributes VALUES(?,?,?,?)",
                (entity_id, field, normalized, source),
            )

    def add_source(db, entity_id, source, role):
        ordinal = 0 if role == "record" else ROLE_ORDER.index(role)
        db.execute(
            "INSERT OR IGNORE INTO sources VALUES(?,?,?,?)",
            (entity_id, source, role, ordinal),
        )

    def valid_iso_date(value):
        try:
            return date.fromisoformat(value).isoformat() == value
        except (TypeError, ValueError):
            return False

    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(schema)
        hg_links = defaultdict(set)
        for source_id, source in sources.items():
            if family(source) != "HG":
                continue
            heading_role = source["heading"].rsplit(" / ", 1)[-1].strip()
            role = ROLE_MAP.get(heading_role)
            if role is None:
                continue
            for record in by_source[source_id]:
                for case_id in record["case_ids"]:
                    if case_id.startswith("HG"):
                        hg_links[case_id].add((source_id, role))

        for case_id, links in sorted(hg_links.items()):
            entity_id = "HG:" + case_id
            add_entity(db, entity_id, "HG", "case", case_id)
            for source_id, role in sorted(
                links, key=lambda item: (ROLE_ORDER.index(item[1]), item[0])
            ):
                reference = source_ref(source_id)
                add_source(db, entity_id, reference, role)
                add_key(db, case_id, "reservation_id", entity_id, reference)
                for record in by_source[source_id]:
                    if case_id not in record["case_ids"]:
                        continue
                    for raw_code in record["identifiers"]:
                        code = normalize_code(raw_code)
                        kind = code_kind(code)
                        if kind:
                            add_key(db, code, kind, entity_id, reference)
                    fields = record["fields"]
                    if role == "events":
                        for label, value in fields.items():
                            profile_field = {
                                "화주": "shipper",
                                "화물": "cargo",
                                "계약": "contract_id",
                            }.get(label)
                            if profile_field:
                                add_attribute(
                                    db, entity_id, profile_field, value, reference
                                )
                            if label == "장비":
                                equipment_class = re.search(
                                    r"\b(20GP|40GP|20RF|40RF)\b", value, re.I
                                )
                                if equipment_class:
                                    add_attribute(
                                        db,
                                        entity_id,
                                        "spec",
                                        equipment_class[1],
                                        reference,
                                    )
                            if label == "화주" and record.get("text"):
                                cargo = re.search(
                                    r"화물\s+(.+?)(?=,\s*(?:예약|장비)|$)",
                                    record["text"],
                                )
                                if cargo:
                                    add_attribute(
                                        db, entity_id, "cargo", cargo[1], reference
                                    )
                    for label, value in fields.items():
                        date_field = DATE_FIELDS.get(label)
                        if date_field:
                            for date_value in re.findall(r"\d{4}-\d{2}-\d{2}", value):
                                if valid_iso_date(date_value):
                                    add_attribute(
                                        db, entity_id, date_field, date_value, reference
                                    )
                    event_field = HG_EVENT_FIELDS.get(fields.get("항목", ""))
                    event_date = fields.get("날짜 또는 상태", "")
                    if event_field and valid_iso_date(event_date):
                        add_attribute(db, entity_id, event_field, event_date, reference)
                    if fields.get("항목") == "개정 효력일":
                        effective_date = fields.get("확인 기록", "")
                        if valid_iso_date(effective_date):
                            add_attribute(
                                db,
                                entity_id,
                                "recorded_amendment_effective_date",
                                effective_date,
                                reference,
                            )

        for source_id, source in sources.items():
            family_name = family(source)
            if family_name == "HA":
                heading = source["heading"]
                role = next(
                    (
                        value
                        for label, value in HA_ROLES.items()
                        if heading.endswith(label)
                    ),
                    "record",
                )
                reference = source_ref(source_id)
                for record in by_source[source_id]:
                    fields = record["fields"]
                    for case_id in record["case_ids"]:
                        if not case_id.startswith("HA28-"):
                            continue
                        entity_id = "HA:" + case_id
                        add_entity(db, entity_id, "HA", "case", case_id)
                        add_source(db, entity_id, reference, role)
                        add_key(db, case_id, "reservation_id", entity_id, reference)
                        label = fields.get("항목", fields.get("확인 항목", ""))
                        value = fields.get("기재 내용", "")
                        if label in {"예약·장비", "대상 연결"}:
                            for raw_code in CODE.findall(value):
                                code = normalize_code(raw_code)
                                if code.startswith("HL-"):
                                    add_key(
                                        db, code, "equipment_id", entity_id, reference
                                    )
                                elif code.startswith("HA28-"):
                                    add_key(
                                        db, code, "reservation_id", entity_id, reference
                                    )
                        if label in {"화주·화물", "적용 대상"} and " / " in value:
                            shipper, subject = value.split(" / ", 1)
                            add_attribute(db, entity_id, "shipper", shipper, reference)
                            field = "cargo" if label == "화주·화물" else "spec"
                            add_attribute(db, entity_id, field, subject, reference)
                        date_field = DATE_FIELDS.get(fields.get("기록", ""))
                        date_value = fields.get("날짜·접수값", "")
                        if date_field and valid_iso_date(date_value):
                            add_attribute(
                                db, entity_id, date_field, date_value, reference
                            )

        is_record_code = re.compile(r"IS\d{2}-\d{3}-\d{2}")
        for source_id, source in sources.items():
            if family(source) != "IS":
                continue
            reference = source_ref(source_id)
            for record in by_source[source_id]:
                if record["kind"] != "table_row":
                    continue
                record_keys = []
                for field, value in record["fields"].items():
                    if field not in IS_KEYS:
                        continue
                    if is_record_code.fullmatch(value):
                        record_keys.append(value)
                    elif field in IS_ANNOTATED_KEYS:
                        key, separator, annotation = value.partition(" ")
                        if (
                            separator
                            and annotation.strip()
                            and is_record_code.fullmatch(key)
                            and len(is_record_code.findall(value)) == 1
                        ):
                            record_keys.append(key)
                record_keys = list(dict.fromkeys(record_keys))
                if not record_keys:
                    continue
                entity_id = "IS:" + record["record_id"]
                add_entity(db, entity_id, "IS", "record", record_keys[0])
                add_source(db, entity_id, reference, "record")
                for key in record_keys:
                    add_key(db, key, "record_id", entity_id, reference)
                for field, value in record["fields"].items():
                    if any(
                        word in field
                        for word in ("대상", "예약", "장비", "근거", "청구", "승인")
                    ):
                        for raw_code in CODE.findall(value):
                            related_code = normalize_code(raw_code)
                            add_key(
                                db,
                                related_code,
                                "record_reference",
                                entity_id,
                                reference,
                                "record_reference",
                            )

        # HA's original contract number is linked only from its contract-profile source.
        ha_contract_sources = db.execute(
            "SELECT s.entity,s.source FROM sources s "
            "JOIN entities e ON e.id=s.entity "
            "WHERE e.family='HA' AND s.role='contract'"
        ).fetchall()
        for entity_id, reference in ha_contract_sources:
            for contract_id in re.findall(
                r"원계약\s+(CT-[A-Z0-9-]+)", page_text[reference]
            ):
                add_key(db, contract_id, "contract_id", entity_id, reference)
                add_attribute(db, entity_id, "contract_id", contract_id, reference)

        for table in catalog:
            metadata = json.dumps(table, ensure_ascii=False)
            db.execute(
                "INSERT INTO tables VALUES(?,?,?)",
                (table["table_id"], table["source_id"], metadata),
            )
            index_text = " ".join(
                [
                    table["heading"],
                    table["section"],
                    *(str(c) for c in table["columns"]),
                ]
            )
            db.execute(
                "INSERT INTO table_terms VALUES(?,?)",
                (table["table_id"], " ".join(tokenize_for_bm25(index_text))),
            )
        db.commit()
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Records foreign_key_check failed")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Records integrity_check failed")
        counts = {
            name: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for name, table in (
                ("entities", "entities"),
                ("keys", "keys"),
                ("attributes", "attributes"),
                ("sources", "sources"),
                ("tables", "tables"),
            )
        }
        for family_name in family_counts:
            family_counts[family_name]["entities"] = db.execute(
                "SELECT count(*) FROM entities WHERE family=?", (family_name,)
            ).fetchone()[0]
            family_counts[family_name]["sources"] = db.execute(
                "SELECT count(*) FROM sources s JOIN entities e ON e.id=s.entity "
                "WHERE e.family=?",
                (family_name,),
            ).fetchone()[0]
    manifest = {
        "schema_version": 1,
        "version": 1,
        "database_sha256": digest(path),
        "inputs": {
            "pages_sha256": digest(pages_path),
            "catalog_sha256": digest(directory / "catalog.json"),
        },
        "counts": counts,
        "families": {name: dict(values) for name, values in family_counts.items()},
    }
    if builder_sha256:
        manifest["builder_sha256"] = builder_sha256
    curation_manifest = curated / "manifest.json"
    if curation_manifest.exists():
        manifest["curation_manifest_sha256"] = digest(curation_manifest)
    dump(directory / "records.json", manifest)
    return manifest
