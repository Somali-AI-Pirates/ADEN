"""Build schema discovery, call Luna once per question, replay, then score."""

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np
from openai import APIError, APITimeoutError
from pydantic import ValidationError

from app.generation.context import build_context
from experimental.u_10o0o.advanced_is_lookup_ab import BASELINE, context_pages
from experimental.u_10o0o.advanced_luna_interpreter import MODEL_ID
from experimental.u_10o0o.advanced_schema_query import (
    INSTRUCTIONS,
    MAX_SCHEMAS,
    TablePlan,
    build_catalog,
    execute_plan,
    public_card,
)
from experimental.u_10o0o.advanced_source_lookup import apply_sources
from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.bm25 import BM25Index, tokenize_bigram_id_normalized
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.embedding_ablation.__main__ import (
    DEFAULT_RUN,
    encode,
    input_stats,
    validate_assets,
    validate_inputs,
)
from experimental.u_10o0o.embedding_ablation.assets import MODELS
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    object_sha,
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
from experimental.u_10o0o.prepare import CACHE_PATH

ARMS = ("baseline", "schema_query")
AUDIT = (
    ROOT / "artifacts/u_10o0o/advanced-is-record-lookup-20261111/audit-v3/audit.json"
)


def checked_catalog(directory):
    manifest = read_json(directory / "index.json")
    for filename, expected in manifest["files"].items():
        if file_sha(directory / filename) != expected:
            raise ValueError("Schema index changed")
    return read_json(directory / "catalog.json"), manifest


def build(directory):
    import torch
    from sentence_transformers import SentenceTransformer

    if directory.exists():
        raise ValueError("Use a new output path")
    validate_saved(BASELINE)
    validate_inputs(DEFAULT_RUN)
    assets = validate_assets(DEFAULT_RUN, "kure_v3")
    if read_json(BASELINE / "questions.json") != read_json(
        DEFAULT_RUN / "questions.json"
    ):
        raise ValueError("Question vectors belong to different questions")
    audit = read_json(AUDIT)
    for item in audit["inputs"]:
        if file_sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Audited source changed")
    with StructuredStore() as store:
        if store.provenance != audit["snapshot"]:
            raise ValueError("Audited snapshot changed")
        catalog = build_catalog(store)
    directory.mkdir(parents=True)
    write_json(directory / "catalog.json", catalog)
    print(
        f"Catalog: {len(catalog['cards'])} schemas, {catalog['rows']} rows", flush=True
    )
    torch.set_num_threads(2)
    model_id, revision = MODELS["kure_v3"]
    started = perf_counter()
    model = SentenceTransformer(
        model_id,
        revision=revision,
        device="cpu",
        local_files_only=True,
        model_kwargs={"dtype": torch.bfloat16},
    )
    texts = [c["text"] for c in catalog["cards"]]
    stats = input_stats(model, texts, "document")
    chunks = []
    for start in range(0, len(texts), 10):
        chunks.append(
            encode(model, texts[start : start + 10], kind="document", batch_size=2)
        )
        print(f"Encoded schemas {min(start + 10, len(texts))}/{len(texts)}", flush=True)
    vectors = np.concatenate(chunks)
    np.save(directory / "schema-vectors.npy", vectors, allow_pickle=False)
    write_json(
        directory / "index.json",
        {
            "status": "complete",
            "model": [model_id, revision],
            "device": "cpu",
            "dtype": "bfloat16",
            "threads": 2,
            "seconds": perf_counter() - started,
            "input_stats": stats,
            "audit_sha256": file_sha(AUDIT),
            "query_vectors_sha256": assets["question_sha256"],
            "question_path": str(DEFAULT_RUN / "questions.json"),
            "questions_sha256": file_sha(DEFAULT_RUN / "questions.json"),
            "files": {
                n: file_sha(directory / n)
                for n in ("catalog.json", "schema-vectors.npy")
            },
        },
    )


