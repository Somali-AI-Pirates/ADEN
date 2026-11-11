"""Versioned local rules for Advanced question interpretation experiments."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from experimental.u_10o0o.advanced_case_sources import ROLES

DEFAULT_DATABASE = Path("artifacts/u_10o0o/advanced-routing/routing_rules.sqlite3")
DATE_TARGETS = (
    "actual_gate_out_date",
    "actual_discharge_date",
    "actual_designated_return_date",
    "scheduled_return_date",
    "system_entry_date",
)
SCOPES = ("target_date", "target_role", "clause_role")
MATCH_MODES = ("legacy_regex", "literal_phrase")
AUTHOR_KINDS = ("legacy", "admin", "llm")


@dataclass(frozen=True)
class RoutingRule:
    scope: str
    target_code: str
    match_mode: str
    pattern: str
    flags: int = 0
    position: int = 0
    enabled: bool = True
    rule_id: str = ""

    def __post_init__(self):
        if self.scope not in SCOPES:
            raise ValueError(f"허용하지 않는 규칙 범위: {self.scope}")
        allowed_targets = DATE_TARGETS if self.scope == "target_date" else ROLES
        if self.target_code not in allowed_targets:
            raise ValueError(f"{self.scope}에서 허용하지 않는 대상: {self.target_code}")
        if self.match_mode not in MATCH_MODES:
            raise ValueError(f"허용하지 않는 매칭 방식: {self.match_mode}")
        if not isinstance(self.pattern, str) or not self.pattern.strip():
            raise ValueError("규칙 표현은 비어 있지 않은 문자열이어야 합니다.")
        if not isinstance(self.flags, int) or self.flags < 0:
            raise ValueError("정규식 플래그는 0 이상의 정수여야 합니다.")
        if not isinstance(self.position, int) or self.position < 0:
            raise ValueError("position은 0 이상의 정수여야 합니다.")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled는 bool이어야 합니다.")
        if self.match_mode == "legacy_regex":
            re.compile(self.pattern, self.flags)
        elif normalize(self.pattern) != self.pattern:
            raise ValueError(
                "literal_phrase는 NFKC·소문자·공백 정규화된 표현이어야 합니다."
            )

        if not self.rule_id:
            identity = "\0".join(
                (
                    self.scope,
                    self.target_code,
                    self.match_mode,
                    self.pattern,
                    str(self.flags),
                    str(self.position),
                )
            )
            object.__setattr__(
                self,
                "rule_id",
                hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            )


@dataclass(frozen=True)
class Activation:
    activation_id: str
    version_id: str
    previous_version_id: str | None
    activated_by: str
    activated_at: str
    reason: str


@dataclass(frozen=True)
class RulesSnapshot:
    version_id: str
    content_sha256: str
    rules: tuple[RoutingRule, ...]

    def _targets(self, scope: str, text: str) -> tuple[str, ...]:
        targets = []
        seen = set()
        for rule in self.rules:
            if (
                rule.enabled
                and rule.scope == scope
                and rule.target_code not in seen
                and matches(rule, text)
            ):
                targets.append(rule.target_code)
                seen.add(rule.target_code)
        return tuple(targets)

    def match_target_roles(self, text: str) -> tuple[str, ...]:
        return self._targets("target_role", text)

    def match_clause_roles(self, text: str) -> tuple[str, ...]:
        return self._targets("clause_role", text)

    def match_target_dates(self, text: str) -> tuple[str, ...]:
        return self._targets("target_date", text)


def normalize(text: str) -> str:
    """Apply the same form-insensitive normalization to stored rules and text."""
    if not isinstance(text, str):
        raise TypeError("규칙 검색 대상은 문자열이어야 합니다.")
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def matches(rule: RoutingRule, text: str) -> bool:
    if rule.match_mode == "literal_phrase":
        return normalize(rule.pattern) in normalize(text)
    return re.search(rule.pattern, text, rule.flags) is not None


def legacy_rules() -> tuple[RoutingRule, ...]:
    """Return the current code rules in deterministic order for the seed version."""
    from experimental.u_10o0o.advanced_clause_binding import ROLE_TERMS
    from experimental.u_10o0o.advanced_target_lookup import DATE_CUES, ROLE_CUES

    rules = []
    for scope, patterns in (
        ("target_date", DATE_CUES),
        ("target_role", ROLE_CUES),
    ):
        rules.extend(
            RoutingRule(
                scope=scope,
                target_code=target,
                match_mode="legacy_regex",
                pattern=pattern.pattern,
                flags=pattern.flags,
                position=position,
            )
            for position, (target, pattern) in enumerate(patterns.items())
        )
    rules.extend(
        RoutingRule(
            scope="clause_role",
            target_code=role,
            match_mode="legacy_regex",
            pattern=pattern.pattern,
            flags=pattern.flags,
            position=position,
        )
        for position, (role, pattern) in enumerate(zip(ROLES, ROLE_TERMS, strict=True))
    )
    return tuple(rules)


class RoutingRuleStore:
    """Manage immutable validated rule versions and their activation history."""

    def __init__(self, database: str | Path = DEFAULT_DATABASE):
        self.database = Path(database)

    @contextmanager
    def _connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        if readonly:
            uri = self.database.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(
                uri, uri=True, timeout=30, isolation_level=None
            )
        else:
            connection = sqlite3.connect(
                self.database, timeout=30, isolation_level=None
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _canonical_rules(rules: Iterable[RoutingRule]) -> tuple[RoutingRule, ...]:
        ordered = tuple(
            sorted(rules, key=lambda r: (r.scope, r.position, r.target_code, r.rule_id))
        )
        identities = set()
        rules_by_id = set()
        for rule in ordered:
            identity = (
                rule.scope,
                rule.target_code,
                rule.match_mode,
                rule.pattern,
                rule.flags,
            )
            if identity in identities:
                raise ValueError(f"중복된 질문 규칙: {rule.scope}/{rule.target_code}")
            identities.add(identity)
            if rule.rule_id in rules_by_id:
                raise ValueError(f"중복 rule_id: {rule.rule_id}")
            rules_by_id.add(rule.rule_id)
        return ordered

    @classmethod
    def _content(
        cls, rules: Iterable[RoutingRule]
    ) -> tuple[tuple[RoutingRule, ...], str]:
        ordered = cls._canonical_rules(rules)
        encoded = json.dumps(
            [asdict(rule) for rule in ordered],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ordered, hashlib.sha256(encoded).hexdigest()

    def initialize(self, seed_rules: Iterable[RoutingRule] | None = None) -> str:
        """Create schema and return the identical seed draft on repeated calls."""
        if seed_rules is None:
            seed_rules = legacy_rules()
        ordered, content_sha = self._content(seed_rules)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        role_values = ", ".join("'" + role.replace("'", "''") + "'" for role in ROLES)
        date_values = ", ".join("'" + value + "'" for value in DATE_TARGETS)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS rule_versions (
                    version_id TEXT PRIMARY KEY,
                    base_version_id TEXT REFERENCES rule_versions(version_id),
                    base_activation_id TEXT REFERENCES rule_activations(activation_id),
                    status TEXT NOT NULL CHECK (status IN ('draft', 'validated')),
                    author_kind TEXT NOT NULL CHECK (author_kind IN ('legacy', 'admin', 'llm')),
                    created_by TEXT NOT NULL CHECK (length(trim(created_by)) > 0),
                    created_at TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
                    validation_report_sha256 TEXT
                        CHECK (validation_report_sha256 IS NULL OR length(validation_report_sha256) = 64),
                    CHECK (status != 'validated' OR validation_report_sha256 IS NOT NULL)
                ) STRICT;

                CREATE TABLE IF NOT EXISTS routing_rules (
                    version_id TEXT NOT NULL REFERENCES rule_versions(version_id),
                    rule_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('target_date', 'target_role', 'clause_role')),
                    target_code TEXT NOT NULL CHECK (
                        (scope = 'target_date' AND target_code IN ({date_values}))
                        OR
                        (scope IN ('target_role', 'clause_role') AND target_code IN ({role_values}))
                    ),
                    match_mode TEXT NOT NULL CHECK (match_mode IN ('legacy_regex', 'literal_phrase')),
                    pattern TEXT NOT NULL CHECK (length(pattern) > 0),
                    flags INTEGER NOT NULL CHECK (flags >= 0),
                    position INTEGER NOT NULL CHECK (position >= 0),
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    PRIMARY KEY (version_id, rule_id),
                    UNIQUE (version_id, scope, target_code, match_mode, pattern, flags)
                ) STRICT;

                CREATE TABLE IF NOT EXISTS rule_activations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    activation_id TEXT NOT NULL UNIQUE,
                    version_id TEXT NOT NULL REFERENCES rule_versions(version_id),
                    previous_version_id TEXT REFERENCES rule_versions(version_id),
                    activated_by TEXT NOT NULL CHECK (length(trim(activated_by)) > 0),
                    activated_at TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0)
                ) STRICT;

                CREATE TRIGGER IF NOT EXISTS validated_rule_content_is_immutable
                BEFORE UPDATE ON routing_rules
                WHEN (SELECT status FROM rule_versions WHERE version_id = OLD.version_id) != 'draft'
                BEGIN
                    SELECT RAISE(ABORT, 'validated routing rules are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS validated_rule_content_cannot_grow
                BEFORE INSERT ON routing_rules
                WHEN (SELECT status FROM rule_versions WHERE version_id = NEW.version_id) != 'draft'
                BEGIN
                    SELECT RAISE(ABORT, 'validated routing rules are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS validated_rule_cannot_be_deleted
                BEFORE DELETE ON routing_rules
                WHEN (SELECT status FROM rule_versions WHERE version_id = OLD.version_id) != 'draft'
                BEGIN
                    SELECT RAISE(ABORT, 'validated routing rules are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS rule_versions_are_immutable_after_validation
                BEFORE UPDATE ON rule_versions
                WHEN OLD.status != 'draft'
                     OR NEW.status != 'validated'
                     OR NEW.version_id != OLD.version_id
                     OR NEW.base_version_id IS NOT OLD.base_version_id
                     OR NEW.base_activation_id IS NOT OLD.base_activation_id
                     OR NEW.author_kind != OLD.author_kind
                     OR NEW.created_by != OLD.created_by
                     OR NEW.created_at != OLD.created_at
                     OR NEW.content_sha256 != OLD.content_sha256
                BEGIN
                    SELECT RAISE(ABORT, 'validated routing versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS only_validated_rules_can_activate
                BEFORE INSERT ON rule_activations
                WHEN (SELECT status FROM rule_versions WHERE version_id = NEW.version_id) != 'validated'
                BEGIN
                    SELECT RAISE(ABORT, 'only validated versions can be activated');
                END;
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                row = connection.execute(
                    """SELECT version_id FROM rule_versions
                       WHERE author_kind = 'legacy' AND content_sha256 = ?
                       ORDER BY created_at, version_id LIMIT 1""",
                    (content_sha,),
                ).fetchone()
                if row is not None:
                    version_id = row["version_id"]
                else:
                    version_id = uuid.uuid4().hex
                    connection.execute(
                        """
                        INSERT INTO rule_versions (
                            version_id, base_version_id, base_activation_id, status,
                            author_kind, created_by, created_at, content_sha256
                        ) VALUES (?, NULL, NULL, 'draft', 'legacy', 'legacy-bootstrap', ?, ?)
                        """,
                        (version_id, _now(), content_sha),
                    )
                    self._insert_rules(connection, version_id, ordered)
        self._snapshot_any(version_id)
        return version_id

    @staticmethod
    def _insert_rules(
        connection: sqlite3.Connection,
        version_id: str,
        rules: Iterable[RoutingRule],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO routing_rules (
                version_id, rule_id, scope, target_code, match_mode, pattern,
                flags, position, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    version_id,
                    rule.rule_id,
                    rule.scope,
                    rule.target_code,
                    rule.match_mode,
                    rule.pattern,
                    rule.flags,
                    rule.position,
                    int(rule.enabled),
                )
                for rule in rules
            ),
        )

    def current_activation(self) -> Activation | None:
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT activation_id, version_id, previous_version_id,
                       activated_by, activated_at, reason
                FROM rule_activations
                ORDER BY sequence DESC LIMIT 1
                """
            ).fetchone()
        return _activation(row) if row else None

    def snapshot(self, version_id: str | None = None) -> RulesSnapshot:
        with self._connect(readonly=True) as connection:
            if version_id is None:
                activation = connection.execute(
                    """
                    SELECT version_id FROM rule_activations
                    ORDER BY sequence DESC LIMIT 1
                    """
                ).fetchone()
                if activation is None:
                    raise ValueError("활성화된 규칙 버전이 없습니다.")
                version_id = activation["version_id"]
            version = connection.execute(
                """
                SELECT version_id, status, content_sha256
                FROM rule_versions WHERE version_id = ?
                """,
                (version_id,),
            ).fetchone()
            if version is None or version["status"] != "validated":
                raise ValueError(f"검증된 규칙 버전이 아닙니다: {version_id}")
            rows = connection.execute(
                """
                SELECT rule_id, scope, target_code, match_mode, pattern,
                       flags, position, enabled
                FROM routing_rules WHERE version_id = ?
                ORDER BY scope, position, target_code, rule_id
                """,
                (version_id,),
            ).fetchall()
        rules = tuple(
            RoutingRule(
                scope=row["scope"],
                target_code=row["target_code"],
                match_mode=row["match_mode"],
                pattern=row["pattern"],
                flags=row["flags"],
                position=row["position"],
                enabled=bool(row["enabled"]),
                rule_id=row["rule_id"],
            )
            for row in rows
        )
        _, content_sha = self._content(rules)
        if content_sha != version["content_sha256"]:
            raise ValueError(f"규칙 버전의 내용 지문이 다릅니다: {version_id}")
        return RulesSnapshot(version_id, content_sha, rules)

    def create_draft(
        self,
        rules: Iterable[RoutingRule],
        *,
        author_kind: str,
        created_by: str,
        base_version_id: str | None = None,
        base_activation_id: str | None = None,
    ) -> str:
        if author_kind not in AUTHOR_KINDS[1:]:
            raise ValueError("초안 작성자는 admin 또는 llm이어야 합니다.")
        if not created_by.strip():
            raise ValueError("초안 작성자를 입력해야 합니다.")
        ordered, content_sha = self._content(rules)
        version_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                activation = connection.execute(
                    """
                    SELECT activation_id, version_id FROM rule_activations
                    ORDER BY sequence DESC LIMIT 1
                    """
                ).fetchone()
                active_version_id = activation["version_id"] if activation else None
                if activation is None:
                    raise ValueError(
                        "활성 규칙 버전을 먼저 만들어야 초안을 등록할 수 있습니다."
                    )
                if (
                    base_activation_id is not None
                    and base_activation_id != activation["activation_id"]
                ):
                    raise ActivationConflictError(
                        "초안을 만들 때 지정한 활성화 이력이 현재와 다릅니다."
                    )
                if base_version_id is None:
                    base_version_id = active_version_id
                if base_version_id is not None:
                    base = connection.execute(
                        "SELECT status FROM rule_versions WHERE version_id = ?",
                        (base_version_id,),
                    ).fetchone()
                    if base is None or base["status"] != "validated":
                        raise ValueError(
                            "초안의 기준 버전이 검증된 규칙 버전이 아닙니다."
                        )
                    if base_version_id != active_version_id:
                        raise ActivationConflictError(
                            "초안을 만들 때 지정한 버전이 현재 활성 버전과 다릅니다."
                        )
                connection.execute(
                    """
                    INSERT INTO rule_versions (
                        version_id, base_version_id, base_activation_id, status,
                        author_kind, created_by, created_at, content_sha256
                    ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        base_version_id,
                        activation["activation_id"] if activation else None,
                        author_kind,
                        created_by,
                        _now(),
                        content_sha,
                    ),
                )
                self._insert_rules(connection, version_id, ordered)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return version_id

    def validate_draft(
        self,
        version_id: str,
        reporter: Callable[[RulesSnapshot], Mapping[str, Any]],
    ) -> str:
        draft = self._snapshot_any(version_id)
        report = reporter(draft)
        if not isinstance(report, Mapping) or report.get("passed") is not True:
            raise ValueError(
                "회귀 검증 보고서가 통과하지 않아 규칙을 검증할 수 없습니다."
            )
        report_version, report_sha, report_activation = (
            report.get("version_id"),
            report.get("content_sha256"),
            report.get("base_activation_id"),
        )
        if (
            not {"version_id", "content_sha256", "base_activation_id"} <= report.keys()
            or report_version != draft.version_id
            or report_sha != draft.content_sha256
        ):
            raise ValueError(
                "회귀 검증 보고서가 검증 중인 규칙 내용에 연결되지 않았습니다."
            )
        encoded = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        report_sha = hashlib.sha256(encoded).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    """
                    SELECT status, base_activation_id, content_sha256,
                           validation_report_sha256
                    FROM rule_versions WHERE version_id = ?
                    """,
                    (version_id,),
                ).fetchone()
                if current is None or current["status"] != "draft":
                    raise ValueError("검증할 수 있는 draft 규칙 버전이 아닙니다.")
                if current["content_sha256"] != draft.content_sha256:
                    raise ValueError("규칙 초안이 검증 중 변경됐습니다.")
                self._snapshot_any(version_id)
                active = connection.execute(
                    """
                    SELECT activation_id FROM rule_activations
                    ORDER BY sequence DESC LIMIT 1
                    """
                ).fetchone()
                current_activation_id = active["activation_id"] if active else None
                if (
                    report_activation != current["base_activation_id"]
                    or current["base_activation_id"] != current_activation_id
                ):
                    raise ActivationConflictError(
                        "검증 보고서의 기준 활성화가 현재와 다릅니다."
                    )
                if current["validation_report_sha256"] is not None:
                    if current["validation_report_sha256"] != report_sha:
                        raise ValueError(
                            "초안의 저장된 검증 보고서가 이미 다른 결과와 연결되어 있습니다."
                        )
                    connection.commit()
                    return report_sha
                connection.execute(
                    """
                    UPDATE rule_versions
                    SET status = 'validated', validation_report_sha256 = ?
                    WHERE version_id = ?
                    """,
                    (report_sha, version_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return report_sha

    def activate(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
        expected_activation_id: str | None,
    ) -> str:
        return self._activate(
            version_id,
            actor=actor,
            reason=reason,
            expected_activation_id=expected_activation_id,
            check_base=True,
        )

    def _activate(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
        expected_activation_id: str | None,
        check_base: bool,
    ) -> str:
        if not actor.strip() or not reason.strip():
            raise ValueError("활성화 담당자와 사유를 입력해야 합니다.")
        activation_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute(
                    "SELECT status, base_version_id, base_activation_id FROM rule_versions WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
                if version is None or version["status"] != "validated":
                    raise ValueError("활성화에는 검증된 규칙 버전이 필요합니다.")
                active = connection.execute(
                    """
                    SELECT activation_id, version_id FROM rule_activations
                    ORDER BY sequence DESC LIMIT 1
                    """
                ).fetchone()
                current_id = active["activation_id"] if active else None
                current_version = active["version_id"] if active else None
                if current_id != expected_activation_id:
                    raise ActivationConflictError(
                        "활성화 상태가 초안 검증 후 변경됐습니다."
                    )
                if check_base:
                    if version["base_activation_id"] != current_id:
                        raise ActivationConflictError(
                            "초안이 현재 활성화 이력을 기준으로 만들어지지 않았습니다."
                        )
                    if version["base_version_id"] != current_version:
                        raise ActivationConflictError(
                            "초안의 기준 버전이 현재 규칙 버전과 다릅니다."
                        )
                elif (
                    connection.execute(
                        "SELECT 1 FROM rule_activations WHERE version_id = ? LIMIT 1",
                        (version_id,),
                    ).fetchone()
                    is None
                ):
                    raise ValueError(
                        "복귀하려는 규칙에는 이전 활성화 이력이 필요합니다."
                    )
                connection.execute(
                    """
                    INSERT INTO rule_activations (
                        activation_id, version_id, previous_version_id,
                        activated_by, activated_at, reason
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        activation_id,
                        version_id,
                        current_version,
                        actor,
                        _now(),
                        reason,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return activation_id

    def rollback(
        self,
        version_id: str,
        *,
        actor: str,
        reason: str,
        expected_activation_id: str | None,
    ) -> str:
        if not reason.strip():
            raise ValueError("복귀 사유를 입력해야 합니다.")
        return self._activate(
            version_id,
            actor=actor,
            reason=f"rollback: {reason}",
            expected_activation_id=expected_activation_id,
            check_base=False,
        )

    def _snapshot_any(self, version_id: str) -> RulesSnapshot:
        """Load one draft for its injected regression reporter."""
        with self._connect(readonly=True) as connection:
            version = connection.execute(
                "SELECT content_sha256 FROM rule_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            if version is None:
                raise ValueError(f"규칙 버전을 찾을 수 없습니다: {version_id}")
            rows = connection.execute(
                """
                SELECT rule_id, scope, target_code, match_mode, pattern,
                       flags, position, enabled
                FROM routing_rules WHERE version_id = ?
                ORDER BY scope, position, target_code, rule_id
                """,
                (version_id,),
            ).fetchall()
        rules = tuple(
            RoutingRule(
                scope=row["scope"],
                target_code=row["target_code"],
                match_mode=row["match_mode"],
                pattern=row["pattern"],
                flags=row["flags"],
                position=row["position"],
                enabled=bool(row["enabled"]),
                rule_id=row["rule_id"],
            )
            for row in rows
        )
        _, computed_sha = self._content(rules)
        if computed_sha != version["content_sha256"]:
            raise ValueError(f"초안의 규칙 내용 지문이 다릅니다: {version_id}")
        return RulesSnapshot(version_id, computed_sha, rules)


class ActivationConflictError(RuntimeError):
    """Raised when a draft's captured activation is no longer current."""


def _activation(row: sqlite3.Row) -> Activation:
    return Activation(
        activation_id=row["activation_id"],
        version_id=row["version_id"],
        previous_version_id=row["previous_version_id"],
        activated_by=row["activated_by"],
        activated_at=row["activated_at"],
        reason=row["reason"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
