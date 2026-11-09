"""Recalculate only explicitly supported HA daily charges for comparison."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from app.ingestion.common_rules import BUSINESS_RULES
from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_format_extension import HA_ROLES, load_formats
from experimental.u_10o0o.advanced_role_context import FACTS, digest
from experimental.u_10o0o.advanced_role_facts import money
from experimental.u_10o0o.advanced_search import KST

REQUIRED_RULES = {
    text
    for text in BUSINESS_RULES
    if text.startswith(
        (
            "기간은 이용 터미널",
            "단가는 장비 한 대당 USD",
            "조건 변경은 계약운영책임자의 서명",
        )
    )
}
AMENDMENT = re.compile(r"개정표|개정 부속표|요율 개정")
CASES = {
    "storage": ("보관료", "보관료 정산액", "보관료 변경 승인 없음"),
    "late_return": ("반납지연료", "반납지연료", "실제 반납 확인"),
}
EVENTS = {
    "양하 원시기록": "실제",
    "적입 반출기록": "실제",
    "공장비 인수증": "실제 증빙",
}


def by_label(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        if (
            row["kind"] == "table_row"
            and row.get("review_status") == "parsed"
            and key in row["fields"]
        ):
            grouped[row["fields"][key]].append(row)
    return {label: rows[0] for label, rows in grouped.items() if len(rows) == 1}


def narrative(rows):
    return " ".join(row.get("text", "") for row in rows if row["kind"] == "narrative")


def tariff(fields):
    free = re.fullmatch(r"(?:기본 )?(\d+)일", fields.get("무료기간", ""))
    band = re.fullmatch(r"1[~～](\d+)일", fields.get("첫 유료구간", ""))
    first = re.fullmatch(r"일 (\d+(?:\.\d+)?)달러", fields.get("첫 구간 단가", ""))
    following = re.fullmatch(
        r"(\d+)일째부터 일 (\d+(?:\.\d+)?)달러", fields.get("다음 구간", "")
    )
    if not (free and band and first and following):
        return None
    if int(band[1]) < 1 or int(following[1]) != int(band[1]) + 1:
        return None
    return int(free[1]), int(band[1]), Decimal(first[1]), Decimal(following[2])


def actual_date(event, label):
    row = event.get(label)
    if not row:
        return None
    values = row["fields"]
    if values["성격"] != EVENTS[label] or values["확인 상태"] != "확인 완료":
        return None
    raw = values["날짜·접수값"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return None


def charge(start, end, terms):
    days = (end - start).days
    if days < 0:
        raise ValueError("invalid_actual_event_order")
    free, first_count, first_rate, next_rate = terms
    paid = max(0, days - free)
    value = min(paid, first_count) * first_rate + max(0, paid - first_count) * next_rate
    return days, paid, value


def audited_case(case, sources, rows_by_source, rules):
    locations = [sources[role]["source_id"] for role in HA_ROLES]
    contract, event, settlement = (rows_by_source[sid] for sid in locations)
    scopes = by_label(contract, "항목")
    terms = by_label(contract, "비용")
    events = by_label(event, "기록")
    reported = by_label(settlement, "항목")
    scope = {label: row["fields"].get("기재 내용", "") for label, row in scopes.items()}
    equipment_ids = {
        key for key in identifiers(scope.get("예약·장비", "")) if key.startswith("HL-")
    }
    notes = [narrative(rows) for rows in (contract, event, settlement)]
    rule_ids = {key for row in contract for key in row.get("context_rule_ids", ())}
    rules_on_page = {
        rules[key]["text"] for key in rule_ids if locations[0] in rules[key]["sources"]
    }
    known_scope = False
    if len(equipment_ids) == 1:
        equipment = next(iter(equipment_ids))
        specs = {"20GP", "40GP"} & set(equipment.split("-"))
        known_scope = (
            len(specs) == 1
            and next(iter(specs)) in scope.get("적용 대상", "")
            and case in identifiers(scope.get("예약·장비", ""))
            and all({case, equipment} <= set(identifiers(text)) for text in notes)
            and scope.get("통화·수량") == "USD / 장비 1대"
            and "계약운영책임자" in scope.get("서명 확인", "")
            and "화주 계약담당" in scope.get("서명 확인", "")
            and "원계약" in notes[0]
            and len(REQUIRED_RULES) == 3
            and REQUIRED_RULES <= rules_on_page
        )
    discharge = actual_date(events, "양하 원시기록")
    gate_out = actual_date(events, "적입 반출기록")
    returned = actual_date(events, "공장비 인수증")
    amended = any(AMENDMENT.search(text) for text in notes)
    results = []
    for cost, (term_label, report_label, reason_text) in CASES.items():
        item = {"family": "HA", "case_id": case, "cost": cost, "source_ids": locations}
        report = reported.get(report_label)
        term = terms.get(term_label)
        start, end = (
            (discharge, gate_out) if cost == "storage" else (gate_out, returned)
        )
        if not known_scope:
            reason = "contract_equipment_currency_or_rules_unverified"
        elif amended:
            reason = "amendment_requires_review"
        elif not start or not end:
            reason = "actual_event_unverified"
        elif not report or report["fields"].get("처리 근거") != reason_text:
            reason = "adjustment_or_correction_requires_review"
        elif term is None or (parsed := tariff(term["fields"])) is None:
            reason = "tariff_band_unverified"
        else:
            try:
                included, paid, calculated = charge(start, end, parsed)
            except ValueError:
                results.append(
                    {**item, "status": "error", "reason": "invalid_actual_event_order"}
                )
                continue
            observed = money(report["fields"]["금액"])
            if observed["state"] != "reported":
                reason = "reported_amount_unconfirmed"
            else:
                results.append(
                    {
                        **item,
                        "status": "matched"
                        if Decimal(observed["value"]) == calculated
                        else "mismatch",
                        "calculated_usd": format(calculated, "f"),
                        "reported_usd": observed["value"],
                        "included_days": included,
                        "paid_days": paid,
                        "tariff_record": term["record_id"],
                        "reported_record": report["record_id"],
                        "event_records": [
                            events[label]["record_id"]
                            for label in (
                                ("양하 원시기록", "적입 반출기록")
                                if cost == "storage"
                                else ("적입 반출기록", "공장비 인수증")
                            )
                        ],
                        "contract_scope_records": [
                            scopes[label]["record_id"]
                            for label in (
                                "예약·장비",
                                "적용 대상",
                                "서명 확인",
                                "통화·수량",
                            )
                        ],
                        "contract_rule_ids": sorted(
                            key
                            for key in rule_ids
                            if rules[key]["text"] in REQUIRED_RULES
                        ),
                    }
                )
                continue
        results.append({**item, "status": "deferred", "reason": reason})
    return results


def hg_deferred(facts_dir, manifest_sha):
    path = facts_dir / "role_facts.jsonl"
    summary = json.loads((facts_dir / "summary.json").read_bytes())
    if summary["curation_manifest_sha256"] != manifest_sha or summary[
        "role_facts_sha256"
    ] != digest(path):
        raise ValueError("HG 역할별 필드와 정제 입력의 지문이 다릅니다.")
    tiers = defaultdict(list)
    for fact in read_rows(path):
        if fact["fact"] in ("first_rate_tier", "following_rate_tier"):
            tiers[fact["case_id"], fact["cost_kind"]].append(fact)
    for (case, cost), rows in sorted(tiers.items()):
        yield {
            "family": "HG",
            "case_id": case,
            "cost": cost,
            "source_ids": sorted({r["source"]["source_id"] for r in rows}),
            "status": "deferred",
            "reason": "contract_currency_missing"
            if any(r["currency"] is None for r in rows)
            else "outside_ha_calculation_scope",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_sha = sha256((args.curated / "manifest.json").read_bytes()).hexdigest()
    _, ha, ha_rows, _, _ = load_formats(args.curated)
    rules = {r["rule_id"]: r for r in read_rows(args.curated / "rules.jsonl")}
    results = list(hg_deferred(args.facts, manifest_sha))
    for case, sources in sorted(ha.items()):
        results.extend(audited_case(case, sources, ha_rows, rules))
    counts = defaultdict(Counter)
    for row in results:
        counts[f"{row['family']}:{row['cost']}"][row["status"]] += 1
    args.output.mkdir(parents=True, exist_ok=False)
    path = args.output / "charges.jsonl"
    write_rows(path, results)
    summary = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "curation_manifest_sha256": manifest_sha,
        "charges_sha256": digest(path),
        "cases": {
            "HG": len({r["case_id"] for r in results if r["family"] == "HG"}),
            "HA": len(ha),
        },
        "counts_by_family_and_cost": dict(counts),
        "reasons": dict(
            Counter(f"{r['family']}:{r['reason']}" for r in results if "reason" in r)
        ),
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (args.output / "summary.json").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
