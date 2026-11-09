"""Build a source-checked Advanced work-question pilot and blind candidate set."""

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path
from subprocess import check_output

from app.evaluation.dataset import load_questions, load_references
from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers, readable
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_format_extension import HA_ROLES, load_formats
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    PROFILE,
    load_cases,
    load_profiles,
    normalized,
)
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.context_evidence import _normalize
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR

EVAL = Path(__file__).with_name("evaluation")
PREVIOUS = (
    DATA_DIR / "evaluation/development.json",
    DATA_DIR / "evaluation/additional.json",
    EVAL / "advanced_holdout/advanced_holdout.json",
    EVAL / "advanced_coverage_v2/pilot_references.json",
    EVAL / "advanced_coverage_v2/blind_references.json",
)
FULL = EVAL / "advanced_full_coverage/references.json"
SPECS = {"20GP": "20피트 일반형 컨테이너", "40GP": "40피트 일반형 컨테이너"}
KEYS = (
    "처리 기록",
    "기록번호",
    "업무 기록번호",
    "대조번호",
    "신청·승인 기록",
    "청구번호",
    "원문 번호",
    "계획번호",
    "입금 기록",
    "상태 기록",
    "접수번호",
    "등록번호",
    "인계번호",
    "검산번호",
)
HANDBOOK_ASK = {
    "대상": "무엇을 대상으로 한 건인지",
    "범위·상태": "어디까지 적용되고 현재 어떻게 처리됐는지",
    "상태·기록일": "현재 상태와 기록된 날",
    "기존·요청 근거": "원래 근거나 요청할 때 내세운 근거",
    "요청·회신 내용": "무엇을 요청했고 어떤 회신이 남았는지",
    "업무·의제": "무슨 업무를 검토한 건인지",
    "확인·회신 내용": "확인 결과나 답변으로 남긴 내용",
    "대상 예약·장비": "어느 예약과 장비에 해당하는지",
    "예약·장비": "어느 예약과 장비에 해당하는지",
    "일자·원자료": "언제 처리했고 어떤 원자료를 봤는지",
    "처리 상태": "어디까지 처리됐는지",
    "후속 처리": "그 뒤 어떻게 처리했는지",
    "요청값": "처음 요청한 값",
    "확인값": "대조 후 확인된 값",
    "대상·수량": "무엇이 몇 개 대상인지",
    "화물 자료": "어떤 화물 자료를 남겼는지",
    "청구·상태": "얼마가 청구됐고 현재 상태가 어떤지",
    "산출 단위": "무엇을 기준으로 계산한 값인지",
    "미배분": "아직 배분되지 않은 금액",
    "배분 완료": "이미 배분된 금액",
    "수신 금액": "실제로 받은 금액",
    "연결청구": "어느 청구에 연결했는지",
    "양하 계획": "배에서 내리기로 한 계획",
    "반출 계획": "밖으로 내보내기로 한 계획",
    "변경 사유·권한": "계획이 바뀐 이유와 변경 권한",
}


@dataclass(frozen=True)
class Fact:
    ask: str
    value: str
    record: dict
    support: tuple[dict, ...] = ()


