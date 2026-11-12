"""Export the verified A(v5) HG inputs for the portable product bundle."""

import argparse
import json
from collections import defaultdict
from hashlib import file_digest
from pathlib import Path

from app.ingestion.curation import read_rows
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_target_lookup import (
    DATE_FACTS,
    FACT_FIELDS,
    load_target_lookup,
)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def export(pages_path, curated, case_index, facts):
    pages = json.loads(pages_path.read_bytes())
    lookup = load_target_lookup(
        pages, curated=curated, case_index=case_index, facts=facts
    )
    load_storage_lookup(pages, curated=curated, case_index=case_index, facts=facts)
    fact_path = facts / "role_facts.jsonl"
    case_facts = defaultdict(list)
    for row in read_rows(fact_path):
        if row["fact"] not in DATE_FACTS | FACT_FIELDS.keys():
            continue
        source = row["source"]
        case_facts[row["case_id"]].append(
            {
                **{
                    key: row[key]
                    for key in (
                        "fact",
                        "state",
                        "value",
                        "raw_value",
                        "unit",
                        "currency",
                        "cost_kind",
                    )
                },
                "role": row["record_role"],
                "source": {
                    "book_id": Path(source["file_name"]).stem,
                    "page": source["page"],
                },
            }
        )
    fields = {
        "case_id": "reservation",
        "contract_id": "contract",
        "equipment_id": "equipment",
        "record_id": "record",
    }
    aliases = defaultdict(lambda: {name: [] for name in fields.values()})
    for identifier, cases in sorted(lookup.id_to_cases.items()):
        for case in sorted(cases):
            for field in sorted(lookup.id_fields[identifier]):
                aliases[case][fields[field]].append(identifier)
    return {
        "schema_version": 1,
        "dataset": "advanced",
        "source_hashes": {
            "pages.json": digest(pages_path),
            "curation_manifest": digest(curated / "manifest.json"),
            "case_sources.jsonl": digest(case_index / "case_sources.jsonl"),
            "role_facts.jsonl": digest(fact_path),
        },
        "roles": list(ROLES),
        "cases": [
            {
                "case_id": case,
                "identifiers": aliases[case],
                "profile": dict(
                    zip(
                        ("shipper", "cargo", "equipment_spec"),
                        lookup.profiles[case],
                        strict=True,
                    )
                ),
                "sources": [
                    {
                        "role": role,
                        "book_id": Path(page["file_name"]).stem,
                        "page": page["page"],
                    }
                    for role, page in zip(ROLES, sources, strict=True)
                ],
                "facts": case_facts[case],
            }
            for case, sources in sorted(lookup.cases.items())
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", required=True, type=Path)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = export(args.pages, args.curated, args.case_index, args.facts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
    print(f"Exported {len(snapshot['cases'])} verified HG cases")


if __name__ == "__main__":
    main()