def discover(directory):
    catalog, manifest = checked_catalog(directory)
    questions = read_json(BASELINE / "questions.json")
    if (
        file_sha(DEFAULT_RUN / "kure_v3/question-vectors.npy")
        != manifest["query_vectors_sha256"]
    ):
        raise ValueError("Question vectors changed")
    if read_json(DEFAULT_RUN / "questions.json") != questions:
        raise ValueError("Query order changed")
    vectors = np.load(directory / "schema-vectors.npy", allow_pickle=False)
    queries = np.load(DEFAULT_RUN / "kure_v3/question-vectors.npy", allow_pickle=False)
    cards = catalog["cards"]
    documents = [{**c, "file_name": c["schema_id"], "page": 1} for c in cards]
    sparse = BM25Index(documents, tokenizer=tokenize_bigram_id_normalized)
    output = []
    for question, vector in zip(questions, queries, strict=True):
        started = perf_counter()
        scores = vectors @ vector
        indices = sorted(
            range(len(cards)), key=lambda i: (-float(scores[i]), cards[i]["schema_id"])
        )[:50]
        dense = [(documents[i], float(scores[i])) for i in indices]
        combined = weighted_rrf(
            sparse.search(question["question"], top_k=50),
            dense,
            (0.5, 0.5),
            top_k=MAX_SCHEMAS,
        )
        output.append(
            {
                "id": question["id"],
                "schema_ids": [c["schema_id"] for c, _ in combined],
                "seconds": perf_counter() - started,
            }
        )
    return catalog, questions, output


def invoke(client, question, cards, effort):
    started = perf_counter()
    telemetry = {"calls": 1, "effort": effort, "model": MODEL_ID, "status": "pending"}
    try:
        response = client.responses.parse(
            model=MODEL_ID,
            instructions=INSTRUCTIONS,
            input=json.dumps(
                {
                    "question": question,
                    "candidate_tables": [public_card(c) for c in cards],
                },
                ensure_ascii=False,
            ),
            text_format=TablePlan,
            reasoning={"effort": effort},
            max_output_tokens=2000,
            tools=[],
            store=False,
            timeout=10.0,
        )
        StructuredLunaInterpreter._record_usage(telemetry, response)
        plan = response.output_parsed
        telemetry["status"] = (
            "parsed"
            if plan is not None and response.status == "completed"
            else "incomplete_or_refused"
        )
    except APITimeoutError:
        plan, telemetry["status"] = None, "timeout"
    except APIError as error:
        plan, telemetry["status"] = None, "api_error"
        telemetry["error_class"] = type(error).__name__
        telemetry["http_status"] = getattr(error, "status_code", None)
    except (ValidationError, ValueError):
        plan, telemetry["status"] = None, "invalid_output"
    telemetry["seconds"] = perf_counter() - started
    return {
        "plan": plan.model_dump() if telemetry["status"] == "parsed" else None,
        "telemetry": telemetry,
    }


def plan_calls(directory, effort, limit):
    catalog, questions, discovery = discover(directory)
    cards = {c["schema_id"]: c for c in catalog["cards"]}
    plan_dir = directory / "plans"
    plan_dir.mkdir(exist_ok=True)
    config = {
        "effort": effort,
        "model": MODEL_ID,
        "timeout": 10,
        "max_output_tokens": 2000,
        "index_sha256": file_sha(directory / "index.json"),
        "questions_sha256": file_sha(BASELINE / "questions.json"),
        "prompt_sha256": object_sha(INSTRUCTIONS),
        "schema_sha256": object_sha(TablePlan.model_json_schema()),
        "runtime_files": loaded_runtime(),
        "maximum_calls": 280,
        "retries": 0,
    }
    path = plan_dir / "config.json"
    if path.exists():
        previous = read_json(path)
        if previous != config:
            raise ValueError("Frozen API run configuration changed")
    else:
        write_json(path, config)
    write_json(directory / "discovery.json", discovery)
    # Include the smoke call in the frozen 280-query batch. Never resubmit an
    # uncertain or completed API request merely to get a better plan.
    client = StructuredLunaInterpreter().client
    pending = [
        (q, d)
        for q, d in zip(questions, discovery, strict=True)
        if not (plan_dir / (object_sha(q["id"]) + ".json")).exists()
    ][:limit]

    def one(pair):
        q, d = pair
        result_path = plan_dir / (object_sha(q["id"]) + ".json")
        write_json(
            result_path,
            {
                "id": q["id"],
                "plan": None,
                "telemetry": {"calls": 1, "status": "interrupted_or_uncertain"},
            },
        )
        selected = [cards[sid] for sid in d["schema_ids"]]
        result = invoke(client, q["question"], selected, effort)
        result.update(
            id=q["id"],
            schema_ids=d["schema_ids"],
            question_sha256=object_sha(q["question"]),
        )
        write_json(result_path, result)
        return result["telemetry"]["status"]

    counts = Counter()
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(pending) or 1)) as executor:
            for i, status in enumerate(executor.map(one, pending), 1):
                counts[status] += 1
                if i % 10 == 0 or i == len(pending):
                    print(f"API saved {i}/{len(pending)}: {dict(counts)}", flush=True)
    finally:
        client.close()