# A scenario asks for related facts needed for one work decision.
HG_SCENARIOS = {
    1: (
        ("현장 인계", ("gate",)),
        ("비용 확인", ("charged",)),
        ("입금 확인", ("received",)),
        ("기한 확인", ("free",)),
    ),
    2: (
        ("입금 대조", ("charged", "received")),
        ("실제와 예정 대조", ("scheduled", "returned")),
        ("계약 변경 확인", ("free", "approval")),
        ("현장 일정 대조", ("discharge", "gate")),
    ),
    3: (
        ("청구 내역 인계", ("paid", "charged", "received")),
        ("계약 조건 대조", ("free", "approval", "charged")),
        ("반납 일정 대조", ("gate", "scheduled", "returned")),
    ),
    4: (
        ("정산 인계", ("gate", "paid", "charged", "received")),
        ("변경과 청구 대조", ("free", "gate", "approval", "charged")),
        ("수납 후속 처리", ("charged", "total", "received", "balance")),
    ),
    5: (
        ("비용 사건 인계", ("free", "gate", "approval", "charged", "received")),
        (
            "현장부터 수납까지 대조",
            ("discharge", "returned", "late_charge", "total", "received"),
        ),
    ),
}
HG_FIELDS = {
    "free": ("base_free_days", "storage", "처음 계약에서 보관에 돈이 붙지 않는 일수"),
    "discharge": ("actual_discharge_date", None, "실제로 배에서 화물을 내린 날"),
    "gate": ("actual_gate_out_date", None, "화물을 실은 장비가 실제로 밖으로 나간 날"),
    "scheduled": ("scheduled_return_date", None, "빈 장비를 돌려주기로 잡아 둔 날"),
    "returned": (
        "actual_designated_return_date",
        None,
        "빈 장비를 지정 장소에 실제로 돌려준 날",
    ),
    "approval": ("approval_record", None, "추가 조건이 승인됐는지에 대한 기록"),
    "paid": ("billed_paid_days", "storage", "보관료 계산에서 실제로 돈을 매긴 일수"),
    "charged": ("billed_amount", "storage", "보관료로 최종 청구한 금액"),
    "late_charge": (
        "billed_amount",
        "late_return",
        "장비를 늦게 돌려줘서 붙은 확정 금액",
    ),
    "total": ("receipt_total", None, "두 비용의 거래 합계"),
    "received": ("received_amount", None, "회계 기록에 실제로 들어온 돈"),
    "balance": ("balance_amount", None, "수납하고도 남은 금액"),
}
HA_FIELDS = {
    "free": (0, "비용", "보관료", "무료기간", "처음 계약에서 보관이 무료인 기간"),
    "discharge": (
        1,
        "기록",
        "양하 원시기록",
        "날짜·접수값",
        "실제로 배에서 화물을 내린 날",
    ),
    "gate": (1, "기록", "적입 반출기록", "날짜·접수값", "장비를 실제로 빼낸 날"),
    "scheduled": (
        1,
        "기록",
        "반납 예약 변경",
        "날짜·접수값",
        "빈 장비 반납을 변경해 잡아 둔 날",
    ),
    "charged": (2, "항목", "보관료 정산액", "금액", "조정을 반영해 정산된 보관료"),
    "total": (2, "항목", "두 비용 발생 합계", "금액", "두 비용을 합친 금액"),
    "received": (2, "항목", "배분 수납", "금액", "이 건에 배분된 입금액"),
}
HA_SCENARIOS = {
    1: (
        ("일정 확인", ("scheduled",)),
        ("정산 확인", ("charged",)),
        ("현장 확인", ("gate",)),
    ),
    2: (
        ("기준과 정산 대조", ("free", "charged")),
        ("현장 일정 확인", ("discharge", "gate")),
        ("수납 대조", ("total", "received")),
    ),
    3: (
        ("정산 인계", ("free", "charged", "received")),
        ("일정 인계", ("discharge", "gate", "scheduled")),
    ),
    4: (
        ("비용 근거 정리", ("free", "discharge", "gate", "charged")),
        ("수납 인계", ("gate", "charged", "total", "received")),
    ),
    5: (
        ("터미널 비용 인계", ("free", "discharge", "gate", "charged", "received")),
        (
            "현장부터 정산까지 대조",
            ("discharge", "gate", "scheduled", "charged", "received"),
        ),
    ),
}
FRAMES = (
    "{subject} 건을 {intent}용으로 보고 있어. {asks} 확인해줄래?",
    "{subject} 건 {intent} 중인데, {asks} 알려줘.",
    "{subject} 건을 넘겨받았어. {intent}에 필요한 {asks} 정리해줘.",
)
BLIND_QUOTAS = (
    (23, 7, 20),
    (25, 7, 18),
    (28, 8, 14),
    (32, 8, 10),
    (41, 9, 0),
)


def key_order(key):
    return sha256(f"advanced-usage-20261109/{key}".encode()).digest()


def reference_locations(reference):
    return {
        (e["book_id"], e["page"])
        for group in reference["evidence_groups"]
        for e in group
    }


