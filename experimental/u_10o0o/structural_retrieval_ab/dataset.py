"""Freeze references separately from retrieval; create unseen questions before scoring."""

import argparse
import asyncio
import random
import re
import sqlite3
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher
from typing import Literal
from pydantic import BaseModel, ConfigDict
from .common import ROOT, BUNDLE, OUT, REC, PRIOR, read, write, file_sha
from .paid import ask
from experimental.u_10o0o.additional_evidence_ab.evaluate import normalize


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proof(Strict):
    source_id: str
    quote: str


class Fact(Strict):
    fact: str
    evidence: list[Proof]


class Draft(Strict):
    question: str
    intended_sources: list[str]


class Drafts(Strict):
    questions: list[Draft]


class Validated(Strict):
    valid: bool
    reason: str
    expected_action: Literal["answer", "needs_clarification", "insufficient"]
    required: list[Fact]


WRITER = """Advanced 원문만 사용해 물류 담당자가 실제로 물어볼 한국어 질문8개를 작성한다.
이전 평가 질문이나 검색 결과는 주어지지 않는다. 문서 내용에서 중요한 업무 판단을 고르고 자연스럽게 묻는다.
문제의 답을 질문에 그대로 쓰지 않는다. 엉뚱한 모호함이나 문서에 없는 허구를 넣지 않는다.
서로 다른 표현과 사건을 사용한다. 모든 질문을 코드+단일 금액 조회로 만들지 않는다.
type의 목적을 따르고, 가능하면 두세 요구가 서로 다른 원문에 걸치게 한다.
명시되지 않은 규정 적용을 추론하지 말고 실제 증거의 대상·날짜·승인·상태를 유지한다.
모호성 유형은 의도적으로 구별 단서를 빼되 원문상 복수 대상이나 미확정 상태를 확인할 수 있어야 한다.
intended_sources는 제공된 source_id만 사용한다. 답변이나 기대 사실은 작성하지 않는다."""
REVIEWER = """검색 결과를 모르는 평가 문항 검수자다. 제공된 질문과 원문으로만 판단한다.
질문이 실제 업무 요청으로 자연스럽고 적용 범위가 명확하며 자료로 답 또는 적절한 보류를 판단할 수 있는지 검수한다.
다른 사건의 연락처/상태/금액으로 대체하거나 유사 제목을 동일 규정으로 간주하지 않는다.
필요한 모든 사실을 독립적으로 나열하되 질문 밖 부가 설명을 필수로 추가하지 않는다.
각 fact에 같은 원문 안의 실제 연속 인용과 source_id를 붙인다. 주소가 표 열 사이에 끼이면 원문 행 전체를 인용한다.
순중량의 정의, 신규와 정정, 요청과 승인, 실제일과 예정일 등 요구된 구분을 생략하지 않는다.
질문이 모호하다면 그 모호성이 의도된 업무 상황인지와 추가 확인이 필요한 근거를 작성한다.
단순히 정답이 없는 부실 질문이면 valid=false로 한다. 원문을 고치거나 사실을 만들어 넣지 않는다."""


