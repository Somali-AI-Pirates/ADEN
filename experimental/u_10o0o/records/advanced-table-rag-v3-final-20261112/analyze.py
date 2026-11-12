"""Trace reviewed failures through saved retrievals; never call a model API."""

from collections import Counter
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from transformers import AutoTokenizer

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
)
from experimental.u_10o0o.table_rag.agent import observation
from experimental.u_10o0o.table_rag.store import TableStore
from experimental.u_10o0o.table_rag.retrieval import KURE, RERANK_MODELS

PREVIOUS = import_module(
    "experimental.u_10o0o.records.advanced-table-rag-kure-v2-preservation-20261111.score"
)
RECORDS = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-v3-final-20261112"
V5 = ROOT / "artifacts/u_10o0o/advanced-table-rag-20261111/v5"
TARGETS = {
    "official_development:AS-DEV06": ["AS009:p0001", "AS014:p0001"],
    "semantic_queries:AS-SEM-18": ["AS025:p0007"],
    "semantic_queries:AS-SEM-31": ["AS010:p0001", "AS011:p0002"],
    "semantic_queries:AS-SEM-35": ["AS020:p0019", "AS025:p0007"],
    "peer_multi_doc:SY3-adv-004": ["AS056:p0056"],
    "peer_multi_doc:SY3-adv-027": ["AS015:p0034"],
    "peer_multi_doc:SY4-adv-018": ["AS072:p0082"],
    "peer_multi_doc:SYF3-adv-027": ["AS015:p0034"],
}


def sid(p):
    book = p.get("book_id") or Path(p["file_name"]).stem
    return f"{book}:p{p['page']:04d}"


