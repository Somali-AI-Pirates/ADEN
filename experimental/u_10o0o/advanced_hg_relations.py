"""Build and query a source-grounded HG code-to-case SQLite overlay."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Self
from unicodedata import normalize

from app.ingestion.curation import read_rows
from app.ingestion.layout import compact, identifiers
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    PROFILE,
    PROFILE_ROLES,
    load_cases,
    load_profiles,
    profile_attributes,
)
from experimental.u_10o0o.advanced_snapshots import (
    ARTIFACTS,
    HG_SNAPSHOT,
    HGStore,
    _page_family,
    _input_identity,
    _page_snapshot_sha,
    _read_pages_and_sources,
    _sha_file,
)
from experimental.u_10o0o.advanced_structured_store import (
    DATE_FACTS,
    STRUCTURED_SNAPSHOT,
    StructuredStore,
)

HG_RELATIONS_SNAPSHOT = ARTIFACTS / "advanced-snapshots/hg-code-relations-v1"
SNAPSHOT_TYPE = "advanced-hg-code-relations-sqlite"
SCHEMA_VERSION = 1
CODE_TYPES = {
    "AP": "approval_id",
    "CT": "contract_id",
    "EV": "evidence_id",
    "HL": "equipment_id",
    "INV": "invoice_id",
    "RCPT": "receipt_id",
    "HS": "source_bundle",
}
EFFECTIVE_DATE_FACT = "recorded_amendment_effective_date"
_EXACT_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_HG_CASE_CODE = re.compile(r"^HG20\d{2}-\d+$")
_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE case_pages (
    case_id TEXT NOT NULL,
    role TEXT NOT NULL,
    role_order INTEGER NOT NULL CHECK (role_order BETWEEN 0 AND 4),
    source_id TEXT NOT NULL UNIQUE,
    book_id TEXT NOT NULL,
    page INTEGER NOT NULL CHECK (page > 0),
    PRIMARY KEY (case_id, role)
) STRICT;
CREATE TABLE code_relations (
    code_type TEXT NOT NULL,
    code TEXT NOT NULL,
    relation_kind TEXT NOT NULL CHECK (relation_kind IN ('exact', 'class_group', 'source_group')),
    case_id TEXT NOT NULL,
    role TEXT NOT NULL,
    source_id TEXT NOT NULL,
    related_code_type TEXT,
    related_code TEXT,
    PRIMARY KEY (code_type, code, relation_kind, case_id, role, source_id),
    FOREIGN KEY (case_id, role) REFERENCES case_pages(case_id, role),
    CHECK ((related_code_type IS NULL) = (related_code IS NULL))
) STRICT;
CREATE INDEX code_relations_exact ON code_relations(code_type, code, case_id, role);
CREATE INDEX code_relations_related ON code_relations(related_code_type, related_code, code_type, code);
CREATE INDEX case_pages_source ON case_pages(source_id);
PRAGMA user_version = 1;
"""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _normalized_code(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("코드 조회 값은 문자열이어야 합니다.")
    code = normalize("NFKC", value).strip().upper()
    if not code or not _EXACT_CODE.fullmatch(code):
        raise ValueError("코드는 공백 없는 전체 코드 값이어야 합니다.")
    return code


def _code_type(code: str) -> tuple[str, str]:
    """Return type and relation kind; HG case IDs retain their full prefix."""
    if _HG_CASE_CODE.fullmatch(code):
        return "reservation_id", "exact"
    prefix = code.partition("-")[0]
    code_type = CODE_TYPES.get(prefix)
    if code_type is None:
        raise ValueError("지원하지 않는 HG 코드 유형입니다.")
    relation_kind = "source_group" if code_type == "source_bundle" else "exact"
    return code_type, relation_kind


@dataclass(frozen=True, slots=True)
class CodeRelation:
    code_type: str
    code: str
    relation_kind: str
    case_id: str
    role: str
    source_id: str
    book_id: str
    page: int
    related_code_type: str | None = None
    related_code: str | None = None


@dataclass(frozen=True, slots=True)
class PageRef:
    case_id: str
    role: str
    source_id: str
    book_id: str
    page: int


def _snapshot_inputs(structured: StructuredStore, pages, sources) -> dict[str, Any]:
    base = _input_identity(pages, sources)
    return {
        "structured_snapshot_id": structured.provenance["snapshot_id"],
        "structured_manifest_sha256": structured.provenance["manifest_sha256"],
        "structured_database_sha256": structured.provenance["database_sha256"],
        "page_snapshot_sha256": base["page_snapshot_sha256"],
        "curation_manifest_sha256": base["curation_manifest_sha256"],
        "curated_sources_sha256": base["curated_sources_sha256"],
        "case_sources_sha256": _sha_file(CASE_INDEX / "case_sources.jsonl"),
        "hg_snapshot_manifest_sha256": _sha_file(HG_SNAPSHOT / "manifest.json"),
        "code_extraction_version": 1,
    }


def _manifest_valid(root: Path, inputs: Mapping[str, Any]) -> bool:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileExistsError(f"스냅샷 manifest가 없습니다: {root}")
    manifest = json.loads(manifest_path.read_bytes())
    outputs = manifest.get("outputs", {})
    if manifest.get("inputs") != inputs:
        raise FileExistsError(
            "기존 HG 코드 스냅샷의 입력이 달라 기존 파일을 보존합니다."
        )
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("snapshot_type") != SNAPSHOT_TYPE
        or set(outputs) != {"advanced.sqlite3"}
        or manifest.get("snapshot_id")
        != _sha_bytes(_json_bytes({"inputs": inputs, "outputs": outputs}))
    ):
        raise ValueError("HG 코드 스냅샷 manifest가 유효하지 않습니다.")
    database = root / "advanced.sqlite3"
    if not database.is_file() or _sha_file(database) != outputs["advanced.sqlite3"]:
        raise ValueError("HG 코드 스냅샷 파일 지문이 다릅니다.")
    return True