def prepare():
    if (OUT / "holdout-seeds.json").exists():
        return
    source = ROOT / "artifacts/u_10o0o/advanced-table-rag-db-first-20261112/v1"
    qs = read(source / "questions.json")
    refs = read(source / "references.json")
    official = {name: read(ROOT / desc["path"]) for name, desc in refs.items()}
    for suite in ["official_development", "official_additional"]:
        existing = {q["question_id"] for q in qs if q["split"] == suite}
        for row in official[suite]:
            if row["id"] not in existing:
                qs.append(
                    {
                        "id": suite + ":" + row["id"],
                        "question_id": row["id"],
                        "split": suite,
                        "question": row["question"],
                    }
                )
    rubrics = {}
    for q in qs:
        row = next(x for x in official[q["split"]] if x["id"] == q["question_id"])
        facts = row.get("required_facts", [])
        rubric = {
            "question": q["question"],
            "required": [{"id": f"R{i + 1}", "fact": f} for i, f in enumerate(facts)],
            "not_required": [],
            "evidence_groups": row["evidence_groups"],
            "clarification_only": not bool(row["evidence_groups"]),
            "expected_action": row.get("expected_action", "answer"),
            "category": str(row.get("scenario", q["split"])),
        }
        if q["split"] == "official_additional":
            old = read(PRIOR / "rubric.json")[q["question_id"]]
            rubric.update(
                {k: old[k] for k in ["required", "not_required", "clarification_only"]}
            )
        rubrics[q["id"]] = rubric
    assert len(qs) == 280
    write(OUT / "base-questions.json", qs)
    write(OUT / "base-rubric.json", rubrics)
    write(
        OUT / "reference-inputs.json",
        {
            name: {"sha256": file_sha(ROOT / desc["path"]), "path": desc["path"]}
            for name, desc in refs.items()
        },
    )
    pages = {
        f"{Path(x['file_name']).stem}:p{x['page']:04d}": x["text"]
        for x in read(BUNDLE / "pages.json")
    }
    c = sqlite3.connect(
        "file:" + str(BUNDLE / "records.sqlite3") + "?mode=ro", uri=True
    )
    used = set()
    for q in qs:
        used.update(
            re.findall(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+|HG\d{4}-\d+", q["question"])
        )
    rng = random.Random(20261113)
    available = {}
    for family in ["HG", "HA"]:
        candidates = [
            x[0]
            for x in c.execute(
                "SELECT id FROM entities WHERE family=? ORDER BY id", (family,)
            )
        ]
        rng.shuffle(candidates)
        available[family] = [
            eid
            for eid in candidates
            if not any(
                k in used
                for (k,) in c.execute("SELECT value FROM keys WHERE entity=?", (eid,))
            )
        ]

    def case_sources(family, n):
        ids = [available[family].pop() for _ in range(n)]
        sources = list(
            dict.fromkeys(
                s
                for eid in ids
                for (s,) in c.execute(
                    "SELECT source FROM sources WHERE entity=? ORDER BY ordinal,source",
                    (eid,),
                )
            )
        )
        return ids, sources

    groups = []
    kinds = ["explicit_records", "indirect_case", "two_cases", "case_and_rule"]
    for i, kind in enumerate(kinds):
        ids, src = case_sources("HA" if i == 1 else "HG", 6 if i == 2 else 5)
        groups.append(
            {
                "type": kind,
                "entities": ids,
                "sources": [{"source_id": s, "text": pages[s]} for s in src],
            }
        )
    policy_groups = [
        (
            "procedures",
            [(8, [1, 3, 8, 31]), (9, [1, 22, 54]), (11, [1, 2]), (12, [35])],
        ),
        ("effective_dates", [(14, [1, 10, 18, 19]), (9, [21, 22, 54]), (8, [8, 31])]),
        (
            "scope_and_exceptions",
            [
                (7, [1]),
                (9, [22, 55]),
                (11, [2]),
                (12, [35]),
                (13, [19, 20]),
                (14, [18, 19]),
            ],
        ),
        ("ambiguity", [(8, [2, 3, 31]), (11, [2]), (12, [35]), (13, [19, 20])]),
    ]
    for kind, books in policy_groups:
        src = [f"AS{book:03d}:p{page:04d}" for book, pp in books for page in pp]
        ids, more = case_sources("HG", 3) if kind == "ambiguity" else ([], [])
        groups.append(
            {
                "type": kind,
                "entities": ids,
                "sources": [{"source_id": s, "text": pages[s]} for s in src + more],
            }
        )
    c.close()
    write(OUT / "holdout-seeds.json", groups)
    write(
        REC / "preparation.json",
        {
            "existing_questions": len(qs),
            "holdout_groups": [
                {
                    "type": g["type"],
                    "entities": g["entities"],
                    "sources": [s["source_id"] for s in g["sources"]],
                }
                for g in groups
            ],
            "same_corpus": True,
            "human_authorship": False,
            "no_retrieval_results_used": True,
        },
    )


async def holdout():
    from dotenv import load_dotenv
    from openai import AsyncOpenAI

    load_dotenv(ROOT / ".env", override=False)
    client = AsyncOpenAI(max_retries=0, timeout=90)
    seeds = read(OUT / "holdout-seeds.json")
    old = read(OUT / "base-questions.json")
    semaphore = asyncio.Semaphore(4)

    async def one(i, group):
        async with semaphore:
            draft, _ = await ask(
                client,
                f"write-v2-{i}",
                WRITER
                + "\n같은 화주명과 장비 분류가 전체 자료에 반복된다. explicit_records, two_cases, case_and_rule 유형에서는 반드시 원문 완전 계약번호나 예약번호로 각 대상을 구별한다. indirect_case는 코드를 쓰지 말고 원문 화주·화물·정확한 실제 날짜 등 최소 세 가지 단서를 함께 쓴다. ambiguity만 대상을 생략해도 된다. unrelated 절차를 억지로 묶지 않는다.",
                group,
                Drafts,
                max_tokens=6000,
            )
        approved = []
        for j, q in enumerate(draft.questions):
            async with semaphore:
                review, log = await ask(
                    client,
                    f"validate-v2-{i}-{j}",
                    REVIEWER,
                    {
                        "type": group["type"],
                        "question": q.question,
                        "sources": group["sources"],
                    },
                    Validated,
                )
            texts = {s["source_id"]: s["text"] for s in group["sources"]}
            errors = []
            if not review.required:
                errors.append("no_requirements")
            for fact in review.required:
                if not fact.evidence:
                    errors.append("no_evidence")
                for proof in fact.evidence:
                    if (
                        proof.source_id not in texts
                        or len(normalize(proof.quote)) < 8
                        or normalize(proof.quote)
                        not in normalize(texts[proof.source_id])
                    ):
                        errors.append("quote_mismatch")
            similarity = max(
                SequenceMatcher(
                    None, normalize(q.question), normalize(x["question"])
                ).ratio()
                for x in old
            )
            if similarity > 0.82:
                errors.append("similar_existing_question")
            row = {
                "question": q.question,
                "type": group["type"],
                "review": review.model_dump(),
                "errors": errors,
                "max_existing_similarity": similarity,
            }
            write(OUT / "holdout-audit" / f"{i}-{j}.json", row)
            if review.valid and not errors:
                approved.append(row)
        if len(approved) < 5:
            return {"incomplete_group": i, "valid": len(approved)}
        return approved[:5]

    try:
        groups = await asyncio.gather(*(one(i, g) for i, g in enumerate(seeds)))
    finally:
        await client.close()
    if any(isinstance(g, dict) for g in groups):
        raise RuntimeError(str([g for g in groups if isinstance(g, dict)]))
    held = [x for group in groups for x in group]
    write(OUT / "holdout-reviewed.json", held)
    qs = read(OUT / "base-questions.json")
    rubric = read(OUT / "base-rubric.json")
    for i, row in enumerate(held, 1):
        qid = f"STRUCT-HOLD-{i:03d}"
        qs.append(
            {
                "id": qid,
                "question_id": qid,
                "split": "holdout",
                "question": row["question"],
            }
        )
        req = row["review"]["required"]
        rubric[qid] = {
            "question": row["question"],
            "required": [
                {"id": f"R{j + 1}", "fact": f["fact"]} for j, f in enumerate(req)
            ],
            "not_required": [],
            "clarification_only": False,
            "expected_action": row["review"]["expected_action"],
            "category": row["type"],
            "evidence_groups": [
                [{"book_id": sid.split(":p")[0], "page": int(sid.split(":p")[1])}]
                for sid in sorted({p["source_id"] for f in req for p in f["evidence"]})
            ],
        }
    write(OUT / "questions.json", qs)
    write(OUT / "rubric.json", rubric)
    write(
        REC / "holdout-manifest.json",
        {
            "count": len(held),
            "types": dict(Counter(x["type"] for x in held)),
            "questions_sha256": file_sha(OUT / "questions.json"),
            "rubric_sha256": file_sha(OUT / "rubric.json"),
            "created_before_retrieval": True,
            "source_overlap": "IS common rules share the known corpus; HG/HA seed keys excluded existing 280 questions. This is unseen questions, not unseen documents.",
            "human_review": False,
        },
    )
    print("Frozen input content: 280 regression + 40 unseen questions", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["prepare", "holdout"])
    args = p.parse_args()
    prepare() if args.command == "prepare" else asyncio.run(holdout())


if __name__ == "__main__":
    main()
