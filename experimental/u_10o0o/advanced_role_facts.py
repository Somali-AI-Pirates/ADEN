"""Normalize reported facts on the five indexed HG source pages."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES

CASE_INDEX = Path("artifacts/u_10o0o/advanced-curation/hg-case-sources-20261109")
COSTS = {
    "보관료": "storage",
    "반납지연료": "late_return",
    "장비반납지연료": "late_return",
}
EVENTS = {
    "실제 양하": "actual_discharge_date",
    "실제 적입 장비 반출": "actual_gate_out_date",
    "실제 지정 장치장 반납": "actual_designated_return_date",
    "반납 예약 예정": "scheduled_return_date",
    "전산 입력일": "system_entry_date",
}
COMPARISON_FIELDS = {
    "관련 계약": "comparison_contract_id",
    "대상 장비": "comparison_equipment",
    "대조 사항": "comparison_subject",
    "기록 번호": "comparison_record_id",
}
APPROVAL = {
    "승인 확인": "approval_confirmed",
    "추가 변경 없음; 최초 계약 적용": "original_contract_recorded",
    "승인 서명 미등록": "signature_unregistered",
    "효력 조건 미충족": "effect_condition_unmet",
    "요청 반려": "request_rejected",
    "후일 서명": "later_signature",
}
UNRESOLVED = {
    "미결": "pending",
    "미확정": "unconfirmed",
    "미산출": "uncomputed",
    "공란": "blank",
}
RETURN_EVIDENCE = {
    "실제 인수증 미수신": "receipt_missing",
    "지정 외 야드 반입증만 있음": "non_designated_yard_only",
}
RECEIPT_FIELDS = {
    "보관료": "receipt_storage_charge",
    "장비반납지연료": "receipt_late_return_charge",
    "거래 합계": "receipt_total",
    "수납": "received_amount",
    "미수 또는 환급": "balance_amount",
}
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"


def typed(value=None, *, unit=None, currency=None, state="reported", reason=None):
    return {
        "value": value,
        "unit": unit,
        "currency": currency,
        "state": state,
        "review_reason": reason,
    }


def review(reason, value=None, *, unit=None):
    return typed(value, unit=unit, state="needs_review", reason=reason)


def calendar_value(raw):
    if raw in UNRESOLVED:
        return typed(state=UNRESOLVED[raw])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return typed(date.fromisoformat(raw).isoformat())
        except ValueError:
            pass
    return review("invalid_or_ambiguous_date")


def money(raw):
    if raw in UNRESOLVED:
        return typed(state=UNRESOLVED[raw])
    if match := re.fullmatch(rf"USD\s*({NUMBER})", raw):
        return typed(format(Decimal(match[1].replace(",", "")), "f"), currency="USD")
    if re.fullmatch(NUMBER, raw):
        return review("currency_missing", format(Decimal(raw.replace(",", "")), "f"))
    return review("unrecognized_amount")


def exact_count(raw, suffix=""):
    if match := re.fullmatch(rf"(\d+)\s*{suffix}", raw):
        return typed(int(match[1]), unit="day")
    return review("unrecognized_day_count")


def contract_facts(row):
    fields = row["fields"]
    cost = COSTS.get(fields.get("비용"))
    if cost is None or not {"기본 무료", "첫 유료 구간", "다음 구간"} <= fields.keys():
        return []
    result = [
        ("base_free_days", "기본 무료", cost, exact_count(fields["기본 무료"], "일"))
    ]
    tiers = row.get("cost_terms", {}).get("rate_tiers", ())
    for i, label in enumerate(("첫 유료 구간", "다음 구간")):
        if len(tiers) == 2:
            value = review("currency_missing", tiers[i], unit="per_day")
        else:
            value = review("unrecognized_rate_band")
        result.append(
            ("first_rate_tier" if i == 0 else "following_rate_tier", label, cost, value)
        )
    return result


def event_facts(fields):
    event = EVENTS.get(fields.get("항목"))
    if not event or "날짜 또는 상태" not in fields:
        return []
    raw = fields["날짜 또는 상태"]
    if event == "actual_designated_return_date" and raw in RETURN_EVIDENCE:
        value = typed(state=RETURN_EVIDENCE[raw])
    else:
        value = calendar_value(raw)
    return [(event, "날짜 또는 상태", None, value)]


def comparison_facts(fields):
    label = fields.get("항목")
    if "확인 기록" not in fields:
        return []
    raw = fields["확인 기록"]
    if label in COMPARISON_FIELDS:
        name, value = COMPARISON_FIELDS[label], typed(raw)
    elif label == "대조 기준":
        name, value = "comparison_recorded_at", review("invalid_or_ambiguous_timestamp")
        if match := re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) KST", raw):
            try:
                value = typed(
                    datetime.fromisoformat(match[1]).isoformat(timespec="minutes"),
                    unit="KST",
                )
            except ValueError:
                pass
    elif label == "승인 대조":
        name = "approval_record"
        value = (
            typed(APPROVAL[raw])
            if raw in APPROVAL
            else review("unrecognized_approval_record")
        )
    elif label == "개정 효력일":
        name, value = "recorded_amendment_effective_date", calendar_value(raw)
    elif label == "새 보관 요율":
        name, value = (
            "amendment_storage_rate",
            review("currency_or_rate_band_unspecified"),
        )
    else:
        return []
    return [(name, "확인 기록", None, value)]


def billing_facts(fields):
    if {"비용", "산입/무료/유료일", "확정 금액"} <= fields.keys():
        cost = COSTS.get(fields["비용"])
        if cost is None:
            return []
        raw = fields["산입/무료/유료일"]
        if match := re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", raw):
            included, free, paid = map(int, match.groups())
            days = [typed(number, unit="day") for number in (included, free, paid)]
            if included != free + paid:
                days = [
                    review("inconsistent_day_breakdown", value["value"], unit="day")
                    for value in days
                ]
        elif raw in UNRESOLVED:
            days = [typed(unit="day", state=UNRESOLVED[raw])] * 3
        else:
            days = [review("unrecognized_day_breakdown", unit="day")] * 3
        return [
            (name, "산입/무료/유료일", cost, value)
            for name, value in zip(
                ("billed_included_days", "billed_free_days", "billed_paid_days"),
                days,
                strict=True,
            )
        ] + [("billed_amount", "확정 금액", cost, money(fields["확정 금액"]))]

    label = fields.get("항목")
    if not {"보관료", "반납지연료"} <= fields.keys():
        return []
    result = []
    for cost in ("보관료", "반납지연료"):
        raw = fields[cost]
        if label == "무료 추가":
            name, value = "reported_extra_free_days", exact_count(raw)
        elif label == "상한":
            name = "reported_cap_amount"
            value = typed(state="not_applicable") if raw == "없음" else money(raw)
        elif label == "정액 감액":
            name, value = "reported_reduction_amount", money(raw)
        elif label == "전액 면제":
            name = "waiver_record"
            states = {"아니오": "no", "승인": "approval_recorded"}
            if raw == "없음":
                value = typed(state="not_applicable")
            else:
                value = (
                    typed(states[raw])
                    if raw in states
                    else review("unrecognized_waiver_record")
                )
        else:
            return []
        result.append((name, cost, COSTS[cost], value))
    return result


def receipt_facts(fields):
    label = fields.get("구분")
    if "금액/상태" not in fields:
        return []
    raw = fields["금액/상태"]
    if label == "청구 상태":
        return [("receipt_billing_record", "금액/상태", None, typed(raw))]
    if label not in RECEIPT_FIELDS:
        return []
    name = RECEIPT_FIELDS[label]
    if label == "미수 또는 환급" and raw.startswith("환급 검토 "):
        name, amount = "refund_review_amount", raw.removeprefix("환급 검토 ")
    else:
        amount = raw
    return [(name, "금액/상태", COSTS.get(label), money(amount))]


def load_source_links(case_index, curated_manifest_sha):
    path = case_index / "case_sources.jsonl"
    summary = json.loads((case_index / "summary.json").read_bytes())
    if (
        summary["curation_manifest_sha256"] != curated_manifest_sha
        or summary["case_sources_sha256"] != sha256(path.read_bytes()).hexdigest()
    ):
        raise ValueError("사건 인덱스의 지문 또는 정제 스냅샷이 다릅니다.")
    links = {}
    roles = defaultdict(list)
    for row in read_rows(path):
        case, role, source_id = row["case_id"], row["record_role"], row["source_id"]
        if source_id in links or row["match_rule"] != "page_heading_case_id":
            raise ValueError(
                "정제 입력에 중복 또는 확인되지 않은 출처 연결이 있습니다."
            )
        links[source_id] = case, role
        roles[case].append(role)
    if (
        len(links) != summary["source_links"]
        or len(roles) != summary["indexed_cases"]
        or any(Counter(values) != Counter(ROLES) for values in roles.values())
    ):
        raise ValueError("사건별 다섯 역할 또는 인덱스 수량이 다릅니다.")
    return links


def normalize_records(curated, links):
    sources = {
        row["source_id"]: row
        for row in read_rows(curated / "sources.jsonl")
        if row["source_id"] in links
    }
    if set(sources) != set(links):
        raise ValueError("사건의 원본 페이지 정보를 찾을 수 없습니다.")

    handlers = (
        contract_facts,
        event_facts,
        comparison_facts,
        billing_facts,
        receipt_facts,
    )
    by_role = dict(zip(ROLES, handlers, strict=True))
    facts, skipped = [], []
    covered, record_ids = set(), set()
    for path in sorted((curated / "records").glob("*.jsonl")):
        for row in read_rows(path):
            source_id = row["source_id"]
            if source_id not in links or row["kind"] != "table_row":
                continue
            case, role = links[source_id]
            source = sources[source_id]
            if (
                row["record_id"] in record_ids
                or row["case_ids"] != [case]
                or source["book_id"] != path.stem
            ):
                raise ValueError("표 행의 사건 또는 원본 위치가 인덱스와 다릅니다.")
            record_ids.add(row["record_id"])
            if row.get("review_status") == "parsed":
                handler = by_role[role]
                values = handler(row if role == ROLES[0] else row["fields"])
            else:
                values = []
            if not values:
                skipped.append(
                    {
                        "case_id": case,
                        "record_role": role,
                        "record_id": row["record_id"],
                        "reason": "unrecognized_table_row",
                    }
                )
                continue
            covered.add(source_id)
            for name, label, cost, value in values:
                facts.append(
                    {
                        "case_id": case,
                        "record_role": role,
                        "fact": name,
                        "cost_kind": cost,
                        **value,
                        "raw_value": row["fields"][label],
                        "column_label": label,
                        "source": {
                            "source_id": source_id,
                            "record_id": row["record_id"],
                            "file_name": source["file_name"],
                            "page": source["page"],
                            "line_start": row["line_start"],
                            "line_end": row["line_end"],
                            "page_rule_refs": row.get("context_rule_ids", []),
                        },
                    }
                )
    if covered != set(links):
        raise ValueError("다섯 역할 중 정규화할 표 행이 없는 출처가 있습니다.")
    return facts, skipped, len(record_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = (args.curated / "manifest.json").read_bytes()
    if json.loads(manifest)["identity"]["dataset"] != "advanced":
        parser.error("Advanced 정제 스냅샷이 필요합니다.")
    manifest_sha = sha256(manifest).hexdigest()
    links = load_source_links(args.case_index, manifest_sha)
    facts, skipped, source_rows = normalize_records(args.curated, links)
    by_role = defaultdict(Counter)
    for fact in facts:
        by_role[fact["record_role"]][fact["state"]] += 1
    args.output.mkdir(parents=True, exist_ok=False)
    result = args.output / "role_facts.jsonl"
    write_rows(result, facts)
    write_rows(args.output / "review_rows.jsonl", skipped)
    summary = {
        "curation_manifest_sha256": manifest_sha,
        "case_sources_sha256": sha256(
            (args.case_index / "case_sources.jsonl").read_bytes()
        ).hexdigest(),
        "role_facts_sha256": sha256(result.read_bytes()).hexdigest(),
        "cases": len({case for case, _ in links.values()}),
        "source_pages": len(links),
        "source_table_rows": source_rows,
        "normalized_facts": len(facts),
        "unrecognized_table_rows": len(skipped),
        "facts_by_role_and_state": dict(by_role),
        "review_reasons": dict(
            Counter(f["review_reason"] for f in facts if f["review_reason"])
        ),
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (args.output / "summary.json").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
