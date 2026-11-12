"""Judge 검증용 사례 세트: 저장된 실제 답변의 질문·답변은 그대로 두고 최종 문맥만 바꾼다(모델 호출 없음).

생성 당시 기록과 분리해 `artifacts/experiments/<세트 ID>/`에 runs.jsonl·manifest.json·references.json을 만든다.
이후 `app.evaluation.scripts.regrade --source <세트 ID>`로 Judge 프롬프트별로 다시 채점한다.

- 추가 청크는 Advanced 원문 청크(`--corpus`, extract_pages·chunk_pages 결과)에서 그대로 가져온다. 규칙을 만들지 않는다.
- 제거한 규칙은 남은 문맥에 같은 문구가 없는지 확인한다.
- 바꾼 기록은 final_context_hash를 다시 계산하고 normalize_run_record로 정합성을 확인한다.
  model_context_text·hash와 attempts는 생성 당시 값이다(생성 모델이 실제로 본 문맥).

    python -m experimental.Damsebi.judge_cases --corpus artifacts/local-index/advanced-pr43/chunks.jsonl \
        --set-id judge-cases-20261111
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from app.ingestion.layout import compact
from app.observability.records import compute_context_hash, normalize_run_record

ROOT = Path(__file__).resolve().parents[2]
DEV_RUNS = "artifacts/experiments/adv-dev-pr43-20261111/runs.jsonl"
M19_RUNS = "experimental/chsopo89/advanced-test/context-regrade-20261109T011711Z/runs.jsonl"
# (사례, 원본 기록 파일, 질문 ID, 검색 방식, 뺄 청크, 더할 청크, 남은 문맥에 없어야 할 문구, 설명)
CASES = [
    ("c1-complete", DEV_RUNS, "AS-DEV19", "hybrid_rerank", [], [], [],
     "약정(규칙·요율)·사건·청구 페이지가 있는 문맥에서 실제로 검산한 답변"),
    ("c2-rule-removed", DEV_RUNS, "AS-DEV19", "hybrid_rerank", ["AS076-p33-c1"], [],
     ["보관료는실제양하일", "1~4일:35/일", "반납지연료는달력일기준"],
     "c1과 같은 답변, 문맥에서 비용 조건 약정(산입 규칙·요율) 페이지만 제거"),
    ("c3-m19-naive", M19_RUNS, "M19", "naive", [], [], [],
     "원래 문맥에 산입 규칙·실제 사건이 없고, 답변이 청구서 금액 기준이라고 밝힌 경우"),
    ("c4-m19-hybrid", M19_RUNS, "M19", "hybrid", [], [], [],
     "원래 문맥에 산입 규칙이 없는데 청구서 산식으로 계산한 것처럼 제시한 경우"),
    ("c4b-m19-hybrid_rerank", M19_RUNS, "M19", "hybrid_rerank", [], [], [],
     "원래 문맥에 산입 규칙이 없는데 청구서 산식과 대조 기록으로 정리한 경우"),
    ("c5-m19-hybrid-rule-added", M19_RUNS, "M19", "hybrid", [], ["AS095-p149-c1"], [],
     "c4와 같은 답변, 문맥에 실제 비용 조건 약정(AS095 p.149)을 추가한 충분 근거 조건"),
    ("c5b-m19-naive-rule-added", M19_RUNS, "M19", "naive", [], ["AS095-p149-c1", "AS096-p149-c1"], [],
     "c3과 같은 답변, 문맥에 실제 약정·사건 원본(AS095·AS096 p.149)을 추가한 충분 근거 조건"),
    ("c6-lookup-amount", DEV_RUNS, "AS-DEV28", "hybrid_rerank", [], [], [],
     "검산을 요구하지 않는 청구 총액·미수액 조회"),
    ("c7-lookup-plain", DEV_RUNS, "AS-DEV01", "hybrid_rerank", [], [], [],
     "비용과 무관한 단순 조회"),
]


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _find(rows: list[dict], question_id: str, mode: str) -> dict:
    for row in rows:
        if row["question_id"] == question_id and row["retrieval_mode"] == mode:
            return row
    raise SystemExit(f"원본 기록 없음: {question_id} {mode}")


def build_case(spec, corpus: dict[str, dict], set_id: str) -> dict:
    name, runs_path, question_id, mode, remove, add, forbidden, description = spec
    source = _find(_jsonl(ROOT / runs_path), question_id, mode)
    record = copy.deepcopy(source)
    chunks = []
    for chunk in record["final_context_chunks"]:
        if chunk["chunk_id"] not in remove:
            chunks.append(chunk)
    if len(chunks) != len(record["final_context_chunks"]) - len(remove):
        raise SystemExit(f"{name}: 뺄 청크가 원래 문맥에 없다 {remove}")
    used_ids = set()
    for chunk in chunks:
        used_ids.add(chunk["chunk_id"])
    next_number = len(record["final_context_chunks"]) + 1  # 남은 청크의 자료 ID는 바꾸지 않는다(답변 인용 유지)
    for chunk_id in add:
        if chunk_id in used_ids:
            raise SystemExit(f"{name}: 이미 문맥에 있는 청크 {chunk_id}")
        added = {**corpus[chunk_id], "source_id": f"S{next_number}"}
        next_number += 1
        chunks.append(added)
    for chunk in chunks:
        body = compact(chunk["text"])
        for phrase in forbidden:
            if phrase in body:
                raise SystemExit(f"{name}: 제거한 규칙 문구 '{phrase}'가 {chunk['chunk_id']}에 남아 있다")
    changed = bool(remove or add)
    record.update(experiment_id=set_id, final_context_chunks=chunks,
                  final_context_hash=compute_context_hash(chunks))
    if changed:
        record["request_id"] = f"{source['request_id']}-{name}"
    record["judge_case"] = {
        "case": name, "description": description, "source_file": runs_path,
        "source_experiment_id": source["experiment_id"], "source_request_id": source["request_id"],
        "source_final_context_hash": source["final_context_hash"], "removed_chunk_ids": remove,
        "added_chunk_ids": add, "context_changed": changed,
        "note": "model_context_text·attempts는 생성 당시 값이고 Judge는 바뀐 final_context_chunks만 본다",
    }
    normalize_run_record(record)  # 해시·청크 필드·출처 ID 정합성 확인(실패하면 사례로 쓰지 않는다)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--corpus", required=True, help="Advanced 원문 청크 jsonl")
    parser.add_argument("--set-id", required=True)
    parser.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    args = parser.parse_args()
    out = Path(args.artifacts_dir) / args.set_id
    if out.exists():
        raise SystemExit(f"{out}가 이미 있다. 기존 기록을 덮어쓰지 않는다")
    corpus = {}
    for row in _jsonl(Path(args.corpus)):
        corpus[row["chunk_id"]] = row
    records = []
    for spec in CASES:
        records.append(build_case(spec, corpus, args.set_id))
    # 추가 청크 본문이 생성 당시 다른 기록의 같은 청크와 같은지 확인한다(원문 추출 차이 방지)
    seen = {}
    for spec in CASES:
        for row in _jsonl(ROOT / spec[1]):
            for chunk in row.get("final_context_chunks") or []:
                seen[chunk["chunk_id"]] = chunk["text"]
    for record in records:
        for chunk_id in record["judge_case"]["added_chunk_ids"]:
            if chunk_id in seen and seen[chunk_id] != corpus[chunk_id]["text"]:
                raise SystemExit(f"{chunk_id}: 원문 청크와 저장 기록의 본문이 다르다")
    references = []
    for name in ("development.json", "custom.json"):
        for item in json.loads((ROOT / "data/advanced/evaluation" / name).read_text(encoding="utf-8")):
            for record in records:
                if item["id"] == record["question_id"] and item not in references:
                    references.append(item)
    out.mkdir(parents=True)
    with (out / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out / "references.json").write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8")
    question_ids = []
    for record in records:
        if record["question_id"] not in question_ids:
            question_ids.append(record["question_id"])
    manifest = {
        "experiment_id": args.set_id, "dataset_id": "advanced", "config_hash": "judge-cases(원본 기록별로 다름)",
        "sample": False, "kind": "judge-case-set",
        "benchmark": {"k": 8, "modes": ["naive", "hybrid", "hybrid_rerank"], "question_ids": question_ids,
                      "selection_reason": "Judge 검산 진단 검증 사례(저장 답변, 문맥만 변경)",
                      "judge": None, "faithfulness": None, "judge_prompt": None},
        "corpus_sha256": hashlib.sha256(Path(args.corpus).read_bytes()).hexdigest(),
        "cases": [record["judge_case"] | {"request_id": record["request_id"]} for record in records],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in records:
        case = record["judge_case"]
        print(case["case"], record["question_id"], record["retrieval_mode"], "청크", len(record["final_context_chunks"]),
              "제거", case["removed_chunk_ids"], "추가", case["added_chunk_ids"])
    print(f"→ {out}")


if __name__ == "__main__":
    main()
