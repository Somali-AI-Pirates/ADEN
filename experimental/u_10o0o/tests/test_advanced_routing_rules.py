import sqlite3
from types import SimpleNamespace

import pytest

from app.generation.context import build_context
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clause_binding import ROLE_TERMS, roles_in
from experimental.u_10o0o.advanced_pair_context import (
    pack_pair_context,
    requested_roles,
)
from experimental.u_10o0o.advanced_patterns import location
from experimental.u_10o0o.advanced_routing_rules import (
    ActivationConflictError,
    RoutingRule,
    RoutingRuleStore,
    RulesSnapshot,
    legacy_rules,
    normalize,
)
from experimental.u_10o0o.advanced_target_lookup import (
    DATE_CUES,
    ROLE_CUES,
    TargetLookup,
)
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens


def report(snapshot, *, base_activation_id, passed=True):
    return {
        "passed": passed,
        "version_id": snapshot.version_id,
        "content_sha256": snapshot.content_sha256,
        "base_activation_id": base_activation_id,
        "fixture": "unit-test",
    }


def activate_seed(store):
    seed_id = store.initialize(legacy_rules())
    store.validate_draft(
        seed_id, lambda snapshot: report(snapshot, base_activation_id=None)
    )
    activation_id = store.activate(
        seed_id,
        actor="test-admin",
        reason="initial test activation",
        expected_activation_id=None,
    )
    return seed_id, activation_id, store.snapshot()


def test_seed_rules_match_current_target_and_clause_regexes(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    seed_id, _, rules = activate_seed(store)
    samples = (
        "계약 요율과 무료기간 조건을 확인해 주세요",
        "실제 반출일과 청구 금액을 알려 주세요",
        "현장 사건 수신대장과 수납 인계 기록",
        "전산 입력일이 다릅니다",
        "실제 2026-05-11 반출된 건의 비용 청구 명세",
    )

    for text in samples:
        expected_target_roles = tuple(
            role for role, pattern in ROLE_CUES.items() if pattern.search(text)
        )
        expected_clause_roles = tuple(
            role
            for role, pattern in zip(ROLES, ROLE_TERMS, strict=True)
            if pattern.search(text)
        )
        expected_dates = tuple(
            target for target, pattern in DATE_CUES.items() if pattern.search(text)
        )
        assert rules.match_target_roles(text) == expected_target_roles
        assert roles_in(text, rules) == expected_clause_roles
        assert requested_roles(text, rules) == expected_target_roles
        assert rules.match_target_dates(text) == expected_dates

    assert store.current_activation().version_id == seed_id
    assert store.snapshot(seed_id).content_sha256 == rules.content_sha256
    assert store.initialize(legacy_rules()) == seed_id


def test_interrupted_seed_insertion_rolls_back(tmp_path, monkeypatch):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    insert_rules = store._insert_rules

    def interrupted(connection, version, rules):
        insert_rules(connection, version, rules[:1])
        raise RuntimeError("simulated interrupted seed")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_insert_rules", interrupted)
        with pytest.raises(RuntimeError, match="interrupted seed"):
            store.initialize()

    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT count(*) FROM rule_versions").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM routing_rules").fetchone() == (
            0,
        )
    seed = store.initialize()
    assert store.initialize() == seed


