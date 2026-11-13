"""Blind semantic scoring; reference-page overlap remains a diagnostic only."""

import argparse
import asyncio
import json
from collections import defaultdict
from .common import OUT, REC, read, write, digest, activate, check_frozen
from .paid import ask
from .run import tasks
from experimental.u_10o0o.additional_evidence_ab.evaluate import (
    Review,
    PROMPT,
    validate_review,
    exact,
    percentile,
)

REVIEW_PROMPT = (
    PROMPT
    + """
특정 원문 페이지만 정답으로 강제하지 않는다. 판정 이유는 한국어로 작성한다.
필수 사실에 원문보다 더 넓은 일반화가 있으면 보수적으로 판정하고 scope_ok=false와 이유를 남긴다.
입력 근거만 평가한다. 검색기 이름이나 다른 비교 결과는 알 수 없다.
"""
)


def evidence(run, field):
    result = sorted(
        ({"source_id": x["source_id"], "text": x["text"]} for x in run.get(field, [])),
        key=lambda x: x["source_id"],
    )
    result += [
        {"source_id": f"SQL{i + 1}", "text": json.dumps(x, ensure_ascii=False)}
        for i, x in enumerate(run.get("included_sql_results", []))
    ]
    return result


def prepare():
    check_frozen()
    expected = tasks(("A", "S", "V", "R", "P"))
    missing = [
        (q["id"], n, a)
        for q, n, a in expected
        if not (OUT / "runs" / f"r{n}-{a}-{q['id']}.json").exists()
    ]
    if missing:
        raise RuntimeError(
            f"Save all retrieval outputs before reading references: {len(missing)} missing"
        )
    rubric = read(OUT / "rubric.json")
    jobs = {}
    mapping = {}
    for p in sorted((OUT / "runs").glob("*.json")):
        if p.name.endswith("started.json"):
            continue
        run = read(p)
        r = rubric[run["question_id"]]
        if r["clarification_only"] or run["status"] == "execution_error":
            continue
        if not r["required"]:
            raise ValueError("Missing requirements: " + run["question_id"])
        job = {
            "question": r["question"],
            "required": r["required"],
            "not_required": r["not_required"],
            "final_evidence": evidence(run, "final_context_chunks"),
        }
        k = digest(job)
        jobs[k] = job
        mapping[run["key"]] = k
    payload = {"jobs": jobs, "run_to_review": mapping}
    path = OUT / "review-jobs.json"
    if path.exists():
        assert read(path) == payload
    else:
        write(path, payload)
    return payload


def validated(k, job):
    path = OUT / "api" / ("review-" + k + ".json")
    if not path.exists():
        return None, ["not_reviewed"]
    row = read(path)
    override = OUT / "adjudications.json"
    fixes = read(override) if override.exists() else {}
    parsed = (
        Review.model_validate(fixes[k]["review"] if k in fixes else row["parsed"])
        if row.get("parsed")
        else None
    )
    return parsed, validate_review(parsed, job) if parsed else ["no_review"]


def satisfied(parsed, errors):
    return bool(
        parsed
        and not errors
        and parsed.scope_ok
        and all(c.status == "supported" for c in parsed.claims)
    )


async def review():
    jobs = prepare()
    activate()
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0, timeout=90)
    semaphore = asyncio.Semaphore(4)
    done = 0

    async def one(k, job):
        nonlocal done
        async with semaphore:
            parsed, _ = await ask(
                client, "review-" + k, REVIEW_PROMPT, job, Review, max_tokens=6500
            )
            errors = validate_review(parsed, job)
            write(OUT / "review-validation" / (k + ".json"), {"errors": errors})
            done += 1
            print(
                f"Reviewed {done}/{len(jobs['jobs'])} quote_errors={len(errors)}",
                flush=True,
            )

    try:
        await asyncio.gather(*(one(k, j) for k, j in jobs["jobs"].items()))
    finally:
        await client.close()