def prior_evaluation():
    rows = [r for path in PREVIOUS for r in load_references(path).values()]
    previous_pages = set().union(*(reference_locations(r) for r in rows))
    previous_cases = {r["case_id"] for r in rows if r.get("case_id")}
    previous_cases.update(k for r in rows for k in r.get("case_keys", ()))
    prior_questions = {
        normalized(r["question"]) for r in rows + list(load_references(FULL).values())
    }
    return previous_pages, previous_cases, prior_questions


def load_inputs():
    restored = json.loads(CACHE_PATH.read_bytes())
    page_index = {location(p): p for p in restored}
    manifest = sha256((CURATED / "manifest.json").read_bytes()).hexdigest()
    cases = load_cases(CASE_INDEX, restored, manifest)
    profiles = load_profiles(CURATED, cases)
    sources, ha, ha_rows, _, _ = load_formats(CURATED)
    records, display, handbook = {}, {}, defaultdict(list)
    for path in sorted((CURATED / "records").glob("*.jsonl")):
        for r in read_rows(path):
            if r["kind"] == "case_context" and (m := PROFILE.search(r["text"])):
                case = r["case_ids"][0]
                if case in profiles:
                    triple = tuple(
                        normalized(m[k]) for k in ("shipper", "cargo", "spec")
                    )
                    if triple == profiles[case]:
                        display[case] = m.groupdict()
            if r["kind"] != "table_row" or r.get("review_status") != "parsed":
                continue
            records[r["record_id"]] = r
            source = sources[r["source_id"]]
            if not re.match(r"IS\d+\s*\|", source["heading"]):
                continue
            fields = r["fields"]
            for label in KEYS:
                if label in fields and len(ids := identifiers(fields[label])) == 1:
                    handbook[ids[0]].append((r, label))
                    break
    if set(display) != set(cases):
        raise ValueError("HG 사건의 원문 식별 단서가 누락됐습니다.")
    fact_file = FACTS / "role_facts.jsonl"
    summary = json.loads((FACTS / "summary.json").read_bytes())
    if (
        summary["curation_manifest_sha256"] != manifest
        or summary["role_facts_sha256"] != sha256(fact_file.read_bytes()).hexdigest()
    ):
        raise ValueError("역할별 필드 입력 지문이 다릅니다.")
    typed = defaultdict(dict)
    wanted = {(name, cost) for name, cost, _ in HG_FIELDS.values()}
    for row in read_rows(fact_file):
        key = row["fact"], row["cost_kind"]
        if key in wanted:
            if key in typed[row["case_id"]]:
                raise ValueError("한 사건에 같은 의미의 필드가 두 번 있습니다.")
            typed[row["case_id"]][key] = row
    return page_index, cases, display, sources, records, typed, ha, ha_rows, handbook


def hg_fact(typed, records, case, field, *, states=("reported",)):
    name, cost, ask = HG_FIELDS[field]
    row = typed[case].get((name, cost))
    if row and row["state"] in states:
        value = (
            row["value"]
            if field == "paid" and row["value"] is not None
            else row["raw_value"]
        )
        if field == "paid" and row["value"] is not None:
            value = f"{value}일"
        return Fact(ask, str(value), records[row["source"]["record_id"]])
    return None


def ha_fields(roles, ha_rows):
    per_role = [
        [r for r in ha_rows[roles[role]["source_id"]] if r["kind"] == "table_row"]
        for role in HA_ROLES
    ]
    out = {}
    for field, (role, key, label, column, ask) in HA_FIELDS.items():
        rows = [r for r in per_role[role] if r["fields"].get(key) == label]
        if len(rows) != 1:
            continue
        value = rows[0]["fields"][column]
        if role == 1 and not re.search(r"\d{4}-\d{2}-\d{2}", value):
            continue
        if role == 2 and not re.fullmatch(r"USD\s+[\d,.]+", value):
            continue
        out[field] = Fact(ask, value, rows[0])
    return out


