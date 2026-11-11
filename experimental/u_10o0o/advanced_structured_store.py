"""Build and query the local Advanced structured-record SQLite snapshot."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Self
from unicodedata import normalize

from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_hg_lookup import load_cases, load_profiles
from experimental.u_10o0o.advanced_snapshots import (
    HG_SNAPSHOT,
    HGStore,
    _read_pages_and_sources,
)
from experimental.u_10o0o.prepare import PROJECT_ROOT

ARTIFACTS = PROJECT_ROOT / "artifacts/u_10o0o"
CASE_INDEX = ARTIFACTS / "advanced-curation/hg-case-sources-20261109"
FACTS = ARTIFACTS / "advanced-curation/hg-role-facts-20261109"
FORMAT_INDEX = ARTIFACTS / "advanced-curation/format-index-20261109"
STRUCTURED_SNAPSHOT = ARTIFACTS / "advanced-snapshots/structured-v1"
SCHEMA_VERSION = 1
SNAPSHOT_TYPE = "advanced-structured-records-sqlite"

HA_ROLES = (
    "계약 조건 확인",
    "사건·증빙 접수 대조",
    "비용별 정산 확인",
)
HANDBOOK_SIGNATURES = {
    frozenset(
        ("기존·요청 근거", "대상", "상태·기록일", "요청·회신 내용", "처리 기록")
    ): "처리 기록",
    frozenset(
        ("기록번호", "대상", "범위·상태", "업무·의제", "확인·회신 내용")
    ): "기록번호",
}
DATE_FACTS = frozenset(
    {
        "actual_discharge_date",
        "actual_gate_out_date",
        "actual_designated_return_date",
        "scheduled_return_date",
        "system_entry_date",
        "recorded_amendment_effective_date",
    }
)
DATETIME_FACTS = frozenset({"comparison_recorded_at"})
MONEY_FACTS = frozenset(
    {
        "billed_amount",
        "receipt_storage_charge",
        "receipt_late_return_charge",
        "receipt_total",
        "received_amount",
        "balance_amount",
        "reported_cap_amount",
        "reported_reduction_amount",
        "refund_review_amount",
        "amendment_storage_rate",
    }
)
IDENTIFIER_FACTS = {
    "contract_id": "comparison_contract_id",
    "equipment_id": "comparison_equipment",
    "record_id": "comparison_record_id",
}
IDENTITY_FIELDS = frozenset(
    {
        "case_id",
        "shipper",
        "cargo",
        "spec",
        "contract_id",
        "equipment_id",
        "record_id",
        *DATE_FACTS,
    }
)
PROFILE_COLUMNS = {
    "shipper": "shipper_key",
    "cargo": "cargo_key",
    "spec": "spec_key",
    "contract_id": "contract_key",
}


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())


def _verify_curation(curated: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = curated / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("identity", {}).get("dataset") != "advanced":
        raise ValueError("Advanced 정제 스냅샷이 필요합니다.")
    output_hashes = manifest.get("outputs")
    if not isinstance(output_hashes, dict) or not output_hashes:
        raise ValueError("정제 manifest에 출력 지문이 없습니다.")
    for relative, expected in output_hashes.items():
        path = curated / relative
        if not path.is_file() or _sha_file(path) != expected:
            raise ValueError(f"정제 출력 지문이 다릅니다: {relative}")
    return manifest, {
        "manifest_sha256": _sha_bytes(manifest_bytes),
        "sources_sha256": _sha_file(curated / "sources.jsonl"),
    }


def _validate_fact_artifacts(
    manifest_sha256: str, case_index: Path, facts: Path
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, tuple[str, str]]]:
    case_path = case_index / "case_sources.jsonl"
    case_summary_path = case_index / "summary.json"
    facts_path = facts / "role_facts.jsonl"
    summary_path = facts / "summary.json"
    case_summary = json.loads(case_summary_path.read_bytes())
    summary = json.loads(summary_path.read_bytes())
    case_sha = _sha_file(case_path)
    facts_sha = _sha_file(facts_path)
    if (
        case_summary.get("curation_manifest_sha256") != manifest_sha256
        or case_summary.get("case_sources_sha256") != case_sha
        or case_summary.get("indexed_cases") != 2730
        or case_summary.get("source_links") != 13_650
    ):
        raise ValueError("HG 사건 연결 인덱스의 출처나 건수가 다릅니다.")
    if (
        summary.get("curation_manifest_sha256") != manifest_sha256
        or summary.get("case_sources_sha256") != case_sha
        or summary.get("role_facts_sha256") != facts_sha
        or summary.get("cases") != 2730
        or summary.get("source_pages") != 13_650
    ):
        raise ValueError("HG 역할 사실의 출처 지문이나 건수가 다릅니다.")

    links: dict[str, tuple[str, str]] = {}
    by_case: dict[str, set[str]] = {}
    for row in read_rows(case_path):
        source_id = row["source_id"]
        case_id = row["case_id"]
        role = row["record_role"]
        if (
            source_id in links
            or row.get("match_rule") != "page_heading_case_id"
            or role not in ROLES
        ):
            raise ValueError("HG 사건 연결에 중복되거나 미승인된 역할이 있습니다.")
        links[source_id] = case_id, role
        by_case.setdefault(case_id, set()).add(role)
    if len(by_case) != 2730 or any(
        set(roles) != set(ROLES) for roles in by_case.values()
    ):
        raise ValueError("HG 사건별 다섯 역할이 완전하지 않습니다.")

    rows = list(read_rows(facts_path))
    if len(rows) != summary.get("normalized_facts"):
        raise ValueError("HG 역할 사실의 행 수가 요약과 다릅니다.")
    for row in rows:
        source = row.get("source", {})
        if (
            links.get(source.get("source_id"))
            != (row.get("case_id"), row.get("record_role"))
            or not source.get("record_id")
            or not isinstance(source.get("line_start"), int)
            or not isinstance(source.get("line_end"), int)
            or source["line_start"] < 1
            or source["line_end"] < source["line_start"]
        ):
            raise ValueError("HG 역할 사실의 사건·역할·줄 출처가 맞지 않습니다.")
    return (
        rows,
        {
            "case_sources_sha256": case_sha,
            "case_sources_summary_sha256": _sha_file(case_summary_path),
            "role_facts_sha256": facts_sha,
            "role_facts_summary_sha256": _sha_file(summary_path),
        },
        links,
    )


def _ha_case_links(sources: Mapping[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    by_case: dict[str, dict[str, str]] = {}
    for source_id, source in sources.items():
        if source.get("page_family") != "HA":
            continue
        heading = source.get("heading", "")
        ids = {value for value in identifiers(heading) if value.startswith("HA28-")}
        if len(ids) != 1 or " > " not in heading:
            raise ValueError("HA 쪽에서 단일 사건 ID와 역할을 확인할 수 없습니다.")
        suffix = heading.rsplit(" > ", 1)[-1]
        case_id = next(iter(ids))
        role = suffix.partition(" ")[2].strip()
        if role not in HA_ROLES:
            raise ValueError("HA 쪽에서 지원되는 역할을 확인할 수 없습니다.")
        if role in by_case.setdefault(case_id, {}):
            raise ValueError("HA 사건에 중복 역할 페이지가 있습니다.")
        by_case[case_id][role] = source_id
    if len(by_case) != 198 or any(
        set(roles) != set(HA_ROLES) for roles in by_case.values()
    ):
        raise ValueError("HA 198건의 세 역할 페이지 연결이 완전하지 않습니다.")
    return [
        (case_id, role, roles[role])
        for case_id, roles in sorted(by_case.items())
        for role in HA_ROLES
    ]


def _parse_fact_value(
    row: Mapping[str, Any],
) -> tuple[str, str | None, int | None, str]:
    value = row.get("value")
    state = row["state"]
    value_json = _json_value(value)
    if state != "reported":
        return "unverified", None, None, value_json
    if value is None:
        return "null", None, None, value_json
    if type(value) is int:
        return "integer", None, value, value_json
    if isinstance(value, str):
        fact = row["fact"]
        if fact in DATE_FACTS:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("보고된 HG 날짜 값이 ISO 날짜가 아닙니다.") from exc
            return "date", value, None, value_json
        if fact in DATETIME_FACTS:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("보고된 HG 시각 값이 ISO 형식이 아닙니다.") from exc
            if parsed.isoformat(timespec="minutes") != value:
                raise ValueError("보고된 HG 시각 값의 정밀도가 예상과 다릅니다.")
            return "datetime", value, None, value_json
        if fact in MONEY_FACTS and row.get("currency"):
            try:
                amount = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("보고된 HG 금액이 십진 값이 아닙니다.") from exc
            if not amount.is_finite():
                raise ValueError("보고된 HG 금액은 유한 값이어야 합니다.")
            return "decimal", format(amount, "f"), None, value_json
        return "text", value, None, value_json
    if isinstance(value, (dict, list)):
        return "json", None, None, value_json
    raise ValueError("HG 역할 사실에 지원되지 않는 값 형식이 있습니다.")


def _create_schema(connection: sqlite3.Connection) -> None:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise RuntimeError("구조화 스냅샷에는 SQLite 3.37 이상이 필요합니다.")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE source_documents (
            book_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL UNIQUE,
            file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
            page_count INTEGER NOT NULL CHECK (page_count > 0)
        ) STRICT;
        CREATE TABLE source_pages (
            source_id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL REFERENCES source_documents(book_id),
            page INTEGER NOT NULL CHECK (page > 0),
            page_family TEXT NOT NULL CHECK (
                page_family IN ('HG', 'HA', 'IS', 'other', 'unknown')
            ),
            heading TEXT NOT NULL,
            UNIQUE (book_id, page)
        ) STRICT;
        CREATE TABLE cases (
            family TEXT NOT NULL CHECK (family IN ('HG', 'HA')),
            case_id TEXT NOT NULL,
            PRIMARY KEY (family, case_id)
        ) STRICT;
        CREATE TABLE case_records (
            family TEXT NOT NULL,
            case_id TEXT NOT NULL,
            role TEXT NOT NULL,
            source_id TEXT NOT NULL UNIQUE REFERENCES source_pages(source_id),
            match_rule TEXT NOT NULL,
            PRIMARY KEY (family, case_id, role),
            FOREIGN KEY (family, case_id)
                REFERENCES cases(family, case_id)
        ) STRICT;
        CREATE TABLE source_records (
            record_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES source_pages(source_id),
            line_start INTEGER NOT NULL CHECK (line_start > 0),
            line_end INTEGER NOT NULL CHECK (line_end >= line_start),
            section TEXT,
            kind TEXT NOT NULL,
            parse_status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE record_fields (
            record_id TEXT NOT NULL REFERENCES source_records(record_id),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            field_name TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            PRIMARY KEY (record_id, ordinal),
            UNIQUE (record_id, field_name)
        ) STRICT;
        CREATE TABLE role_facts (
            record_id TEXT NOT NULL REFERENCES source_records(record_id),
            family TEXT NOT NULL CHECK (family = 'HG'),
            case_id TEXT NOT NULL,
            role TEXT NOT NULL,
            fact_code TEXT NOT NULL,
            cost_kind TEXT NOT NULL CHECK (
                cost_kind IN ('storage', 'late_return', 'none')
            ),
            column_label TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            state TEXT NOT NULL,
            value_type TEXT NOT NULL CHECK (
                value_type IN ('unverified', 'null', 'integer', 'decimal', 'date', 'datetime', 'text', 'json')
            ),
            value_text TEXT,
            value_int INTEGER,
            value_json TEXT NOT NULL,
            unit TEXT,
            currency TEXT,
            review_reason TEXT,
            source_id TEXT NOT NULL REFERENCES source_pages(source_id),
            PRIMARY KEY (record_id, fact_code, cost_kind),
            FOREIGN KEY (family, case_id, role)
                REFERENCES case_records(family, case_id, role),
            CHECK (
                (value_type = 'integer' AND value_int IS NOT NULL AND value_text IS NULL)
                OR (value_type IN ('decimal', 'date', 'datetime', 'text')
                    AND value_text IS NOT NULL AND value_int IS NULL)
                OR (value_type IN ('unverified', 'null', 'json')
                    AND value_text IS NULL AND value_int IS NULL)
            )
        ) STRICT;
        CREATE TABLE case_profiles (
            family TEXT NOT NULL CHECK (family = 'HG'),
            case_id TEXT NOT NULL,
            source_record_id TEXT NOT NULL REFERENCES source_records(record_id),
            shipper_key TEXT NOT NULL,
            cargo_key TEXT NOT NULL,
            spec_key TEXT NOT NULL,
            contract_key TEXT NOT NULL,
            PRIMARY KEY (family, case_id),
            UNIQUE (source_record_id),
            FOREIGN KEY (family, case_id) REFERENCES cases(family, case_id)
        ) STRICT;
        CREATE TABLE handbook_records (
            key_type TEXT NOT NULL CHECK (key_type IN ('기록번호', '처리 기록')),
            key_value TEXT NOT NULL,
            record_id TEXT NOT NULL REFERENCES source_records(record_id),
            PRIMARY KEY (key_type, key_value, record_id)
        ) STRICT;
        CREATE INDEX case_records_source ON case_records(source_id);
        CREATE INDEX source_records_source ON source_records(source_id, kind, parse_status);
        CREATE INDEX record_fields_lookup ON record_fields(field_name, raw_value, record_id);
        CREATE INDEX role_facts_lookup
            ON role_facts(family, case_id, fact_code, role, cost_kind, state);
        CREATE INDEX role_facts_value_lookup
            ON role_facts(fact_code, state, value_text, case_id);
        CREATE INDEX case_profiles_shipper ON case_profiles(shipper_key, case_id);
        CREATE INDEX case_profiles_cargo ON case_profiles(cargo_key, case_id);
        CREATE INDEX case_profiles_spec ON case_profiles(spec_key, case_id);
        CREATE INDEX case_profiles_contract ON case_profiles(contract_key, case_id);
        CREATE INDEX handbook_key_lookup ON handbook_records(key_type, key_value);
        PRAGMA user_version = 1;
        """
    )


