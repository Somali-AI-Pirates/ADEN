"""B/C 통합 검증용 고정 근거 파일. 검색 성능 비교에 쓰지 않는다.

저장된 실제 실행 기록의 후보·최종 문맥을 `stored_evidence`(ADEN_EVIDENCE_PROVIDER) 형식으로 옮기고,
근거 충분/부족 조건만 바꾼다. 추가 청크는 Advanced 원문 청크에서 그대로 가져온다(규칙을 만들지 않는다).
k(8)를 넘지 않도록 M19 충분 조건은 다른 예약의 청크 하나를 약정 페이지로 바꾼다.

    python -m experimental.Damsebi.bc_fixed_evidence --corpus artifacts/local-index/advanced-pr43/chunks.jsonl \
        --out artifacts/bc-fixed-evidence-20261111
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from app.ingestion.layout import compact

ROOT = Path(__file__).resolve().parents[2]
DEV_RUNS = "artifacts/experiments/adv-dev-pr43-20261111/runs.jsonl"
M19_RUNS = "experimental/chsopo89/advanced-test/context-regrade-20261109T011711Z/runs.jsonl"
MODE = "hybrid_rerank"
# 조건 → [(원본 기록, 질문 ID, {뺄 청크: 대신 넣을 청크 또는 None})]
CONDITIONS = {
    "sufficient": [(M19_RUNS, "M19", {"AS057-p102-c1": "AS095-p149-c1"}), (DEV_RUNS, "AS-DEV19", {})],
    "insufficient": [(M19_RUNS, "M19", {}), (DEV_RUNS, "AS-DEV19", {"AS076-p33-c1": None})],
}
RULE_PHRASES = ("보관료는실제양하일", "반납지연료는달력일기준")


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _question_texts() -> dict[str, str]:
    texts = {}
    for name in ("development_questions.json", "custom_questions.json"):
        for item in json.loads((ROOT / "data/advanced/evaluation" / name).read_text(encoding="utf-8")):
            texts[item["id"]] = item["question"]
    return texts


def build(condition: str, corpus: dict[str, dict], texts: dict[str, str]) -> list[dict]:
    records = []
    for runs_path, question_id, swaps in CONDITIONS[condition]:
        source = None
        for row in _jsonl(ROOT / runs_path):
            if row["question_id"] == question_id and row["retrieval_mode"] == MODE:
                source = row
        if source is None:
            raise SystemExit(f"원본 없음: {question_id}")
        if source["question"] != texts[question_id]:
            raise SystemExit(f"{question_id}: 저장 기록의 질문이 현재 질문 파일과 다르다")
        candidates = copy.deepcopy(source["retrieved_candidates"])
        final, swapped = [], 0
        for chunk in source["final_context_chunks"]:
            if chunk["chunk_id"] not in swaps:
                final.append({k: v for k, v in chunk.items() if k != "source_id"})
                continue
            swapped += 1
            replacement = swaps[chunk["chunk_id"]]
            if replacement is not None:
                added = {k: corpus[replacement][k] for k in ("chunk_id", "book_id", "file_name", "page", "text",
                                                               "heading")}
                final.append(added)
                candidates.append({**added, "rank": len(candidates) + 1, "score": 0.0, "retriever": "fixed"})
        if swapped != len(swaps):
            raise SystemExit(f"{question_id}: 바꿀 청크가 문맥에 없다 {list(swaps)}")
        has_rule = False
        for chunk in final:
            body = compact(chunk["text"])
            for phrase in RULE_PHRASES:
                if phrase in body and question_id_case(question_id) in chunk["heading"]:
                    has_rule = True
        records.append({"question": source["question"], "question_id": question_id, "retrieval_mode": MODE,
                        "retrieved_candidates": candidates, "final_context_chunks": final,
                        "fixed_evidence": {"condition": condition, "source_file": runs_path,
                                           "source_request_id": source["request_id"], "swaps": swaps,
                                           "target_case_rule_in_context": has_rule}})
    return records


def question_id_case(question_id: str) -> str:
    return {"M19": "02713", "AS-DEV19": "00784"}[question_id]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out}가 이미 있다")
    corpus = {}
    for row in _jsonl(Path(args.corpus)):
        corpus[row["chunk_id"]] = row
    texts = _question_texts()
    out.mkdir(parents=True)
    for condition in CONDITIONS:
        records = build(condition, corpus, texts)
        with (out / f"{condition}.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        for record in records:
            print(condition, record["question_id"], "최종", len(record["final_context_chunks"]),
                  "규칙 페이지", record["fixed_evidence"]["target_case_rule_in_context"])
    questions, references = [], []
    for name, target in (("development", "AS-DEV19"), ("custom", "M19")):
        for item in json.loads((ROOT / "data/advanced/evaluation" / f"{name}_questions.json").read_text(encoding="utf-8")):
            if item["id"] == target:
                questions.append({"id": item["id"], "question": item["question"]})
        for item in json.loads((ROOT / "data/advanced/evaluation" / f"{name}.json").read_text(encoding="utf-8")):
            if item["id"] == target:
                references.append(item)
    (out / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "references.json").write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
