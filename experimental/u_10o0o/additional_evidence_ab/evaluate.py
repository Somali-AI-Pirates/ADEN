"""Blind, frozen-requirement reviews after all retrieval outputs are saved."""

import argparse
import asyncio
import json
import re
import statistics
import time
import unicodedata
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .common import ROOT, OUTPUT, RECORDS, read, write, digest, file_sha

PROMPT = """
최종 검색 문맥이 질문의 고정 필수 요구를 지원하는지 판정한다. 답변 생성 평가가 아니다.
required 목록은 실행 전에 질문 범위에 맞춰 고정했다. 이 목록을 늘리거나 줄이지 않는다.
각 요구 ID를 정확히 한 번 판정한다. 요구에 여러 항목이 있으면 모두 지원해야 supported이다.
다른 원본 페이지도 같은 대상·날짜 종류·효력·역할·적용 범위의 근거라면 인정한다.
다른 사건 값이나 다른 시점 규정을 대신 적용하지 않는다. 일반 판단은 적용 조건이 같고
원문이 그 판단 원칙을 직접 설명할 때 사례에 붙은 설명도 근거가 될 수 있다.
단순 주제 유사성이나 상식으로 빠진 의무를 채우지 않는다.
질문이 조건부 날짜를 주면 그 날짜를 이용한 명확한 계산을 허용한다.
반환된 피연산자·단위·규칙으로 가능한 단순 산술·날짜 계산은 근거와 과정을 적는다.
표의 추출에서 줄바꿈·열 사이에 내용이 끼어 있을 수 있다. 원문 행과 열의 관계를
확인할 수 있을 때만 해석하고, 참고 사실에 적힌 값을 원문에 있는 것처럼 인용하지 않는다.
지원 인용은 final_evidence의 해당 source_id 안에 실제 존재하는 연속 문자열이어야 한다.
출처에 없는 글자를 보완하거나 두 구절을 붙여 한 인용으로 만들지 않는다.
supported에는 인용이 반드시 필요하다. 부족하면 missing, 질문 적용 범위가 불명확하면 ambiguous.
not_required에 적힌 부가 설명을 강제하지 않는다. 구성 이름·실행 점수·계획자 요약은 제공되지 않는다.
"""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proof(Strict):
    source_id: str
    quote: str


class Claim(Strict):
    requirement_id: str
    status: Literal["supported", "missing", "ambiguous"]
    evidence: list[Proof]
    reason: str


class Review(Strict):
    claims: list[Claim]
    scope_ok: bool
    reason: str


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def validate_review(review, job):
    required = [x["id"] for x in job["required"]]
    if sorted(c.requirement_id for c in review.claims) != sorted(required):
        return ["requirement_ids_differ"]
    errors = []
    sources = {x["source_id"]: x["text"] for x in job["final_evidence"]}
    for claim in review.claims:
        if claim.status == "supported" and not claim.evidence:
            errors.append(claim.requirement_id + ":no_quote")
        for proof in claim.evidence:
            if (
                proof.source_id not in sources
                or len(normalize(proof.quote)) < 8
                or normalize(proof.quote) not in normalize(sources[proof.source_id])
            ):
                errors.append(claim.requirement_id + ":quote_not_in_context")
    return errors


