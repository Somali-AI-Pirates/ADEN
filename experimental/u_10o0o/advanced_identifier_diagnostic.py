"""Diagnose identifier wording and mixed requests without changing the baseline."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.ingestion.curation import read_rows, write_rows
from experimental.u_10o0o import advanced_experiment_eval as ev
from experimental.u_10o0o.advanced_case_sources import CURATED
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_clause_binding import roles_in
from experimental.u_10o0o.advanced_field_roles import FIELD_POOL, HEADING_WEIGHT
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, location, matched_case
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    HYBRID_WEIGHTS,
    KURE,
    MODELS,
    retain_reranked_head,
    rerank_variants,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.hybrid import RRF_K, weighted_rrf
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.search import load_index, rank_pages

ROLE_NAMES = {
    "bill": "비용 청구 명세",
    "receipt": "수납 및 미결 인계",
    "event": "현장 사건 수신대장",
}
DEFAULT_INPUT = Path("artifacts/u_10o0o/advanced-identifier-diagnostic-20261110")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def frozen_fielded(index, question):
    """Extend scores without extending the baseline's heading-bonus eligibility."""
    body = index.base_index.search(question, top_k=len(index.pages))
    if not body:
        return []
    scores = {id(page): score / body[0][1] for page, score in body}
    eligible = {id(page) for page, _ in body[:FIELD_POOL]}
    if index.heading_index is not None:
        headings = index.heading_index.search(question, top_k=FIELD_POOL)
        if headings:
            for field, score in headings:
                key = id(index._heading_page_by_id[id(field)])
                if key in eligible:
                    scores[key] += HEADING_WEIGHT * score / headings[0][1]
    ordered = [(page, scores[id(page)]) for page, _ in body]
    return sorted(
        ordered, key=lambda row: (-row[1], index._page_index_by_id[id(row[0])])
    )


def actual_confirmation(question, retrieval, lookup):
    """Only report a target when the actual source-lookup route ran."""
    route = retrieval[4].split("/")
    cases = set()
    if route[0] == "explicit_hg":
        case = matched_case(question, lookup.cases)
        if case:
            cases.add(case)
    elif route[0] == "indirect_hg":
        case, _ = lookup.profile_index.candidate(question)
        if case:
            cases.add(case)
    if route[1] in ("unique", "pair"):
        cases.update(retrieval[3])
    return sorted(cases)