def _load_format_index(
    format_index: Path, *, manifest_sha256: str, page_cache_sha256: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    ha_path = format_index / "ha_pages.jsonl"
    handbook_path = format_index / "handbook_rows.jsonl"
    if not ha_path.is_file() or not handbook_path.is_file():
        raise FileNotFoundError("HA·IS 양식 인덱스가 준비되지 않았습니다.")
    ha_rows = list(read_rows(ha_path))
    handbook_rows = list(read_rows(handbook_path))
    if len(ha_rows) != 594 or len(handbook_rows) != 11_073:
        raise ValueError("HA·IS 양식 인덱스 건수가 예상과 다릅니다.")
    if sum(row.get("key_column") == "기록번호" for row in handbook_rows) != 5_321:
        raise ValueError("IS 기록번호 인덱스 건수가 예상과 다릅니다.")
    if sum(row.get("key_column") == "처리 기록" for row in handbook_rows) != 5_752:
        raise ValueError("IS 처리 기록 인덱스 건수가 예상과 다릅니다.")
    fingerprints = {
        "ha_pages_sha256": _sha_file(ha_path),
        "handbook_rows_sha256": _sha_file(handbook_path),
    }
    config_path = (
        PROJECT_ROOT
        / "experimental/u_10o0o/records/advanced-curation/format-extension-20261109/config.json"
    )
    if config_path.is_file():
        config = json.loads(config_path.read_bytes())
        if (
            config.get("curation_manifest_sha256") != manifest_sha256
            or config.get("pages", {}).get("sha256") != page_cache_sha256
        ):
            raise ValueError(
                "HA·IS 양식 인덱스의 입력 지문이 현재 정제 자료와 다릅니다."
            )
        fingerprints["format_config_sha256"] = _sha_file(config_path)
    return ha_rows, handbook_rows, fingerprints
    raise FileNotFoundError("HA·IS 양식 인덱스의 검증 설정 파일이 없습니다.")


def _input_identity(
    curated_fingerprints: Mapping[str, str],
    fact_fingerprints: Mapping[str, str],
    format_fingerprints: Mapping[str, str],
    hg_provenance: Mapping[str, Any],
    pages_sha256: str,
) -> dict[str, Any]:
    return {
        "curation_manifest_sha256": curated_fingerprints["manifest_sha256"],
        "curated_sources_sha256": curated_fingerprints["sources_sha256"],
        **fact_fingerprints,
        **format_fingerprints,
        "page_cache_sha256": pages_sha256,
        "hg_snapshot_id": hg_provenance["snapshot_id"],
        "hg_snapshot_manifest_sha256": hg_provenance["manifest_sha256"],
        "hg_database_sha256": hg_provenance["database_sha256"],
        "source_page_count": 18_000,
    }


def _existing_snapshot(output_root: Path, inputs: Mapping[str, Any]) -> bool:
    if not output_root.exists():
        return False
    manifest_path = output_root / "manifest.json"
    database_path = output_root / "advanced.sqlite3"
    if not manifest_path.is_file() or not database_path.is_file():
        raise FileExistsError(f"완성되지 않은 출력 경로입니다: {output_root}")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("inputs") != inputs:
        raise FileExistsError(f"입력 지문이 다른 스냅샷을 보존합니다: {output_root}")
    outputs = manifest.get("outputs")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("snapshot_type") != SNAPSHOT_TYPE
        or not isinstance(outputs, dict)
        or set(outputs) != {"advanced.sqlite3"}
        or manifest.get("snapshot_id")
        != _sha_bytes(_json_bytes({"inputs": inputs, "outputs": outputs}))
    ):
        raise ValueError("기존 구조화 스냅샷 manifest가 유효하지 않습니다.")
    if _sha_file(database_path) != outputs["advanced.sqlite3"]:
        raise ValueError("기존 구조화 스냅샷 DB 지문이 다릅니다.")
    return True