def subject_for(case, display, style):
    p = display[case]
    if style == "explicit":
        return case
    if style == "equipment_code":
        return p["equipment"]
    if style == "partial_terms":
        return f"{p['shipper']} {p['cargo']}"
    spec = SPECS[p["spec"]] if style == "colloquial_spec" else p["spec"]
    return f"{p['shipper']} {p['cargo']} {spec}"


def candidate(
    family, case_keys, intent, style, subject, facts, *, action="answer", question=None
):
    if question is None:
        asks = ", ".join(f.ask for f in facts)
        frame = FRAMES[key_order("/".join(case_keys))[0] % len(FRAMES)]
        question = frame.format(subject=subject, intent=intent, asks=asks)
    return {
        "family": family,
        "case_keys": list(case_keys),
        "intent": intent,
        "style": style,
        "question": question,
        "action": action,
        "facts": facts,
    }


def answer_pools(
    cases,
    display,
    sources,
    records,
    typed,
    ha,
    ha_rows,
    handbook,
    reserved_pages,
    reserved_cases,
):
    pools = defaultdict(list)
    styles = (
        "explicit",
        "source_terms",
        "colloquial_spec",
        "partial_terms",
        "equipment_code",
    )
    for case, pages in cases.items():
        if case in reserved_cases or any(
            location(page) in reserved_pages for page in pages
        ):
            continue
        if any(row["state"] != "reported" for row in typed[case].values()):
            # Cases with unconfirmed return charges form their own outcome slice.
            continue
        for size, scenarios in HG_SCENARIOS.items():
            for intent, keys in scenarios:
                facts = tuple(hg_fact(typed, records, case, field) for field in keys)
                if all(facts):
                    style = styles[key_order(f"{case}/{intent}")[0] % len(styles)]
                    pools["hg", size].append(
                        candidate(
                            "hg",
                            [case],
                            intent,
                            style,
                            subject_for(case, display, style),
                            facts,
                        )
                    )
        scheduled = hg_fact(typed, records, case, "scheduled")
        returned = hg_fact(typed, records, case, "returned")
        if scheduled and returned and scheduled.value != returned.value:
            style = "colloquial_spec"
            subject = subject_for(case, display, style)
            question = (
                f"{subject} 건, 예약 잡았던 날에 빈 장비를 실제로 돌려준 걸로 적어도 돼? "
                "잡아 둔 날과 실제 돌려준 날을 같이 확인해줘."
            )
            pools["hg", 2].append(
                candidate(
                    "hg",
                    [case],
                    "예정일과 실제일 혼동",
                    style,
                    subject,
                    (scheduled, returned),
                    question=question,
                )
            )
    for case, roles in ha.items():
        if case in reserved_cases or any(
            (s["book_id"], s["page"]) in reserved_pages for s in roles.values()
        ):
            continue
        fields = ha_fields(roles, ha_rows)
        for size, scenarios in HA_SCENARIOS.items():
            for intent, keys in scenarios:
                if all(key in fields for key in keys):
                    pools["ha", size].append(
                        candidate(
                            "ha",
                            [case],
                            intent,
                            "explicit",
                            case,
                            tuple(fields[key] for key in keys),
                        )
                    )
    for key, rows in handbook.items():
        if len(rows) != 1 or key in reserved_cases:
            continue
        row, label = rows[0]
        source = sources[row["source_id"]]
        if (source["book_id"], source["page"]) in reserved_pages:
            continue
        fields = row["fields"]
        other = [
            k
            for k in fields
            if k != label and fields[k].strip() and fields[k] != fields[label]
        ]
        other.sort(key=lambda k: key_order(f"{key}/{k}"))
        for size in range(1, min(len(other), 4) + 1):
            facts = tuple(
                Fact(HANDBOOK_ASK.get(k, f"기록에 나온 {k}"), fields[k], row)
                for k in other[:size]
            )
            pools["handbook", size].append(
                candidate(
                    "handbook", [key], f"{label} 업무 확인", "record_key", key, facts
                )
            )
    return pools


