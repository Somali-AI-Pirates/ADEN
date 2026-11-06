import json
import subprocess
from pathlib import Path

from app.ingestion.curation import read_rows

ROOT = Path(__file__).resolve().parents[2]
CURATED = ROOT / "data" / "curated"


def record_rows(dataset, book, page=1):
    return [row for row in read_rows(CURATED / dataset / "records" / (book + ".jsonl"))
            if row["source_id"] == f"{dataset}:{book}:p{page}"]


def test_all_tiers_have_complete_source_snapshots():
    for tier, docs, pages in (("basic", 15, 360), ("intermediate", 50, 3750), ("advanced", 120, 18000)):
        root = CURATED / tier
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((root / "validation_report.json").read_text(encoding="utf-8"))
        assert len(manifest["identity"]["documents"]) == docs
        assert report["pages"] == pages
        assert report["body_blocks"] == report["covered_blocks"]
        assert len(list(root.joinpath("records").glob("*.jsonl"))) == docs
        assert all(item["file_name"].endswith(".pdf") for item in manifest["identity"]["documents"])
        assert report["quality_gate"]["full_semantic_review"] == "pending"


def test_basic_rules_are_visible_tracked_knowledge_with_conditions():
    rules = {row["rule_id"]: row for row in read_rows(CURATED / "basic" / "rules.jsonl")}
    assert len(rules) == 17
    after = rules["basic.rule.si-deadline-from-2026-11-01"]
    assert after["parameters"]["conditions"][0]["field"] == "scheduled_departure_date"
    assert after["parameters"]["effect"]["time"] == "12:00"
    assert after["scope"] == "linked_source_context" and after["trust"] == "retrieved_evidence"


def test_intermediate_preserves_pending_version_and_unknown_money():
    rows = record_rows("intermediate", "IS38")
    pending = next(row for row in rows if row.get("fields", {}).get("처리 기록") == "IS38-001-06")
    assert pending["fields"]["요청·회신 내용"] == "v2 검토본"
    assert pending["states"]["상태·기록일"].startswith("미승인 유지")
    bills = record_rows("intermediate", "IS45")
    unknown = next(row for row in bills if row.get("fields", {}).get("기재 금액") == "USD미산출")
    zero = next(row for row in bills if row.get("fields", {}).get("기재 금액") == "USD0")
    assert unknown["amounts"][0]["value"] is None and zero["amounts"][0]["value"] == 0
    rules = list(read_rows(CURATED / "intermediate" / "rules.jsonl"))
    rate = next(row for row in rules if row.get("parameters", {}).get("new_daily_rate") == 74)
    assert rate["parameters"]["date_basis"] == "actual_discharge_date"
    assert rate["parameters"]["equipment_type"] == "40GP"
    assert any(row["case_id"].startswith("GR26-") for row in read_rows(CURATED / "intermediate" / "cases.jsonl"))


def test_advanced_contract_variants_do_not_share_wrong_period_rules():
    rules = {row["rule_id"]: row for row in read_rows(CURATED / "advanced" / "rules.jsonl")}
    rows_a = record_rows("advanced", "AS061", 1)
    rows_b = record_rows("advanced", "AS061", 2)
    a = next(row for row in rows_a if row.get("cost_terms", {}).get("cost") == "보관료")
    b = next(row for row in rows_b if row.get("cost_terms", {}).get("cost") == "보관료")
    pa = next(rules[key]["parameters"] for key in a["context_rule_ids"] if rules[key]["kind"] == "period_rule")
    pb = next(rules[key]["parameters"] for key in b["context_rule_ids"] if rules[key]["kind"] == "period_rule")
    assert pa["storage"]["start_offset_days"] == 0 and pa["storage"]["day_basis"] == "weekdays"
    assert pa["return_delay"]["start_offset_days"] == 2 and pa["return_delay"]["end_inclusive"] is False
    assert pb["storage"]["start_offset_days"] == 1 and pb["storage"]["day_basis"] == "calendar"
    assert pb["return_delay"]["end_inclusive"] is True
    assert a["cost_terms"]["rate_tiers"][0]["daily_rate"] == 25


def test_advanced_corrections_and_partial_settlement_remain_distinct():
    correction = record_rows("advanced", "AS003")
    storage = next(row for row in correction if row.get("fields", {}).get("항목") == "보관료")
    unchanged = next(row for row in correction if row.get("fields", {}).get("항목") == "장비반납지연료")
    assert storage["fields"]["수정 USD"] == "280"
    assert unchanged["fields"]["수정 USD"] == "210"
    assert any(row["kind"] == "external_quotation" for row in correction)
    partial = record_rows("advanced", "AS006")
    unresolved = next(row for row in partial if row.get("fields", {}).get("항목") == "장비반납지연료")
    assert unresolved["amounts"][0]["value"] is None
    assert "확인 대기" in unresolved["states"]["상태·근거"]


def test_git_tracks_curated_data_but_excludes_raw_and_search_results():
    included = ["data/curated/basic/rules.jsonl", "data/curated/intermediate/records/IS12.jsonl",
                "data/curated/advanced/sources.jsonl"]
    excluded = ["data/advanced/documents/AS001.pdf", "data/advanced/evaluation/development.json",
                "artifacts/knowledge/search/advanced/example/search_records.jsonl"]
    for name in included:
        assert subprocess.run(["git", "check-ignore", "--no-index", "-q", name], cwd=ROOT, check=False).returncode == 1
    for name in excluded:
        assert subprocess.run(["git", "check-ignore", "--no-index", "-q", name], cwd=ROOT, check=False).returncode == 0
