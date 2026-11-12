"""Read-only access to the Advanced shared record and table index."""

import json
import re
import sqlite3
from contextlib import closing
from hashlib import file_digest
from pathlib import Path
from time import perf_counter
from unicodedata import normalize
from urllib.parse import quote

from app.rag.keyword_retriever import tokenize_for_bm25

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
_FIELD_SET = frozenset(FIELDS)
_ROLE_SET = frozenset(ROLES)
_CODE = re.compile(
    r"(?<![A-Z0-9_])([A-Z][A-Z0-9]*(?:\s*[-‐‑–—−－]\s*[A-Z0-9]+)+)(?![A-Z0-9_])",
    re.IGNORECASE,
)
_DASH = re.compile(r"\s*[-‐‑–—−－]\s*")
_SPEC_FORMS = {
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
_SPEC_LOOKUP = {
    form: canonical
    for canonical, forms in _SPEC_FORMS.items()
    for form in forms
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def norm(value: str) -> str:
    """Normalize values using the shared-record index convention."""
    return " ".join(normalize("NFKC", value).casefold().split())


def codes(text: str) -> tuple[str, ...]:
    """Extract complete, explicitly hyphenated identifiers without guessing."""
    normalized = normalize("NFKC", text)
    return tuple(
        dict.fromkeys(_DASH.sub("-", match[1]).upper() for match in _CODE.finditer(normalized))
    )


def canonical_spec(value: str) -> str:
    compact = "".join(norm(value).split())
    return _SPEC_LOOKUP.get(compact, norm(value))


def grounded(request, question: str, observations) -> None:
    """Require lookup constraints to appear in the question or prior evidence."""
    text = norm(question + " " + json.dumps(observations, ensure_ascii=False))
    compact = "".join(text.split())
    allowed_codes = set(codes(text))
    excluded_codes = {
        code
        for clause in re.split(r"[,;\n]", question)
        for code in codes(clause)
        if re.search(
            re.escape(code) + r"\s*(?:은|는|을|를)?\s*(?:제외|말고|빼고|아닌)",
            clause,
            re.IGNORECASE,
        )
    }
    for target in request.targets:
        if not target.identifiers and not target.constraints:
            raise ValueError("A target needs an identifier or a typed constraint")
        for identifier in target.identifiers:
            if identifier.upper() in excluded_codes:
                raise ValueError("Identifier explicitly excluded by the question")
            if codes(identifier) != (identifier.upper(),) or identifier.upper() not in allowed_codes:
                raise ValueError("Identifier not grounded in question or observed sources")
        for constraint in target.constraints:
            value = norm(constraint.value)
            if constraint.field == "spec":
                forms = _SPEC_FORMS.get(canonical_spec(value), ("".join(value.split()),))
                if not any(form in compact for form in forms):
                    raise ValueError("Equipment class not grounded")
            elif value not in text:
                normalized_dates = re.sub(
                    r"(20\d{2})[./년-]\s*(\d{1,2})[./월-]\s*(\d{1,2})일?",
                    lambda match: (
                        f"{int(match[1]):04d}-{int(match[2]):02d}-"
                        f"{int(match[3]):02d}"
                    ),
                    text,
                )
                if value not in normalized_dates:
                    raise ValueError("Constraint value not grounded")


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _is_non_negative_int(value) -> bool:
    return type(value) is int and value >= 0


def _database_uri(path: Path) -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"


class RecordIndex:
    """Validated read-only identity, source-role, date, and table-catalog index."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "records.sqlite3"
        self.manifest_path = self.directory / "records.json"
        self._closed = False
        self.manifest = _read_json(self.manifest_path)
        self._validate_manifest()

    def _validate_manifest(self) -> None:
        manifest = self.manifest
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 1
            or type(manifest.get("version")) is not int
            or manifest.get("version") != 1
        ):
            raise ValueError("Unsupported records snapshot schema")
        if _sha256(self.path) != manifest.get("database_sha256"):
            raise ValueError("Records database fingerprint mismatch")
        with closing(sqlite3.connect(_database_uri(self.path), uri=True)) as db:
            indexes = db.execute("PRAGMA index_list(keys)").fetchall()
            indexed_columns = []
            for index in indexes:
                index_name = index[1].replace('"', '""')
                columns = [
                    row[2]
                    for row in db.execute(f'PRAGMA index_info("{index_name}")')
                ]
                indexed_columns.append(columns)
        if not any(columns and columns[0] == "entity" for columns in indexed_columns):
            raise ValueError("Records index is missing its entity lookup index")
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict):
            raise ValueError("Records snapshot is missing input fingerprints")
        for key, filename in (("pages_sha256", "pages.json"), ("catalog_sha256", "catalog.json")):
            expected = inputs.get(key)
            if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                raise ValueError(f"Records input fingerprint is invalid: {key}")
            if _sha256(self.directory / filename) != expected:
                raise ValueError(f"Records index does not match {filename}")

        counts = manifest.get("counts")
        if not isinstance(counts, dict) or any(
            not _is_non_negative_int(counts.get(name))
            for name in ("entities", "keys", "attributes", "sources", "tables")
        ):
            raise ValueError("Records snapshot counts are invalid")

        families = manifest.get("families")
        if not isinstance(families, dict) or any(
            family not in families
            or not isinstance(families[family], dict)
            or any(
                not _is_non_negative_int(families[family].get(name))
                for name in ("entities", "sources")
            )
            for family in ("HG", "HA", "IS")
        ):
            raise ValueError("Records family counts are invalid")

        self._validate_source_references()

    def _validate_source_references(self) -> None:
        """Fail closed when record links no longer point to the published pages/tables."""
        pages = _read_json(self.directory / "pages.json")
        if not isinstance(pages, list) or not pages:
            raise ValueError("Records snapshot requires original page entries")

        page_metadata = {}
        for page in pages:
            if not isinstance(page, dict):
                raise ValueError("Original page entry is invalid")
            file_name = page.get("file_name")
            number = page.get("page")
            book_id = page.get("book_id") or (
                Path(file_name).stem if isinstance(file_name, str) else ""
            )
            if (
                not isinstance(file_name, str)
                or not file_name
                or not isinstance(book_id, str)
                or not book_id
                or type(number) is not int
                or number < 1
            ):
                raise ValueError("Original page source metadata is invalid")
            source = f"{book_id}:p{number:04d}"
            if source in page_metadata:
                raise ValueError("Original page sources are not unique")
            page_metadata[source] = (file_name, book_id, number)

        catalog = _read_json(self.directory / "catalog.json")
        if not isinstance(catalog, list):
            raise ValueError("Physical table catalog is invalid")
        tables_by_id = {}
        for table in catalog:
            if not isinstance(table, dict):
                raise ValueError("Physical table catalog entry is invalid")
            table_id = table.get("table_id")
            book_id = table.get("book_id")
            file_name = table.get("file_name")
            number = table.get("page")
            source = table.get("source_id")
            if (
                not isinstance(table_id, str)
                or not table_id
                or not isinstance(book_id, str)
                or not isinstance(file_name, str)
                or type(number) is not int
                or number < 1
                or source != f"{book_id}:p{number:04d}"
                or page_metadata.get(source) != (file_name, book_id, number)
                or table_id in tables_by_id
            ):
                raise ValueError("Physical table catalog source is invalid")
            tables_by_id[table_id] = table

        with closing(sqlite3.connect(_database_uri(self.path), uri=True)) as db:
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Records database has a foreign-key violation")

            for (source,) in db.execute("SELECT source FROM sources"):
                if source not in page_metadata:
                    raise ValueError("Record source does not refer to an original page")

            for table_name in ("keys", "attributes"):
                linked = db.execute(
                    f"SELECT 1 FROM {table_name} child "
                    "LEFT JOIN sources parent "
                    "ON parent.entity=child.entity AND parent.source=child.source "
                    "WHERE parent.entity IS NULL LIMIT 1"
                ).fetchone()
                if linked is not None:
                    raise ValueError(
                        f"Record {table_name} source is not linked to its entity"
                    )

            table_rows = db.execute("SELECT id,source,metadata FROM tables").fetchall()
            if len(table_rows) != len(tables_by_id):
                raise ValueError("Record table index does not match the catalog")
            for row in table_rows:
                table = tables_by_id.get(row[0])
                try:
                    metadata = json.loads(row[2])
                except (TypeError, json.JSONDecodeError) as error:
                    raise ValueError(
                        "Record table metadata does not match the catalog"
                    ) from error
                if table is None or row[1] != table["source_id"] or metadata != table:
                    raise ValueError("Record table index does not match the catalog")

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Record index is closed")
        connection = sqlite3.connect(_database_uri(self.path), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def lookup(self, targets, *, roles=()):
        """Resolve each target independently. Identifier and field filters are ANDed."""
        if not isinstance(targets, list) or not targets or len(targets) > 4:
            raise ValueError("Provide between one and four record targets")
        if any(role not in _ROLE_SET for role in roles):
            raise ValueError("Unsupported record role")

        started = perf_counter()
        results = []
        with closing(self._connect()) as db:
            for target in targets:
                identifiers = target.get("identifiers", [])
                constraints = target.get("constraints", [])
                target_roles = target.get("roles", roles)
                if not identifiers and not constraints:
                    raise ValueError("A target needs an identifier or a typed constraint")
                if any(role not in _ROLE_SET for role in target_roles):
                    raise ValueError("Unsupported record role")

                matches = []
                for identifier in identifiers:
                    values = codes(identifier)
                    if len(values) != 1 or values[0] != _DASH.sub("-", normalize("NFKC", identifier)).upper():
                        raise ValueError("Use one complete identifier per filter")
                    matches.append(
                        {
                            row[0]
                            for row in db.execute(
                                "SELECT DISTINCT entity FROM keys WHERE value=?",
                                (values[0],),
                            )
                        }
                    )

                for constraint in constraints:
                    field = constraint.get("field")
                    raw_value = constraint.get("value")
                    if field not in _FIELD_SET or not isinstance(raw_value, str) or not raw_value.strip():
                        raise ValueError("Unsupported typed constraint")
                    value = canonical_spec(raw_value) if field == "spec" else norm(raw_value)
                    matches.append(
                        {
                            row[0]
                            for row in db.execute(
                                "SELECT DISTINCT entity FROM attributes "
                                "WHERE field=? AND value=?",
                                (field, value),
                            )
                        }
                    )

                candidates = set.intersection(*matches)
                status = (
                    "matched" if len(candidates) == 1 else
                    "ambiguous" if candidates else
                    "conflict" if len(matches) > 1 and all(matches) else
                    "not_found"
                )
                entities = []
                for entity_id in sorted(candidates)[:8]:
                    row = db.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
                    if row is None:
                        raise ValueError("Record index has a key without an entity")
                    entity = dict(row)
                    source_rows = [dict(r) for r in db.execute(
                        "SELECT source,role,ordinal FROM sources WHERE entity=? ORDER BY ordinal,source",
                        (entity_id,),
                    )]
                    if target_roles:
                        source_rows = [source for source in source_rows if source["role"] in target_roles]
                    entity["sources"] = source_rows
                    entity["attributes"] = [dict(r) for r in db.execute(
                        "SELECT field,value,source FROM attributes WHERE entity=? ORDER BY field,value,source",
                        (entity_id,),
                    )]
                    entity["identifiers"] = [dict(r) for r in db.execute(
                        "SELECT DISTINCT value,kind,relation FROM keys WHERE entity=? ORDER BY kind,value",
                        (entity_id,),
                    )]
                    entities.append(entity)
                results.append({
                    "status": status,
                    "candidate_count": len(candidates),
                    "truncated": len(candidates) > 8,
                    "entities": entities,
                })
        return {"targets": results, "seconds": perf_counter() - started}

    def discover(self, query: str, limit: int = 6) -> list[str]:
        if not isinstance(limit, int) or limit < 1 or limit > 20:
            raise ValueError("Table discovery limit must be between 1 and 20")
        terms = list(dict.fromkeys(tokenize_for_bm25(query)))[:80]
        if not terms:
            return []
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with closing(self._connect()) as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT table_id FROM table_terms WHERE table_terms MATCH ? "
                    "ORDER BY bm25(table_terms), table_id LIMIT ?",
                    (expression, limit),
                )
            ]

    def close(self) -> None:
        self._closed = True
