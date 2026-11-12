"""Read-only original sections and verified table cells; no inferred policy edges."""

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from itertools import pairwise
from pathlib import Path

from experimental.u_10o0o.advanced_structured_curation import LAYOUT_CACHE
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.bm25 import tokenize_bigram_id_normalized
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    object_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.table_rag_unified.index import codes

DEFAULT = ROOT / "artifacts/u_10o0o/advanced-snapshots/workflow-20261112-v2"


def connect(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def build(directory, table_directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    tables = read_json(table_directory / "catalog.json")
    with closing(connect(table_directory / "tables.sqlite3")) as db:
        lineage = {r["row_id"]: dict(r) for r in db.execute("SELECT * FROM lineage")}
    by_id = {t["table_id"]: t for t in tables}
    path = directory / "evidence.sqlite3"
    with sqlite3.connect(path) as db, StructuredStore() as src:
        db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE cards(id TEXT PRIMARY KEY, source TEXT NOT NULL, family TEXT NOT NULL,
          heading TEXT NOT NULL, section TEXT NOT NULL, kind TEXT NOT NULL, table_id TEXT,
          line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, text TEXT NOT NULL, fields TEXT NOT NULL,
          scope_id TEXT NOT NULL);
        CREATE INDEX source_cards ON cards(source,line_start);
        CREATE INDEX section_cards ON cards(scope_id,line_start);
        CREATE TABLE code_references(card TEXT REFERENCES cards(id), code TEXT, PRIMARY KEY(card,code));
        CREATE TABLE section_links(origin TEXT REFERENCES cards(id), target TEXT REFERENCES cards(id),
          relation TEXT NOT NULL CHECK(relation='same_original_section'), PRIMARY KEY(origin,target));
        CREATE VIRTUAL TABLE policy_terms USING fts5(card UNINDEXED,tokens);
        """)
        documents = [dict(r) for r in src._execute("SELECT * FROM source_documents")]
        layouts, stamps = {}, []
        for doc in documents:
            f = LAYOUT_CACHE / f"{doc['book_id']}-{doc['file_sha256'][:16]}.txt"
            meta = read_json(f.with_suffix(".json"))
            if (
                file_sha(f) != meta["text_sha256"]
                or meta["identity"]["pdf_sha256"] != doc["file_sha256"]
            ):
                raise ValueError("Layout/PDF fingerprint changed")
            layouts[doc["book_id"]] = [
                p.splitlines() for p in f.read_text().split("\f")
            ]
            stamps.append(
                {
                    "book_id": doc["book_id"],
                    "pdf_sha256": doc["file_sha256"],
                    "layout_sha256": file_sha(f),
                }
            )
        records = {}
        for r in src._execute("""SELECT r.*,p.book_id,p.page,p.heading,p.page_family,f.field_name,f.raw_value
          FROM source_records r JOIN source_pages p USING(source_id) LEFT JOIN record_fields f USING(record_id)
          WHERE (p.page_family='IS' AND r.kind='narrative') OR r.kind='table_row'
          ORDER BY r.record_id,f.ordinal"""):
            item = records.setdefault(r["record_id"], {"row": dict(r), "fields": {}})
            if r["field_name"] is not None:
                item["fields"][r["field_name"]] = r["raw_value"]
        groups = defaultdict(list)
        for rid, item in records.items():
            r, fields = item["row"], item["fields"]
            if r["kind"] == "table_row" and rid not in lineage:
                continue
            lines = layouts[r["book_id"]][r["page"] - 1]
            if not 1 <= r["line_start"] <= r["line_end"] <= len(lines):
                raise ValueError("Source range invalid")
            tid = lineage[rid]["table_id"] if rid in lineage else None
            if tid and (by_id[tid]["book_id"], by_id[tid]["page"]) != (
                r["book_id"],
                r["page"],
            ):
                raise ValueError("Table/source identity differs")
            section = r["section"] or ""
            source = f"{r['book_id']}:p{r['page']:04d}"
            scope = object_sha([source, section])[:20]
            # Narrative is verbatim; cells retain ordered labels and raw values already round-trip checked by TableStore.
            text = (
                "\n".join(f"{k}: {v}" for k, v in fields.items())
                if tid
                else "\n".join(lines[r["line_start"] - 1 : r["line_end"]])
            )
            if not text.strip():
                continue
            card_id = "e_" + object_sha([rid, text])[:20]
            db.execute(
                "INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card_id,
                    source,
                    r["page_family"],
                    r["heading"],
                    section,
                    r["kind"],
                    tid,
                    r["line_start"],
                    r["line_end"],
                    text,
                    json.dumps(fields, ensure_ascii=False),
                    scope,
                ),
            )
            for code in codes(text):
                db.execute(
                    "INSERT OR IGNORE INTO code_references VALUES(?,?)", (card_id, code)
                )
            groups[scope].append((r["line_start"], card_id))
            if r["page_family"] == "IS":
                tokens = " ".join(
                    tokenize_bigram_id_normalized(
                        r["heading"] + " " + section + " " + text
                    )
                )
                db.execute("INSERT INTO policy_terms VALUES(?,?)", (card_id, tokens))
        for members in groups.values():
            ordered = sorted(members)
            for (_, a), (_, b) in pairwise(ordered):
                db.executemany(
                    "INSERT INTO section_links VALUES(?,?,?)",
                    [(a, b, "same_original_section"), (b, a, "same_original_section")],
                )
        db.commit()
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("cards", "section_links", "code_references")
        }
        manifest = {
            "counts": counts,
            "source_snapshot": src.provenance,
            "documents": stamps,
            "table_store_sha256": file_sha(table_directory / "store.json"),
            "builder_sha256": file_sha(__file__),
            "scope": "Original page+section only. No cross-case/topic/effective-date deduplication; references are not entity equivalence.",
        }
    manifest["database_sha256"] = file_sha(path)
    write_json(directory / "manifest.json", manifest)
    path.chmod(0o444)
    return counts


class Catalog:
    def __init__(self, directory=DEFAULT):
        self.directory = Path(directory)
        self.path = self.directory / "evidence.sqlite3"
        self.manifest = read_json(self.directory / "manifest.json")
        if file_sha(self.path) != self.manifest["database_sha256"]:
            raise ValueError("Evidence snapshot changed")

    def page_cards(self, source, query, *, table_id=None, limit=5):
        terms = set(tokenize_bigram_id_normalized(query))
        with closing(connect(self.path)) as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM cards WHERE source=? ORDER BY line_start,id",
                    (source,),
                )
            ]

        def rank(r):
            overlap = len(terms & set(tokenize_bigram_id_normalized(r["text"])))
            return (
                -(overlap + (3 if table_id and table_id == r["table_id"] else 0)),
                r["line_start"],
                r["id"],
            )

        ranked = sorted(rows, key=rank)
        if not ranked:
            return []
        # Keep the matched evidence and adjacent original section; do not infer applicability across pages.
        chosen = ranked[: max(1, limit - 2)]
        scopes = {r["scope_id"] for r in chosen}
        for r in rows:
            if r["scope_id"] in scopes and r not in chosen:
                chosen.append(r)
                if len(chosen) >= limit:
                    break
        return chosen[:limit]

    def discover(self, query, limit=4):
        terms = list(dict.fromkeys(tokenize_bigram_id_normalized(query)))[:80]
        if not terms:
            return []
        expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with closing(connect(self.path)) as db:
            rows = db.execute(
                """SELECT c.*,bm25(policy_terms) score FROM policy_terms
              JOIN cards c ON c.id=policy_terms.card WHERE policy_terms MATCH ? ORDER BY score,c.id LIMIT 40""",
                (expr,),
            ).fetchall()
        result, seen = [], set()
        for row in rows:
            if row["source"] not in seen:
                result.append(dict(row))
                seen.add(row["source"])
                if len(result) == limit:
                    break
        return result