async def candidate_review():
    data = prepare()
    rubrics = read(OUT / "rubric.json")
    jobs = {}
    mapping = {}
    for p in sorted((OUT / "runs").glob("r*.json")):
        if p.name.endswith("started.json"):
            continue
        run = read(p)
        k = data["run_to_review"].get(run["key"])
        if not k:
            continue
        parsed, errors = validated(k, data["jobs"][k])
        if errors:
            raise RuntimeError("Resolve quote validation before candidate scoring")
        if satisfied(parsed, errors):
            continue
        r = rubrics[run["question_id"]]
        job = {
            "question": r["question"],
            "required": r["required"],
            "not_required": r["not_required"],
            "final_evidence": evidence(run, "retrieved_candidates"),
        }
        ck = digest(job)
        jobs[ck] = job
        mapping[run["key"]] = ck
    path = OUT / "candidate-review-jobs.json"
    payload = {"jobs": jobs, "run_to_review": mapping}
    if path.exists():
        assert read(path) == payload
    else:
        write(path, payload)
    activate()
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0, timeout=90)
    sem = asyncio.Semaphore(4)
    done = 0

    async def one(k, job):
        nonlocal done
        async with sem:
            parsed, _ = await ask(
                client, "review-" + k, REVIEW_PROMPT, job, Review, max_tokens=6500
            )
            write(
                OUT / "review-validation" / (k + ".json"),
                {"errors": validate_review(parsed, job)},
            )
            done += 1
            print("Candidate review", done, len(jobs), flush=True)

    try:
        await asyncio.gather(*(one(k, j) for k, j in jobs.items()))
    finally:
        await client.close()


