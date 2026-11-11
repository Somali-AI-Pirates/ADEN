"""Offline scoring: called only after every retrieval result has been persisted."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

_SUPPORTED_CONDITIONS = ("A", "B", "C", "D", "E", "F", "E_coverage", "F_coverage")
_RAW_PAGE_CONDITIONS = frozenset({"A", "D", "E", "F", "E_coverage", "F_coverage"})
_RELATION_CONDITIONS = frozenset({"C", "D", "E", "F", "E_coverage", "F_coverage"})
_COVERAGE_CONDITION_BASELINES = (("E_coverage", "E"), ("F_coverage", "F"))
_COVERAGE_FIXED_INPUT_FIELDS = (
    "candidate_unit_ids",
    "candidate_source_ids",
    "reranked_unit_ids",
    "rerank_order",
    "relation",
)


def read_jsonl(path):
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact(value):
    return " ".join(value.split())


def fact_present(fact, texts):
    return any(
        alt["source_id"] in texts
        and bool(compact(alt["quote"]))
        and compact(alt["quote"]) in compact(texts[alt["source_id"]])
        for alt in fact["alternatives"]
    )


def rank_metrics(ranking, required):
    # Strict primary-page recall is distinct from allowed-alternative fact coverage.
    primary = {fact["alternatives"][0]["source_id"] for fact in required}
    allowed = {a["source_id"] for f in required for a in f["alternatives"]}
    unique = list(dict.fromkeys(ranking))
    recall = len(primary.intersection(unique)) / len(primary) if primary else None
    ranks = [i for i, sid in enumerate(unique, 1) if sid in allowed]
    mrr = 1.0 / ranks[0] if ranks else 0.0
    dcg = sum(1 / math.log2(i + 1) for i in ranks)
    ideal = sum(
        1 / math.log2(i + 1) for i in range(1, min(len(allowed), len(unique)) + 1)
    )
    return {"page_recall": recall, "mrr": mrr, "ndcg": dcg / ideal if ideal else None}


def fact_changes(before, after):
    if len(before) != len(after):
        raise ValueError("Compared conditions have different required-fact counts")
    gained = [
        index + 1
        for index, (left, right) in enumerate(zip(before, after, strict=True))
        if right and not left
    ]
    lost = [
        index + 1
        for index, (left, right) in enumerate(zip(before, after, strict=True))
        if left and not right
    ]
    return {
        "gained_fact_indices": gained,
        "lost_fact_indices": lost,
        "gained_fact_count": len(gained),
        "lost_fact_count": len(lost),
    }


def score_one(row, reference, pages):
    facts = reference["required"]
    candidates = row["candidate_source_ids"]
    final_texts = {x["source_id"]: x["text"] for x in row["final_sources"]}
    cand_texts = {sid: pages[sid] for sid in candidates}
    ambiguous = bool(reference.get("ambiguous"))
    fact_c = [fact_present(f, cand_texts) for f in facts]
    fact_f = [fact_present(f, final_texts) for f in facts]
    proposed = set(row.get("relation", {}).get("event_ids", []))
    expected = set(reference.get("expected_relation_events", []))
    relation_eligible = reference.get("relation_eligible", bool(expected))
    result = {
        "id": row["id"],
        "condition": row["condition"],
        "split": reference["split"],
        "category": reference["category"],
        "ambiguous": ambiguous,
        "required_count": len(facts),
        "candidate_pages": len(cand_texts),
        "final_pages": len(final_texts),
        "candidate_fact_hits": sum(fact_c),
        "final_fact_hits": sum(fact_f),
        "candidate_fact_mask": fact_c,
        "final_fact_mask": fact_f,
        "candidate_complete": all(fact_c) if facts and not ambiguous else None,
        "final_complete": all(fact_f) if facts and not ambiguous else None,
        "candidate": rank_metrics(candidates, facts)
        if facts and not ambiguous
        else None,
        "final": rank_metrics(list(final_texts), facts)
        if facts and not ambiguous
        else None,
        "relation_status": row.get("relation", {}).get("status"),
        "relation_eligible": bool(relation_eligible),
        "relation_proposed_count": len(proposed),
        "relation_wrong_count": len(proposed - expected)
        if row["condition"] in _RELATION_CONDITIONS
        else None,
        "relation_exact": proposed == expected
        if row["condition"] in _RELATION_CONDITIONS and relation_eligible
        else None,
        "timings_ms": row.get("timings_ms", {}),
        "rerank": row.get("rerank", {}),
        "context_tokens": row.get("context_tokens"),
    }
    return result


def summarize(rows):
    scored = [r for r in rows if r["final_complete"] is not None]
    result = {
        "total": len(rows),
        "scored": len(scored),
        "unscored": len(rows) - len(scored),
    }
    for stage in ("candidate", "final"):
        result[stage + "_complete"] = sum(r[stage + "_complete"] for r in scored)
        for metric in ("page_recall", "mrr", "ndcg"):
            values = [r[stage][metric] for r in scored if r[stage][metric] is not None]
            result[stage + "_" + metric] = float(np.mean(values)) if values else None
    for metric in ("candidate_pages", "final_pages", "context_tokens"):
        values = [r[metric] for r in rows if r.get(metric) is not None]
        result[metric + "_mean"] = float(np.mean(values)) if values else None
    for name in sorted({k for r in rows for k in r["timings_ms"]}):
        vals = [r["timings_ms"][name] for r in rows if name in r["timings_ms"]]
        result[name + "_ms_p50"] = float(np.percentile(vals, 50))
        result[name + "_ms_p95"] = float(np.percentile(vals, 95))
    relation = [r for r in rows if r["relation_exact"] is not None]
    result["relation_eligible"] = len(relation)
    result["relation_exact"] = sum(r["relation_exact"] for r in relation)
    result["relation_questions_with_proposal"] = sum(
        r["relation_proposed_count"] > 0 for r in rows
    )
    wrong = [r for r in rows if r["relation_wrong_count"] is not None]
    result["relation_wrong_events"] = (
        sum(r["relation_wrong_count"] for r in wrong) if wrong else None
    )
    result["relation_wrong_questions"] = (
        sum(r["relation_wrong_count"] > 0 for r in wrong) if wrong else None
    )
    result["rerank_pairs"] = sum(r["rerank"].get("pairs", 0) for r in rows)
    result["rerank_input_tokens"] = sum(
        r["rerank"].get("input_tokens", 0) for r in rows
    )
    result["rerank_truncated_pairs"] = sum(
        r["rerank"].get("truncated_pairs", 0) for r in rows
    )
    return result


def _validate_coverage_fixed_inputs(rows, conditions):
    if not any(
        coverage in conditions for coverage, _baseline in _COVERAGE_CONDITION_BASELINES
    ):
        return

    by_condition_id = {(row["condition"], row["id"]): row for row in rows}
    for coverage, baseline in _COVERAGE_CONDITION_BASELINES:
        if coverage not in conditions:
            continue
        coverage_rows = [row for row in rows if row["condition"] == coverage]
        for coverage_row in coverage_rows:
            question_id = coverage_row["id"]
            baseline_row = by_condition_id[baseline, question_id]
            for field in _COVERAGE_FIXED_INPUT_FIELDS:
                if field not in baseline_row or field not in coverage_row:
                    raise ValueError(
                        f"Coverage pair is missing fixed input field {field} "
                        f"for question {question_id}"
                    )

            for condition, row in (
                (baseline, baseline_row),
                (coverage, coverage_row),
            ):
                candidate_ids = row["candidate_unit_ids"]
                if not isinstance(candidate_ids, list) or len(candidate_ids) != 20:
                    raise ValueError(
                        f"{condition} requires exactly 20 candidate units "
                        f"for question {question_id}"
                    )

                reranked_ids = row["reranked_unit_ids"]
                rerank_order = row["rerank_order"]
                if not isinstance(reranked_ids, list) or not isinstance(
                    rerank_order, list
                ):
                    raise ValueError(
                        f"{condition} has an invalid rerank_order "
                        f"for question {question_id}"
                    )
                if any(not isinstance(item, dict) for item in rerank_order):
                    raise ValueError(
                        f"{condition} has an invalid rerank_order "
                        f"for question {question_id}"
                    )
                order_ids = [item.get("unit_id") for item in rerank_order]
                if order_ids != reranked_ids:
                    raise ValueError(
                        f"{condition} rerank_order unit IDs do not match "
                        f"reranked_unit_ids for question {question_id}"
                    )
                for item in rerank_order:
                    score = item.get("score")
                    if isinstance(score, bool) or not isinstance(score, (int, float)):
                        raise ValueError(
                            f"{condition} rerank_order scores must be finite numbers "
                            f"for question {question_id}"
                        )
                    try:
                        finite = math.isfinite(score)
                    except OverflowError:
                        finite = False
                    if not finite:
                        raise ValueError(
                            f"{condition} rerank_order scores must be finite numbers "
                            f"for question {question_id}"
                        )

            for field in _COVERAGE_FIXED_INPUT_FIELDS:
                if baseline_row[field] != coverage_row[field]:
                    raise ValueError(
                        f"{coverage} changed fixed input field {field} "
                        f"from {baseline} for question {question_id}"
                    )


def score_run(
    results,
    questions,
    references,
    pages,
    output,
    units,
    conditions=("A", "B", "C"),
):
    # Cardinality/ID gate precedes opening the reference file.
    conditions = tuple(conditions)
    supported = set(_SUPPORTED_CONDITIONS)
    if (
        not conditions
        or len(conditions) != len(set(conditions))
        or not set(conditions) <= supported
    ):
        raise ValueError(
            "conditions must be a non-empty unique subset of "
            "A/B/C/D/E/F/E_coverage/F_coverage"
        )
    for coverage, baseline in _COVERAGE_CONDITION_BASELINES:
        if coverage in conditions and baseline not in conditions:
            raise ValueError(
                f"{coverage} requires {baseline} in conditions for fixed-input scoring"
            )
    rows = read_jsonl(results)
    qs = read_jsonl(questions)
    expected = {(q["id"], c) for q in qs for c in conditions}
    actual = [(r["id"], r["condition"]) for r in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        if conditions == ("A", "B", "C"):
            message = "All three conditions for all frozen questions must exist before scoring"
        else:
            names = "/".join(conditions)
            message = (
                f"All requested {names} conditions for all frozen questions "
                "must exist before scoring"
            )
        raise ValueError(message)
    if len({q["id"] for q in qs}) != len(qs):
        raise ValueError("Duplicate question IDs")
    question_hashes = {
        q["id"]: hashlib.sha256(q["question"].encode("utf-8")).hexdigest() for q in qs
    }
    for row in rows:
        if row.get("question_sha256") != question_hashes[row["id"]]:
            raise ValueError("Saved result does not match the frozen question text")
        candidate_units = row.get("candidate_unit_ids", [])
        source_ids = [source["source_id"] for source in row["final_sources"]]
        if len(candidate_units) > 20 or len(candidate_units) != len(
            set(candidate_units)
        ):
            raise ValueError("Invalid candidate unit budget or duplicates")
        if len(source_ids) > 8 or len(source_ids) != len(set(source_ids)):
            raise ValueError("Invalid final page budget or duplicates")
        if row.get("context_tokens", 0) > 8000:
            raise ValueError("Context exceeds the frozen token budget")
    _validate_coverage_fixed_inputs(rows, conditions)
    unit_rows = read_jsonl(units)
    unit_map = {unit["unit_id"]: unit for unit in unit_rows}
    if len(unit_map) != len(unit_rows):
        raise ValueError("Duplicate unit IDs")
    for row in rows:
        candidate_ids = row["candidate_unit_ids"]
        corpus = "A" if row["condition"] in _RAW_PAGE_CONDITIONS else "B"
        if any(
            uid not in unit_map or unit_map[uid]["corpus"] != corpus
            for uid in candidate_ids
        ):
            raise ValueError("Candidate units do not belong to the requested corpus")
        derived = list(
            dict.fromkeys(
                sid for uid in candidate_ids for sid in unit_map[uid]["sources"]
            )
        )
        if row["candidate_source_ids"] != derived:
            raise ValueError("Candidate sources do not match selected units")
        ranked = row["reranked_unit_ids"]
        if len(ranked) != len(candidate_ids) or set(ranked) != set(candidate_ids):
            raise ValueError("Reranked units are not a permutation of the candidates")
        reachable = list(
            dict.fromkeys(sid for uid in ranked for sid in unit_map[uid]["sources"])
        )
        final_ids = [part["source_id"] for part in row["final_sources"]]
        if any(sid not in reachable for sid in final_ids):
            raise ValueError("Final source is outside the candidate selection chain")
        positions = [reachable.index(sid) for sid in final_ids]
        if positions != sorted(positions):
            raise ValueError(
                "Final source order contradicts the reranked selection chain"
            )
    refs = read_jsonl(references)
    refmap = {r["id"]: r for r in refs}
    if len(refmap) != len(refs) or set(refmap) != {q["id"] for q in qs}:
        raise ValueError("Reference/question ID mismatch")
    page_rows = read_jsonl(pages)
    page_map = {p["source_id"]: p["text"] for p in page_rows}
    import tiktoken

    encoding = tiktoken.get_encoding("o200k_base")
    for row in rows:
        for part in row["final_sources"]:
            if not part["text"] or not page_map.get(part["source_id"], "").startswith(
                part["text"]
            ):
                raise ValueError("Saved context is not an exact original page prefix")
        context = "\n\n".join(part["text"] for part in row["final_sources"])
        actual_tokens = len(encoding.encode(context, disallowed_special=()))
        if actual_tokens != row["context_tokens"] or actual_tokens > 8000:
            raise ValueError("Saved context token count does not match the actual text")
    for ref in refs:
        if not ref.get("ambiguous") and not ref["required"]:
            raise ValueError("Answerable item has no mandatory facts")
        for fact in ref["required"]:
            if not fact["alternatives"] or not fact_present(fact, page_map):
                raise ValueError("Fact has no valid literal original source")
            for alt in fact["alternatives"]:
                if not compact(alt["quote"]) or compact(alt["quote"]) not in compact(
                    page_map[alt["source_id"]]
                ):
                    raise ValueError("Alternative quote missing from source")
    scores = [score_one(r, refmap[r["id"]], page_map) for r in rows]
    groups = defaultdict(list)
    for r in scores:
        for category in ("all", r["category"]):
            groups[(r["split"], category, r["condition"])].append(r)
    summary = [
        {"split": s, "category": c, "condition": a, **summarize(items)}
        for (s, c, a), items in sorted(groups.items())
    ]
    pairs = {}
    keyed = {(r["id"], r["condition"]): r for r in scores}
    comparison_pairs = [
        pair
        for pair in [("A", "B"), ("B", "C"), ("A", "C")]
        if set(pair) <= set(conditions)
    ]
    if {"A", "D"} <= set(conditions):
        comparison_pairs.append(("A", "D"))
    if {"D", "C"} <= set(conditions):
        comparison_pairs.append(("D", "C"))
    if {"D", "E"} <= set(conditions):
        comparison_pairs.append(("D", "E"))
    if {"E", "F"} <= set(conditions):
        comparison_pairs.append(("E", "F"))
    if {"E", "E_coverage"} <= set(conditions):
        comparison_pairs.append(("E", "E_coverage"))
    if {"F", "F_coverage"} <= set(conditions):
        comparison_pairs.append(("F", "F_coverage"))
    for before, after in comparison_pairs:
        changes = []
        for q in qs:
            a, b = keyed[q["id"], before], keyed[q["id"], after]
            if a["final_complete"] is None:
                continue
            changes.append(
                {
                    "id": q["id"],
                    "split": a["split"],
                    "category": a["category"],
                    "candidate_before": a["candidate_complete"],
                    "candidate_after": b["candidate_complete"],
                    "final_before": a["final_complete"],
                    "final_after": b["final_complete"],
                    "final_fact_hits_before": a["final_fact_hits"],
                    "final_fact_hits_after": b["final_fact_hits"],
                    "candidate_fact_changes": fact_changes(
                        a["candidate_fact_mask"], b["candidate_fact_mask"]
                    ),
                    "final_fact_changes": fact_changes(
                        a["final_fact_mask"], b["final_fact_mask"]
                    ),
                }
            )
        pairs[before + "_" + after] = changes
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [
        ("scores.json", scores),
        ("summary.json", summary),
        ("changes.json", pairs),
    ]:
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
    manifest = {
        "results_sha256": digest(results),
        "questions_sha256": digest(questions),
        "references_sha256": digest(references),
        "pages_sha256": digest(pages),
        "units_sha256": digest(units),
        "conditions": list(conditions),
        "questions": len(qs),
        "results": len(rows),
        "categories": dict(Counter((r["split"] + "|" + r["category"]) for r in refs)),
        "policy": "References opened only after complete result ID gate; candidate covers original pages accessible from selected units; final quotes must occur in actual saved context.",
    }
    (output / "score-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ["results", "questions", "references", "pages", "output", "units"]:
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument(
        "--conditions",
        nargs="+",
        choices=_SUPPORTED_CONDITIONS,
        default=("A", "B", "C"),
        help="Arms required in the saved result file; defaults to the frozen A/B/C run",
    )
    args = p.parse_args()
    score_run(**vars(args))


if __name__ == "__main__":
    main()
