"""Build the self-contained Advanced TableRAG data bundle from raw PDFs."""

from __future__ import annotations
import hashlib
import inspect
import json
from importlib.metadata import version as distribution_version
from contextlib import contextmanager
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import date
from unicodedata import normalize
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from time import perf_counter
import numpy as np
from pypdf import PdfReader
from app.ingestion.curated_schema import CuratedRecord, CuratedSource
from app.ingestion.curation import (
    curation_environment,
    curate_dataset,
    read_rows,
    validate_curated,
)
from app.indexing.keyword_store import TOKEN, tokenize
from app.rag.keyword_retriever import (
    _CODE_DASHES,
    _CODE_PATTERN,
    tokenize_for_bm25,
)

EMBEDDING_ID = "nlpai-lab/KURE-v3"
EMBEDDING_REVISION = "73d5e710bc0b1f324a60746922cbad6d38f480d4"
RERANKER_ID = "nlpai-lab/KURE-Reranker-nano"
RERANKER_REVISION = "8514b9e113f3b27c441ed9c2b50f81d80388b2fd"
DIMENSIONS, PAGE_MAX_LENGTH, QUERY_MAX_LENGTH, TABLE_MAX_LENGTH = 2048, 8192, 2048, 2048
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
ROLE_ORDER = (
    "contract",
    "events",
    "approval",
    "invoice",
    "receipt",
    "settlement",
    "record",
)
HA_ROLES = {
    "계약 조건 확인": "contract",
    "사건·증빙 접수 대조": "events",
    "비용별 정산 확인": "settlement",
}
DATE_FIELDS = {
    "양하 원시기록": "actual_discharge_date",
    "적입 반출기록": "actual_gate_out_date",
    "반출 전표": "system_entry_date",
    "공장비 인수증": "actual_designated_return_date",
}
HG_EVENT_FIELDS = {
    "실제 양하": "actual_discharge_date",
    "실제 적입 장비 반출": "actual_gate_out_date",
    "실제 지정 장치장 반납": "actual_designated_return_date",
    "반납 예약 예정": "scheduled_return_date",
    "전산 입력일": "system_entry_date",
}
CODE = re.compile(
    r"(?<![A-Z0-9_])([A-Z][A-Z0-9]*(?:\s*[-‐‑–—−－]\s*[A-Z0-9]+)+)(?![A-Z0-9_])", re.I
)
IS_KEYS = {
    "SI기록",
    "VGM 대조",
    "검산번호",
    "검토 기록번호",
    "계산 메모",
    "계획번호",
    "기록번호",
    "단위 대조",
    "당사자 등록",
    "대조번호",
    "등록번호",
    "문서 기록",
    "반입 기록",
    "상태 기록",
    "수정통지",
    "신청·승인 기록",
    "업무 기록번호",
    "운항 문의",
    "인계번호",
    "입금 기록",
    "접수 대조",
    "접수번호",
    "처리 기록",
    "확인번호",
}
IS_ANNOTATED_KEYS = {"수정통지", "입금 기록"}
BUNDLE_FILES = (
    "pages.json",
    "vectors.npy",
    "store.json",
    "catalog.json",
    "tables.sqlite3",
    "table-chunks.json",
    "records.json",
    "records.sqlite3",
    "corpus.json",
)
VECTOR_DEPENDENCIES = (
    "numpy",
    "sentence-transformers",
    "torch",
    "transformers",
    "tokenizers",
    "huggingface-hub",
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def norm(value):
    return " ".join(normalize("NFKC", value).casefold().split())


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


def page_references(pages_path):
    pages = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    references = set()
    texts = {}
    for page in pages:
        book_id = Path(page["file_name"]).stem
        source = {"book_id": book_id, "page": page["page"]}
        reference = page_id(source)
        if reference in references:
            raise ValueError(f"Duplicate source page: {reference}")
        references.add(reference)
        texts[reference] = page["text"]
    return references, texts


def callable_fingerprint(*functions, values=()):
    digest = hashlib.sha256()
    for function in functions:
        digest.update(function.__module__.encode())
        digest.update(function.__qualname__.encode())
        digest.update(inspect.getsource(function).encode())
    digest.update(
        json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            default=lambda value: (
                sorted(value) if isinstance(value, set) else repr(value)
            ),
        ).encode()
    )
    return digest.hexdigest()