def prepare_jobs():
    status = read(OUTPUT / "run-status.json")
    assert status["status"] == "complete" and status["saved_count"] == 180
    rubrics = read(OUTPUT / "rubric.json")
    protocol = read(OUTPUT / "protocol.json")
    assert file_sha(OUTPUT / "rubric.json") == protocol["rubric_sha256"]
    jobs = {}
    mapping = {}
    for path in sorted((OUTPUT / "runs").glob("r*-?-AS-ADD??.json")):
        run = read(path)
        qid = run["question_id"]
        rubric = rubrics[qid]
        if rubric["clarification_only"]:
            continue
        final = sorted(
            (
                {"source_id": x["source_id"], "text": x["text"]}
                for x in run.get("final_context_chunks", [])
            ),
            key=lambda x: x["source_id"],
        )
        for i, sql in enumerate(run.get("included_sql_results", [])):
            final.append(
                {
                    "source_id": f"SQL{i + 1}",
                    "text": json.dumps(sql, ensure_ascii=False),
                }
            )
        job = {
            "question": rubric["question"],
            "required": rubric["required"],
            "not_required": rubric["not_required"],
            "final_evidence": final,
        }
        key = digest(job)
        jobs[key] = job
        mapping[run["key"]] = key
    target = OUTPUT / "review-jobs.json"
    payload = {"jobs": jobs, "run_to_review": mapping}
    if target.exists():
        assert read(target) == payload
    else:
        write(target, payload)
        write(
            OUTPUT / "review-protocol.json",
            {
                "prompt": PROMPT,
                "schema": Review.model_json_schema(),
                "model": "gpt-6-luna",
                "reasoning": "medium",
                "timeout": 60,
                "max_output_tokens": 5500,
                "automatic_retries": 0,
                "unique_contexts": len(jobs),
                "jobs_sha256": file_sha(target),
                "rubric_sha256": protocol["rubric_sha256"],
                "all_retrieval_saved_before_review": True,
                "blind_arm_labels": True,
                "same_content_review_reused": True,
                "independent_human_review": False,
            },
        )
    return payload


async def review_all():
    from dotenv import load_dotenv
    from openai import AsyncOpenAI

    data = prepare_jobs()
    load_dotenv(ROOT / ".env", override=False)
    client = AsyncOpenAI(max_retries=0, timeout=60)
    semaphore = asyncio.Semaphore(4)
    count = 0

    async def one(key, job):
        nonlocal count
        path = OUTPUT / "reviews" / (key + ".json")
        if path.exists():
            return read(path)
        marker = path.with_suffix(".started.json")
        if marker.exists():
            raise RuntimeError("Interrupted review; no automatic retry: " + key)
        async with semaphore:
            write(marker, {"input_sha256": digest(job)})
            tick = time.perf_counter()
            row = {"input_sha256": digest(job)}
            try:
                response = await client.responses.create(
                    model="gpt-6-luna",
                    instructions=PROMPT,
                    input=[
                        {"role": "user", "content": json.dumps(job, ensure_ascii=False)}
                    ],
                    tools=[
                        {
                            "type": "function",
                            "name": "record_review",
                            "strict": True,
                            "description": "고정 요구별 최종 원문 지원 판정.",
                            "parameters": Review.model_json_schema(),
                        }
                    ],
                    tool_choice={"type": "function", "name": "record_review"},
                    parallel_tool_calls=False,
                    reasoning={"effort": "medium"},
                    max_output_tokens=5500,
                    store=False,
                )
                calls = [x for x in response.output if x.type == "function_call"]
                if response.status != "completed" or len(calls) != 1:
                    raise ValueError("Incomplete review")
                parsed = Review.model_validate_json(calls[0].arguments)
                errors = validate_review(parsed, job)
                row.update(
                    review=parsed.model_dump(),
                    validation_errors=errors,
                    usage=response.usage.model_dump() if response.usage else None,
                    response_id=response.id,
                    status="complete",
                )
            except Exception as exc:
                row.update(status="error", error_class=type(exc).__name__)
            row["seconds"] = time.perf_counter() - tick
            write(path, row)
            count += 1
            print(
                f"Reviewed {count}/{len(data['jobs'])}: {row['status']} "
                f"quote_errors={len(row.get('validation_errors', []))}",
                flush=True,
            )
            return row

    try:
        await asyncio.gather(*(one(k, j) for k, j in data["jobs"].items()))
    finally:
        await client.close()