def exception_pools(cases, display, typed, records, reserved_pages, reserved_cases):
    eligible = {
        c
        for c, pages in cases.items()
        if c not in reserved_cases
        and not any(location(p) in reserved_pages for p in pages)
    }
    dated = defaultdict(list)
    abstain = []
    for case, p in display.items():
        gate = hg_fact(typed, records, case, "gate")
        if gate:
            dated[p["shipper"], p["spec"], gate.value].append((case, gate))
        if case not in eligible:
            continue
        returned = hg_fact(
            typed,
            records,
            case,
            "returned",
            states=("receipt_missing", "non_designated_yard_only"),
        )
        charge = hg_fact(typed, records, case, "late_charge", states=("unconfirmed",))
        if returned and charge:
            style = ("source_terms", "colloquial_spec", "partial_terms")[
                key_order(case)[0] % 3
            ]
            subject = subject_for(case, display, style)
            question = f"{subject} 건 정산 마감하려고 해. 빈 장비를 실제로 돌려준 날짜랑 늦게 돌려줘서 붙은 비용을 확정할 수 있어?"
            abstain.append(
                candidate(
                    "hg",
                    [case],
                    "반납 증빙 확인",
                    style,
                    subject,
                    (returned, charge),
                    action="abstain",
                    question=question,
                )
            )
    clarify = []
    for (shipper, spec, day), matches in dated.items():
        if len(matches) != 2 or any(c not in eligible for c, _ in matches):
            continue
        keys = [c for c, _ in matches]
        cargo = [display[c]["cargo"] for c in keys]
        charges = [
            charge
            for case in keys
            if (charge := hg_fact(typed, records, case, "charged"))
        ]
        if (
            len(set(cargo)) != 2
            or len(charges) != 2
            or charges[0].value == charges[1].value
        ):
            continue
        facts = tuple(
            Fact(
                f"후보 {i + 1}의 실제 반출일", gate.value, gate.record, (charge.record,)
            )
            for i, ((_, gate), charge) in enumerate(zip(matches, charges, strict=True))
        )
        subject = f"{shipper} {SPECS[spec]}"
        question = (
            f"{day}에 밖으로 나간 {subject} 건 보관료 얼마였어? 회계 인계에 넣으려고."
        )
        clarify.append(
            candidate(
                "hg",
                keys,
                "유사 사건 식별",
                "shared_clues",
                subject,
                facts,
                action="clarify",
                question=question,
            )
        )
    return clarify, abstain


@cache
def original_pages(file_name, expected_sha):
    path = DATA_DIR / "documents" / file_name
    if sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise ValueError("원본 PDF 지문이 정제 출처와 다릅니다.")
    pages = check_output(["pdftotext", "-layout", str(path), "-"]).decode().split("\f")
    if not pages[-1].strip():
        pages.pop()
    if len(pages) != 150:
        raise ValueError("Advanced PDF는 150쪽이어야 합니다.")
    return pages


def row_evidence(row, sources, restored):
    source = sources[row["source_id"]]
    key = source["book_id"], source["page"]
    raw = original_pages(source["file_name"], source["file_sha256"])[source["page"] - 1]
    lines = raw.splitlines()[row["line_start"] - 1 : row["line_end"]]
    quote = readable(" ".join(lines))
    normalized_page = _normalize(restored[key]["text"])
    if not quote or _normalize(quote) not in normalized_page:
        return None
    return {"book_id": key[0], "page": key[1], "quote": quote}


def source_evidence(fact, sources, restored):
    primary = row_evidence(fact.record, sources, restored)
    support = [row_evidence(row, sources, restored) for row in fact.support]
    if not primary or not all(support):
        return None
    fields = fact.record["fields"]
    value = fact.value
    if value not in fields.values():
        days = re.fullmatch(
            r"\d+\s*/\s*\d+\s*/\s*(\d+)", fields.get("산입/무료/유료일", "")
        )
        if not days or value != f"{int(days[1])}일":
            return None
        value = fields["산입/무료/유료일"]
    if _normalize(value) not in _normalize(primary["quote"]):
        return None
    return [primary, *support]