def _profile_class_links(cases, profiles) -> list[tuple[str, str, str, str, str]]:
    """Return verified (class, equipment, case, role, source) co-occurrences."""
    wanted = {}
    page_by_source = {}
    for case_id, pages in cases.items():
        for role in PROFILE_ROLES:
            page = pages[ROLES.index(role)]
            book_id = Path(page["file_name"]).stem
            source_id = f"advanced:{book_id}:p{page['page']}"
            if source_id in wanted:
                raise ValueError("HG 프로필 출처 페이지가 중복됩니다.")
            wanted[source_id] = (case_id, role)
            page_by_source[source_id] = page

    verified_profiles = load_profiles(CURATED, cases)
    if verified_profiles != profiles:
        raise ValueError("HG 프로필 결과가 재검증 결과와 다릅니다.")

    found = set()
    result = []
    for path in sorted((CURATED / "records").glob("*.jsonl")):
        for row in read_rows(path):
            source_id = row.get("source_id")
            if source_id not in wanted or row.get("kind") != "case_context":
                continue
            case_id, role = wanted[source_id]
            if (
                source_id in found
                or row.get("review_status") != "parsed"
                or row.get("case_ids") != [case_id]
                or row.get("fields", {}).get("기록 종류") != role
                or compact(row["text"])
                not in compact(page_by_source[source_id]["text"])
            ):
                raise ValueError("HG 프로필 원문과 출처 페이지가 맞지 않습니다.")
            if profile_attributes(row["text"], row["fields"]) != profiles[case_id]:
                raise ValueError("HG 프로필 값의 독립 재검증이 실패했습니다.")
            matches = list(PROFILE.finditer(row["text"]))
            if len(matches) != 1:
                raise ValueError(
                    "HG 원문에서 장비 코드·규격의 단일 관계를 확인할 수 없습니다."
                )
            match = matches[0]
            equipment = _normalized_code(match.group("equipment"))
            code_ids = identifiers(row["text"])
            if equipment not in code_ids or not equipment.startswith("HL-"):
                raise ValueError("검증된 HG 장비 코드가 원문 코드와 다릅니다.")
            equipment_class = normalize("NFKC", match.group("spec")).strip().upper()
            if not _EXACT_CODE.fullmatch(equipment_class):
                raise ValueError("HG 장비 규격 코드가 단일 값이 아닙니다.")
            found.add(source_id)
            result.append((equipment_class, equipment, case_id, role, source_id))
    if found != set(wanted):
        raise ValueError("HG 장비 규격의 검증된 원문 관계가 일부 누락되었습니다.")
    return result