def dependency_versions(names):
    return {name: distribution_version(name) for name in names}


def embedding_contract(device):
    encoder_sha256 = callable_fingerprint(
        _encode_document_batch,
        _input_token_stats,
        values=(
            DIMENSIONS,
            PAGE_MAX_LENGTH,
            TABLE_MAX_LENGTH,
            "bfloat16",
            "float32",
            4,
            256,
        ),
    )
    return {
        "model_id": EMBEDDING_ID,
        "revision": EMBEDDING_REVISION,
        "dimensions": DIMENSIONS,
        "query_max_length": QUERY_MAX_LENGTH,
        "page_max_length": PAGE_MAX_LENGTH,
        "table_max_length": TABLE_MAX_LENGTH,
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
        "normalized": True,
        "model_dtype": "bfloat16",
        "output_dtype": "float32",
        "encoder_sha256": encoder_sha256,
        "encoder_dependencies": dependency_versions(VECTOR_DEPENDENCIES),
        "build_device": "cuda:0" if device == "cuda" else device,
    }


def stage_fingerprints():
    curation_digest = hashlib.sha256()
    for name in ("curation.py", "curated_schema.py", "layout.py", "common_rules.py"):
        curation_digest.update(name.encode())
        curation_digest.update(Path(__file__).with_name(name).read_bytes())
    curation_digest.update(
        json.dumps(curation_environment(), ensure_ascii=False, sort_keys=True).encode()
    )
    curation = curation_digest.hexdigest()
    pages = callable_fingerprint(
        extract_pages,
        dependency_versions,
        values=("pypdf-layout", dependency_versions(("pypdf",))),
    )
    physical = callable_fingerprint(
        build_physical_store,
        _read_curated,
        typed,
        family,
        page_id,
        norm,
        dump,
        digest,
        values=(ROLE_MAP, DATE_FIELDS, HG_EVENT_FIELDS, ROLE_ORDER),
    )
    records = callable_fingerprint(
        build_records_index,
        page_references,
        page_id,
        family,
        norm,
        normalize_code,
        code_kind,
        dump,
        digest,
        tokenize_for_bm25,
        tokenize,
        values=(
            ROLE_MAP,
            HA_ROLES,
            DATE_FIELDS,
            HG_EVENT_FIELDS,
            IS_KEYS,
            IS_ANNOTATED_KEYS,
            ROLE_ORDER,
            CODE.pattern,
            TOKEN.pattern,
            _CODE_PATTERN.pattern,
            tuple(sorted(_CODE_DASHES.items())),
        ),
    )
    vectors = callable_fingerprint(
        build_vectors,
        _encode_document_batch,
        _input_token_stats,
        embedding_contract,
        dependency_versions,
        values=(embedding_contract("cuda:0"),),
    )
    orchestration = callable_fingerprint(
        build_bundle,
        _build_bundle_locked,
        _output_build_lock,
        stage_fingerprints,
    )
    stages = {
        "curation": curation,
        "pages": pages,
        "physical": physical,
        "records": records,
        "vectors": vectors,
        "orchestration": orchestration,
    }
    stages["overall"] = hashlib.sha256(
        json.dumps(stages, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return stages


def page_id(source):
    return f"{source['book_id']}:p{int(source['page']):04d}"


def family(source):
    heading = source["heading"]
    found = set()
    if re.search(r"HG20\d{2}-", heading):
        found.add("HG")
    if re.search(r"HA28-", heading):
        found.add("HA")
    if re.match(r"IS\d+\s*\|", heading):
        found.add("IS")
    if any(code.startswith(("SW28-", "DL28-")) for code in CODE.findall(heading)):
        found.add("other")
    return next(iter(found)) if len(found) == 1 else "unknown"


def extract_pages(documents):
    pages = []
    for pdf in sorted(Path(documents).glob("*.pdf")):
        for number, page in enumerate(PdfReader(pdf).pages, 1):
            raw = page.extract_text(extraction_mode="layout") or ""
            blocks = re.split(r"\n\s*\n", raw.strip())
            restored = []
            for block in blocks:
                text = ""
                for line in block.splitlines():
                    line = " ".join(line.split())
                    if not line:
                        continue
                    if text.endswith((".", ",", ")", "?", "!", ":", "”", "’")):
                        text += " "
                    text += line
                if text:
                    restored.append(text)
            pages.append(
                {"file_name": pdf.name, "page": number, "text": "\n\n".join(restored)}
            )
    if not pages:
        raise ValueError(f"No PDFs found under {documents}")
    return pages


def typed(raw, label):
    value = raw.strip()
    state = next(
        (
            s
            for s in ("검토 필요", "미결", "해당 없음", "미정", "확인 필요", "미확정")
            if s in value
        ),
        "reported",
    )
    if state != "reported":
        return {"value": None, "unit": None, "state": state, "type": "TEXT"}
    if value.startswith(label):
        value = value[len(label) :].lstrip(" :：")
    date_match = re.fullmatch(
        r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})일?", value
    )
    if date_match:
        try:
            parsed_date = date(*(int(part) for part in date_match.groups()))
        except ValueError:
            parsed_date = None
        if parsed_date:
            return {
                "value": parsed_date.isoformat(),
                "unit": "date",
                "state": state,
                "type": "DATE",
            }
    number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    signed_number = rf"[+-]?{number}"
    measure = re.fullmatch(
        rf"(?:(KRW|USD|EUR)\s*({signed_number})|({signed_number})\s*(원|달러|KRW|USD|EUR|kg|KG|톤|일|대|개|%))",
        value,
    )
    if measure:
        number = measure[2] or measure[3]
        unit = measure[1] or measure[4]
        unit = {"원": "KRW", "달러": "USD", "KG": "kg"}.get(unit, unit)
        return {
            "value": str(Decimal(number.replace(",", ""))),
            "unit": unit,
            "state": state,
            "type": "DECIMAL",
        }
    if re.fullmatch(signed_number, value) and not any(
        x in label for x in ("번호", "코드", "예약")
    ):
        return {
            "value": str(Decimal(value.replace(",", ""))),
            "unit": "",
            "state": state,
            "type": "DECIMAL",
        }
    return {"value": None, "unit": None, "state": state, "type": "TEXT"}