def make_reference(qid, item, evidence):
    facts = [f"{fact.ask}은(는) {fact.value}" for fact in item["facts"]]
    requirements = [
        {
            "role": item["family"],
            "ask": fact.ask,
            "fact": text,
            "primary": citations,
            "alternatives": [],
        }
        for fact, text, citations in zip(item["facts"], facts, evidence, strict=True)
    ]
    action = item["action"]
    behavior = {
        "answer": "질문이 요청한 사실을 원문에 근거해 빠짐없이 답한다.",
        "clarify": "같은 화주·규격·반출일에 둘 이상의 사건이 있다. 화물명이나 예약·장비 번호를 물어본다.",
        "abstain": "실제 지정 반납 증빙이 부족하고 비용이 미확정임을 알려준다. 예정일이나 확정 금액을 추정하지 않는다.",
    }[action]
    if action == "clarify":
        facts = [
            "질문의 단서와 일치하는 사건이 두 건이다.",
            "화물명이나 예약·장비 번호 중 하나를 확인해야 한다.",
        ]
    if item["intent"] == "예정일과 실제일 혼동":
        behavior = "예약한 날과 실제 지정 반납일이 서로 다름을 지적하고 두 날짜를 각각 설명한다."
        answer = "예약일을 실제 반납일로 쓸 수 없다. " + " ".join(facts)
    else:
        answer = (
            " ".join(facts) if action == "answer" else f"{behavior} {' '.join(facts)}"
        )
    citations = [citation for bundle in evidence for citation in bundle]
    return {
        "id": qid,
        "case_keys": item["case_keys"],
        "expected_action": action,
        "expected_behavior": behavior,
        "accepted_action_labels": [action],
        "reference_answer": answer,
        "required_facts": facts,
        "evidence_required": True,
        "evidence_groups": [list({tuple(e.items()): e for e in citations}.values())],
        "evidence_requirements": requirements,
        "question_type": item["intent"],
        "source_family": item["family"],
        "identifier_mode": item["style"],
    }