def build_hg_relations(
    output_root: Path = HG_RELATIONS_SNAPSHOT,
    *,
    structured_snapshot: Path = STRUCTURED_SNAPSHOT,
) -> Path:
    """Build a provenance-bound overlay; never copy page text into the output."""
    pages, source_by_id = _read_pages_and_sources()
    output_root = Path(output_root)
    with StructuredStore(structured_snapshot) as structured:
        inputs = _snapshot_inputs(structured, pages, source_by_id)
        if output_root.exists():
            _manifest_valid(output_root, inputs)
            return output_root

        cases = load_cases(
            CASE_INDEX,
            pages,
            curation_manifest_sha256=_sha_file(CURATED / "manifest.json"),
        )
        profiles = load_profiles(CURATED, cases)
        profile_class_links = _profile_class_links(cases, profiles)

        case_pages: list[tuple[str, str, int, str, str, int]] = []
        page_by_source = {}
        for case_id, linked_pages in cases.items():
            if len(linked_pages) != len(ROLES):
                raise ValueError("HG 사건의 역할 페이지 수가 다릅니다.")
            for role_order, (role, page) in enumerate(
                zip(ROLES, linked_pages, strict=True)
            ):
                book_id = Path(page["file_name"]).stem
                source_id = f"advanced:{book_id}:p{page['page']}"
                source = source_by_id.get(source_id)
                if source is None or _page_family(source["heading"]) != "HG":
                    raise ValueError("HG 역할 페이지의 원본 manifest 연결이 다릅니다.")
                case_pages.append(
                    (case_id, role, role_order, source_id, book_id, page["page"])
                )
                page_by_source[source_id] = (case_id, role, book_id, page["page"], page)

        if len(case_pages) != 2_730 * len(ROLES):
            raise ValueError("기대하는 HG 전체 역할 페이지를 확인하지 못했습니다.")

        relation_rows = set()
        for source_id, (
            case_id,
            role,
            book_id,
            page_number,
            page,
        ) in page_by_source.items():
            for code in identifiers(page["text"]):
                try:
                    code_type, relation_kind = _code_type(code)
                except ValueError:
                    continue
                relation_rows.add(
                    (
                        code_type,
                        code,
                        relation_kind,
                        case_id,
                        role,
                        source_id,
                        None,
                        None,
                    )
                )

        # The class-to-full-ID relationship is recorded only on validated source
        # rows where the same profile explicitly names both values.
        for equipment_class, equipment, case_id, role, source_id in profile_class_links:
            for code_type, code, related_type, related_code in (
                ("equipment_class", equipment_class, "equipment_id", equipment),
                ("equipment_id", equipment, "equipment_class", equipment_class),
            ):
                relation_rows.add(
                    (
                        code_type,
                        code,
                        "class_group",
                        case_id,
                        role,
                        source_id,
                        related_type,
                        related_code,
                    )
                )

        if not relation_rows:
            raise ValueError("HG 코드 관계가 비어 있습니다.")

        role_order = {role: index for index, role in enumerate(ROLES)}
        ordered_relations = sorted(
            relation_rows,
            key=lambda row: (
                row[0],
                row[1],
                row[3],
                role_order[row[4]],
                row[5],
                row[2],
            ),
        )
        output_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=output_root.parent, prefix=f".{output_root.name}-"
        ) as temporary:
            staging = Path(temporary)
            database = staging / "advanced.sqlite3"
            connection = sqlite3.connect(database)
            try:
                if sqlite3.sqlite_version_info < (3, 37, 0):
                    raise RuntimeError(
                        "HG 코드 스냅샷에는 SQLite 3.37 이상이 필요합니다."
                    )
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(_SCHEMA)
                connection.executemany(
                    "INSERT INTO case_pages VALUES (?, ?, ?, ?, ?, ?)", case_pages
                )
                connection.executemany(
                    "INSERT INTO code_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ordered_relations,
                )
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("HG 코드 스냅샷 integrity_check가 실패했습니다.")
                if list(connection.execute("PRAGMA foreign_key_check")):
                    raise ValueError("HG 코드 스냅샷 foreign_key_check가 실패했습니다.")
                counts = {
                    "case_pages": connection.execute(
                        "SELECT count(*) FROM case_pages"
                    ).fetchone()[0],
                    "code_relations": connection.execute(
                        "SELECT count(*) FROM code_relations"
                    ).fetchone()[0],
                    "code_types": dict(
                        connection.execute(
                            "SELECT code_type, count(DISTINCT code) FROM code_relations GROUP BY code_type"
                        ).fetchall()
                    ),
                    "relation_kinds": dict(
                        connection.execute(
                            "SELECT relation_kind, count(*) FROM code_relations GROUP BY relation_kind"
                        ).fetchall()
                    ),
                }
                connection.commit()
            finally:
                connection.close()
            outputs = {"advanced.sqlite3": _sha_file(database)}
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "snapshot_type": SNAPSHOT_TYPE,
                "snapshot_id": _sha_bytes(
                    _json_bytes({"inputs": inputs, "outputs": outputs})
                ),
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "inputs": inputs,
                "outputs": outputs,
                "counts": counts,
                "config": {
                    "code_prefix_types": CODE_TYPES,
                    "case_id_prefix": "HG20xx-",
                    "equipment_class_source": "parsed profile co-occurrence on exact source page",
                    "prefix_matching": False,
                    "page_text_persisted": False,
                },
            }
            (staging / "manifest.json").write_bytes(_json_bytes(manifest))
            try:
                staging.rename(output_root)
            except OSError:
                if not output_root.exists() or not _manifest_valid(output_root, inputs):
                    raise
    return output_root


