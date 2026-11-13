"""Second-pass review of every failing context and its comparison counterparts."""

import asyncio
from collections import defaultdict
from experimental.u_10o0o.structural_retrieval_ab.common import (
    OUT,
    REC,
    read,
    write,
    activate,
)
from experimental.u_10o0o.structural_retrieval_ab.paid import ask
from experimental.u_10o0o.structural_retrieval_ab.evaluate import (
    prepare,
    validated,
    satisfied,
    REVIEW_PROMPT,
)
from experimental.u_10o0o.additional_evidence_ab.evaluate import Review, validate_review

AUDIT = (
    REVIEW_PROMPT
    + """
아래는 1차 채점의 검토다. previous_review를 그대로 믿지 말고 원문을 다시 확인한다.
지원 판정에도 누락된 조건/역할/날짜/대상이나 잘못 적용한 값이 없는지 확인한다.
부족 판정도 동일 사실을 담은 다른 출처나 표의 행·열 관계가 충분하지 않은지 확인한다.
완료/요청/승인, 날짜 효력, 전액과 부분금액을 엄격히 구분한다.
질문하지 않은 부가 요구를 참고 사실이 강제하면 scope_ok=false로 표시하고 이유를 적는다.
기존 판정과 다를 때 무엇이 잘못됐는지 reason에 구체적으로 설명한다.
이 검토는 사람 검수가 아니며 검색 결과를 바꾸지 않는다.
"""
)


async def main():
    data = prepare()
    runs = {}
    byquestion = defaultdict(set)
    for p in (OUT / "runs").glob("r*.json"):
        if p.name.endswith("started.json"):
            continue
        x = read(p)
        runs[x["key"]] = x
        k = data["run_to_review"].get(x["key"])
        if k:
            byquestion[(x["question_id"], x["repeat"])].add(k)
    selected = set()
    failed_pairs = set()
    for rk, k in data["run_to_review"].items():
        parsed, errors = validated(k, data["jobs"][k])
        if not satisfied(parsed, errors):
            r = runs[rk]
            failed_pairs.add((r["question_id"], r["repeat"]))
    for pair in failed_pairs:
        selected.update(byquestion[pair])
    write(
        OUT / "audit-scope.json",
        {
            "selected_contexts": sorted(selected),
            "failing_question_repeats": [list(x) for x in sorted(failed_pairs)],
            "counterparts_included": True,
            "human_review": False,
        },
    )
    activate()
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0, timeout=90)
    sem = asyncio.Semaphore(4)
    done = 0
    outputs = {}

    async def one(k):
        nonlocal done
        original = read(OUT / "api" / ("review-" + k + ".json"))
        job = data["jobs"][k]
        async with sem:
            parsed, log = await ask(
                client,
                "audit-" + k,
                AUDIT,
                {**job, "previous_review": original.get("parsed")},
                Review,
                max_tokens=7500,
            )
            errors = validate_review(parsed, job)
            outputs[k] = {
                "review": parsed.model_dump(),
                "validation_errors": errors,
                "audit_response_id": log.get("response_id"),
                "human_review": False,
            }
            done += 1
            print(
                "Audited", done, len(selected), "quote_errors", len(errors), flush=True
            )

    try:
        await asyncio.gather(*(one(k) for k in sorted(selected)))
    finally:
        await client.close()
    old = (
        read(OUT / "adjudications.json")
        if (OUT / "adjudications.json").exists()
        else {}
    )
    old.update(outputs)
    write(OUT / "adjudications.json", old)
    write(
        REC / "audit-summary.json",
        {
            "contexts": len(selected),
            "failing_question_repeats": len(failed_pairs),
            "counterparts_included": True,
            "quote_validation_pending": sum(
                bool(x["validation_errors"]) for x in outputs.values()
            ),
            "human_review": False,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