def _append_many(connection: sqlite3.Connection, query: str, rows, *, size=1000):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            connection.executemany(query, batch)
            batch.clear()
    if batch:
        connection.executemany(query, batch)


def _record_fields(record: Mapping[str, Any]) -> list[tuple[str, int, str, str]]:
    fields = record.get("fields") or {}
    if not isinstance(fields, dict):
        if record.get("review_status") == "parsed":
            raise ValueError("파싱된 정제 행의 열 필드가 객체가 아닙니다.")
        return []
    rows = []
    for ordinal, (name, value) in enumerate(fields.items()):
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("정제된 열 이름과 값은 문자열이어야 합니다.")
        rows.append((record["record_id"], ordinal, name, value))
    return rows


def _profile_records(
    curated: Path,
    case_index: Path,
    pages: Sequence[dict[str, Any]],
    records_by_source: Mapping[str, list[dict[str, Any]]],
    hg_links: Sequence[tuple[str, str, str]],
    manifest_sha256: str,
) -> list[tuple[str, str, str, str, str, str, str]]:
    cases = load_cases(case_index, pages, manifest_sha256)
    profiles = load_profiles(curated, cases)
    if len(profiles) != 2730:
        raise ValueError("HG 사건의 검증된 프로필 건수가 예상과 다릅니다.")
    roles_by_case: dict[str, dict[str, str]] = {}
    for linked_case, role, source_id in hg_links:
        roles_by_case.setdefault(linked_case, {})[role] = source_id
    contracts_by_case: dict[str, set[str]] = {case_id: set() for case_id in profiles}
    contract_record_by_case: dict[str, str] = {}
    for case_id, linked_roles in roles_by_case.items():
        if set(linked_roles) != set(ROLES):
            raise ValueError("HG 프로필의 다섯 역할 연결이 완전하지 않습니다.")
        for role, source_id in linked_roles.items():
            context_rows = [
                row
                for row in records_by_source.get(source_id, ())
                if row.get("kind") == "case_context"
            ]
            if len(context_rows) != 1:
                raise ValueError("HG 역할 쪽마다 단일 사건 문맥 기록이 필요합니다.")
            row = context_rows[0]
            fields = row.get("fields", {})
            contract = fields.get("계약", "")
            if (
                row.get("review_status") != "parsed"
                or row.get("case_ids") != [case_id]
                or fields.get("예약") != case_id
                or fields.get("기록 종류") != role
                or not contract.startswith("CT-")
                or identifiers(contract) != [contract]
            ):
                raise ValueError(
                    "HG 프로필의 계약·사건·역할 원본 일치 검증 실패입니다."
                )
            contracts_by_case[case_id].add(contract)
            if role == ROLES[1]:
                contract_record_by_case[case_id] = row["record_id"]
    if any(len(values) != 1 for values in contracts_by_case.values()):
        raise ValueError("HG 사건마다 하나의 확인된 계약 키가 필요합니다.")
    if set(contract_record_by_case) != set(profiles):
        raise ValueError("HG 프로필의 출처 기록이 누락되었습니다.")
    result = []
    for case_id, (shipper, cargo, spec) in sorted(profiles.items()):
        result.append(
            (
                "HG",
                case_id,
                contract_record_by_case[case_id],
                shipper,
                cargo,
                spec,
                next(iter(contracts_by_case[case_id])),
            )
        )
    return result