def _read_curated(curated):
    sources = {
        row["source_id"]: CuratedSource.model_validate(row).model_dump()
        for row in read_rows(curated / "sources.jsonl")
    }
    records = []
    for path in sorted((curated / "records").glob("*.jsonl")):
        records.extend(
            CuratedRecord.model_validate(row).model_dump() for row in read_rows(path)
        )
    return sources, records


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


def prefetch_models():
    from huggingface_hub import snapshot_download

    return {
        model: snapshot_download(repo_id=model, revision=revision)
        for model, revision in (
            (EMBEDDING_ID, EMBEDDING_REVISION),
            (RERANKER_ID, RERANKER_REVISION),
        )
    }


def _atomic_dump(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    dump(temporary, value)
    os.replace(temporary, path)


def _input_token_stats(model, texts, maximum, *, batch_size=256):
    prompt = model.prompts.get("document", "")
    total = 0
    longest = 0
    truncated = 0
    for start in range(0, len(texts), batch_size):
        tokenized = model.tokenizer(
            [prompt + text for text in texts[start : start + batch_size]],
            truncation=False,
            padding=False,
            verbose=False,
        )
        lengths = [len(input_ids) for input_ids in tokenized["input_ids"]]
        total += sum(lengths)
        longest = max(longest, max(lengths, default=0))
        truncated += sum(length > maximum for length in lengths)
    return {
        "count": len(texts),
        "raw_tokens": total,
        "max_raw_tokens": longest,
        "truncated_inputs": truncated,
        "max_seq_length": maximum,
        "prompt": prompt,
    }


def _encode_document_batch(model, texts, *, batch_size=4):
    import torch

    with torch.inference_mode():
        vectors = model.encode_document(
            texts,
            batch_size=batch_size,
            normalize_embeddings=False,
            show_progress_bar=False,
            convert_to_tensor=True,
        )
    normalized = torch.nn.functional.normalize(vectors.float(), p=2, dim=1)
    result = normalized.cpu().numpy().astype(np.float32, copy=False)
    if (
        result.ndim != 2
        or result.shape[1] != DIMENSIONS
        or not np.isfinite(result).all()
        or not np.allclose(np.linalg.norm(result, axis=1), 1.0, atol=1e-4)
    ):
        raise ValueError("Invalid normalized document embeddings")
    return result


def build_vectors(
    pages_path,
    chunks_path,
    output,
    device="cpu",
    cache_bundle=None,
    progress_dir=None,
    input_identity=None,
):
    page_data = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    if cache_bundle:
        cache = Path(cache_bundle)
        manifest_path = cache / "corpus.json"
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            old_store = json.loads((cache / "store.json").read_text(encoding="utf-8"))
            old_pages = json.loads((cache / "pages.json").read_text(encoding="utf-8"))
            old_chunks = json.loads(
                (cache / "table-chunks.json").read_text(encoding="utf-8")
            )
            declared_files = old["files"]
            store_files = old_store["files"]
            required_files = {
                "pages.json",
                "vectors.npy",
                "store.json",
                "catalog.json",
                "tables.sqlite3",
                "table-chunks.json",
                "records.json",
                "records.sqlite3",
            }
            missing_hashes = required_files - set(declared_files)
            if missing_hashes:
                raise ValueError(
                    "Cached bundle is missing required declared SHA values: "
                    + ", ".join(sorted(missing_hashes))
                )
            for name in required_files:
                if (
                    not (cache / name).is_file()
                    or digest(cache / name) != declared_files[name]
                ):
                    raise ValueError(f"Cached bundle declared SHA mismatch: {name}")
            for name in ("catalog.json", "tables.sqlite3", "table-chunks.json"):
                if (
                    name not in store_files
                    or not (cache / name).is_file()
                    or digest(cache / name) != store_files[name]
                ):
                    raise ValueError(f"Cached store declared SHA mismatch: {name}")
        except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Cached bundle is missing a valid declared file hash"
            ) from error
        expected_identity = input_identity or {}
        expected_contract = expected_identity.get("embedding") or embedding_contract(
            device
        )
        same = (
            old.get("embedding") == expected_contract
            and old.get("pipeline_stages", {}).get("vectors")
            == expected_identity.get("builder_sha256")
            and expected_identity.get("builder_sha256") is not None
            and old_pages == page_data
            and old_chunks == chunks
            and old.get("counts", {}).get("pages") == len(page_data)
            and old.get("counts", {}).get("table_documents") == len(chunks)
        )
        if same:
            vectors = np.load(cache / "vectors.npy", mmap_mode="r", allow_pickle=False)
            if (
                vectors.shape == (len(page_data) + len(chunks), DIMENSIONS)
                and vectors.dtype == np.float32
                and np.isfinite(vectors).all()
                and np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)
            ):
                shutil.copyfile(cache / "vectors.npy", output)
                return {
                    "reused": True,
                    "reused_from": str(cache.resolve()),
                    "vectors_sha256": digest(output),
                }
        raise ValueError(
            "Cached vectors lack a complete compatible embedding contract, "
            "verified file hashes, or identical page/table inputs; omit "
            "--reuse-vectors-from to encode current inputs"
        )

    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(8)
    model = SentenceTransformer(
        EMBEDDING_ID,
        revision=EMBEDDING_REVISION,
        device=device,
        trust_remote_code=True,
        model_kwargs={"dtype": torch.bfloat16},
    )
    if model.get_sentence_embedding_dimension() != DIMENSIONS:
        raise ValueError("Unexpected embedding dimension")
    if model.dtype != torch.bfloat16:
        raise ValueError("Pinned embedding model must load in bfloat16")
    if (
        model.prompts.get("document", "") != "passage: "
        or model.prompts.get("query", "") != "query: "
    ):
        raise ValueError("Pinned embedding prompts differ from the corpus contract")

    progress = Path(progress_dir or Path(output).parent / ".vector-progress")
    progress.mkdir(parents=True, exist_ok=True)
    state_path = progress / "build.json"
    identity = input_identity or {
        "pages_sha256": digest(pages_path),
        "table_chunks_sha256": digest(chunks_path),
        "embedding": embedding_contract(device),
    }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {
            "status": "building",
            "inputs": identity,
            "groups": {"pages": [], "tables": []},
            "model": {
                "id": EMBEDDING_ID,
                "revision": EMBEDDING_REVISION,
                "dtype": str(model.dtype),
                "device": str(model.device),
                "normalized_output_dtype": "float32",
                "batch_size": 4,
                "chunk_size": 256,
            },
        }
        _atomic_dump(state_path, state)
    except json.JSONDecodeError as error:
        raise ValueError("Vector progress manifest is not valid JSON") from error
    if state.get("inputs") != identity:
        raise ValueError(
            "Vector progress belongs to different inputs or encoder code; choose a new work directory"
        )
    groups = {"pages": page_data, "tables": chunks}
    group_vectors = {}
    run_started = perf_counter()
    reused_chunks = 0
    for group_name, items in groups.items():
        texts = [item["text"] for item in items]
        maximum = PAGE_MAX_LENGTH if group_name == "pages" else TABLE_MAX_LENGTH
        model.max_seq_length = maximum
        manifest_entries = state["groups"].setdefault(group_name, [])
        vectors_for_group = []
        for chunk_index, start in enumerate(range(0, len(texts), 256)):
            end = min(start + 256, len(texts))
            chunk_path = progress / f"{group_name}-{start:05d}-{end:05d}.npy"
            if chunk_index < len(manifest_entries):
                entry = manifest_entries[chunk_index]
                if (
                    entry.get("start") != start
                    or entry.get("count") != end - start
                    or entry.get("file") != chunk_path.name
                    or not chunk_path.exists()
                    or entry.get("sha256") != digest(chunk_path)
                ):
                    raise ValueError(
                        f"Saved vector chunk is corrupt: {chunk_path.name}"
                    )
                values = np.load(chunk_path, mmap_mode="r", allow_pickle=False)
                if (
                    values.shape != (end - start, DIMENSIONS)
                    or values.dtype != np.float32
                ):
                    raise ValueError(
                        f"Saved vector chunk has invalid shape: {chunk_path.name}"
                    )
                reused_chunks += 1
            else:
                batch_texts = texts[start:end]
                stats = _input_token_stats(model, batch_texts, maximum)
                started = perf_counter()
                values = _encode_document_batch(model, batch_texts, batch_size=4)
                elapsed = perf_counter() - started
                temporary = chunk_path.with_suffix(".npy.tmp")
                with temporary.open("wb") as stream:
                    np.save(stream, values, allow_pickle=False)
                os.replace(temporary, chunk_path)
                entry = {
                    "start": start,
                    "count": end - start,
                    "file": chunk_path.name,
                    "sha256": digest(chunk_path),
                    "seconds": elapsed,
                    "input_stats": stats,
                }
                manifest_entries.append(entry)
                state["groups"][group_name] = manifest_entries
                _atomic_dump(state_path, state)
                print(
                    f"Embedded {group_name}: {end}/{len(texts)} items",
                    flush=True,
                )
            vectors_for_group.append(values)
        group_vectors[group_name] = vectors_for_group
        stats_entries = [
            entry["input_stats"] for entry in manifest_entries if "input_stats" in entry
        ]
        state.setdefault("input_stats", {})[group_name] = {
            "count": sum(item["count"] for item in stats_entries),
            "raw_tokens": sum(item["raw_tokens"] for item in stats_entries),
            "max_raw_tokens": max(
                (item["max_raw_tokens"] for item in stats_entries), default=0
            ),
            "truncated_inputs": sum(item["truncated_inputs"] for item in stats_entries),
            "max_seq_length": maximum,
            "prompt": model.prompts.get("document", ""),
        }
        _atomic_dump(state_path, state)

    output = Path(output)
    temporary_output = output.with_suffix(".npy.tmp")
    matrix = np.lib.format.open_memmap(
        temporary_output,
        mode="w+",
        dtype=np.float32,
        shape=(len(page_data) + len(chunks), DIMENSIONS),
    )
    offset = 0
    for group_name in ("pages", "tables"):
        for vectors in group_vectors[group_name]:
            matrix[offset : offset + len(vectors)] = vectors
            offset += len(vectors)
    matrix.flush()
    del matrix
    os.replace(temporary_output, output)
    final = np.load(output, mmap_mode="r", allow_pickle=False)
    if (
        final.shape != (len(page_data) + len(chunks), DIMENSIONS)
        or final.dtype != np.float32
        or not np.isfinite(final).all()
        or not np.allclose(np.linalg.norm(final, axis=1), 1.0, atol=1e-4)
    ):
        raise ValueError("Invalid vector matrix")
    del final
    state.update(
        status="complete",
        vectors_sha256=digest(output),
        encoding_seconds=perf_counter() - run_started,
        reused_chunks=reused_chunks,
    )
    _atomic_dump(state_path, state)
    del model
    return {
        "reused": False,
        "device": device,
        "dtype": state["model"]["dtype"],
        "batch_size": 4,
        "chunk_size": 256,
        "input_stats": state["input_stats"],
        "encoding_seconds": state["encoding_seconds"],
        "reused_chunks": reused_chunks,
        "vectors_sha256": state["vectors_sha256"],
    }


