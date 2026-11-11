"""Audit IS record keys and replay their source lookup on saved KURE/HG results."""

import argparse
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from app.generation.context import build_context
from app.ingestion.curation import read_rows
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_is_record_lookup import (
    LEGACY_COLUMNS,
    RECORD_CODE,
    RECORD_COLUMNS,
    lookup_record,
    resolve_records,
    source_record_key,
)
from experimental.u_10o0o.advanced_source_lookup import apply_sources
from experimental.u_10o0o.advanced_structured_curation import _verified_layout_pages
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import loaded_runtime
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    differences,
    page_key,
    score_item,
    validate_saved,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import (
    LegacyBaseline,
    _source_info,
)
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR

BASELINE = ROOT / "artifacts/u_10o0o/advanced-kure-hg-role-ab-20261111/v1/lookup"
ARMS = ("baseline", "legacy_is", "expanded_is")
MAX_CANDIDATES, FINAL_PAGES, TOKEN_BUDGET = 20, 8, 8000


def audit(output):
    """Check the existing DB against source-derived records and actual PDF pages."""
    output.mkdir(parents=True, exist_ok=False)
    sources = {s["source_id"]: s for s in read_rows(CURATED / "sources.jsonl")}
    curated_manifest = read_json(CURATED / "manifest.json")
    layout = _verified_layout_pages(curated_manifest, sources)
    expected, forms, columns = {}, Counter(), Counter()
    unsupported_rows = 0
    inputs = [
        {"path": str(p.resolve().relative_to(ROOT)), "sha256": file_sha(p)}
        for p in (CURATED / "sources.jsonl", CURATED / "manifest.json", CACHE_PATH)
    ]
    for path in sorted((CURATED / "records").glob("*.jsonl")):
        inputs.append(
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": file_sha(path)}
        )
        for row in read_rows(path):
            if row["source_id"] not in layout or row["kind"] != "table_row":
                continue
            if row["review_status"] != "parsed":
                continue
            fields = row["fields"]
            forms[tuple(sorted(fields))] += 1
            keys = [
                (k, key)
                for k, v in fields.items()
                if (key := source_record_key(k, v)) is not None
            ]
            if not keys:
                unsupported_rows += 1
                continue
            if len(keys) != 1 or keys[0][0] not in RECORD_COLUMNS:
                raise ValueError(
                    "Observed IS row is outside the frozen supported columns"
                )
            col, key = keys[0]
            if key in expected:
                raise ValueError(
                    "Real corpus has duplicate record keys; audit separately"
                )
            raw = "\n".join(
                layout[row["source_id"]].splitlines()[
                    row["line_start"] - 1 : row["line_end"]
                ]
            )
            if key not in raw:
                raise ValueError(
                    "Record key absent from the recorded original line range"
                )
            expected[key] = (col, row)
            columns[col] += 1
    if set(columns) != set(RECORD_COLUMNS):
        raise ValueError("Frozen observed key-column set changed")
    examples, sql_seconds = {}, []
    with StructuredStore() as store:
        provenance = store.provenance
        if provenance["inputs"]["page_cache_sha256"] != file_sha(CACHE_PATH):
            raise ValueError("Structured snapshot and context page cache differ")
        if provenance["inputs"]["curation_manifest_sha256"] != file_sha(
            CURATED / "manifest.json"
        ):
            raise ValueError("Structured snapshot and curation version differ")
        for book, name, sha in store._execute(
            "SELECT book_id,file_name,file_sha256 FROM source_documents"
        ):
            path = DATA_DIR / "documents" / name
            if file_sha(path) != sha:
                raise ValueError(f"Original PDF fingerprint differs: {book}")
        for key, (column, row) in expected.items():
            started = perf_counter()
            matches = lookup_record(store, key)
            sql_seconds.append(perf_counter() - started)
            if len(matches) != 1:
                raise ValueError(
                    "Every supported original record must be uniquely retrievable"
                )
            match = matches[0]
            if (
                match.record_id != row["record_id"]
                or match.fields != row["fields"]
                or match.source.source_id != row["source_id"]
                or (match.source.line_start, match.source.line_end)
                != (row["line_start"], row["line_end"])
            ):
                raise ValueError("SQL result fields or original line provenance differ")
            examples.setdefault(column, (key, row))
        # Retain only aggregate ambiguity evidence, not source values.
        occurrences = defaultdict(set)
        for row in store._execute(
            "SELECT fields.field_name, fields.raw_value, records.source_id "
            "FROM record_fields fields JOIN source_records records USING(record_id) "
            "JOIN source_pages pages USING(source_id) WHERE pages.page_family='IS' "
            "AND records.kind='table_row' AND fields.field_name IN "
            "('예약','예약·장비','대상','계약','연결청구','연결 대상','예약·장비관리번호')"
        ):
            for key in re.findall(
                r"(?<![A-Za-z0-9-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9-])",
                row["raw_value"],
            ):
                if not key.startswith("IS"):
                    occurrences[(row["field_name"], key)].add(row["source_id"])
        record_fields = defaultdict(dict)
        record_pages = {}
        for row in store._execute(
            "SELECT fields.record_id, fields.field_name, fields.raw_value, records.source_id "
            "FROM record_fields fields JOIN source_records records USING(record_id) "
            "JOIN source_pages pages USING(source_id) WHERE pages.page_family='IS' "
            "AND records.kind='table_row' AND records.parse_status='parsed'"
        ):
            record_fields[row["record_id"]][row["field_name"]] = row["raw_value"]
            record_pages[row["record_id"]] = row["source_id"]
        invoices = defaultdict(set)
        for rid, fields in record_fields.items():
            key = fields.get("청구번호", "")
            if re.fullmatch(r"INV-[A-Z0-9-]+", key):
                invoices[key].add(rid)
        invoice_links = {label: Counter() for label in ("수정통지", "입금 기록")}
        linked_records = set()
        for rid, fields in record_fields.items():
            labels = set(invoice_links) & fields.keys()
            if not labels or "연결청구" not in fields:
                continue
            targets = invoices.get(fields["연결청구"], set())
            status = (
                "unique_target"
                if len(targets) == 1
                else "ambiguous_target"
                if targets
                else "no_target"
            )
            for label in labels:
                invoice_links[label][status] += 1
                if len(targets) == 1:
                    linked_records.add((rid, next(iter(targets))))
    pdf_checks = []
    for column, (key, row) in sorted(examples.items()):
        source = sources[row["source_id"]]
        original = subprocess.run(
            [
                "pdftotext",
                "-layout",
                "-f",
                str(source["page"]),
                "-l",
                str(source["page"]),
                str(DATA_DIR / "documents" / source["file_name"]),
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if original.strip() != layout[row["source_id"]].strip():
            raise ValueError("Fresh PDF layout differs from the verified cache")
        pdf_checks.append(
            {
                "column": column,
                "book_id": source["book_id"],
                "page": source["page"],
                "matched": True,
            }
        )
    cross = set()
    for sid, text in layout.items():
        for key in RECORD_CODE.findall(text):
            if key in expected and expected[key][1]["source_id"] != sid:
                cross.add((sid, key))
    sorted_times = sorted(sql_seconds)
    result = {
        "status": "complete",
        "is_pages": len(layout),
        "parsed_table_rows": sum(forms.values()),
        "table_signatures": len(forms),
        "key_columns": dict(sorted(columns.items())),
        "exact_keys": len(expected),
        "ambiguous_keys": 0,
        "key_pages": len({r["source_id"] for _, r in expected.values()}),
        "legacy_keys": sum(n for c, n in columns.items() if c in LEGACY_COLUMNS),
        "legacy_pages": len(
            {r["source_id"] for c, r in expected.values() if c in LEGACY_COLUMNS}
        ),
        "unsupported_rows": unsupported_rows,
        "original_line_range_checks": len(expected),
        "original_pdf_hash_checks": 120,
        "sql_record_field_and_source_checks": len(expected),
        "fresh_pdf_samples": pdf_checks,
        "explicit_is_code_cross_page_links": len(cross),
        "explicit_invoice_links": invoice_links,
        "unique_invoice_row_edges": len(linked_records),
        "referenced_unique_invoice_rows": len({target for _, target in linked_records}),
        "invoice_id_unique": len(invoices),
        "invoice_id_ambiguous": sum(len(v) > 1 for v in invoices.values()),
        "shared_non_record_keys_with_multiple_pages": sum(
            len(v) > 1 for v in occurrences.values()
        ),
        "shared_non_record_key_max_pages": max(map(len, occurrences.values())),
        "warm_sql_ms": {
            "p50": sorted_times[len(sorted_times) // 2] * 1000,
            "p95": sorted_times[int(len(sorted_times) * 0.95)] * 1000,
        },
        "snapshot": provenance,
        "inputs": inputs,
        "notes": [
            "Record keys do not establish HG-like cases or fixed role bundles.",
            "Shared booking/equipment/contract mentions are not automatic same-event links.",
            "This is deterministic source/field validation, not human semantic review.",
        ],
        "references_opened": False,
    }
    write_json(output / "audit.json", result)
    print(
        f"Verified {len(expected)} exact IS records and {len(pdf_checks)} fresh PDF samples",
        flush=True,
    )


def context_pages(context):
    return [
        {"book_id": c.chunk.book_id, "page": c.chunk.page, "text": c.chunk.text}
        for c in context.final_chunks
    ]


def retrieve(output, baseline, audit_path):
    verified = read_json(audit_path)
    if verified["status"] != "complete":
        raise ValueError("Completed source audit is required")
    for item in verified["inputs"]:
        if file_sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Audited original input changed")
    manifest, traces, contexts = validate_saved(baseline)
    questions = read_json(baseline / "questions.json")
    engine = LegacyBaseline()
    engine._ensure_corpus()
    pages = {page_key(p): p for p in engine.pages}
    output.mkdir(parents=True, exist_ok=False)
    runtime = loaded_runtime()
    manifests = {}
    for arm in ARMS:
        directory = output / arm
        directory.mkdir()
        write_json(directory / "questions.json", questions)
        manifests[arm] = {
            **manifest,
            "arm": arm,
            "status": "running",
            "runtime_files": runtime,
            "questions_sha256": file_sha(directory / "questions.json"),
            "references_opened": False,
        }
        write_json(directory / "manifest.json", manifests[arm])
    replay_count, routes = 0, {arm: Counter() for arm in ARMS}
    with StructuredStore() as store:
        if store.provenance != verified["snapshot"]:
            raise ValueError("Audited structured snapshot changed")
        for i, question in enumerate(questions, 1):
            qid, text = question["id"], question["question"]
            trace, saved = traces[qid], contexts[qid]
            ranked = [(pages[page_key(r)], r["score"]) for r in trace["ranked"]]
            packed = build_context(
                _context_candidates(ranked), FINAL_PAGES, TOKEN_BUDGET, _count_tokens
            )
            if (
                context_pages(packed) != saved["pages"]
                or packed.context_hash != trace["context_sha256"]
            ):
                raise ValueError(f"Saved baseline context did not reproduce: {qid}")
            replay_count += 1
            for arm in ARMS:
                started, old_count = perf_counter(), store.query_count
                current, context, selection = (
                    ranked,
                    packed,
                    trace["routes"]["selection"],
                )
                resolution = None
                if arm != "baseline":
                    resolution = resolve_records(
                        text, store, legacy_only=arm == "legacy_is"
                    )
                    if resolution.status == "resolved":
                        links = [
                            pages[(r.source.book_id, r.source.page)]
                            for r in resolution.records
                        ]
                        current, context, selection = apply_sources(
                            text,
                            ranked,
                            engine.lookup,
                            TOKEN_BUDGET,
                            links,
                            (ranked, packed, selection),
                        )
                status = resolution.status if resolution else "disabled"
                routes[arm][status] += 1
                final = context_pages(context)
                members = {page_key(p) for p, s in current}
                if (
                    len(current) > MAX_CANDIDATES
                    or len(members) != len(current)
                    or len(final) > FINAL_PAGES
                    or context.token_count > TOKEN_BUDGET
                    or not {page_key(p) for p in final} <= members
                ):
                    raise ValueError("Candidate/context limits or membership differ")
                result = {
                    **trace,
                    "arm": arm,
                    "ranked": [_source_info(p, s) for p, s in current],
                    "context_sha256": context.context_hash,
                    "context_tokens": context.token_count,
                    "final_pages": [
                        {"book_id": p["book_id"], "page": p["page"]} for p in final
                    ],
                    "routes": {**trace["routes"], "is": status, "selection": selection},
                    "is_records": [
                        {"role": r.role, "source": asdict(r.source)}
                        for r in resolution.records
                    ]
                    if resolution
                    else [],
                    "is_sql_statements": store.query_count - old_count,
                    "is_lookup_and_context_seconds": perf_counter() - started,
                }
                append_json(output / arm / "traces.jsonl", result)
                append_json(
                    output / arm / "contexts.jsonl",
                    {"id": qid, "arm": arm, "pages": final},
                )
            if i % 40 == 0:
                print(f"Saved {i}/280 queries for all three conditions", flush=True)
    check_runtime(runtime)
    for arm, m in manifests.items():
        directory = output / arm
        m.update(
            status="complete",
            trace_count=len(questions),
            traces_sha256=file_sha(directory / "traces.jsonl"),
            contexts_sha256=file_sha(directory / "contexts.jsonl"),
        )
        write_json(directory / "manifest.json", m)
    write_json(
        output / "run.json",
        {
            "status": "complete",
            "question_count": len(questions),
            "baseline_replayed": replay_count,
            "baseline": str(baseline.relative_to(ROOT)),
            "baseline_manifest_sha256": file_sha(baseline / "manifest.json"),
            "source_audit_sha256": file_sha(audit_path),
            "source_audit": str(audit_path.relative_to(ROOT)),
            "arms": ARMS,
            "routes": routes,
            "candidate_limit": MAX_CANDIDATES,
            "final_pages": FINAL_PAGES,
            "context_tokens": TOKEN_BUDGET,
            "trace_manifests": {
                a: file_sha(output / a / "manifest.json") for a in ARMS
            },
            "references_opened": False,
            "external_api_calls": 0,
            "method": "Saved KURE-v3 5:5 and HG-date results; only exact IS lookup and existing packing replayed",
        },
    )


def score(output):
    from app.evaluation.dataset import load_references

    run = read_json(output / "run.json")
    if run["status"] != "complete":
        raise ValueError("All retrieval results must be saved before scoring")
    saved = {}
    for arm in ARMS:
        if file_sha(output / arm / "manifest.json") != run["trace_manifests"][arm]:
            raise ValueError("Retrieval manifest changed")
        saved[arm] = validate_saved(output / arm)
    manifest = saved["baseline"][0]
    for arm in ARMS:
        if any(
            saved[arm][0][k] != manifest[k]
            for k in ("items", "questions_sha256", "question_sources")
        ):
            raise ValueError("Compared query sources differ")
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    results = {}
    for arm, (_, traces, contexts) in saved.items():
        results[arm] = [
            score_item(
                m,
                refs[m["suite"]][m["question_id"]],
                traces[m["id"]],
                contexts[m["id"]],
                pages,
            )
            for m in manifest["items"]
        ]
    changes = {
        a: differences({"baseline": results["baseline"], "A": results[a]})
        for a in ARMS
        if a != "baseline"
    }
    write_json(
        output / "summary.json", {a: aggregates(rows) for a, rows in results.items()}
    )
    write_json(output / "per-question.json", results)
    write_json(output / "differences.json", changes)
    diagnostics = {}
    for arm, (_, traces, _) in saved.items():
        diagnostics[arm] = [
            {
                "id": m["id"],
                "routes": traces[m["id"]]["routes"],
                "is_records": traces[m["id"]]["is_records"],
                "is_sql_statements": traces[m["id"]]["is_sql_statements"],
                "is_lookup_and_context_seconds": traces[m["id"]][
                    "is_lookup_and_context_seconds"
                ],
                "context_changed": traces[m["id"]]["context_sha256"]
                != saved["baseline"][1][m["id"]]["context_sha256"],
            }
            for m in manifest["items"]
        ]
    write_json(output / "routes.json", diagnostics)
    write_json(
        output / "score-manifest.json",
        {
            "run_sha256": file_sha(output / "run.json"),
            "references_read_after_retrieval": True,
            "reference_files": [
                {"path": r["references"], "sha256": file_sha(ROOT / r["references"])}
                for r in manifest["question_sources"]
            ],
        },
    )
    for arm in ARMS:
        summary = aggregates(results[arm])
        print(
            arm,
            {
                s: summary[s]["stages"]["final"]["complete"]
                for s in ("official_all", "categories_positive")
            },
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "retrieve", "score"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.baseline = args.baseline.resolve()
    if args.audit is not None:
        args.audit = args.audit.resolve()
    if args.command == "audit":
        audit(args.output)
    elif args.command == "retrieve":
        if args.audit is None:
            parser.error("retrieve requires --audit")
        retrieve(args.output, args.baseline, args.audit)
    else:
        score(args.output)


if __name__ == "__main__":
    main()