def _insert_structured_records(
    connection: sqlite3.Connection,
    curated: Path,
    base_source_rows: Mapping[str, dict[str, Any]],
    source_families: Mapping[str, str],
    ha_index: Sequence[dict[str, Any]],
    handbook_index: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    source_rows = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT source_id, book_id, page, page_family FROM source_pages"
        )
    }
    if set(source_rows) != set(base_source_rows):
        raise ValueError("정제 출처와 HG 스냅샷의 페이지 집합이 다릅니다.")
    for source_id, source in base_source_rows.items():
        row = source_rows[source_id]
        if row != (source["book_id"], source["page"], source_families[source_id]):
            raise ValueError("정제 출처의 문서·쪽·양식과 SQLite 출처가 다릅니다.")

    records_seen: set[str] = set()
    records_by_source: dict[str, list[dict[str, Any]]] = {}
    record_count = 0
    field_count = 0
    family_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    record_insert_batch = []
    field_insert_batch = []
    for path in sorted((curated / "records").glob("*.jsonl")):
        for record in read_rows(path):
            record_id = record["record_id"]
            source_id = record["source_id"]
            if record_id in records_seen or source_id not in source_rows:
                raise ValueError("중복 기록 또는 연결되지 않은 정제 출처가 있습니다.")
            records_seen.add(record_id)
            record_count += 1
            family = source_families[source_id]
            family_counts[family] = family_counts.get(family, 0) + 1
            status = record.get("review_status", "unreviewed")
            status_counts[status] = status_counts.get(status, 0) + 1
            line_start = record.get("line_start")
            line_end = record.get("line_end")
            if (
                not isinstance(line_start, int)
                or not isinstance(line_end, int)
                or line_start < 1
                or line_end < line_start
            ):
                raise ValueError("정제 기록의 원본 줄 범위를 확인할 수 없습니다.")
            record_insert_batch.append(
                (
                    record_id,
                    source_id,
                    line_start,
                    line_end,
                    record.get("section"),
                    record["kind"],
                    status,
                )
            )
            field_rows = _record_fields(record)
            field_insert_batch.extend(field_rows)
            field_count += len(field_rows)
            if record.get("kind") == "case_context" or (
                family == "HA" and record.get("kind") == "table_row"
            ):
                records_by_source.setdefault(source_id, []).append(record)
            if len(record_insert_batch) >= 2000:
                connection.executemany(
                    "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                    record_insert_batch,
                )
                record_insert_batch.clear()
            if len(field_insert_batch) >= 5000:
                if record_insert_batch:
                    connection.executemany(
                        "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        record_insert_batch,
                    )
                    record_insert_batch.clear()
                connection.executemany(
                    "INSERT INTO record_fields VALUES (?, ?, ?, ?)", field_insert_batch
                )
                field_insert_batch.clear()
    if record_insert_batch:
        connection.executemany(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            record_insert_batch,
        )
    if field_insert_batch:
        connection.executemany(
            "INSERT INTO record_fields VALUES (?, ?, ?, ?)", field_insert_batch
        )
    if record_count != 141_174:
        raise ValueError("구조화할 Advanced 정제 기록 건수가 예상과 다릅니다.")

    ha_links = _ha_case_links(base_source_rows)
    ha_rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for source_id, records in records_by_source.items():
        if source_families[source_id] == "HA":
            ha_rows_by_source[source_id] = [
                row for row in records if row.get("kind") == "table_row"
            ]
    if set(ha_rows_by_source) != {source for _, _, source in ha_links}:
        raise ValueError("HA 사건 페이지 중 표 행이 없는 쪽이 있습니다.")
    ha_table_count = 0
    ha_signatures: set[tuple[str, ...]] = set()
    for case_id, role, source_id in ha_links:
        rows = ha_rows_by_source[source_id]
        if not rows:
            raise ValueError("HA 역할 쪽에 표 행이 없습니다.")
        for row in rows:
            if row.get("review_status") != "parsed" or row.get("case_ids") != [case_id]:
                raise ValueError("HA 표 행의 상태 또는 사건 출처가 머리말과 다릅니다.")
            ha_table_count += 1
            ha_signatures.add(tuple(sorted((row.get("fields") or {}).keys())))
    if ha_table_count != 4_356 or len(ha_signatures) != 5:
        raise ValueError("HA 파싱된 행 수 또는 필드 형식이 예상과 다릅니다.")
    expected_ha_links = {
        (row["case_id"], row["record_role"], row["source"]["source_id"])
        for row in ha_index
    }
    if expected_ha_links != set(ha_links):
        raise ValueError("HA 역할 인덱스와 원본 머리말의 사건·역할 연결이 다릅니다.")
    expected_ha_rows = {
        (
            row["case_id"],
            row["record_role"],
            table_row["record_id"],
            table_row["source_id"],
        )
        for row in ha_index
        for table_row in row.get("table_rows", ())
    }
    actual_ha_rows = {
        (case_id, role, row["record_id"], row["source_id"])
        for case_id, role, source_id in ha_links
        for row in ha_rows_by_source[source_id]
    }
    if expected_ha_rows != actual_ha_rows:
        raise ValueError("HA 역할 인덱스와 파싱된 표 행의 원본 위치가 다릅니다.")

    actual_handbook = set()
    is_rows = 0
    for path in sorted((curated / "records").glob("*.jsonl")):
        for row in read_rows(path):
            source_id = row["source_id"]
            if (
                source_families[source_id] != "IS"
                or row.get("kind") != "table_row"
                or row.get("review_status") != "parsed"
            ):
                continue
            is_rows += 1
            fields = row.get("fields") or {}
            key_type = HANDBOOK_SIGNATURES.get(frozenset(fields))
            if key_type is None:
                continue
            keys = identifiers(fields[key_type])
            if len(keys) != 1:
                continue
            actual_handbook.add((key_type, keys[0], row["record_id"], source_id))
    expected_handbook = {
        (
            row["key_column"],
            row["record_key"],
            row["record"]["record_id"],
            row["record"]["source_id"],
        )
        for row in handbook_index
    }
    if actual_handbook != expected_handbook or len(actual_handbook) != 11_073:
        raise ValueError("IS 키 인덱스와 정제 행의 출처가 정확히 일치하지 않습니다.")
    if is_rows != 27_715:
        raise ValueError("IS 정제 표 행 건수가 예상과 다릅니다.")

    connection.executemany(
        "INSERT INTO cases VALUES ('HA', ?)",
        [(case_id,) for case_id in sorted({case for case, _, _ in ha_links})],
    )
    connection.executemany(
        "INSERT INTO case_records VALUES ('HA', ?, ?, ?, 'page_heading_case_id')",
        ha_links,
    )
    for row in handbook_index:
        record = row["record"]
        connection.execute(
            "INSERT INTO handbook_records VALUES (?, ?, ?)",
            (row["key_column"], row["record_key"], record["record_id"]),
        )
    return (
        {
            "source_records": record_count,
            "record_fields": field_count,
            "records_by_family": family_counts,
            "records_by_status": status_counts,
            "ha_cases": len({case for case, _, _ in ha_links}),
            "ha_case_records": len(ha_links),
            "ha_table_rows": ha_table_count,
            "ha_field_signatures": len(ha_signatures),
            "is_table_rows": is_rows,
            "handbook_records": len(actual_handbook),
            "handbook_key_types": {
                key_type: sum(1 for row in actual_handbook if row[0] == key_type)
                for key_type in ("기록번호", "처리 기록")
            },
        },
        records_by_source,
    )


