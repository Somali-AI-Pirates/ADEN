"""Build a local HG case-to-source index from an Advanced curation snapshot."""

import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import CASE_ID, identifiers

CURATED = Path("artifacts/u_10o0o/pattern-curation/advanced")
ROLES = (
    "비용 조건 약정",
    "현장 사건 수신대장",
    "계약 조건 대조 회신",
    "비용 청구 명세",
    "수납 및 미결 인계",
)


def hg_ids(text):
    return {
        item
        for item in identifiers(text)
        if item.startswith("HG") and CASE_ID.match(item)
    }


def build_index(curated):
    sources = {}
    headings = {}
    for source in read_rows(curated / "sources.jsonl"):
        source_id = source["source_id"]
        if source_id in sources:
            raise ValueError("정제본에 중복 출처 ID가 있습니다.")
        sources[source_id] = source
        if cases := hg_ids(source["heading"]):
            headings[source_id] = cases

    contexts = defaultdict(list)
    files = sorted((curated / "records").glob("*.jsonl"))
    if not files:
        raise ValueError("Advanced 정제 기록 파일이 없습니다.")
    for path in files:
        for record in read_rows(path):
            if record["kind"] == "case_context":
                contexts[record["source_id"]].append(record)

    links = defaultdict(list)
    issues = defaultdict(set)
    unmatched = []
    for source_id in sorted(headings.keys() | contexts.keys()):
        header_cases = headings.get(source_id, set())
        rows = contexts.get(source_id, ())
        claimed_cases = set().union(
            *(hg_ids(" ".join(row.get("case_ids", ()))) for row in rows)
        )
        affected = header_cases | claimed_cases
        source = sources.get(source_id)
        if source is None:
            reason = "missing_source"
        elif source["dataset"] != "advanced" or source_id != (
            f"advanced:{source['book_id']}:p{source['page']}"
        ):
            reason = "invalid_source"
        elif len(rows) != 1:
            reason = "missing_context" if not rows else "duplicate_context"
        else:
            row = rows[0]
            fields = row.get("fields", {})
            role = fields.get("기록 종류")
            contract = fields.get("계약", "")
            if (
                len(header_cases) != 1
                or set(row.get("case_ids", ())) != header_cases
                or {fields.get("예약")} != header_cases
            ):
                reason = "case_id_mismatch"
            elif role not in ROLES or role != source["heading"].rsplit(" / ", 1)[-1]:
                reason = "role_mismatch"
            elif row.get("review_status") != "parsed":
                reason = "unreviewed_context"
            elif not contract.startswith("CT-") or identifiers(contract) != [contract]:
                reason = "invalid_contract_id"
            else:
                case_id = next(iter(header_cases))
                links[case_id].append((role, source_id, contract))
                continue

        if not affected:
            unmatched.append({"source_id": source_id, "reasons": [reason]})
        for case_id in affected:
            issues[case_id].add(reason)

    output = []
    review = []
    for case_id in sorted(links.keys() | issues.keys()):
        rows = links.get(case_id, ())
        reasons = issues.get(case_id, set())
        roles = Counter(role for role, _, _ in rows)
        missing = [role for role in ROLES if roles[role] == 0]
        duplicates = [role for role in ROLES if roles[role] > 1]
        if missing:
            reasons.add("missing_role")
        if duplicates:
            reasons.add("duplicate_role")
        if len({contract for _, _, contract in rows}) > 1:
            reasons.add("contract_mismatch")
        if reasons:
            review.append(
                {
                    "case_id": case_id,
                    "reasons": sorted(reasons),
                    "missing_roles": missing,
                    "duplicate_roles": duplicates,
                }
            )
            continue

        for role, source_id, _ in sorted(rows, key=lambda row: ROLES.index(row[0])):
            output.append(
                {
                    "case_id": case_id,
                    "record_role": role,
                    "source_id": source_id,
                    "match_rule": "page_heading_case_id",
                }
            )
    return output, review, unmatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = (args.curated / "manifest.json").read_bytes()
    if json.loads(manifest)["identity"]["dataset"] != "advanced":
        parser.error("Advanced 정제 스냅샷이 필요합니다.")

    links, review, unmatched = build_index(args.curated)
    args.output.mkdir(parents=True, exist_ok=False)
    links_path = args.output / "case_sources.jsonl"
    write_rows(links_path, links)
    write_rows(args.output / "review.jsonl", [*review, *unmatched])
    summary = {
        "curation_manifest_sha256": sha256(manifest).hexdigest(),
        "case_sources_sha256": sha256(links_path.read_bytes()).hexdigest(),
        "indexed_cases": len(links) // len(ROLES),
        "source_links": len(links),
        "review_cases": len(review),
        "unidentified_contexts": len(unmatched),
        "review_reasons": dict(
            Counter(
                reason for row in [*review, *unmatched] for reason in row["reasons"]
            )
        ),
        "links_by_role": dict(Counter(row["record_role"] for row in links)),
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (args.output / "summary.json").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