def main():
    qs, saved, _, refs = PREVIOUS.load()
    questions = {q["id"]: q for q in qs}
    reviewed = read_json(PREVIOUS.RECORDS / "reviewed-per-question.json")
    failures = [
        r for r in reviewed if r["scored"] and not r["arms"]["v3_B"]["reviewed"]
    ]
    true_failures = {
        r["id"] for r in failures if r["arms"]["v3_B"]["verdict"] == "insufficient"
    }
    assert true_failures == TARGETS.keys()
    pages = {
        sid(p): p
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    chunks = {p["table_id"]: p for p in read_json(V5 / "table-chunks.json")}
    store = TableStore(V5)
    tokenizer = AutoTokenizer.from_pretrained(
        KURE, revision=RERANK_MODELS[KURE], local_files_only=True
    )
    output, details = [], []
    for rid, targets in TARGETS.items():
        row = saved["v3_B"][rid]
        final = {sid(p) for p in row["contexts"]["table_rag"]}
        selected = row["finish"]["selected_sources"]
        traces = []
        observations = []
        following = [
            t["observation"] for t in row["turns"] if t.get("status") == "executed"
        ]
        assert len(following) == len(row["searches"]) - 1
        for i, search in enumerate(row["searches"]):
            scores = [item["score"] for item in search["ranked"]]
            pure_reranker_order = (
                all(left >= right for left, right in zip(scores, scores[1:]))
                and {item["source_id"] for item in search["ranked"]}
                == {item["source_id"] for item in search["unit_candidates"]}
                and search["source_route"] == "fallback"
            )
            assert pure_reranker_order, (rid, i)
            if i == 0:
                initial = {
                    **search,
                    "ranked": [
                        (pages[p["source_id"]], p["score"]) for p in search["ranked"]
                    ],
                }
                obs = observation(SimpleNamespace(store=store), initial)
            else:
                obs = following[i - 1]
                assert obs["subquery"] == search["query"]
            observations.append(obs)
            for target in targets:
                pre = next(
                    (
                        (j + 1, c)
                        for j, c in enumerate(search["unit_candidates"])
                        if c["source_id"] == target
                    ),
                    None,
                )
                post = next(
                    (
                        (j + 1, c)
                        for j, c in enumerate(search["ranked"])
                        if c["source_id"] == target
                    ),
                    None,
                )
                unit = (
                    (
                        chunks[pre[1]["table_id"]]
                        if pre[1].get("table_id")
                        else pages[target]
                    )
                    if pre
                    else None
                )
                pair_tokens = (
                    len(
                        tokenizer(
                            search["query"],
                            unit["text"],
                            truncation=False,
                            verbose=False,
                        )["input_ids"]
                    )
                    if unit
                    else None
                )
                visible = next(
                    (p for p in obs["passages"] if p["source_id"] == target), None
                )
                traces.append(
                    {
                        "search": i,
                        "source_id": target,
                        "hybrid_candidate_rank": pre[0] if pre else None,
                        "candidate_unit": "table"
                        if pre and pre[1].get("table_id")
                        else "page"
                        if pre
                        else None,
                        "post_rerank_and_lookup_rank": post[0] if post else None,
                        "score": post[1]["score"] if post else None,
                        "hg_route": search["hg_route"],
                        "pure_reranker_order_verified": pure_reranker_order,
                        "reranker_pair_tokens": pair_tokens,
                        "reranker_input_truncated": pair_tokens > 2048
                        if pair_tokens
                        else None,
                        "passage_visible": visible is not None,
                        "visible_passage_count": len(obs["passages"]),
                        "schema_visible": any(sid(t) == target for t in obs["tables"]),
                        "excerpt_truncated": visible["excerpt_truncated"]
                        if visible
                        else None,
                        "selected": target in selected,
                        "final": target in final,
                    }
                )
        present = any(t["hybrid_candidate_rank"] is not None for t in traces)
        category = (
            "candidate_present_outside_visible_top8"
            if present
            else "absent_before_reranker"
        )
        output.append(
            {
                "id": rid,
                "category": category,
                "finish_status": row["finish"]["status"],
                "final_tokens": row["traces"]["table_rag"]["context_tokens"],
                "selected_not_retained": row["selection_audit"]["not_retained"],
                "targets": targets,
                "stages": traces,
            }
        )
        details.append(
            {
                "id": rid,
                "question": questions[rid]["question"],
                "reference": refs[rid],
                "observations": observations,
                "finish": row["finish"],
                "searches": row["searches"],
                "final_context": row["contexts"]["table_rag"],
            }
        )
    ambiguity = [
        {"id": r["id"], "finish_status": r["arms"]["v3_B"]["status"]}
        for r in failures
        if r["arms"]["v3_B"]["verdict"] == "ambiguous_scope"
    ]
    false_confirmation = [
        {"id": r["id"], "status": r["arms"]["v3_B"]["status"]}
        for r in reviewed
        if r["kind"] == "clarification"
        and not r["arms"]["v3_B"]["correct_clarification"]
    ]
    for r in false_confirmation:
        raw = saved["v3_B"][r["id"]]
        details.append(
            {
                "id": r["id"],
                "question": questions[r["id"]]["question"],
                "finish": raw["finish"],
                "turns": raw["turns"],
                "final_context": raw["contexts"]["table_rag"],
            }
        )
    write_json(OUT / "private-diagnosis.json", details)
    write_json(RECORDS / "failure-stages.json", output)
    write_json(
        RECORDS / "ambiguity.json",
        {
            "scored_questions": ambiguity,
            "clarification_control_overconfirmations": false_confirmation,
        },
    )
    summary = {
        "reviewed_failed": len(failures),
        "true_insufficient": len(output),
        "ambiguous": len(ambiguity),
        "failure_classes": dict(Counter(r["category"] for r in output)),
        "visible_missing_target_passages": sum(
            t["passage_visible"] for r in output for t in r["stages"]
        ),
        "visible_missing_target_schemas": sum(
            t["schema_visible"] for r in output for t in r["stages"]
        ),
        "truncated_target_pairs": sum(
            t["reranker_input_truncated"] is True for r in output for t in r["stages"]
        ),
        "selected_source_losses": sum(len(r["selected_not_retained"]) for r in output),
        "new_api_calls": 0,
    }
    write_json(RECORDS / "diagnosis-summary.json", summary)
    write_json(
        RECORDS / "diagnosis-inputs.json",
        {
            "reviewed_sha256": file_sha(
                PREVIOUS.RECORDS / "reviewed-per-question.json"
            ),
            "adjudications_sha256": file_sha(PREVIOUS.RECORDS / "adjudications.json"),
            "store_sha256": file_sha(V5 / "store.json"),
            "table_chunks_sha256": file_sha(V5 / "table-chunks.json"),
            "raw_pages_sha256": file_sha(
                ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
            ),
            "analysis_code_sha256": file_sha(Path(__file__)),
            "initial_observation_reconstructed": True,
            "following_observations_from_saved_output": True,
            "raw_reranker_scores_before_HG_not_separately_saved": True,
        },
    )
    print(summary)


if __name__ == "__main__":
    main()