def pick(pools, clarify, abstain, sources, restored, prior_questions):
    used_cases, used_pages, used_questions = set(), set(), set(prior_questions)
    result, counts, books = {}, Counter(), Counter()

    def add(pool, amount, bucket):
        chosen = []
        pool = sorted(pool, key=lambda entry: key_order(entry[2]))
        rejected = set()

        def priority(entry):
            item, locs, _ = entry
            return (
                counts[bucket, item["intent"]],
                -sum(not books[book] for book in {b for b, _ in locs}),
                sum(books[book] for book, _ in locs),
                counts[bucket, item["style"]],
            )

        while len(chosen) < amount:
            candidates = (
                (item, locs, texts)
                for item, locs, texts in pool
                if not (
                    set(item["case_keys"]) & used_cases
                    or locs & used_pages
                    or texts in used_questions
                    or texts in rejected
                )
            )
            for item, locs, question_key in sorted(candidates, key=priority):
                evidence = [
                    source_evidence(fact, sources, restored) for fact in item["facts"]
                ]
                if all(evidence):
                    break
                rejected.add(question_key)
            else:
                raise ValueError(
                    f"적합 후보 부족 · {bucket} · 채택 {len(chosen)}/{amount} · 출처 제외 {len(rejected)}"
                )
            qid = f"ADV-USE-{bucket[0]}-{len(result[bucket[0]]) + len(chosen) + 1:03}"
            question = {"id": qid, "question": item["question"]}
            ref = make_reference(qid, item, evidence)
            context = packed_context([(restored[loc], 1.0) for loc in sorted(locs)])
            if len(context.final_chunks) != len(locs):
                raise ValueError(
                    "모든 필수 근거 페이지를 동일 문맥 예산에 넣을 수 없습니다."
                )
            chosen.append((question, ref))
            used_cases.update(item["case_keys"])
            used_pages.update(locs)
            used_questions.add(question_key)
            books.update(book for book, _ in locs)
            counts[bucket, item["intent"]] += 1
            counts[bucket, item["style"]] += 1
        result[bucket[0]].extend(chosen)
        if amount:
            print(
                f"후보 확정 · {' / '.join(map(str, bucket))} · {amount}건 · 출처 제외 {len(rejected)}건",
                flush=True,
            )

    def locate(items):
        return [
            (
                item,
                {
                    (source["book_id"], source["page"])
                    for fact in item["facts"]
                    for row in (fact.record, *fact.support)
                    for source in (sources[row["source_id"]],)
                },
                normalized(item["question"]),
            )
            for item in items
        ]

    for split in ("B", "P"):
        result[split] = []
        n = 25 if split == "B" else 5
        add(locate(clarify), n, (split, "clarify"))
        add(locate(abstain), n, (split, "abstain"))
        for size, quota in enumerate(BLIND_QUOTAS, 1):
            if split == "P":
                quota = (2, 1, 1) if size < 5 else (3, 1, 0)
            for family, amount in zip(("hg", "ha", "handbook"), quota, strict=True):
                add(locate(pools[family, size]), amount, (split, family, size))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("새 출력 경로가 필요합니다.")
    reserved_pages, reserved_cases, prior_questions = prior_evaluation()
    restored, cases, display, sources, records, typed, ha, ha_rows, handbook = (
        load_inputs()
    )
    pools = answer_pools(
        cases,
        display,
        sources,
        records,
        typed,
        ha,
        ha_rows,
        handbook,
        reserved_pages,
        reserved_cases,
    )
    clarify, abstain = exception_pools(
        cases, display, typed, records, reserved_pages, reserved_cases
    )
    result = pick(pools, clarify, abstain, sources, restored, prior_questions)
    args.output.mkdir(parents=True)
    summary = {}
    for name, split in (("blind", "B"), ("pilot", "P")):
        questions, refs = (list(values) for values in zip(*result[split], strict=True))
        questions_path = args.output / f"{name}_questions.json"
        refs_path = args.output / f"{name}_references.json"
        questions_path.write_text(
            json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        refs_path.write_text(
            json.dumps(refs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        assert {r["id"] for r in load_questions(questions_path)} == set(
            load_references(refs_path)
        )
        locs = set().union(*(reference_locations(r) for r in refs))
        assert not locs & reserved_pages
        summary[name] = {
            "questions": len(questions),
            "actions": dict(Counter(r["expected_action"] for r in refs)),
            "answer_fact_counts": dict(
                sorted(
                    Counter(
                        len(r["required_facts"])
                        for r in refs
                        if r["expected_action"] == "answer"
                    ).items()
                )
            ),
            "families": dict(Counter(r["source_family"] for r in refs)),
            "question_styles": dict(Counter(r["identifier_mode"] for r in refs)),
            "intents": dict(Counter(r["question_type"] for r in refs)),
            "pages": len(locs),
            "books": len({b for b, _ in locs}),
            "question_source_page_counts": dict(
                sorted(Counter(len(reference_locations(r)) for r in refs).items())
            ),
            "source_regions": dict(
                sorted(
                    Counter(
                        "1–50" if p <= 50 else "51–100" if p <= 100 else "101–150"
                        for _, p in locs
                    ).items()
                )
            ),
            "evidence_slots": sum(len(r["evidence_requirements"]) for r in refs),
            "sha256": {
                p.name: sha256(p.read_bytes()).hexdigest()
                for p in (questions_path, refs_path)
            },
        }
    summary["validation"] = {
        "previous_formal_holdout_pilot_and_blind_pages_excluded": len(reserved_pages),
        "old_full_coverage_question_texts_excluded": True,
        "same_case_between_splits": 0,
        "previous_full_coverage_source_overlap_allowed": True,
        "original_pdf_and_restored_quote_check": True,
        "context_limit": {"pages": 8, "tokens": 4000},
        "review_state": "source_and_structure_checked_human_review_pending",
    }
    inputs = {
        "curation_manifest": CURATED / "manifest.json",
        "case_sources": CASE_INDEX / "case_sources.jsonl",
        "role_facts": FACTS / "role_facts.jsonl",
        "restored_pages": CACHE_PATH,
        "full_coverage_references": FULL,
        **{p.name: p for p in PREVIOUS},
    }
    summary["input_sha256"] = {
        label: sha256(path.read_bytes()).hexdigest() for label, path in inputs.items()
    }
    write_json(args.output / "manifest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
