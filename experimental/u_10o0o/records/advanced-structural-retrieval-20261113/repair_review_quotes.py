"""One explicit repair pass for non-literal evidence quotations, preserving raw reviews."""

import asyncio
from experimental.u_10o0o.structural_retrieval_ab.common import (
    OUT,
    REC,
    activate,
    read,
    write,
)
from experimental.u_10o0o.structural_retrieval_ab.evaluate import (
    prepare,
    validated,
    REVIEW_PROMPT,
)
from experimental.u_10o0o.structural_retrieval_ab.paid import ask
from experimental.u_10o0o.additional_evidence_ab.evaluate import Review, validate_review

PROMPT = (
    REVIEW_PROMPT
    + """
기존 의미 검수의 원문 인용 형식 검사를 통과하지 못한 항목을 정정한다.
모든 필수 요구 판정을 다시 반환하되, 기존 의미 판정을 임의로 바꾸지 않는다.
표의 주소·값 사이에 다른 열이 끼어 있으면 그 내용을 생략해 접합하지 말고 원문 행 전체를 인용한다.
한 인용이 너무 짧으면 주변 문장 또는 표 행을 포함한다.
필요한 사실을 뒷받침하는 실제 연속 원문을 찾을 수 없다면 그 요구를 missing으로 정정하고 이유를 쓴다.
단순히 검사를 통과하려고 관련 없는 짧은 문자열을 고르지 않는다.
유효한 인용은 원래대로 유지한다. 잘못된 중복 인용을 제거한다면 남은 인용이 해당 요구 전체를 지원해야 한다.
"""
)


async def main():
    data = prepare()
    path = OUT / "candidate-review-jobs.json"
    if path.exists():
        data["jobs"].update(read(path)["jobs"])
    pending = {}
    for key, job in data["jobs"].items():
        parsed, errors = validated(key, job)
        if errors and errors != ["not_reviewed"]:
            pending[key] = (job, parsed.model_dump(), errors)
    activate()
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0, timeout=90)
    sem = asyncio.Semaphore(4)
    fixes = (
        read(OUT / "adjudications.json")
        if (OUT / "adjudications.json").exists()
        else {}
    )
    count = 0

    async def one(key, value):
        nonlocal count
        job, previous, errors = value
        async with sem:
            parsed, log = await ask(
                client,
                "audit-literal-" + key,
                PROMPT,
                {**job, "previous_review": previous, "validation_errors": errors},
                Review,
                max_tokens=7500,
            )
            validation = validate_review(parsed, job)
            fixes[key] = {
                **fixes.get(key, {}),
                "review": parsed.model_dump(),
                "before_literal_repair": previous,
                "validation_errors": validation,
                "literal_repair_response_id": log.get("response_id"),
                "human_review": False,
            }
            count += 1
            print(
                "Literal repair",
                count,
                len(pending),
                "remaining",
                len(validation),
                flush=True,
            )

    try:
        await asyncio.gather(*(one(k, v) for k, v in pending.items()))
    finally:
        await client.close()
    write(OUT / "adjudications.json", fixes)
    write(
        REC
        / ("candidate-literal-repair.json" if path.exists() else "literal-repair.json"),
        {
            "contexts": len(pending),
            "pending": sum(
                bool(
                    validate_review(
                        Review.model_validate(fixes[k]["review"]), pending[k][0]
                    )
                )
                for k in pending
            ),
            "human_review": False,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