def _insert_facts(
    connection: sqlite3.Connection,
    facts: Sequence[Mapping[str, Any]],
    links: Mapping[str, tuple[str, str]],
) -> int:
    rows = []
    for fact in facts:
        source = fact["source"]
        source_id = source["source_id"]
        case_id, role = fact["case_id"], fact["record_role"]
        if links.get(source_id) != (case_id, role):
            raise ValueError("HG 사실의 역할 링크가 사건 인덱스와 다릅니다.")
        if not isinstance(fact.get("raw_value"), str):
            raise TypeError("HG 사실의 원문 값은 문자열이어야 합니다.")
        value_type, value_text, value_int, value_json = _parse_fact_value(fact)
        cost_kind = fact.get("cost_kind") or "none"
        if cost_kind not in {"storage", "late_return", "none"}:
            raise ValueError("HG 사실에 지원되지 않는 비용 구분이 있습니다.")
        rows.append(
            (
                source["record_id"],
                "HG",
                case_id,
                role,
                fact["fact"],
                cost_kind,
                fact["column_label"],
                fact["raw_value"],
                fact["state"],
                value_type,
                value_text,
                value_int,
                value_json,
                fact.get("unit"),
                fact.get("currency"),
                fact.get("review_reason"),
                source_id,
            )
        )
    _append_many(
        connection,
        "INSERT INTO role_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    book_id: str
    file_name: str
    pdf_sha256: str
    page: int
    record_id: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class RecordRow:
    family: str
    case_id: str
    role: str
    record_id: str
    kind: str
    fields: dict[str, str]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class FactRow:
    case_id: str
    role: str
    fact_code: str
    cost_kind: str
    column_label: str
    raw_value: str
    value_type: str
    value_text: str | None
    value_int: int | None
    value_json: Any
    state: str
    unit: str | None
    currency: str | None
    review_reason: str | None
    source: SourceRef

    @property
    def value(self) -> str | int | Any | None:
        if self.value_type == "integer":
            return self.value_int
        if self.value_type in {"decimal", "date", "datetime", "text"}:
            return self.value_text
        return self.value_json


class StructuredStore:
    """Read the validated structured snapshot without opening a write handle."""

    def __init__(self, snapshot: Path = STRUCTURED_SNAPSHOT):
        snapshot = Path(snapshot)
        self.snapshot = snapshot.parent if snapshot.suffix == ".sqlite3" else snapshot
        self.database = (
            snapshot if snapshot.suffix == ".sqlite3" else snapshot / "advanced.sqlite3"
        )
        self._connection: sqlite3.Connection | None = None
        self._manifest: dict[str, Any] | None = None
        self.query_count = 0

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("StructuredStore가 이미 열려 있습니다.")
        manifest_path = self.snapshot / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        outputs = manifest.get("outputs", {})
        if (
            manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("snapshot_type") != SNAPSHOT_TYPE
            or set(outputs) != {"advanced.sqlite3"}
            or manifest.get("snapshot_id")
            != _sha_bytes(
                _json_bytes({"inputs": manifest.get("inputs"), "outputs": outputs})
            )
            or _sha_file(self.database) != outputs.get("advanced.sqlite3")
        ):
            raise ValueError(
                "구조화 SQLite 스냅샷의 manifest 또는 파일 지문이 다릅니다."
            )
        uri = f"{self.database.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if (
            connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or list(connection.execute("PRAGMA foreign_key_check"))
        ):
            connection.close()
            raise ValueError("구조화 SQLite 스냅샷의 스키마 또는 무결성이 다릅니다.")
        self._connection = connection
        self._manifest = manifest
        self.query_count = 0
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("조회하려면 StructuredStore를 열어야 합니다.")
        return self._connection

    def _execute(self, sql: str, parameters=()):
        self.query_count += 1
        return self._db().execute(sql, parameters)

    @property
    def provenance(self) -> dict[str, Any]:
        if self._manifest is None:
            raise RuntimeError(
                "StructuredStore를 열기 전에는 출처 정보를 읽을 수 없습니다."
            )
        return {
            "snapshot_id": self._manifest["snapshot_id"],
            "manifest_sha256": _sha_bytes(_json_bytes(self._manifest)),
            "inputs": self._manifest["inputs"],
            "database_sha256": self._manifest["outputs"]["advanced.sqlite3"],
        }

    def _source(self, row: sqlite3.Row) -> SourceRef:
        return SourceRef(
            source_id=row["source_id"],
            book_id=row["book_id"],
            file_name=row["file_name"],
            pdf_sha256=row["file_sha256"],
            page=row["page"],
            record_id=row["record_id"],
            line_start=row["line_start"],
            line_end=row["line_end"],
        )

    def identities_for(self, field: str, value: str) -> set[str]:
        """Return source-grounded HG case IDs for one supported exact field."""
        self._db()
        if field not in IDENTITY_FIELDS:
            raise ValueError(f"지원하지 않는 HG 식별 필드입니다: {field}")
        if not isinstance(value, str) or not value.strip():
            return set()
        if field == "case_id":
            rows = self._execute(
                "SELECT case_id FROM cases WHERE family = 'HG' AND case_id = ?",
                (value,),
            )
            return {row[0] for row in rows}
        if field in PROFILE_COLUMNS:
            column = PROFILE_COLUMNS[field]
            key = _normalized(value) if field in {"shipper", "cargo", "spec"} else value
            rows = self._execute(
                f"SELECT case_id FROM case_profiles WHERE family = 'HG' AND {column} = ?",
                (key,),
            )
            result = {row[0] for row in rows}
            if field == "contract_id":
                rows = self._execute(
                    "SELECT DISTINCT case_id FROM role_facts "
                    "WHERE fact_code = 'comparison_contract_id' AND state = 'reported' "
                    "AND value_text = ?",
                    (value,),
                )
                result.update(row[0] for row in rows)
            return result
        fact_code = field if field in DATE_FACTS else IDENTIFIER_FACTS[field]
        if field == "equipment_id":
            keys = identifiers(normalize("NFKC", value).upper())
            if len(keys) != 1:
                return set()
            value = keys[0]
        elif field == "record_id" or field == "contract_id":
            keys = identifiers(value)
            if len(keys) != 1:
                return set()
            value = keys[0]
        if field in DATE_FACTS:
            rows = self._execute(
                "SELECT DISTINCT case_id FROM role_facts "
                "WHERE fact_code = ? AND state = 'reported' AND value_text = ?",
                (fact_code, value),
            )
            return {row[0] for row in rows}
        rows = self._execute(
            "SELECT case_id, raw_value FROM role_facts "
            "WHERE fact_code = ? AND state = 'reported'",
            (fact_code,),
        )
        return {
            row["case_id"]
            for row in rows
            if identifiers(
                normalize("NFKC", row["raw_value"]).upper()
                if field == "equipment_id"
                else row["raw_value"]
            )
            == [value]
        }

    def profile_candidates(
        self, family: str = "HG", *, filters: Mapping[str, str]
    ) -> set[str]:
        if family != "HG":
            raise ValueError("프로필 직접 조회는 현재 HG에서만 지원합니다.")
        if not filters:
            raise ValueError("프로필 조회에는 하나 이상의 확인된 조건이 필요합니다.")
        if any(field not in IDENTITY_FIELDS for field in filters):
            raise ValueError("프로필 조건에 지원하지 않는 필드가 있습니다.")
        candidates: set[str] | None = None
        for field, value in filters.items():
            if not isinstance(value, str) or not value.strip():
                return set()
            matched = self.identities_for(field, value)
            candidates = matched if candidates is None else candidates & matched
            if not candidates:
                return set()
        return candidates or set()

    def get_hg_facts(
        self,
        case_id: str,
        *,
        fact_code: str | None = None,
        role: str | None = None,
        cost_kind: str | None = None,
    ) -> list[FactRow]:
        if role is not None and role not in ROLES:
            raise ValueError("지원되지 않는 HG 역할입니다.")
        if cost_kind is not None and cost_kind not in {
            "storage",
            "late_return",
            "none",
        }:
            raise ValueError("지원되지 않는 비용 구분입니다.")
        clauses = ["facts.family = 'HG'", "facts.case_id = ?"]
        parameters: list[str] = [case_id]
        for column, value in (
            ("fact_code", fact_code),
            ("role", role),
            ("cost_kind", cost_kind),
        ):
            if value is not None:
                clauses.append(f"facts.{column} = ?")
                parameters.append(value)
        rows = self._execute(
            "SELECT facts.*, records.line_start, records.line_end, pages.book_id, pages.page, "
            "documents.file_name, documents.file_sha256 FROM role_facts AS facts "
            "JOIN source_records AS records ON records.record_id = facts.record_id "
            "JOIN source_pages AS pages ON pages.source_id = facts.source_id "
            "JOIN source_documents AS documents ON documents.book_id = pages.book_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY facts.role, records.line_start, facts.fact_code, facts.cost_kind",
            parameters,
        )
        result = []
        for row in rows:
            result.append(
                FactRow(
                    case_id=row["case_id"],
                    role=row["role"],
                    fact_code=row["fact_code"],
                    cost_kind=row["cost_kind"],
                    column_label=row["column_label"],
                    raw_value=row["raw_value"],
                    value_type=row["value_type"],
                    value_text=row["value_text"],
                    value_int=row["value_int"],
                    value_json=json.loads(row["value_json"]),
                    state=row["state"],
                    unit=row["unit"],
                    currency=row["currency"],
                    review_reason=row["review_reason"],
                    source=self._source(row),
                )
            )
        return result

    def get_role_rows(
        self,
        family: str,
        case_id: str,
        role: str | None = None,
        *,
        qualifiers: Mapping[str, str] | None = None,
    ) -> list[RecordRow]:
        if family != "HA":
            raise ValueError("역할별 표 행 조회는 현재 HA만 지원합니다.")
        if role is not None and role not in HA_ROLES:
            raise ValueError("지원되지 않는 HA 역할입니다.")
        qualifiers = qualifiers or {}
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in qualifiers.items()
        ):
            raise ValueError("표 행 조건은 문자열 열 이름과 문자열 값이어야 합니다.")
        clauses = [
            "links.family = ?",
            "links.case_id = ?",
            "records.kind = 'table_row'",
            "records.parse_status = 'parsed'",
        ]
        parameters: list[str] = [family, case_id]
        if role is not None:
            clauses.append("links.role = ?")
            parameters.append(role)
        for index, (field_name, raw_value) in enumerate(qualifiers.items()):
            alias = f"qualifier_{index}"
            clauses.append(
                "EXISTS (SELECT 1 FROM record_fields AS "
                + alias
                + f" WHERE {alias}.record_id = records.record_id"
                + f" AND {alias}.field_name = ? AND {alias}.raw_value = ?)"
            )
            parameters.extend((field_name, raw_value))
        rows = self._execute(
            "SELECT links.family, links.case_id, links.role, records.record_id, records.kind, "
            "records.source_id, records.line_start, records.line_end, pages.book_id, pages.page, "
            "documents.file_name, documents.file_sha256 FROM case_records AS links "
            "JOIN source_records AS records ON records.source_id = links.source_id "
            "JOIN source_pages AS pages ON pages.source_id = records.source_id "
            "JOIN source_documents AS documents ON documents.book_id = pages.book_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY links.role, records.line_start, records.record_id",
            parameters,
        )
        return [self._record_row(row) for row in rows]

    def lookup_handbook(self, key_type: str, key_value: str) -> list[RecordRow]:
        if key_type not in {"기록번호", "처리 기록"}:
            raise ValueError("지원되지 않는 IS 기록 키 종류입니다.")
        if not isinstance(key_value, str) or not key_value.strip():
            return []
        rows = self._execute(
            "SELECT handbook.key_type, handbook.key_value, records.record_id, records.kind, "
            "records.source_id, records.line_start, records.line_end, pages.book_id, pages.page, "
            "documents.file_name, documents.file_sha256 FROM handbook_records AS handbook "
            "JOIN source_records AS records ON records.record_id = handbook.record_id "
            "JOIN source_pages AS pages ON pages.source_id = records.source_id "
            "JOIN source_documents AS documents ON documents.book_id = pages.book_id "
            "WHERE handbook.key_type = ? AND handbook.key_value = ? "
            "ORDER BY records.line_start, records.record_id",
            (key_type, key_value),
        )
        return [
            self._record_row(
                row,
                family="IS",
                case_id="",
                role=row["key_type"],
            )
            for row in rows
        ]

    def _record_row(
        self,
        row: sqlite3.Row,
        *,
        family: str | None = None,
        case_id: str | None = None,
        role: str | None = None,
    ) -> RecordRow:
        field_rows = self._execute(
            "SELECT field_name, raw_value FROM record_fields "
            "WHERE record_id = ? ORDER BY ordinal",
            (row["record_id"],),
        )
        fields = {field["field_name"]: field["raw_value"] for field in field_rows}
        return RecordRow(
            family=family if family is not None else row["family"],
            case_id=case_id if case_id is not None else row["case_id"],
            role=role if role is not None else row["role"],
            record_id=row["record_id"],
            kind=row["kind"],
            fields=fields,
            source=self._source(row),
        )


