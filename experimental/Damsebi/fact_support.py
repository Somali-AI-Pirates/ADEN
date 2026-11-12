"""필수 사실 근거 평가(검색 평가). 질문·필수 사실·후보 또는 최종 문맥으로 사실별 충족을 판정한다.

답변은 넣지 않는다(답변 평가는 Judge가 따로 한다). 사실마다 상태(supported·not_supported·needs_review),
출처, 원문 구절, 사유를 저장한다. 모델이 낸 구절이 그 출처의 실제 문맥에 없으면 needs_review로 바꾼다.

- 후보 문맥: `retrieved_candidates`(검색·DB 조회 후보 전체)
- 최종 문맥: `final_context_chunks`와 최종 문맥에 남은 SQL 결과(답변 모델에 실제로 전달한 문맥)

    python -m experimental.Damsebi.fact_support --runs <runs.jsonl> --facts <필수 사실 JSON> \
        --model gpt-6-luna --out <새 출력 폴더> [--ids AS-ADD01,AS-ADD03]

원본 runs.jsonl(본문 포함)이 필요하다. 본문 제거본으로는 내용 충족을 판정할 수 없다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.graders import chunk_text, included_sql_contexts, make_openai_judge
from app.evaluation.scripts._common import git_info, load_env

PROMPT = Path(__file__).with_name("fact-support") / "prompt-v1.txt"
STATUSES = ("supported", "not_supported", "needs_review")
CONTEXTS = ("candidate", "final")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _compact(text: str) -> str:
    """구절 대조용: 공백을 모두 지운다(PDF 추출 본문의 띄어쓰기가 불규칙하다)."""
    return re.sub(r"\s+", "", text)


def context_pages(record: dict, which: str) -> list[dict]:
    if which == "candidate":
        chunks = record.get("retrieved_candidates") or []
    else:
        chunks = record.get("final_context_chunks") or []
    pages = []
    for chunk in chunks:
        pages.append({"source_id": chunk["source_id"],
                      "label": f"{chunk['file_name']} p.{chunk['page']}",
                      "text": chunk_text(chunk)})
    if which == "final":
        number = 0
        for block in included_sql_contexts(record):
            number += 1
            pages.append({"source_id": f"SQL{number}", "label": "SQL 조회 결과", "text": block})
    return pages


def build_prompt(template: str, question: str, facts: list[dict], pages: list[dict]) -> str:
    fact_lines = []
    for fact in facts:
        fact_lines.append(f"{fact['id']}. {fact['text']}")
    blocks = []
    for page in pages:
        blocks.append(f"[{page['source_id']}] {page['label']}\n{page['text']}")
    if blocks:
        context = "\n\n".join(blocks)
    else:
        context = "(문맥 없음)"
    values = {"question": question, "facts": "\n".join(fact_lines), "context": context}
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def parse_verdicts(text: str, facts: list[dict], pages: list[dict]) -> list[dict]:
    """모델 출력을 검사한다. 구조가 틀리면 ValueError, 구절이 문맥에 없으면 해당 사실만 needs_review."""
    data = json.loads(text)
    items = data.get("facts")
    if not isinstance(items, list):
        raise ValueError("facts 배열이 없다")
    expected = []
    for fact in facts:
        expected.append(fact["id"])
    got = []
    for item in items:
        got.append(item.get("id") if isinstance(item, dict) else None)
    if got != expected:
        raise ValueError(f"사실 ID가 다르다: {got} != {expected}")

    page_text = {}
    for page in pages:
        page_text[page["source_id"]] = _compact(page["text"])

    verdicts = []
    for fact, item in zip(facts, items, strict=True):
        status = item.get("status")
        if status not in STATUSES:
            raise ValueError(f"{fact['id']}: 알 수 없는 status {status}")
        source_ids = item.get("source_ids") or []
        quotes = item.get("quotes") or []
        reason = str(item.get("reason") or "")
        problems = []
        for source_id in source_ids:
            if source_id not in page_text:
                problems.append(f"문맥에 없는 출처 {source_id}")
        checked_quotes = []
        for quote in quotes:
            quote_source = quote.get("source_id") if isinstance(quote, dict) else None
            quote_text = str(quote.get("text") or "") if isinstance(quote, dict) else ""
            found = quote_source in page_text and bool(_compact(quote_text)) \
                and _compact(quote_text) in page_text[quote_source]
            if not found:
                problems.append(f"{quote_source} 구절이 문맥에 없음")
            checked_quotes.append({"source_id": quote_source, "text": quote_text, "found": found})
        if status == "supported" and not checked_quotes:
            problems.append("supported인데 근거 구절이 없음")

        verdict = {"id": fact["id"], "fact": fact["text"], "model_status": status, "status": status,
                   "source_ids": source_ids, "quotes": checked_quotes, "reason": reason, "check_problems": problems}
        if status == "supported" and problems:
            verdict["status"] = "needs_review"
        verdicts.append(verdict)
    return verdicts


def evaluate(record: dict, entry: dict, which: str, template: str, judge_fn) -> dict:
    pages = context_pages(record, which)
    source_ids = []
    for page in pages:
        source_ids.append(page["source_id"])
    result = {"question_id": record["question_id"], "request_id": record["request_id"], "context": which,
              "context_source_ids": source_ids, "status": "scored", "error": None, "verdicts": [],
              "input_tokens": None, "output_tokens": None}
    prompt = build_prompt(template, entry["question"], entry["facts"], pages)
    result["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    try:
        output = judge_fn(prompt)
        result["input_tokens"] = output.get("input_tokens")
        result["output_tokens"] = output.get("output_tokens")
        result["verdicts"] = parse_verdicts(output["text"], entry["facts"], pages)
    except Exception as exc:  # 평가기 오류는 0점이 아니라 평가 불가로 남긴다
        result["status"] = "unavailable"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--runs", required=True, help="원본 runs.jsonl(본문 포함)")
    parser.add_argument("--facts", required=True, help="필수 사실 JSON")
    parser.add_argument("--model", required=True, help="평가 모델(예: gpt-6-luna)")
    parser.add_argument("--out", required=True, help="새 출력 폴더(이미 있으면 멈춤)")
    parser.add_argument("--ids", help="문항 ID 쉼표 구분(생략 시 kind=evidence 전체)")
    parser.add_argument("--contexts", default="candidate,final", help="candidate,final 중 선택")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"이미 있다(덮어쓰지 않음): {out}")
    runs_path = Path(args.runs)
    facts_path = Path(args.facts)
    facts_doc = json.loads(facts_path.read_text(encoding="utf-8"))
    entries = {}
    for entry in facts_doc["questions"]:
        entries[entry["id"]] = entry
    records = {}
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["question_id"]] = record

    if args.ids:
        ids = args.ids.split(",")
    else:
        ids = []
        for question_id, entry in entries.items():
            if entry["kind"] == "evidence":
                ids.append(question_id)
    contexts = args.contexts.split(",")
    for which in contexts:
        if which not in CONTEXTS:
            raise SystemExit(f"알 수 없는 문맥: {which}")
    for question_id in ids:
        if question_id not in entries or question_id not in records:
            raise SystemExit(f"필수 사실 또는 실행 기록에 없는 문항: {question_id}")
        if records[question_id]["question"] != entries[question_id]["question"]:
            raise SystemExit(f"{question_id}: 실행 기록과 필수 사실의 질문 원문이 다르다")

    load_env()
    template = PROMPT.read_text(encoding="utf-8")
    judge_fn = make_openai_judge(args.model)
    out.mkdir(parents=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "evaluator": "fact-support-v1",
                "model": args.model, "prompt": _sha256_file(PROMPT), "facts_file": facts_path.name,
                "facts_sha256": _sha256_file(facts_path), "facts_version": facts_doc.get("version"),
                "runs_sha256": _sha256_file(runs_path), "question_ids": ids, "contexts": contexts, **git_info()}
    (out / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    with (out / "judgments.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for question_id in ids:
            for which in contexts:
                result = evaluate(records[question_id], entries[question_id], which, template, judge_fn)
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                stream.flush()
                counts = {}
                for verdict in result["verdicts"]:
                    counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
                print(question_id, which, result["status"], counts, result["error"] or "")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
