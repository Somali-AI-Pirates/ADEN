"""Build the frozen, source-grounded identifier-variation fixture."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from unicodedata import normalize

from pypdf import PdfReader

from app.ingestion.curation import read_rows
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    PROFILE,
    PROFILE_ROLES,
    load_cases,
    load_profiles,
)
from experimental.u_10o0o.advanced_hg_relations import HGRelations
from experimental.u_10o0o.prepare import PROJECT_ROOT

ROOT = PROJECT_ROOT
SEED = 20261110
OUTPUT = ROOT / "artifacts/u_10o0o/advanced-identifier-diagnostic-20261110"
OUTPUT_FILES = ("questions.json", "references.json", "fixture-manifest.json")
IDENTIFIER_TYPES = (
    ("reservation_id", "HG"),
    ("equipment_id", "HL"),
    ("contract_id", "CT"),
    ("evidence_id", "EV"),
)
ROLES_BY_KIND = {
    "bill": ROLES[3],
    "receipt": ROLES[4],
    "event": ROLES[1],
}
REQUESTS = {
    "bill": "청구 명세에 적힌 보관료 금액을 알려주세요.",
    "receipt": "수납 기록의 입금액과 미결 잔액을 알려주세요.",
    "event": "실제 장비 반출일을 알려주세요.",
}
FACT_REQUESTS = {
    "bill": (("billed_amount", "storage"),),
    "receipt": (("received_amount", "none"), ("balance_amount", "none")),
    "event": (("actual_gate_out_date", "none"),),
}
FACT_HINTS = {
    "billed_amount": ("보관료", "청구"),
    "received_amount": ("수납", "입금"),
    "balance_amount": ("미결", "미수", "잔액"),
    "actual_gate_out_date": ("실제", "반출"),
}
DASH_TRANSLATION = str.maketrans({"–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-"})


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _normalized(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())


def _flat(value: str) -> str:
    return "".join(normalize("NFKC", value).casefold().split())


def _rank(value: str) -> str:
    return hashlib.sha256(f"{SEED}\0{value}".encode()).hexdigest()


def _mask_cargo(value: str) -> str:
    value = re.sub(r"\d+", " ", value)
    value = re.sub(r"[-–—−‐‑/,:;().]+", " ", value)
    value = " ".join(value.split())
    if not value or re.search(r"\d", value):
        raise ValueError("숫자 없는 화물 표현을 만들 수 없습니다.")
    return value


def _masked_key(value: str) -> str:
    return _normalized(_mask_cargo(value))


def _slot_hash(slot: tuple[str, str, str, int]) -> str:
    return _rank("|".join(map(str, slot)))


def _profile_values(text: str) -> tuple[str, str, str]:
    match = PROFILE.search(text)
    if match is None:
        raise ValueError("검증된 curated 프로필 레코드에서 프로필을 찾지 못했습니다.")
    return match.group("shipper"), match.group("cargo"), match.group("spec")


def _book_id(page: dict) -> str:
    return Path(page["file_name"]).stem


def _citation_base(case_id: str, role: str, page: dict, quote: str, kind: str) -> dict:
    return {
        "case_id": case_id,
        "role": role,
        "book_id": _book_id(page),
        "page": page["page"],
        "quote": quote,
        "kind": kind,
    }


def _pdf_text(
    book_id: str, page_number: int, pages_by_book: dict[str, PdfReader]
) -> str:
    file_name = f"{book_id}.pdf"
    if file_name not in pages_by_book:
        pages_by_book[file_name] = PdfReader(
            str(ROOT / "data/advanced/documents" / file_name)
        )
    reader = pages_by_book[file_name]
    if not 1 <= page_number <= len(reader.pages):
        raise ValueError("원본 PDF 페이지 범위가 맞지 않습니다.")
    return reader.pages[page_number - 1].extract_text(extraction_mode="layout") or ""


def _verify_page(page: dict, pages_by_book: dict[str, PdfReader]) -> str:
    book_id = _book_id(page)
    text = _pdf_text(book_id, page["page"], pages_by_book)
    if _flat(text) != _flat(page["text"]):
        raise ValueError("선택된 원본 PDF 쪽과 복원 원문이 다릅니다.")
    return text


def _profile_quote(
    case_id: str,
    page: dict,
    raw_profile: tuple[str, str, str],
    pages_by_book: dict[str, PdfReader],
) -> dict:
    text = _verify_page(page, pages_by_book)
    targets = [_flat(value) for value in raw_profile]
    lines = text.splitlines()
    for start in range(len(lines)):
        assembled = ""
        for end in range(start, min(len(lines), start + 5)):
            assembled += _flat(lines[end])
            if all(target in assembled for target in targets):
                quote = "\n".join(lines[start : end + 1]).strip()
                if quote and all(target in _flat(quote) for target in targets):
                    return _citation_base(
                        case_id, ROLES[3], page, quote, "profile_identity"
                    )
    raise ValueError("프로필 원문 인용을 원본 PDF에서 찾지 못했습니다.")


def _fact_quote(
    case_id: str,
    fact,
    page: dict,
    pages_by_book: dict[str, PdfReader],
    kind: str,
) -> dict:
    if (fact.source.file_name, fact.source.page) != (page["file_name"], page["page"]):
        raise ValueError("구조화 사실과 역할 페이지 출처가 다릅니다.")
    text = _verify_page(page, pages_by_book)
    lines = text.splitlines()
    anchors = [fact.raw_value]
    if fact.value_text:
        anchors.append(fact.value_text)
    if fact.value_int is not None:
        anchors.append(str(fact.value_int))
    candidates = []
    hints = FACT_HINTS.get(fact.fact_code, ())
    for index, line in enumerate(lines):
        flat_line = _flat(line)
        if any(anchor and _flat(anchor) in flat_line for anchor in anchors):
            hint_count = sum(_normalized(hint) in _normalized(line) for hint in hints)
            candidates.append((hint_count, -len(line), -index, line.strip()))
    if not candidates:
        raise ValueError("구조화 사실 인용을 원본 PDF에서 찾지 못했습니다.")
    quote = max(candidates)[3]
    if not quote or _flat(quote) not in _flat(text):
        raise ValueError("원본 PDF 인용 검증에 실패했습니다.")
    return _citation_base(case_id, fact.role, page, quote, kind)


def _load_fact_bank(relations, cases: dict[str, list[dict]]) -> dict[str, dict]:
    structured = relations._structured
    bank = {}
    for case_id in cases:
        rows = {}
        for kind, role in ROLES_BY_KIND.items():
            facts = []
            for fact_code, cost_kind in FACT_REQUESTS[kind]:
                matching = structured.get_hg_facts(
                    case_id,
                    fact_code=fact_code,
                    role=role,
                    cost_kind=cost_kind,
                )
                facts.extend(fact for fact in matching if fact.state == "reported")
            rows[kind] = tuple(facts)
        event_dates = [
            fact
            for fact in structured.get_hg_facts(
                case_id, fact_code="actual_gate_out_date", role=ROLES[1]
            )
            if fact.state == "reported"
        ]
        rows["gate_date_facts"] = tuple(event_dates)
        bank[case_id] = rows
    return bank


def _load_raw_profiles(
    cases: dict[str, list[dict]],
    profiles: dict[str, tuple[str, str, str]],
) -> dict[str, tuple[str, str, str]]:
    expected = {}
    for case_id, pages in cases.items():
        for role in PROFILE_ROLES:
            page = pages[ROLES.index(role)]
            source_id = f"advanced:{_book_id(page)}:p{page['page']}"
            expected[source_id] = (case_id, role)
    raw_profiles = {}
    found = set()
    for path in sorted((CURATED / "records").glob("*.jsonl")):
        for record in read_rows(path):
            source_id = record.get("source_id")
            if source_id not in expected or record.get("kind") != "case_context":
                continue
            case_id, role = expected[source_id]
            if (
                source_id in found
                or record.get("review_status") != "parsed"
                or record.get("case_ids") != [case_id]
                or record.get("fields", {}).get("기록 종류") != role
            ):
                raise ValueError("원문 프로필 레코드의 사건·역할 연결이 다릅니다.")
            values = _profile_values(record["text"])
            if tuple(_normalized(value) for value in values) != profiles[case_id]:
                raise ValueError("원문 프로필과 검증된 profile 값이 다릅니다.")
            if case_id in raw_profiles and tuple(
                _normalized(value) for value in raw_profiles[case_id]
            ) != tuple(_normalized(value) for value in values):
                raise ValueError("같은 사건의 원문 프로필 역할 간 값이 다릅니다.")
            raw_profiles.setdefault(case_id, values)
            found.add(source_id)
    if found != set(expected) or set(raw_profiles) != set(cases):
        raise ValueError("검증된 원문 프로필 역할 레코드가 빠졌습니다.")
    return raw_profiles


def _choose_cases(
    relations,
    cases: dict[str, list[dict]],
    profiles: dict[str, tuple[str, str, str]],
    raw_profiles: dict[str, tuple[str, str, str]],
    fact_bank: dict[str, dict],
    code_by_case_type: dict[str, dict[str, list[str]]],
) -> list[dict]:
    profile_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    exact_index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    masked_index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    gate_index: dict[str, set[str]] = defaultdict(set)
    for case_id, (shipper, cargo, spec) in profiles.items():
        profile_index[(shipper, spec)].add(case_id)
        exact_index[(shipper, cargo, spec)].add(case_id)
        masked_index[(shipper, spec, _masked_key(cargo))].add(case_id)
        for fact in fact_bank[case_id]["gate_date_facts"]:
            if fact.value_text:
                gate_index[fact.value_text].add(case_id)

    eligible = {}
    for case_id, profile in profiles.items():
        raw = raw_profiles[case_id]
        if set(code_by_case_type.get(case_id, {})) != {
            code_type for code_type, _ in IDENTIFIER_TYPES
        }:
            continue
        if any(
            len(fact_bank[case_id][kind]) != len(FACT_REQUESTS[kind])
            for kind in ROLES_BY_KIND
        ):
            continue
        if len(exact_index[profile]) != 1:
            continue
        gate_facts = fact_bank[case_id]["gate_date_facts"]
        if len(gate_facts) != 1 or not gate_facts[0].value_text:
            continue
        shipper, cargo, spec = profile
        gate_matches = (
            profile_index[(shipper, spec)] & gate_index[gate_facts[0].value_text]
        )
        eligible[case_id] = {
            "profile": profile,
            "raw_profile": raw,
            "gate_date": gate_facts[0].value_text,
            "exact_profile": len(exact_index[profile]) == 1,
            "shipper_spec_gate_date": gate_matches == {case_id},
            "mask_candidates": masked_index[(shipper, spec, _masked_key(cargo))],
        }

    slot_roles = {
        "exact_profile": {"bill": 1, "receipt": 1, "event": 4},
        "shipper_spec_gate_date": {"bill": 3, "receipt": 3},
    }
    slots = [
        (subtype, spec, role, occurrence)
        for subtype, roles in slot_roles.items()
        for spec in ("20gp", "40gp")
        for role, count in roles.items()
        for occurrence in range(count)
    ]
    options = {}
    for slot in slots:
        subtype, spec, role, _ = slot
        role_name = ROLES_BY_KIND[role]
        role_book_index = ROLES.index(role_name)
        options[slot] = sorted(
            (
                case_id
                for case_id, info in eligible.items()
                if info[subtype]
                and info["profile"][2] == spec
                and len(info["mask_candidates"]) > 1
                and info["raw_profile"][0].strip()
                and _book_id(cases[case_id][role_book_index])
            ),
            key=lambda case_id, s=slot: _rank(f"{s}|{case_id}"),
        )
    slot_order = sorted(slots, key=lambda slot: (len(options[slot]), _slot_hash(slot)))
    selected = []
    used_cases = set()
    used_shippers = set()
    used_books = set()
    visits = 0

    def search(index: int) -> bool:
        nonlocal visits
        if index == len(slot_order):
            return True
        slot = slot_order[index]
        subtype, spec, role, _ = slot
        role_book_index = ROLES.index(ROLES_BY_KIND[role])
        for case_id in options[slot]:
            visits += 1
            if visits > 250_000:
                return False
            if case_id in used_cases:
                continue
            shipper = eligible[case_id]["profile"][0]
            book = _book_id(cases[case_id][role_book_index])
            if shipper in used_shippers or book in used_books:
                continue
            used_cases.add(case_id)
            used_shippers.add(shipper)
            used_books.add(book)
            selected.append((case_id, subtype, spec, role, book))
            if search(index + 1):
                return True
            selected.pop()
            used_cases.remove(case_id)
            used_shippers.remove(shipper)
            used_books.remove(book)
        return False

    if not search(0) or len(selected) != 24:
        raise ValueError("고정 seed 표본의 균형·유일성 조건을 만족하지 못했습니다.")

    assignments = []
    subtype_cases = defaultdict(list)
    for case_id, subtype, spec, role, book in selected:
        subtype_cases[subtype].append((case_id, subtype, spec, role, book))
    for subtype in ("exact_profile", "shipper_spec_gate_date"):
        rows = sorted(subtype_cases[subtype], key=lambda row: _rank(row[0]))
        if len(rows) != 12:
            raise ValueError("간접 식별 하위 유형별 사건 수가 다릅니다.")
        for type_index, (code_type, code_prefix) in enumerate(IDENTIFIER_TYPES):
            for row in rows[type_index * 3 : (type_index + 1) * 3]:
                case_id, row_subtype, spec, role, book = row
                codes = code_by_case_type[case_id][code_type]
                code = min(codes, key=lambda value: _rank(f"{case_id}|{value}"))
                if code_prefix == "HG" and not re.fullmatch(r"HG20\d{2}-\d+", code):
                    raise ValueError("HG full code 형식이 다릅니다.")
                resolved = {
                    item.case_id
                    for item in relations.identify(code)
                    if item.code_type == code_type and item.relation_kind == "exact"
                }
                if resolved != {case_id}:
                    raise ValueError("완전 코드가 원래 사건 하나로 연결되지 않습니다.")
                assignments.append(
                    {
                        "case_id": case_id,
                        "indirect_subtype": row_subtype,
                        "spec": spec,
                        "role": role,
                        "source_book": book,
                        "code_type": code_type,
                        "code_prefix": code_prefix,
                        "code": code,
                        **eligible[case_id],
                    }
                )

    assignments.sort(key=lambda row: _rank(f"group|{row['case_id']}"))
    if Counter(row["code_prefix"] for row in assignments) != Counter(
        {"HG": 6, "HL": 6, "CT": 6, "EV": 6}
    ):
        raise ValueError("식별자 유형별 대상 수가 6건이 아닙니다.")
    if Counter(row["role"] for row in assignments) != Counter(
        {"bill": 8, "receipt": 8, "event": 8}
    ):
        raise ValueError("역할별 대상 수가 8건이 아닙니다.")
    if Counter(row["profile"][2].upper() for row in assignments) != Counter(
        {"20GP": 12, "40GP": 12}
    ):
        raise ValueError("장비 규격별 대상 수가 균형을 이루지 않습니다.")
    if len({row["raw_profile"][0] for row in assignments}) != 24:
        raise ValueError("표본 화주가 서로 다르지 않습니다.")
    if len({row["source_book"] for row in assignments}) != 24:
        raise ValueError("표본 원본 책이 서로 다르지 않습니다.")

    # Check selected indirect joins against the read-only structured indexes.
    for row in assignments:
        shipper, cargo, _ = row["raw_profile"]
        if row["indirect_subtype"] == "exact_profile":
            candidates = relations.profile_candidates(
                {"shipper": shipper, "cargo": cargo, "spec": row["profile"][2]}
            )
        else:
            profile_candidates = relations.profile_candidates(
                {"shipper": shipper, "spec": row["profile"][2]}
            )
            date_candidates = relations.identities_for(
                "actual_gate_out_date", row["gate_date"]
            )
            candidates = profile_candidates & date_candidates
        if candidates != {row["case_id"]}:
            raise ValueError("선택된 간접 조건의 구조화 후보가 단일 사건이 아닙니다.")
    return assignments


def _variant_code(code: str, variant: str) -> str:
    if variant == "canonical":
        return code
    if variant == "lowercase":
        return code.lower()
    if variant == "unicode_dash":
        return code.replace("-", "–")
    if variant == "separator_spaces":
        return " ".join(code.split("-"))
    raise ValueError("지원되지 않는 식별자 표기 변형입니다.")


def _restore_variant(value: str, variant: str) -> str:
    value = normalize("NFKC", value).strip().upper().translate(DASH_TRANSLATION)
    if variant == "separator_spaces":
        value = "-".join(value.split())
    return value


def _request_for(row: dict, variant: str) -> str:
    request = REQUESTS[row["role"]]
    if variant in {"canonical", "lowercase", "unicode_dash", "separator_spaces"}:
        code = _variant_code(row["code"], variant)
        if _restore_variant(code, variant) != row["code"]:
            raise ValueError("표기 변형이 원래 full code를 복원하지 못합니다.")
        return f"{code} 건의 {request}"
    shipper, cargo, spec = row["raw_profile"]
    if variant == "exact_profile":
        return f"{shipper}의 '{cargo}' 화물 {spec} 장비 건의 {request}"
    if variant == "shipper_spec_gate_date":
        return (
            f"{shipper}의 {spec} 장비 중 실제 적입 장비 반출일이 "
            f"{row['gate_date']}인 건의 {request}"
        )
    if variant == "shipper_spec_only":
        return f"{shipper}의 {spec} 장비 건의 {request}"
    if variant == "masked_cargo":
        masked = _mask_cargo(cargo)
        return f"{shipper}의 '{masked}' 화물 {spec} 장비 건의 {request}"
    raise ValueError("질문 변형이 없습니다.")


def _citation_list(
    row: dict,
    cases: dict[str, list[dict]],
    raw_profiles: dict[str, tuple[str, str, str]],
    fact_bank: dict[str, dict],
    pages_by_book: dict[str, PdfReader],
    insufficient_candidates: set[str] | None = None,
) -> list[dict]:
    case_id = row["case_id"]
    role_name = ROLES_BY_KIND[row["role"]]
    role_page = cases[case_id][ROLES.index(role_name)]
    citations = [
        _fact_quote(
            case_id,
            fact,
            role_page,
            pages_by_book,
            "requested_fact",
        )
        for fact in fact_bank[case_id][row["role"]]
    ]
    if row["indirect_subtype"] == "exact_profile" or insufficient_candidates:
        profile_page = cases[case_id][ROLES.index(ROLES[3])]
        citations.append(
            _profile_quote(case_id, profile_page, row["raw_profile"], pages_by_book)
        )
    if row["indirect_subtype"] == "shipper_spec_gate_date":
        event_page = cases[case_id][ROLES.index(ROLES[1])]
        citations.append(
            _fact_quote(
                case_id,
                fact_bank[case_id]["gate_date_facts"][0],
                event_page,
                pages_by_book,
                "indirect_disambiguator",
            )
        )
    if insufficient_candidates:
        alternate = min(
            (
                candidate
                for candidate in insufficient_candidates
                if candidate != case_id
            ),
            key=_rank,
        )
        citations.append(
            _profile_quote(
                alternate,
                cases[alternate][ROLES.index(ROLES[3])],
                raw_profiles[alternate],
                pages_by_book,
            )
        )
    unique = {}
    for citation in citations:
        key = (
            citation["case_id"],
            citation["role"],
            citation["book_id"],
            citation["page"],
            citation["quote"],
            citation["kind"],
        )
        unique[key] = citation
    return list(unique.values())


def build_fixture() -> tuple[bytes, bytes, bytes, dict]:
    if Path.cwd().resolve() != ROOT:
        raise RuntimeError("프로젝트 루트에서 fixture generator를 실행하세요.")
    existing = [name for name in OUTPUT_FILES if (OUTPUT / name).exists()]
    if existing:
        raise FileExistsError(f"기존 fixture 파일을 보존합니다: {', '.join(existing)}")

    questions = []
    references = {}
    pages_by_book: dict[str, PdfReader] = {}
    with HGRelations() as relations:
        curation_sha = _sha_bytes((CURATED / "manifest.json").read_bytes())
        cases = load_cases(CASE_INDEX, relations.pages, curation_sha)
        profiles = load_profiles(CURATED, cases)
        raw_profiles = _load_raw_profiles(cases, profiles)

        code_by_case_type: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        rows = relations._db().execute(
            "SELECT code_type, code, case_id FROM code_relations "
            "WHERE relation_kind = 'exact' AND code_type IN "
            "('reservation_id', 'equipment_id', 'contract_id', 'evidence_id') "
            "ORDER BY case_id, code_type, code"
        )
        for code_type, code, case_id in rows:
            code_by_case_type[case_id][code_type].append(code)
        fact_bank = _load_fact_bank(relations, cases)
        assignments = _choose_cases(
            relations,
            cases,
            profiles,
            raw_profiles,
            fact_bank,
            code_by_case_type,
        )

        insufficient_variants = {}
        for subtype in ("exact_profile", "shipper_spec_gate_date"):
            rows_for_subtype = sorted(
                (row for row in assignments if row["indirect_subtype"] == subtype),
                key=lambda row: _rank(f"insufficient|{row['case_id']}"),
            )
            if len(rows_for_subtype) != 12:
                raise ValueError("간접 하위 유형별 표본 수가 12건이 아닙니다.")
            for index, row in enumerate(rows_for_subtype):
                insufficient_variants[row["case_id"]] = (
                    "shipper_spec_only" if index < 6 else "masked_cargo"
                )

        group_rows = []
        all_profile_tuples = profiles
        profile_candidates_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
        masked_candidates_by_key: dict[tuple[str, str, str], set[str]] = defaultdict(
            set
        )
        for case_id, (shipper, cargo, spec) in all_profile_tuples.items():
            profile_candidates_by_key[(shipper, spec)].add(case_id)
            masked_candidates_by_key[(shipper, spec, _masked_key(cargo))].add(case_id)

        for group_number, row in enumerate(assignments, 1):
            case_id = row["case_id"]
            group_id = f"AIF-{group_number:03d}"
            exact_profile = row["indirect_subtype"] == "exact_profile"
            if exact_profile:
                indirect_candidates = relations.profile_candidates(
                    {
                        "shipper": row["raw_profile"][0],
                        "cargo": row["raw_profile"][1],
                        "spec": row["profile"][2],
                    }
                )
                if indirect_candidates != {case_id}:
                    raise ValueError(
                        "화주·화물·규격 profile 조건이 단일 사건이 아닙니다."
                    )
            else:
                indirect_candidates = relations.profile_candidates(
                    {"shipper": row["raw_profile"][0], "spec": row["profile"][2]}
                ) & relations.identities_for("actual_gate_out_date", row["gate_date"])
                if indirect_candidates != {case_id}:
                    raise ValueError(
                        "화주·규격·실제 반출일 조건이 단일 사건이 아닙니다."
                    )

            insufficient_variant = insufficient_variants[case_id]
            if insufficient_variant == "shipper_spec_only":
                insufficient_candidates = profile_candidates_by_key[
                    (row["profile"][0], row["profile"][2])
                ]
            else:
                masked = _masked_key(row["profile"][1])
                insufficient_candidates = masked_candidates_by_key[
                    (row["profile"][0], row["profile"][2], masked)
                ]
            if (
                len(insufficient_candidates) < 2
                or case_id not in insufficient_candidates
            ):
                raise ValueError("불충분 질문 후보가 여러 사건을 포함하지 않습니다.")

            variants = (
                ("full_identifier", "canonical", "canonical", None),
                ("identifier_variant", "lowercase", "lowercase", None),
                ("identifier_variant", "unicode_dash", "unicode_dash", None),
                ("identifier_variant", "separator_spaces", "separator_spaces", None),
                ("indirect", row["indirect_subtype"], row["indirect_subtype"], None),
                (
                    "insufficient",
                    insufficient_variant,
                    insufficient_variant,
                    insufficient_candidates,
                ),
            )
            for condition, variant, render_variant, candidate_set in variants:
                question_id = f"AIQ-{len(questions) + 1:04d}"
                question = {
                    "id": question_id,
                    "group_id": group_id,
                    "condition": condition,
                    "variant": variant,
                    "question": _request_for(row, render_variant),
                }
                questions.append(question)
                citations = _citation_list(
                    row,
                    cases,
                    raw_profiles,
                    fact_bank,
                    pages_by_book,
                    candidate_set,
                )
                references[question_id] = {
                    "original_case_id": case_id,
                    "role": row["role"],
                    "possible_case_ids": sorted(candidate_set or indirect_candidates),
                    "sufficient": condition != "insufficient",
                    "source_citations": citations,
                    "group_id": group_id,
                    "variant": variant,
                    "indirect_subtype": (
                        row["indirect_subtype"] if condition == "indirect" else None
                    ),
                }
            group_rows.append(row)

        condition_counts = Counter(question["condition"] for question in questions)
        identifier_counts = Counter(row["code_prefix"] for row in assignments)
        role_counts = Counter(row["role"] for row in assignments)
        class_counts = Counter(row["profile"][2].upper() for row in assignments)
        indirect_counts = Counter(row["indirect_subtype"] for row in assignments)
        insufficient_counts = Counter(insufficient_variants.values())
        if condition_counts != Counter(
            {
                "full_identifier": 24,
                "identifier_variant": 72,
                "indirect": 24,
                "insufficient": 24,
            }
        ):
            raise ValueError("조건별 질문 수가 예상과 다릅니다.")
        if len(questions) != 144 or len(references) != 144:
            raise ValueError("질문과 gold 문항 수가 144건이 아닙니다.")

        question_bytes = _json_bytes(questions)
        reference_bytes = _json_bytes(references)
        input_fingerprints = {
            "relations_snapshot_id": relations.provenance["snapshot_id"],
            "relations_database_sha256": relations.provenance["database_sha256"],
            "structured_manifest_sha256": relations.provenance["inputs"][
                "structured_manifest_sha256"
            ],
            "structured_database_sha256": relations.provenance["inputs"][
                "structured_database_sha256"
            ],
            "curation_manifest_sha256": curation_sha,
        }
        source_books = sorted(
            {
                citation["book_id"]
                for reference in references.values()
                for citation in reference["source_citations"]
            }
        )
        input_fingerprints["source_pdf_sha256"] = {
            book_id: _sha_file(ROOT / "data/advanced/documents" / f"{book_id}.pdf")
            for book_id in source_books
        }

    manifest = {
        "fixture": "advanced-identifier-diagnostic",
        "schema_version": 1,
        "seed": SEED,
        "question_schema": ["id", "group_id", "condition", "variant", "question"],
        "reference_schema": [
            "original_case_id",
            "role",
            "possible_case_ids",
            "sufficient",
            "source_citations",
            "group_id",
            "variant",
            "indirect_subtype",
        ],
        "sampling": {
            "target_cases": 24,
            "question_count": len(questions),
            "condition_counts": dict(sorted(condition_counts.items())),
            "identifier_type_case_counts": dict(sorted(identifier_counts.items())),
            "role_case_counts": dict(sorted(role_counts.items())),
            "equipment_class_case_counts": dict(sorted(class_counts.items())),
            "indirect_subtype_case_counts": dict(sorted(indirect_counts.items())),
            "insufficient_variant_counts": dict(sorted(insufficient_counts.items())),
            "distinct_shippers": 24,
            "distinct_primary_source_books": 24,
        },
        "inputs": input_fingerprints,
        "outputs": {
            "questions.json": _sha_bytes(question_bytes),
            "references.json": _sha_bytes(reference_bytes),
        },
    }
    manifest_bytes = _json_bytes(manifest)
    return question_bytes, reference_bytes, manifest_bytes, manifest


def main() -> None:
    question_bytes, reference_bytes, manifest_bytes, manifest = build_fixture()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("questions.json", question_bytes),
        ("references.json", reference_bytes),
        ("fixture-manifest.json", manifest_bytes),
    ):
        with (OUTPUT / name).open("xb") as stream:
            stream.write(payload)
    print(
        json.dumps(
            {
                "path": OUTPUT.relative_to(ROOT).as_posix(),
                "counts": manifest["sampling"],
                "output_sha256": {
                    name: digest for name, digest in manifest["outputs"].items()
                },
                "manifest_sha256": _sha_bytes(manifest_bytes),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