def test_target_lookup_default_and_explicit_rules_keep_role_order(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    _, _, rules = activate_seed(store)
    legacy = TargetLookup.__new__(TargetLookup)
    legacy.rules = None
    configured = TargetLookup.__new__(TargetLookup)
    configured.rules = rules
    text = "요율과 청구액, 승인 및 실제 반출 기록을 확인해 주세요"
    expected = tuple(role for role, cue in ROLE_CUES.items() if cue.search(text))
    assert legacy._requested_roles(text) == expected
    assert configured._requested_roles(text) == expected


def test_literal_rules_normalize_unicode_and_whitespace(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    _, activation_id, baseline = activate_seed(store)
    phrase = RoutingRule(
        scope="clause_role",
        target_code=ROLES[3],
        match_mode="literal_phrase",
        pattern=normalize("請求 金額"),
        position=100,
    )
    draft_id = store.create_draft(
        (*baseline.rules, phrase),
        author_kind="admin",
        created_by="test-admin",
        base_version_id=baseline.version_id,
    )
    store.validate_draft(
        draft_id,
        lambda snapshot: report(snapshot, base_activation_id=activation_id),
    )

    candidate = store.snapshot(draft_id)
    assert candidate.match_clause_roles("請求  金額") == (ROLES[3],)
    assert normalize("ＡＢＣ　안녕하세요") == "abc 안녕하세요"


def test_legacy_regex_keeps_original_matching_semantics():
    rules = RulesSnapshot(
        "version",
        "sha",
        (
            RoutingRule(
                scope="target_role",
                target_code=ROLES[0],
                match_mode="legacy_regex",
                pattern=r"A  B",
            ),
            RoutingRule(
                scope="target_role",
                target_code=ROLES[1],
                match_mode="literal_phrase",
                pattern=normalize("A  B"),
                position=1,
            ),
        ),
    )
    assert rules.match_target_roles("A  B") == (ROLES[0], ROLES[1])
    assert rules.match_target_roles("a b") == (ROLES[1],)


def test_failed_or_unbound_validation_report_cannot_be_activated(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    seed_id, activation_id, baseline = activate_seed(store)
    changed = RoutingRule(
        scope="target_role",
        target_code=ROLES[3],
        match_mode="literal_phrase",
        pattern=normalize("invoice amount"),
        position=100,
    )
    draft_id = store.create_draft(
        (*baseline.rules, changed),
        author_kind="admin",
        created_by="test-admin",
        base_version_id=seed_id,
    )
    with pytest.raises(ValueError, match="통과하지"):
        store.validate_draft(
            draft_id,
            lambda snapshot: report(
                snapshot, base_activation_id=activation_id, passed=False
            ),
        )
    with pytest.raises(ValueError, match="연결되지"):
        store.validate_draft(
            draft_id,
            lambda snapshot: (
                report(snapshot, base_activation_id=activation_id)
                | {"content_sha256": "0" * 64}
            ),
        )
    with pytest.raises(ValueError, match="검증된"):
        store.activate(
            draft_id,
            actor="test-admin",
            reason="unvalidated",
            expected_activation_id=activation_id,
        )


def test_activation_is_compare_and_swap_and_rollback_appends_history(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    seed_id, seed_activation_id, seed_snapshot = activate_seed(store)
    first = RoutingRule(
        scope="target_role",
        target_code=ROLES[3],
        match_mode="literal_phrase",
        pattern=normalize("invoice amount"),
        position=100,
    )
    second = RoutingRule(
        scope="target_role",
        target_code=ROLES[2],
        match_mode="literal_phrase",
        pattern=normalize("approval history"),
        position=100,
    )
    first_id = store.create_draft(
        (*seed_snapshot.rules, first),
        author_kind="admin",
        created_by="test-admin",
    )
    stale_id = store.create_draft(
        (*seed_snapshot.rules, second),
        author_kind="llm",
        created_by="test-model",
    )
    store.validate_draft(
        first_id,
        lambda snapshot: report(snapshot, base_activation_id=seed_activation_id),
    )
    store.validate_draft(
        stale_id,
        lambda snapshot: report(snapshot, base_activation_id=seed_activation_id),
    )
    first_activation_id = store.activate(
        first_id,
        actor="test-admin",
        reason="validated first",
        expected_activation_id=seed_activation_id,
    )
    with pytest.raises(ActivationConflictError):
        store.activate(
            stale_id,
            actor="test-admin",
            reason="stale activation",
            expected_activation_id=seed_activation_id,
        )

    with pytest.raises(ValueError, match="이전 활성화"):
        store.rollback(
            stale_id,
            actor="test-admin",
            reason="cannot activate stale draft by rollback",
            expected_activation_id=first_activation_id,
        )
    rollback_id = store.rollback(
        seed_id,
        actor="test-admin",
        reason="restore seed",
        expected_activation_id=first_activation_id,
    )
    active = store.current_activation()
    assert active.activation_id == rollback_id
    assert active.version_id == seed_id
    assert active.previous_version_id == first_id
    assert active.activation_id != seed_activation_id
    assert store.snapshot().version_id == seed_id
    with pytest.raises(ActivationConflictError):
        store.create_draft(
            seed_snapshot.rules,
            author_kind="admin",
            created_by="test-admin",
            base_version_id=seed_id,
            base_activation_id=seed_activation_id,
        )


def test_store_connections_are_read_only_and_validated_rows_are_immutable(tmp_path):
    store = RoutingRuleStore(tmp_path / "routing.sqlite3")
    seed_id, _, _ = activate_seed(store)
    with store._connect(readonly=True) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "DELETE FROM routing_rules WHERE version_id = ?", (seed_id,)
            )
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE routing_rules SET enabled = 0 WHERE version_id = ?",
                (seed_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT INTO routing_rules (
                    version_id, rule_id, scope, target_code, match_mode, pattern,
                    flags, position, enabled
                ) VALUES (?, 'new-rule', 'target_role', ?, 'literal_phrase',
                          'new phrase', 0, 100, 1)
                """,
                (seed_id, ROLES[0]),
            )


def test_pair_packer_accepts_explicit_targets_and_reserves_general_page():
    first = {
        "file_name": "HG-100001.pdf",
        "page": 1,
        "text": "첫 대상 역할에 필요한 원문 기록입니다.",
        "chunk_id": "first",
    }
    second = {
        "file_name": "HG-100001.pdf",
        "page": 2,
        "text": "첫 대상의 두 번째 요청 기록입니다.",
        "chunk_id": "second",
    }
    general = {
        "file_name": "IS-200001.pdf",
        "page": 3,
        "text": "업무 적용 조건과 예외를 설명하는 문서입니다.",
        "chunk_id": "general",
    }
    ranked = [(first, 3.0), (second, 2.0), (general, 1.0)]
    lookup = SimpleNamespace(
        page_roles={
            location(first): ("HG000001", ROLES[0]),
            location(second): ("HG000001", ROLES[1]),
        }
    )
    targets = {"HG000001": (ROLES[0], ROLES[1])}
    baseline = build_context(
        _context_candidates(ranked[:2]),
        8,
        10_000,
        _count_tokens,
    )

    old_order, old_context, old_route = pack_pair_context(
        "질문에는 저장된 단서가 없습니다.",
        ranked,
        lookup,
        baseline,
        targets=targets,
        context_tokens=10_000,
    )
    reserved_order, reserved_context, reserved_route = pack_pair_context(
        "질문에는 저장된 단서가 없습니다.",
        ranked,
        lookup,
        baseline,
        targets=targets,
        context_tokens=10_000,
        reserve_general=True,
    )

    assert old_route == "covered"
    assert len(old_context.final_chunks) == 2
    assert reserved_route == "repacked_with_general"
    assert [row[0]["page"] for row in reserved_order[:3]] == [1, 2, 3]
    assert [item.chunk.page for item in reserved_context.final_chunks] == [1, 2, 3]