class HGRelations:
    """Read the validated code overlay and the existing role-page source store."""

    def __init__(
        self,
        snapshot: Path = HG_RELATIONS_SNAPSHOT,
        *,
        structured_snapshot: Path = STRUCTURED_SNAPSHOT,
        pages: Sequence[dict[str, Any]] | None = None,
        hg_snapshot: Path = HG_SNAPSHOT,
    ):
        self.snapshot = Path(snapshot)
        self.database = self.snapshot / "advanced.sqlite3"
        self.structured_snapshot = Path(structured_snapshot)
        self.hg_snapshot = Path(hg_snapshot)
        if pages is None:
            pages, sources = _read_pages_and_sources()
        else:
            current_pages, sources = _read_pages_and_sources()
            if _page_snapshot_sha(pages) != _page_snapshot_sha(current_pages):
                raise ValueError("HG 코드 overlay와 현재 복원 페이지가 다릅니다.")
        self.pages = list(pages)
        self.sources = sources
        self._connection: sqlite3.Connection | None = None
        self._structured: StructuredStore | None = None
        self._hg_store: HGStore | None = None
        self._manifest: dict[str, Any] | None = None

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("HGRelations가 이미 열려 있습니다.")
        self._structured = StructuredStore(self.structured_snapshot)
        self._structured.__enter__()
        try:
            self._hg_store = HGStore(self.hg_snapshot, self.pages)
            self._hg_store.__enter__()
            expected_inputs = _snapshot_inputs(
                self._structured, self.pages, self.sources
            )
            manifest = json.loads((self.snapshot / "manifest.json").read_bytes())
            if (
                manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("snapshot_type") != SNAPSHOT_TYPE
                or manifest.get("inputs") != expected_inputs
                or manifest.get("snapshot_id")
                != _sha_bytes(
                    _json_bytes(
                        {
                            "inputs": expected_inputs,
                            "outputs": manifest.get("outputs", {}),
                        }
                    )
                )
                or set(manifest.get("outputs", {})) != {"advanced.sqlite3"}
                or _sha_file(self.database)
                != manifest.get("outputs", {}).get("advanced.sqlite3")
            ):
                raise ValueError("HG 코드 overlay manifest 또는 입력 지문이 다릅니다.")
            connection = sqlite3.connect(
                f"{self.database.resolve().as_uri()}?mode=ro", uri=True
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            if (
                connection.execute("PRAGMA user_version").fetchone()[0]
                != SCHEMA_VERSION
                or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or list(connection.execute("PRAGMA foreign_key_check"))
            ):
                connection.close()
                raise ValueError("HG 코드 overlay 스키마 또는 무결성이 다릅니다.")
            self._connection = connection
            self._manifest = manifest
            return self
        except BaseException:
            if self._hg_store is not None:
                self._hg_store.__exit__(None, None, None)
                self._hg_store = None
            self._structured.__exit__(None, None, None)
            self._structured = None
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._hg_store is not None:
            self._hg_store.__exit__(exc_type, exc, traceback)
            self._hg_store = None
        if self._structured is not None:
            self._structured.__exit__(exc_type, exc, traceback)
            self._structured = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("조회하려면 HGRelations를 열어야 합니다.")
        return self._connection

    @property
    def provenance(self) -> dict[str, Any]:
        if self._manifest is None:
            raise RuntimeError("HGRelations를 열기 전에는 출처를 읽을 수 없습니다.")
        return {
            "snapshot_id": self._manifest["snapshot_id"],
            "inputs": self._manifest["inputs"],
            "database_sha256": self._manifest["outputs"]["advanced.sqlite3"],
            "counts": self._manifest["counts"],
        }

    def identify(self, raw: str) -> list[CodeRelation]:
        """Look up a full exact identifier, or a verified equipment-class group."""
        code = _normalized_code(raw)
        if _HG_CASE_CODE.fullmatch(code):
            code_type = "reservation_id"
        elif "-" not in code:
            code_type = "equipment_class"
        else:
            try:
                code_type, _ = _code_type(code)
            except ValueError:
                return []
        rows = (
            self._db()
            .execute(
                "SELECT links.code_type, links.code, links.relation_kind, links.case_id, "
                "links.role, links.source_id, pages.book_id, pages.page, "
                "links.related_code_type, links.related_code "
                "FROM code_relations AS links JOIN case_pages AS pages "
                "ON pages.case_id = links.case_id AND pages.role = links.role "
                "WHERE links.code_type = ? AND links.code = ? "
                "ORDER BY pages.role_order, pages.book_id, pages.page, links.case_id",
                (code_type, code),
            )
            .fetchall()
        )
        return [
            CodeRelation(
                code_type=row["code_type"],
                code=row["code"],
                relation_kind=row["relation_kind"],
                case_id=row["case_id"],
                role=row["role"],
                source_id=row["source_id"],
                book_id=row["book_id"],
                page=row["page"],
                related_code_type=row["related_code_type"],
                related_code=row["related_code"],
            )
            for row in rows
        ]

    def code_candidates(self, raw: str, code_type: str | None = None) -> set[str]:
        code = _normalized_code(raw)
        if code_type is None:
            return {relation.case_id for relation in self.identify(code)}
        rows = self._db().execute(
            "SELECT DISTINCT case_id FROM code_relations "
            "WHERE code_type = ? AND code = ?",
            (code_type, code),
        )
        return {row[0] for row in rows}

    def identities_for(self, field: str, value: str) -> set[str]:
        """Use the structured store's exact identity index with one approved extra date."""
        if self._structured is None:
            raise RuntimeError("조회하려면 HGRelations를 열어야 합니다.")
        if field == "spec":
            code = normalize("NFKC", value).strip().upper()
            related = {
                row[0]
                for row in self._db().execute(
                    "SELECT DISTINCT case_id FROM code_relations "
                    "WHERE code_type = 'equipment_class' AND code = ?",
                    (code,),
                )
            }
            return related | self._structured.identities_for(field, value)
        if field != EFFECTIVE_DATE_FACT:
            return self._structured.identities_for(field, value)
        if field not in DATE_FACTS:
            raise ValueError("지원하지 않는 날짜 사실입니다.")
        rows = self._structured._execute(
            "SELECT DISTINCT case_id FROM role_facts "
            "WHERE family = 'HG' AND fact_code = ? AND state = 'reported' AND value_text = ?",
            (field, value),
        )
        return {row[0] for row in rows}

    def profile_candidates(self, filters: Mapping[str, str]) -> set[str]:
        if self._structured is None:
            raise RuntimeError("조회하려면 HGRelations를 열어야 합니다.")
        if not filters:
            raise ValueError("프로필 조회에는 하나 이상의 확인된 조건이 필요합니다.")
        candidates: set[str] | None = None
        for field, value in filters.items():
            if not isinstance(value, str) or not value.strip():
                return set()
            matched = self.identities_for(field, value)
            candidates = matched if candidates is None else candidates & matched
            if not candidates:
                return set()
        return candidates or set()

    def page_refs_for(
        self, case_id: str, roles: Sequence[str] = ROLES
    ) -> list[PageRef]:
        if self._hg_store is None:
            raise RuntimeError("조회하려면 HGRelations를 열어야 합니다.")
        wanted = tuple(roles)
        if len(wanted) != len(set(wanted)) or any(role not in ROLES for role in wanted):
            raise ValueError("요청 역할은 중복 없이 기존 HG 역할에서 골라야 합니다.")
        if not wanted:
            return []
        rows = (
            self._db()
            .execute(
                "SELECT case_id, role, source_id, book_id, page FROM case_pages "
                f"WHERE case_id = ? AND role IN ({', '.join('?' for _ in wanted)})",
                (case_id, *wanted),
            )
            .fetchall()
        )
        by_role = {row["role"]: row for row in rows}
        if len(by_role) != len(rows) or set(by_role) != set(wanted):
            return []
        return [
            PageRef(
                case_id=case_id,
                role=role,
                source_id=by_role[role]["source_id"],
                book_id=by_role[role]["book_id"],
                page=by_role[role]["page"],
            )
            for role in wanted
        ]

    def pages_for(
        self, case_id: str, roles: Sequence[str] = ROLES
    ) -> list[dict[str, Any]]:
        if self._hg_store is None:
            raise RuntimeError("조회하려면 HGRelations를 열어야 합니다.")
        refs = self.page_refs_for(case_id, roles)
        if not refs:
            return []
        pages = self._hg_store.pages_for(case_id, roles)
        if len(pages) != len(refs):
            raise ValueError("원본 HG 페이지와 코드 overlay의 링크 수가 다릅니다.")
        for page, reference in zip(pages, refs, strict=True):
            book_id = Path(page["file_name"]).stem
            if (book_id, page["page"]) != (reference.book_id, reference.page):
                raise ValueError("원본 HG 페이지와 코드 overlay의 링크가 다릅니다.")
        return pages