def summarize():
    data = prepare()
    rubrics = read(OUT / "rubric.json")
    cpath = OUT / "candidate-review-jobs.json"
    candidate_data = (
        read(cpath) if cpath.exists() else {"jobs": {}, "run_to_review": {}}
    )
    rows = []
    for p in sorted((OUT / "runs").glob("r*.json")):
        if p.name.endswith("started.json"):
            continue
        x = read(p)
        r = rubrics[x["question_id"]]
        k = data["run_to_review"].get(x["key"])
        parsed, errors = validated(k, data["jobs"][k]) if k else (None, [])
        success = satisfied(parsed, errors) if k else None
        ck = candidate_data["run_to_review"].get(x["key"])
        cp, ce = validated(ck, candidate_data["jobs"][ck]) if ck else (None, [])
        candidate = success if success else (satisfied(cp, ce) if ck else None)
        calls = x.get("model_calls", [])
        input_tokens = sum(c.get("input_tokens", 0) or 0 for c in calls)
        output_tokens = sum(c.get("output_tokens", 0) or 0 for c in calls)
        selector = x.get("selector_call", {})
        usage = selector.get("usage") or {}
        searches = x.get("diagnostics", {}).get("searches", [])
        expected = r["expected_action"]
        correct_status = (
            x["status"] in {"insufficient", "needs_clarification"}
            if expected in {"abstain", "insufficient"}
            else (
                x["status"] == "needs_clarification"
                if expected == "needs_clarification" or r["clarification_only"]
                else x["status"] == "sufficient"
            )
        )
        rows.append(
            {
                "key": x["key"],
                "question_id": x["question_id"],
                "split": x["split"],
                "category": r["category"],
                "repeat": x["repeat"],
                "arm": x["arm"],
                "semantic_complete": success,
                "semantic_candidate_complete": candidate,
                "review_pending": bool(errors or ce),
                "review_key": k,
                "status": x["status"],
                "expected_action": expected,
                "status_correct": correct_status,
                "false_sufficient": x["status"] == "sufficient" and success is False,
                "supported_facts": sum(c.status == "supported" for c in parsed.claims)
                if parsed
                else None,
                "required_facts": len(r["required"]),
                "exact_final": exact(
                    x.get("final_context_chunks", []), r["evidence_groups"]
                ),
                "exact_candidates": exact(
                    x.get("retrieved_candidates", []), r["evidence_groups"]
                ),
                "latency_seconds": x["latency_seconds"],
                "selector_seconds": x.get("selector_latency_seconds", 0),
                "reused_baseline": bool(x.get("reused_from")),
                "planner_calls": len(calls),
                "selector_calls": bool(selector),
                "new_api_calls": x.get("new_api_calls", 0),
                "input_tokens": input_tokens + usage.get("input_tokens", 0),
                "output_tokens": output_tokens + usage.get("output_tokens", 0),
                "search_calls": len(searches),
                "rerank_pairs": sum(
                    s.get("reranker", {}).get("pairs", 0) for s in searches
                ),
                "rerank_tokens": sum(
                    s.get("reranker", {}).get("raw_pair_tokens", 0) for s in searches
                ),
                "final_sources": x.get("final_source_ids", []),
                "context_tokens": x.get("evidence_token_count"),
                "selection_audit": x.get("selection_audit"),
            }
        )
    groups = defaultdict(list)
    for row in rows:
        groups[(row["split"], row["arm"])].append(row)
    summary = []
    for (split, arm), items in sorted(groups.items()):
        scored = [x for x in items if x["semantic_complete"] is not None]
        summary.append(
            {
                "split": split,
                "arm": arm,
                "runs": len(items),
                "scored": len(scored),
                "complete": sum(bool(x["semantic_complete"]) for x in scored),
                "candidate_complete": sum(
                    bool(x["semantic_candidate_complete"]) for x in scored
                ),
                "pending": sum(x["review_pending"] for x in items),
                "status_correct": sum(x["status_correct"] for x in items),
                "false_sufficient": sum(x["false_sufficient"] for x in items),
                "p50": percentile([x["latency_seconds"] for x in items], 0.5),
                "p95": percentile([x["latency_seconds"] for x in items], 0.95),
                "planner_calls": sum(x["planner_calls"] for x in items),
                "selector_calls": sum(x["selector_calls"] for x in items),
                "search_calls": sum(x["search_calls"] for x in items),
                "rerank_pairs": sum(x["rerank_pairs"] for x in items),
                "rerank_tokens": sum(x["rerank_tokens"] for x in items),
                "input_tokens": sum(x["input_tokens"] for x in items),
                "output_tokens": sum(x["output_tokens"] for x in items),
            }
        )
    lookup = {(x["question_id"], x["repeat"], x["arm"]): x for x in rows}
    changes = []
    for x in rows:
        if x["arm"] == "A":
            continue
        a = lookup[(x["question_id"], x["repeat"], "A")]
        changes.append(
            {
                "question_id": x["question_id"],
                "repeat": x["repeat"],
                "arm": x["arm"],
                "split": x["split"],
                "A": a["semantic_complete"],
                "changed": x["semantic_complete"],
                "A_supported": a["supported_facts"],
                "changed_supported": x["supported_facts"],
                "gained_sources": sorted(
                    set(x["final_sources"]) - set(a["final_sources"])
                ),
                "lost_sources": sorted(
                    set(a["final_sources"]) - set(x["final_sources"])
                ),
            }
        )
    write(REC / "per-run.json", rows)
    write(REC / "summary.json", summary)
    write(REC / "paired-changes.json", changes)
    write(
        REC / "evaluation-protocol.json",
        {
            "prompt_sha256": digest(REVIEW_PROMPT),
            "reference_page_recall": "diagnostic only; semantic requirements are primary",
            "blind_arm_labels": True,
            "alternative_sources": "same entity, role, scope and effective date only",
            "all_retrieval_saved_before_scoring": True,
            "human_review": False,
            "pending": sum(x["review_pending"] for x in rows),
            "reviews": len(data["jobs"]),
            "candidate_reviews": len(candidate_data["jobs"]),
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["review", "candidate-review", "summarize"])
    a = p.parse_args()
    if a.command == "review":
        asyncio.run(review())
    elif a.command == "candidate-review":
        asyncio.run(candidate_review())
    else:
        summarize()


if __name__ == "__main__":
    main()