def check_database(path):
    with sqlite3.connect(path) as db:
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError(f"SQLite integrity failure: {path}")


def check_bundle(directory):
    from app.rag.corpus import load_corpus
    from app.rag.records import RecordIndex

    directory = Path(directory).resolve()
    corpus = load_corpus(directory)
    index = RecordIndex(directory)
    for name in ("tables.sqlite3", "records.sqlite3"):
        check_database(directory / name)
    result = {
        "path": str(directory),
        "pages": len(corpus.pages_by_source),
        "documents": len(corpus.documents),
        "vectors": list(corpus.vectors.shape),
        "tables": len(corpus.table_store.catalog),
        "record_counts": index.manifest["counts"],
        "families": index.manifest["families"],
    }
    index.close()
    return result


@contextmanager
def _output_build_lock(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.build.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            detail = f"pid={owner.get('pid', 'unknown')}"
        except (OSError, json.JSONDecodeError):
            detail = "owner unknown"
        raise FileExistsError(
            f"Another build holds {lock_path} ({detail}). If that process stopped, "
            "verify it is no longer running and remove the stale lock before retrying."
        ) from error

    try:
        os.write(
            descriptor,
            json.dumps({"pid": os.getpid(), "output": str(output)}).encode(),
        )
        yield
    finally:
        owner_stat = os.fstat(descriptor)
        os.close(descriptor)
        try:
            current_stat = lock_path.stat()
        except FileNotFoundError:
            current_stat = None
        if current_stat is not None and (
            current_stat.st_dev,
            current_stat.st_ino,
        ) == (owner_stat.st_dev, owner_stat.st_ino):
            lock_path.unlink(missing_ok=True)


def build_bundle(documents, output, device="cpu", work_dir=None, cache_bundle=None):
    output = Path(output).resolve()
    with _output_build_lock(output):
        return _build_bundle_locked(
            documents,
            output,
            device=device,
            work_dir=work_dir,
            cache_bundle=cache_bundle,
        )


def _build_bundle_locked(
    documents, output, device="cpu", work_dir=None, cache_bundle=None
):
    documents = Path(documents).resolve()
    output = Path(output).resolve()
    if not documents.is_dir():
        raise FileNotFoundError(documents)
    pdfs = sorted(documents.glob("*.pdf"))
    source_identity = [{"file_name": p.name, "sha256": digest(p)} for p in pdfs]
    if not source_identity:
        raise ValueError(f"No PDFs found under {documents}")
    source_fingerprint = hashlib.sha256(
        json.dumps(source_identity, separators=(",", ":")).encode()
    ).hexdigest()
    stage_codes = stage_fingerprints()
    existing_corpus_path = output / "corpus.json"
    publish_state_path = output / ".build-state.json"
    publication_identity = {
        "source_pdfs_sha256": source_fingerprint,
        "pipeline_sha256": stage_codes["overall"],
    }
    if output.is_file():
        raise FileExistsError(f"Output path is a file: {output}")
    if publish_state_path.exists():
        try:
            publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Incomplete publication state is unreadable: {publish_state_path}"
            ) from error
        if publish_state != publication_identity:
            raise ValueError(
                "Incomplete output belongs to different inputs or pipeline; choose a new output directory"
            )
    existing_corpus = None
    if existing_corpus_path.exists():
        existing_corpus = json.loads(existing_corpus_path.read_text(encoding="utf-8"))
        if (
            existing_corpus.get("inputs", {}).get("source_pdfs_sha256")
            != source_fingerprint
        ):
            raise ValueError(
                "Existing bundle was built from different source PDFs; choose a new output directory"
            )
        if existing_corpus.get("pipeline_sha256") != stage_codes["overall"]:
            raise ValueError(
                "Existing bundle uses a different pipeline fingerprint; choose a new output directory"
            )
        try:
            check_bundle(output)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            pass
        else:
            publish_state_path.unlink(missing_ok=True)
            return output
    elif any((output / name).exists() for name in BUNDLE_FILES):
        raise FileExistsError(
            "Output contains bundle files without a matching build-state marker; "
            "choose a new output directory or move those files aside"
        )
    output.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir or output.parent / f".{output.name}-build").resolve()
    work.mkdir(parents=True, exist_ok=True)
    curated_root = (
        work / f"curated-{source_fingerprint[:12]}-{stage_codes['curation'][:12]}"
    )
    cache_root = work / "layout-cache"
    curated, curation_report = curate_dataset(
        "advanced",
        documents.parent.parent,
        curated_root,
        cache_root,
        documents_directory=documents,
    )
    validate_curated(curated)
    curation_manifest = json.loads(
        (curated / "manifest.json").read_text(encoding="utf-8")
    )
    curated_pdfs = [
        {"file_name": item["file_name"], "sha256": item["sha256"]}
        for item in curation_manifest["identity"]["documents"]
    ]
    if curated_pdfs != source_identity:
        raise ValueError("Curation inputs do not match the requested PDF directory")
    stage = work / "bundle"
    stage.mkdir(exist_ok=True)
    pages_path = stage / "pages.json"
    page_identity = stage / "pages-input.json"
    expected_page_identity = {
        "inputs": source_identity,
        "builder_sha256": stage_codes["pages"],
    }
    try:
        existing_page_identity = json.loads(page_identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing_page_identity = None
    pages_valid = (
        pages_path.exists()
        and isinstance(existing_page_identity, dict)
        and all(
            existing_page_identity.get(key) == value
            for key, value in expected_page_identity.items()
        )
        and existing_page_identity.get("pages_sha256") == digest(pages_path)
    )
    if not pages_valid:
        dump(pages_path, extract_pages(documents))
        dump(
            page_identity,
            {**expected_page_identity, "pages_sha256": digest(pages_path)},
        )
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    if len(pdfs) == 120 and len(pages) != 18000:
        raise ValueError(f"Expected 18000 pages, got {len(pages)}")
    curation_manifest_sha256 = digest(curated / "manifest.json")
    expected_store_inputs = {
        "source_fingerprint": source_fingerprint,
        "curation_manifest_sha256": curation_manifest_sha256,
        "builder_sha256": stage_codes["physical"],
    }
    try:
        store_state = json.loads((stage / "store.json").read_text(encoding="utf-8"))
        store_valid = (
            store_state.get("inputs") == expected_store_inputs
            and {"catalog.json", "tables.sqlite3", "table-chunks.json"}
            <= set(store_state.get("files", {}))
            and all(
                (stage / name).exists() and digest(stage / name) == value
                for name, value in store_state.get("files", {}).items()
            )
        )
    except (OSError, json.JSONDecodeError):
        store_valid = False
    if not store_valid:
        build_physical_store(
            curated,
            pages,
            stage,
            source_fingerprint,
            cache_root / "advanced",
            builder_sha256=stage_codes["physical"],
            curation_manifest_sha256=curation_manifest_sha256,
        )
    pages_hash = digest(pages_path)
    catalog_hash = digest(stage / "catalog.json")
    records_manifest_path = stage / "records.json"
    try:
        record_state = json.loads(records_manifest_path.read_text(encoding="utf-8"))
        records_valid = (
            record_state.get("inputs")
            == {"pages_sha256": pages_hash, "catalog_sha256": catalog_hash}
            and record_state.get("database_sha256") == digest(stage / "records.sqlite3")
            and record_state.get("builder_sha256") == stage_codes["records"]
            and record_state.get("curation_manifest_sha256")
            == digest(curated / "manifest.json")
        )
    except (OSError, json.JSONDecodeError):
        records_valid = False
    if not records_valid:
        build_records_index(
            curated, pages_path, stage, builder_sha256=stage_codes["records"]
        )
    vectors = stage / "vectors.npy"
    identity = stage / "vectors-input.json"
    vector_manifest = stage / "vectors-manifest.json"
    vector_input = {
        "pages_sha256": pages_hash,
        "table_chunks_sha256": digest(stage / "table-chunks.json"),
        "builder_sha256": stage_codes["vectors"],
        "embedding": embedding_contract(device),
    }
    try:
        vector_state = json.loads(vector_manifest.read_text(encoding="utf-8"))
        vectors_valid = (
            vectors.exists()
            and identity.exists()
            and json.loads(identity.read_text(encoding="utf-8")) == vector_input
            and vector_state.get("inputs") == vector_input
            and vector_state.get("vectors_sha256") == digest(vectors)
        )
    except (OSError, json.JSONDecodeError):
        vectors_valid = False
    if not vectors_valid:
        execution = build_vectors(
            pages_path,
            stage / "table-chunks.json",
            vectors,
            device=device,
            cache_bundle=cache_bundle,
            progress_dir=stage / f".vectors-{stage_codes['vectors'][:12]}",
            input_identity=vector_input,
        )
        dump(identity, vector_input)
        dump(
            vector_manifest,
            {
                "inputs": vector_input,
                "vectors_sha256": digest(vectors),
                "execution": execution,
            },
        )
    else:
        execution = vector_state.get("execution", {})
    chunks = json.loads((stage / "table-chunks.json").read_text(encoding="utf-8"))
    matrix = np.load(vectors, mmap_mode="r", allow_pickle=False)
    if (
        matrix.shape != (len(pages) + len(chunks), DIMENSIONS)
        or matrix.dtype != np.float32
        or not np.isfinite(matrix).all()
        or not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=2e-3)
    ):
        raise ValueError("Vector matrix failed validation")
    del matrix
    store = json.loads((stage / "store.json").read_text(encoding="utf-8"))
    records_manifest = json.loads((stage / "records.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "dataset": "advanced",
        "pipeline_sha256": stage_codes["overall"],
        "pipeline_stages": stage_codes,
        "embedding": vector_input["embedding"],
        "reranker": {"model_id": RERANKER_ID, "revision": RERANKER_REVISION},
        "counts": {
            "pages": len(pages),
            "table_documents": len(chunks),
            "physical_tables": store["tables"],
        },
        "files": {
            name: digest(stage / name)
            for name in (
                "pages.json",
                "vectors.npy",
                "store.json",
                "catalog.json",
                "tables.sqlite3",
                "table-chunks.json",
                "records.json",
                "records.sqlite3",
            )
        },
        "ingestion": {
            "curation": {
                "documents": curation_report["documents"],
                "pages": curation_report["pages"],
                "records": curation_report["records"],
                "covered_blocks": curation_report["covered_blocks"],
                "table_rows": curation_report["table_rows"],
                "source_coverage": curation_report["quality_gate"]["source_coverage"],
            },
            "table_store": {
                "tables": store["tables"],
                "raw_rows_roundtrip_verified": store["raw_rows_roundtrip_verified"],
            },
            "records": records_manifest["counts"],
            "family_tables": store["family_tables"],
            "vector_build": execution,
        },
        "inputs": {
            "curation_manifest_sha256": curation_manifest_sha256,
            "page_source_sha256": pages_hash,
            "table_chunks_sha256": digest(stage / "table-chunks.json"),
            "source_pdfs_sha256": source_fingerprint,
        },
    }
    dump(stage / "corpus.json", manifest)
    from app.rag.corpus import load_corpus
    from app.rag.records import RecordIndex

    load_corpus(stage)
    RecordIndex(stage).close()
    check_database(stage / "tables.sqlite3")
    check_database(stage / "records.sqlite3")
    names = BUNDLE_FILES
    if publish_state_path.exists():
        current_state = json.loads(publish_state_path.read_text(encoding="utf-8"))
        if current_state != publication_identity:
            raise ValueError(
                "Refusing to continue a publication with a different input or pipeline fingerprint"
            )
    else:
        _atomic_dump(publish_state_path, publication_identity)
    with tempfile.TemporaryDirectory(dir=output, prefix=".publishing-") as temporary:
        temp = Path(temporary)
        for name in names:
            shutil.copyfile(stage / name, temp / name)
        for name in names[:-1]:
            (temp / name).replace(output / name)
        (temp / "corpus.json").replace(output / "corpus.json")
    check_bundle(output)
    publish_state_path.unlink(missing_ok=True)
    return output