def exact(chunks, groups):
    got = {(x["book_id"], x["page"]) for x in chunks}
    wanted = {(x["book_id"], x["page"]) for group in groups for x in group}
    return {
        "all_groups": bool(groups)
        and all(
            any((x["book_id"], x["page"]) in got for x in group) for group in groups
        ),
        "page_recall": len(wanted & got) / len(wanted) if wanted else None,
    }


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * p
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def summarize():
    data = prepare_jobs()
    rubrics = read(OUTPUT / "rubric.json")
    manual = (
        read(OUTPUT / "adjudications.json")
        if (OUTPUT / "adjudications.json").exists()
        else {}
    )
    rows = []
    for path in sorted((OUTPUT / "runs").glob("r*-?-AS-ADD??.json")):
        run = read(path)
        rubric = rubrics[run["question_id"]]
        groups = rubric["evidence_groups"]
        reference = exact(run.get("final_context_chunks", []), groups)
        candidate = exact(run.get("retrieved_candidates", []), groups)
        clarification = rubric["clarification_only"]
        review_key = data["run_to_review"].get(run["key"])
        complete = False
        pending = False
        if not clarification:
            result = read(OUTPUT / "reviews" / (review_key + ".json"))
            chosen = manual.get(review_key, result)
            parsed = (
                Review.model_validate(chosen["review"])
                if chosen.get("review")
                else None
            )
            errors = (
                validate_review(parsed, data["jobs"][review_key])
                if parsed
                else ["no_review"]
            )
            pending = bool(errors)
            if parsed:
                complete = (
                    not pending
                    and parsed.scope_ok
                    and all(c.status == "supported" for c in parsed.claims)
                )
        calls = run.get("model_calls", [])
        usage = [c.get("usage") or {} for c in calls]
        rows.append(
            {
                "key": run["key"],
                "question_id": run["question_id"],
                "repeat": run["repeat"],
                "arm": run["arm"],
                "status": run["status"],
                "retrieval_complete": complete if not clarification else None,
                "review_pending": pending,
                "review_key": review_key,
                "clarification_correct": run["status"] == "needs_clarification"
                if clarification
                else None,
                "exact_final": reference,
                "exact_candidates": candidate,
                "latency_seconds": run["latency_seconds"],
                "planner_calls": len(calls),
                "search_calls": run.get("diagnostics", {}).get("search_calls", 0),
                "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
                "context_tokens": run.get("evidence_token_count"),
                "final_sources": run.get("final_source_ids", []),
            }
        )
    groups = defaultdict(list)
    for row in rows:
        groups[(row["arm"], row["repeat"])].append(row)
    summary = []
    for (arm, repeat), items in sorted(groups.items()):
        scored = [x for x in items if x["retrieval_complete"] is not None]
        summary.append(
            {
                "arm": arm,
                "repeat": repeat,
                "n": len(scored),
                "complete": sum(x["retrieval_complete"] for x in scored),
                "pending": sum(x["review_pending"] for x in scored),
                "clarify": sum(x["clarification_correct"] or False for x in items),
                "exact_final_all": sum(x["exact_final"]["all_groups"] for x in scored),
                "exact_candidate_all": sum(
                    x["exact_candidates"]["all_groups"] for x in scored
                ),
                "page_recall": statistics.mean(
                    x["exact_final"]["page_recall"] for x in scored
                ),
                "p50": percentile([x["latency_seconds"] for x in items], 0.5),
                "p95": percentile([x["latency_seconds"] for x in items], 0.95),
                "planner_calls": sum(x["planner_calls"] for x in items),
                "search_calls": sum(x["search_calls"] for x in items),
                "input_tokens": sum(x["input_tokens"] for x in items),
                "output_tokens": sum(x["output_tokens"] for x in items),
            }
        )
    by_key = {(x["question_id"], x["repeat"], x["arm"]): x for x in rows}
    comparisons = []
    for qid in rubrics:
        if qid == "AS-ADD12":
            continue
        pairs = [(by_key[qid, n, "A"], by_key[qid, n, "B"]) for n in range(1, 4)]
        comparisons.append(
            {
                "question_id": qid,
                "A_successes": sum(a["retrieval_complete"] for a, _ in pairs),
                "B_successes": sum(b["retrieval_complete"] for _, b in pairs),
                "improved_repeats": [
                    i
                    for i, (a, b) in enumerate(pairs, 1)
                    if not a["retrieval_complete"] and b["retrieval_complete"]
                ],
                "regressed_repeats": [
                    i
                    for i, (a, b) in enumerate(pairs, 1)
                    if a["retrieval_complete"] and not b["retrieval_complete"]
                ],
            }
        )
    write(RECORDS / "per-run.json", rows)
    write(RECORDS / "summary.json", summary)
    write(RECORDS / "per-question.json", comparisons)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Pending:", sum(x["review_pending"] for x in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["review", "summarize"])
    args = parser.parse_args()
    if args.command == "review":
        asyncio.run(review_all())
    else:
        summarize()


if __name__ == "__main__":
    main()