def retrieve(directory):
    catalog, _ = checked_catalog(directory)
    cards = {c["schema_id"]: c for c in catalog["cards"]}
    manifest, traces, contexts = validate_saved(BASELINE)
    questions = read_json(BASELINE / "questions.json")
    plan_dir = directory / "plans"
    config = read_json(plan_dir / "config.json")
    check_runtime(config["runtime_files"])
    calls = [read_json(plan_dir / (object_sha(q["id"]) + ".json")) for q in questions]
    discovery = {r["id"]: r for r in read_json(directory / "discovery.json")}
    engine = LegacyBaseline()
    engine._ensure_corpus()
    pages = {page_key(p): p for p in engine.pages}
    runtime = loaded_runtime()
    manifests = {}
    for arm in ARMS:
        output = directory / arm
        output.mkdir(exist_ok=False)
        write_json(output / "questions.json", questions)
        manifests[arm] = {
            **manifest,
            "arm": arm,
            "status": "running",
            "runtime_files": runtime,
            "questions_sha256": file_sha(output / "questions.json"),
            "references_opened": False,
        }
        write_json(output / "manifest.json", manifests[arm])
    routes = Counter()
    with StructuredStore() as store:
        if store.provenance != catalog["snapshot"]:
            raise ValueError("Source snapshot changed")
        for i, (q, call) in enumerate(zip(questions, calls, strict=True), 1):
            qid = q["id"]
            if qid != call["id"]:
                raise ValueError("Plan identity mismatch")
            trace, saved = traces[qid], contexts[qid]
            ranked = [(pages[page_key(p)], p["score"]) for p in trace["ranked"]]
            packed = build_context(_context_candidates(ranked), 8, 8000, _count_tokens)
            if (
                context_pages(packed) != saved["pages"]
                or packed.context_hash != trace["context_sha256"]
            ):
                raise ValueError("Baseline did not reproduce")
            selected = [cards[s] for s in discovery[qid]["schema_ids"]]
            if call["plan"] is not None:
                result = execute_plan(
                    store,
                    q["question"],
                    TablePlan.model_validate(call["plan"]),
                    selected,
                )
            else:
                result = {
                    "status": call["telemetry"]["status"],
                    "records": [],
                    "requests": [],
                    "sql_statements": 0,
                    "seconds": 0,
                }
            routes[result["status"]] += 1
            for arm in ARMS:
                started = perf_counter()
                current, context, selection = (
                    ranked,
                    packed,
                    trace["routes"]["selection"],
                )
                if arm == "schema_query" and result["status"] == "resolved":
                    linked = [
                        pages[(r.source.book_id, r.source.page)]
                        for r in result["records"]
                    ]
                    current, context, selection = apply_sources(
                        q["question"],
                        ranked,
                        engine.lookup,
                        8000,
                        linked,
                        (ranked, packed, selection),
                    )
                final = context_pages(context)
                members = {page_key(p) for p, _ in current}
                if (
                    len(current) > 20
                    or len(members) != len(current)
                    or len(final) > 8
                    or context.token_count > 8000
                    or not {page_key(p) for p in final} <= members
                ):
                    raise ValueError("Candidate/context contract violated")
                out = {
                    **trace,
                    "arm": arm,
                    "ranked": [_source_info(p, s) for p, s in current],
                    "context_sha256": context.context_hash,
                    "context_tokens": context.token_count,
                    "final_pages": [
                        {"book_id": p["book_id"], "page": p["page"]} for p in final
                    ],
                    "routes": {
                        **trace["routes"],
                        "schema_query": result["status"]
                        if arm != "baseline"
                        else "disabled",
                        "selection": selection,
                    },
                    "schema_discovery": discovery[qid],
                    "schema_plan": call["telemetry"],
                    "schema_lookup": {
                        k: v for k, v in result.items() if k != "records"
                    },
                    "schema_records": [asdict(r.source) for r in result["records"]],
                    "context_assembly_seconds": perf_counter() - started,
                }
                append_json(directory / arm / "traces.jsonl", out)
                append_json(
                    directory / arm / "contexts.jsonl",
                    {"id": qid, "arm": arm, "pages": final},
                )
            if i % 40 == 0:
                print(f"Saved both retrieval conditions {i}/280", flush=True)
    check_runtime(runtime)
    for arm, m in manifests.items():
        output = directory / arm
        m.update(
            status="complete",
            trace_count=len(questions),
            traces_sha256=file_sha(output / "traces.jsonl"),
            contexts_sha256=file_sha(output / "contexts.jsonl"),
        )
        write_json(output / "manifest.json", m)
    write_json(
        directory / "run.json",
        {
            "status": "complete",
            "references_opened": False,
            "count": len(questions),
            "baseline": str(BASELINE.relative_to(ROOT)),
            "baseline_manifest_sha256": file_sha(BASELINE / "manifest.json"),
            "index_sha256": file_sha(directory / "index.json"),
            "plans_config_sha256": file_sha(plan_dir / "config.json"),
            "plan_files": {
                p.name: file_sha(p) for p in sorted(plan_dir.glob("*.json"))
            },
            "trace_manifests": {
                a: file_sha(directory / a / "manifest.json") for a in ARMS
            },
            "routes": routes,
            "runtime_files": runtime,
        },
    )
    print("Retrieval complete without reading references:", dict(routes), flush=True)