def evidence_reference(citations):
    return {
        "evidence_groups": [citations],
        "evidence_requirements": [{"primary": citations}],
    }


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["condition"]].append(row)
        if row["condition"] in ("identifier_variant", "indirect"):
            grouped[f"{row['condition']}/{row['variant']}"].append(row)
    summary = {}
    for name, group in grouped.items():
        entry = {
            "count": len(group),
            "routes": dict(Counter(r["route"] for r in group)),
        }
        for field in (
            "target_confirmed_correct",
            "confirmed_target_with_role_cue",
            "role_cue_contains_expected",
            "role_cue_exact",
            "candidate_complete",
            "expanded_complete",
            "final_complete",
            "wrong_confirmation",
            "any_confirmation",
            "unsupported_multiple_confirmation",
            "confirmation_outside_possible_cases",
        ):
            values = [int(r[field]) for r in group if field in r]
            if values:
                entry[field] = {"hits": sum(values), "total": len(values)}
        for field in ("page_recall_at_20", "page_recall_at_8"):
            values = [r[field] for r in group if field in r]
            if values:
                entry[field] = sum(values) / len(values)
        entry["latency"] = ev.latency([r["elapsed_ms"] for r in group])
        summary[name] = entry
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    log_path = output / "run.log"

    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    inputs = args.inputs
    sources = [
        inputs / "questions.json",
        inputs / "references.json",
        inputs / "fixture-manifest.json",
        inputs / "mixed-inputs.json",
        inputs / "mixed-references.json",
        CACHE_PATH,
        VECTORS_PATH,
        CURATED / "manifest.json",
        CURATED / "sources.jsonl",
        *sorted((CURATED / "records").glob("*.jsonl")),
        Path("app/generation/context.py"),
        Path("shared/contracts.py"),
        Path("app/ingestion/layout.py"),
        Path("app/ingestion/curation.py"),
        Path(__file__).with_name("context_evidence.py"),
        *sorted(path for path in CASE_INDEX.rglob("*") if path.is_file()),
        *sorted(path for path in FACTS.rglob("*") if path.is_file()),
        *sorted(Path(__file__).parent.glob("advanced_*.py")),
        *[
            Path(__file__).with_name(name)
            for name in ("bm25.py", "hybrid.py", "embeding.py", "search.py")
        ],
    ]
    # Hash reference bytes for provenance; do not parse them before retrieval.
    stamps = [ev.stamp(path) for path in sources]
    questions = read_json(inputs / "questions.json")
    mixed = read_json(inputs / "mixed-inputs.json")
    if len(questions) != 144 or len({q["id"] for q in questions}) != 144:
        raise ValueError("Expected the frozen 144-query fixture.")
    forbidden = {"original_case_id", "source_citations", "possible_case_ids"}
    if any(forbidden.intersection(q) for q in questions):
        raise ValueError("Gold fields appeared in retrieval inputs.")
    if len(mixed) != 6 or len({q["id"] for q in mixed}) != 6:
        raise ValueError("Expected six distinct mixed diagnostic queries.")
    for q in mixed:
        if any(
            not isinstance(q.get(key), str) or not q[key].strip()
            for key in ("id", "question", "literal_is_query", "planned_is_query")
        ):
            raise ValueError("Mixed input contains an empty or invalid query.")
    groups = defaultdict(list)
    for q in questions:
        if any(
            not isinstance(q.get(key), str) or not q[key].strip()
            for key in ("id", "group_id", "condition", "variant", "question")
        ):
            raise ValueError("Identifier input contains an empty or invalid field.")
        groups[q["group_id"]].append((q["condition"], q["variant"]))
    expected = Counter(
        {
            ("full_identifier", "canonical"): 1,
            ("identifier_variant", "lowercase"): 1,
            ("identifier_variant", "unicode_dash"): 1,
            ("identifier_variant", "separator_spaces"): 1,
        }
    )
    if len(groups) != 24 or any(
        len(rows) != 6
        or any(Counter(rows)[key] != count for key, count in expected.items())
        or sum(c == "indirect" for c, _ in rows) != 1
        or sum(c == "insufficient" for c, _ in rows) != 1
        for rows in groups.values()
    ):
        raise ValueError("Each of 24 targets must have the six frozen conditions.")
    if stamps != [ev.stamp(path) for path in sources]:
        raise RuntimeError("Inputs changed while opening the fixture.")
    manifest = {
        "inputs_and_code": stamps,
        "baseline_unchanged": True,
        "candidate_k": CANDIDATES,
        "final_k": FINAL_K,
        "tokens": DEFAULT_CONTEXT_TOKENS,
        "rrf_k": RRF_K,
        "weights": HYBRID_WEIGHTS,
        "embedding": [MODEL_ID, MODEL_REVISION],
        "reranker": [KURE, MODELS[KURE]],
        "external_api_calls": 0,
        "execution": "sequential queries; cached models; diagnostic deep ranks excluded from baseline",
    }
    ev.write_json(output / "manifest.json", manifest)
    start = perf_counter()
    pages, vectors = load_index()
    lookup = load_target_lookup(
        pages, curated=CURATED, case_index=CASE_INDEX, facts=FACTS
    )
    storage = load_storage_lookup(
        pages, curated=CURATED, case_index=CASE_INDEX, facts=FACTS
    )
    search = ev.Search(pages, vectors)
    # Warm model loading separately from measured query latency.
    _ = search.index, search.embedder, search.reranker
    log(
        f"Loaded {len(pages)} pages, {len(lookup.cases)} cases in {perf_counter() - start:.2f}s"
    )
    is_sources = {
        (row["book_id"], row["page"])
        for row in read_rows(CURATED / "sources.jsonl")
        if re.match(r"IS\d+\s*\|", row["heading"])
    }
    if len(is_sources) != 3750:
        raise ValueError("Expected 3,750 page-level IS sources.")
    stored = {}
    traces = []

    def retrieve(qid, question, *, arm="baseline", deep=False, is_only=False):
        started = perf_counter()
        sparse = search.index.fielded(question, top_k=CANDIDATES)
        vector = search.embedder.encode_query(
            [question], normalize_embeddings=True, show_progress_bar=False
        )[0]
        dense = rank_pages(
            vector, vectors, pages, top_k=len(pages) if deep else CANDIDATES
        )
        all_sparse = frozen_fielded(search.index, question) if deep else sparse
        if deep and ev.sources(all_sparse[:CANDIDATES]) != ev.sources(sparse):
            raise AssertionError(
                "Extended BM25 diagnostics changed the baseline top 20."
            )
        branch_sparse = (
            [row for row in all_sparse if location(row[0]) in is_sources][:CANDIDATES]
            if is_only
            else sparse
        )
        branch_dense = (
            [row for row in dense if location(row[0]) in is_sources][:CANDIDATES]
            if is_only
            else dense[:CANDIDATES]
        )
        pool = weighted_rrf(
            branch_sparse, branch_dense, HYBRID_WEIGHTS, top_k=CANDIDATES
        )
        head = rerank_variants(
            search.reranker,
            [{"question": question}],
            {"baseline": [pool]},
            lambda _: None,
        )["baseline"][0]
        order = retain_reranked_head(pool, head)
        retrieval, linked = ev.finish(question, pool, order, lookup, storage)
        elapsed = (perf_counter() - started) * 1000
        trace = {
            "id": qid,
            "arm": arm,
            **ev.trace(retrieval, linked, lookup, order),
            "bm25": ev.sources(branch_sparse),
            "dense": ev.sources(branch_dense),
            "kure_full": ev.sources(head),
            "actual_confirmed_cases": actual_confirmation(question, retrieval, lookup),
            "query_role_cues": list(roles_in(question, getattr(lookup, "rules", None))),
            "elapsed_ms": elapsed,
            "tokens": retrieval[2].token_count,
            "token_excluded_chunk_ids": list(retrieval[2].excluded_chunk_ids),
        }
        if deep:
            trace["full_bm25_diagnostic"] = ev.sources(all_sparse)
            trace["full_dense_diagnostic"] = ev.sources(dense)
        if len(trace["pool"]) > CANDIDATES or len(trace["packed"]) > FINAL_K:
            raise AssertionError("Page budget exceeded.")
        if trace["tokens"] > DEFAULT_CONTEXT_TOKENS:
            raise AssertionError("Token budget exceeded.")
        stored[qid, arm] = retrieval
        traces.append(trace)
        # Persist retrieval evidence before opening any reference file.
        with (output / "traces.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
        with (output / "contexts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "id": qid,
                        "arm": arm,
                        "context_sha256": retrieval[2].context_hash,
                        "pages": [
                            {
                                "book_id": c.chunk.book_id,
                                "page": c.chunk.page,
                                "text": c.chunk.text,
                            }
                            for c in retrieval[2].final_chunks
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    for i, question in enumerate(questions, 1):
        retrieve(question["id"], question["question"])
        if i % 12 == 0:
            log(f"Identifier queries saved: {i}/{len(questions)}")
    for row in mixed:
        for arm, field, restricted in (
            ("full", "question", False),
            ("literal_is", "literal_is_query", False),
            ("planned_is", "planned_is_query", False),
            ("full_is_scope", "question", True),
            ("literal_is_scope", "literal_is_query", True),
        ):
            retrieve(row["id"], row[field], arm=arm, deep=True, is_only=restricted)
        log(f"Mixed diagnostics saved: {row['id']}")

    log("All retrievals saved. Begin reference scoring.")
    references = read_json(inputs / "references.json")
    if set(references) != {q["id"] for q in questions}:
        raise ValueError("Identifier reference keys differ from frozen inputs.")
    trace_map = {(row["id"], row["arm"]): row for row in traces}
    scores = []
    for q in questions:
        ref = references[q["id"]]
        possible = set(ref["possible_case_ids"])
        if (
            type(ref["sufficient"]) is not bool
            or ref["original_case_id"] not in possible
            or (ref["sufficient"] and possible != {ref["original_case_id"]})
            or (not ref["sufficient"] and len(possible) < 2)
        ):
            raise ValueError("Reference uniqueness/ambiguity invariant is invalid.")
        trace = trace_map[q["id"], "baseline"]
        confirmed = trace["actual_confirmed_cases"]
        row = {key: q[key] for key in ("id", "group_id", "condition", "variant")}
        row.update(
            route=trace["route"],
            elapsed_ms=trace["elapsed_ms"],
            sufficient=ref["sufficient"],
            possible_cases=len(ref["possible_case_ids"]),
            any_confirmation=bool(confirmed),
            unsupported_multiple_confirmation=not ref["sufficient"]
            and len(confirmed) > 1,
            confirmation_outside_possible_cases=bool(
                set(confirmed) - set(ref["possible_case_ids"])
            ),
            wrong_confirmation=bool(confirmed)
            and (
                len(confirmed) == 1
                if not ref["sufficient"]
                else set(confirmed) != {ref["original_case_id"]}
            ),
        )
        if ref["sufficient"]:
            if not any(c["kind"] == "requested_fact" for c in ref["source_citations"]):
                raise ValueError("Sufficient query has no requested-fact ground truth.")
            parsed = set(trace["query_role_cues"])
            expected = ROLE_NAMES[ref["role"]]
            correct = set(confirmed) == {ref["original_case_id"]}
            row.update(
                target_confirmed_correct=correct,
                role_cue_contains_expected=expected in parsed,
                role_cue_exact=parsed == {expected},
                confirmed_target_with_role_cue=correct and expected in parsed,
                **ev.score_one(
                    evidence_reference(
                        [
                            c
                            for c in ref["source_citations"]
                            if c["kind"] == "requested_fact"
                        ]
                    ),
                    stored[q["id"], "baseline"],
                ),
            )
        scores.append(row)
    write_rows(output / "scores.jsonl", scores)
    summary = summarize(scores)
    baseline_by_group = {
        r["group_id"]: r for r in scores if r["condition"] == "full_identifier"
    }
    changes = []
    for row in scores:
        if row["condition"] in ("full_identifier", "insufficient"):
            continue
        before = baseline_by_group[row["group_id"]]
        changes.append(
            {
                "id": row["id"],
                "group_id": row["group_id"],
                "variant": row["variant"],
                **{
                    f"{stage}_delta": int(row[f"{stage}_complete"])
                    - int(before[f"{stage}_complete"])
                    for stage in ("candidate", "expanded", "final")
                },
                "confirmation_delta": int(row["target_confirmed_correct"])
                - int(before["target_confirmed_correct"]),
            }
        )
    write_rows(output / "paired-changes.jsonl", changes)
    mixed_references = read_json(inputs / "mixed-references.json")
    if set(mixed_references) != {q["id"] for q in mixed}:
        raise ValueError("Mixed reference keys differ from frozen inputs.")
    mixed_scores = []
    for q in mixed:
        ref = mixed_references[q["id"]]
        for arm in (
            "full",
            "literal_is",
            "planned_is",
            "full_is_scope",
            "literal_is_scope",
        ):
            retrieval = stored[q["id"], arm]
            trace = trace_map[q["id"], arm]
            row = {"id": q["id"], "arm": arm, "route": trace["route"]}
            for label, citations in (
                ("is", ref["is_evidence"]),
                ("hg", ref["hg_evidence"]),
            ):
                row[label] = ev.score_one(evidence_reference(citations), retrieval)
                keys = {(c["book_id"], c["page"]) for c in citations}
                row[f"{label}_positions"] = {
                    stage: {
                        f"{book}:{page}": next(
                            (
                                i
                                for i, key in enumerate(trace[stage], 1)
                                if key == (book, page) or key == [book, page]
                            ),
                            None,
                        )
                        for book, page in sorted(keys)
                    }
                    for stage in (
                        "bm25",
                        "dense",
                        "pool",
                        "kure_full",
                        "reranked",
                        "linked",
                        "expanded",
                        "packed",
                        "full_bm25_diagnostic",
                        "full_dense_diagnostic",
                    )
                }
            mixed_scores.append(row)
    write_rows(output / "mixed-scores.jsonl", mixed_scores)
    if stamps != [ev.stamp(path) for path in sources]:
        raise RuntimeError("Inputs or baseline source changed during this run.")
    manifest["completed_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["retrieval_executions"] = len(stored)
    manifest["wall_seconds"] = perf_counter() - start
    ev.write_json(output / "manifest.json", manifest)
    ev.write_json(output / "summary.json", summary)
    log(f"Complete: {len(scores)} identifier rows and {len(mixed_scores)} mixed rows.")


if __name__ == "__main__":
    main()