def build_structured_store(
    output_root: Path = STRUCTURED_SNAPSHOT,
) -> Path:
    """Build a verified immutable-ready SQLite snapshot from local Advanced inputs."""
    output_root = Path(output_root)
    curated = CURATED
    case_index = CASE_INDEX
    facts = FACTS
    format_index = FORMAT_INDEX
    hg_snapshot = HG_SNAPSHOT
    _manifest, curated_fingerprints = _verify_curation(curated)
    manifest_sha = curated_fingerprints["manifest_sha256"]
    facts_rows, fact_fingerprints, case_source_links = _validate_fact_artifacts(
        manifest_sha, Path(case_index), Path(facts)
    )
    pages, source_by_id = _read_pages_and_sources()
    cache_path = PROJECT_ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
    page_cache_sha256 = _sha_file(cache_path)
    ha_index, handbook_index, format_fingerprints = _load_format_index(
        Path(format_index),
        manifest_sha256=manifest_sha,
        page_cache_sha256=page_cache_sha256,
    )
    with HGStore(Path(hg_snapshot), pages) as validated_hg:
        hg_provenance = validated_hg.provenance
        base = validated_hg._connection
        if base is None:
            raise RuntimeError("검증된 HG SQLite 연결이 없습니다.")
        source_documents = list(
            base.execute(
                "SELECT book_id, file_name, file_sha256, page_count FROM source_documents ORDER BY book_id"
            )
        )
        source_pages = list(
            base.execute(
                "SELECT source_id, book_id, page, page_family, heading FROM source_pages ORDER BY book_id, page"
            )
        )
        hg_cases = list(
            base.execute(
                "SELECT family, case_id FROM cases WHERE family = 'HG' ORDER BY case_id"
            )
        )
        hg_links = list(
            base.execute(
                "SELECT family, case_id, role, source_id, match_rule FROM case_records "
                "WHERE family = 'HG' ORDER BY case_id, role"
            )
        )
    source_families = {row[0]: row[3] for row in source_pages}
    source_data = {
        source_id: {**source, "page_family": source_families[source_id]}
        for source_id, source in source_by_id.items()
    }
    if len(source_pages) != 18_000 or len(hg_cases) != 2730 or len(hg_links) != 13_650:
        raise ValueError("검증된 HG 스냅샷의 출처 또는 사건 건수가 다릅니다.")
    facts_link_map: dict[str, tuple[str, str]] = {}
    for family, case_id, role, source_id, match_rule in hg_links:
        if family != "HG" or match_rule != "page_heading_case_id":
            raise ValueError("HG SQLite에 미승인된 사건 연결이 있습니다.")
        facts_link_map[source_id] = case_id, role
    if facts_link_map != case_source_links:
        raise ValueError("기존 HG SQLite와 검증된 사건·역할 인덱스가 다릅니다.")

    inputs = _input_identity(
        curated_fingerprints,
        fact_fingerprints,
        format_fingerprints,
        hg_provenance,
        page_cache_sha256,
    )
    if _existing_snapshot(output_root, inputs):
        return output_root

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_root.parent, prefix=f".{output_root.name}-"
    ) as temporary:
        staging = Path(temporary)
        database_path = staging / "advanced.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            _create_schema(connection)
            connection.executemany(
                "INSERT INTO source_documents VALUES (?, ?, ?, ?)",
                [tuple(row) for row in source_documents],
            )
            connection.executemany(
                "INSERT INTO source_pages VALUES (?, ?, ?, ?, ?)",
                [tuple(row) for row in source_pages],
            )
            connection.executemany(
                "INSERT INTO cases VALUES (?, ?)", [tuple(row) for row in hg_cases]
            )
            connection.executemany(
                "INSERT INTO case_records VALUES (?, ?, ?, ?, ?)",
                [tuple(row) for row in hg_links],
            )
            counts, records_by_source = _insert_structured_records(
                connection,
                Path(curated),
                source_data,
                source_families,
                ha_index,
                handbook_index,
            )
            # Store the verified profile keys and the exact source record used to support them.
            profile_rows = _profile_records(
                Path(curated),
                Path(case_index),
                pages,
                records_by_source,
                [(row[1], row[2], row[3]) for row in hg_links],
                manifest_sha,
            )
            connection.executemany(
                "INSERT INTO case_profiles VALUES (?, ?, ?, ?, ?, ?, ?)", profile_rows
            )
            fact_count = _insert_facts(connection, facts_rows, facts_link_map)
            # Compare every fact's independent source line to the curated row.
            fact_source_refs: dict[str, tuple[str, int, int, str, str, int]] = {}
            for fact in facts_rows:
                source = fact["source"]
                record_id = source["record_id"]
                reference = (
                    source["source_id"],
                    source["line_start"],
                    source["line_end"],
                    Path(source["file_name"]).stem,
                    source["file_name"],
                    source["page"],
                )
                previous = fact_source_refs.setdefault(record_id, reference)
                if previous != reference:
                    raise ValueError(
                        "한 HG 기록 행의 사실들이 서로 다른 출처를 가리킵니다."
                    )
            connection.execute(
                "CREATE TEMP TABLE expected_fact_sources ("
                "record_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, "
                "line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, "
                "book_id TEXT NOT NULL, file_name TEXT NOT NULL, page INTEGER NOT NULL) STRICT"
            )
            connection.executemany(
                "INSERT INTO expected_fact_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (record_id, *reference)
                    for record_id, reference in fact_source_refs.items()
                ],
            )
            invalid_fact_sources = connection.execute(
                "SELECT count(*) FROM role_facts AS facts "
                "LEFT JOIN source_records AS records ON records.record_id = facts.record_id "
                "LEFT JOIN case_records AS links ON links.family = facts.family "
                "AND links.case_id = facts.case_id AND links.role = facts.role "
                "AND links.source_id = facts.source_id "
                "LEFT JOIN expected_fact_sources AS expected ON expected.record_id = facts.record_id "
                "LEFT JOIN source_pages AS pages ON pages.source_id = facts.source_id "
                "LEFT JOIN source_documents AS documents ON documents.book_id = pages.book_id "
                "WHERE records.record_id IS NULL OR links.source_id IS NULL "
                "OR expected.record_id IS NULL OR pages.source_id IS NULL "
                "OR documents.book_id IS NULL OR records.source_id != expected.source_id "
                "OR records.line_start != expected.line_start "
                "OR records.line_end != expected.line_end "
                "OR pages.book_id != expected.book_id "
                "OR documents.file_name != expected.file_name "
                "OR pages.page != expected.page "
                "OR records.parse_status != 'parsed'"
            ).fetchone()[0]
            if invalid_fact_sources:
                raise ValueError(
                    "HG 역할 사실의 기록 행 또는 사건 출처가 일치하지 않습니다."
                )
            connection.execute("DROP TABLE expected_fact_sources")
            profiles_count = connection.execute(
                "SELECT count(*) FROM case_profiles"
            ).fetchone()[0]
            if fact_count != len(facts_rows) or profiles_count != 2730:
                raise ValueError("HG 구조화 사실 또는 프로필 건수가 다릅니다.")

            foreign_key_issues = list(connection.execute("PRAGMA foreign_key_check"))
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if foreign_key_issues or integrity != "ok":
                raise ValueError("구조화 SQLite 무결성 검사가 실패했습니다.")
            state_counts: dict[str, dict[str, int]] = {}
            for role, state, count in connection.execute(
                "SELECT role, state, count(*) FROM role_facts "
                "GROUP BY role, state ORDER BY role, state"
            ):
                state_counts.setdefault(role, {})[state] = count
            counts.update(
                {
                    "source_documents": len(source_documents),
                    "source_pages": len(source_pages),
                    "hg_cases": len(hg_cases),
                    "hg_case_records": len(hg_links),
                    "hg_role_facts": fact_count,
                    "hg_case_profiles": profiles_count,
                    "case_records_by_family": dict(
                        connection.execute(
                            "SELECT family, count(*) FROM case_records GROUP BY family ORDER BY family"
                        ).fetchall()
                    ),
                    "page_families": dict(
                        connection.execute(
                            "SELECT page_family, count(*) FROM source_pages GROUP BY page_family ORDER BY page_family"
                        ).fetchall()
                    ),
                    "handbook_ambiguous_keys": connection.execute(
                        "SELECT count(*) FROM (SELECT key_type, key_value FROM handbook_records "
                        "GROUP BY key_type, key_value HAVING count(*) > 1)"
                    ).fetchone()[0],
                    "hg_facts_by_role_and_state": state_counts,
                    "hg_fact_review_reasons": dict(
                        connection.execute(
                            "SELECT review_reason, count(*) FROM role_facts "
                            "WHERE review_reason IS NOT NULL "
                            "GROUP BY review_reason ORDER BY review_reason"
                        ).fetchall()
                    ),
                }
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        outputs = {"advanced.sqlite3": _sha_file(database_path)}
        snapshot_id = _sha_bytes(_json_bytes({"inputs": inputs, "outputs": outputs}))
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        manifest_output = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_type": SNAPSHOT_TYPE,
            "snapshot_id": snapshot_id,
            "built_at": built_at,
            "inputs": inputs,
            "outputs": outputs,
            "counts": counts,
            "config": {
                "sqlite_version": sqlite3.sqlite_version,
                "read_only_at_runtime": True,
                "value_semantics": "reported values are typed; other states retain normalized JSON and raw source text",
            },
        }
        (staging / "manifest.json").write_bytes(_json_bytes(manifest_output))
        try:
            staging.rename(output_root)
        except OSError:
            if not _existing_snapshot(output_root, inputs):
                raise
    return output_root


if __name__ == "__main__":
    print(build_structured_store())