def score(directory):
    from app.evaluation.dataset import load_references

    run = read_json(directory / "run.json")
    if run["status"] != "complete":
        raise ValueError("Retrieval must finish before scoring")
    for name, sha in run["plan_files"].items():
        if file_sha(directory / "plans" / name) != sha:
            raise ValueError("Saved plan changed")
    saved = {}
    for arm in ARMS:
        if file_sha(directory / arm / "manifest.json") != run["trace_manifests"][arm]:
            raise ValueError("Saved traces changed")
        saved[arm] = validate_saved(directory / arm)
    manifest = saved["baseline"][0]
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    results = {
        arm: [
            score_item(
                m,
                refs[m["suite"]][m["question_id"]],
                traces[m["id"]],
                contexts[m["id"]],
                pages,
            )
            for m in manifest["items"]
        ]
        for arm, (_, traces, contexts) in saved.items()
    }
    catalog, _ = checked_catalog(directory)
    card_sources = {
        c["schema_id"]: {(r["book_id"], r["page"]) for r in c["sources"]}
        for c in catalog["cards"]
    }
    all_is = set().union(*card_sources.values())
    diagnostics = []
    for m in manifest["items"]:
        qid = m["id"]
        trace = saved["schema_query"][1][qid]
        reference = refs[m["suite"]][m["question_id"]]
        expected = (
            {
                page_key(e)
                for group in reference.get("evidence_groups", [])
                for e in group
            }
            if m["suite"].startswith("official_")
            else {
                page_key(e)
                for req in reference.get("evidence_requirements", [])
                for e in req["primary"]
            }
        )
        expected_is = expected & all_is
        schema_pages = set().union(
            *(card_sources[s] for s in trace["schema_discovery"]["schema_ids"])
        )
        row_pages = {page_key(s) for s in trace["schema_records"]}
        diagnostics.append(
            {
                "id": qid,
                "suite": m["suite"],
                "schema_ids": trace["schema_discovery"]["schema_ids"],
                "route": trace["routes"]["schema_query"],
                "api": trace["schema_plan"],
                "lookup": trace["schema_lookup"],
                "record_sources": trace["schema_records"],
                "expected_pages_in_is_tables": len(expected_is),
                "schema_source_coverage_count": len(expected_is & schema_pages),
                "queried_source_coverage_count": len(expected_is & row_pages),
                "queried_unreferenced_page_count": len(row_pages - expected),
                "context_changed": trace["context_sha256"]
                != saved["baseline"][1][qid]["context_sha256"],
                "discovery_seconds": trace["schema_discovery"]["seconds"],
                "assembly_seconds": trace["context_assembly_seconds"],
            }
        )
    write_json(
        directory / "summary.json", {a: aggregates(r) for a, r in results.items()}
    )
    write_json(directory / "per-question.json", results)
    write_json(
        directory / "differences.json",
        differences({"baseline": results["baseline"], "A": results["schema_query"]}),
    )
    write_json(directory / "diagnostics.json", diagnostics)
    write_json(
        directory / "score-manifest.json",
        {
            "run_sha256": file_sha(directory / "run.json"),
            "references_read_after_retrieval": True,
            "references": [
                {"path": r["references"], "sha256": file_sha(ROOT / r["references"])}
                for r in manifest["question_sources"]
            ],
            "interpretation_accuracy": "not measured: no independent intent gold",
            "schema_coverage": "source-membership proxy; not semantic correctness",
        },
    )
    for a, rows in results.items():
        summary = aggregates(rows)
        print(
            a,
            {
                k: summary[k]["stages"]["final"]["complete"]
                for k in ("official_all", "categories_positive")
            },
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "plan", "retrieve", "score"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--limit", type=int, default=280)
    args = parser.parse_args()
    directory = args.output.resolve()
    if args.command == "plan":
        if not 1 <= args.limit <= 280:
            parser.error("--limit must be 1..280")
        plan_calls(directory, args.effort, args.limit)
    else:
        {"build": build, "retrieve": retrieve, "score": score}[args.command](directory)


if __name__ == "__main__":
    main()
