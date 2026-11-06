"""일반 평가 실행기: 같은 질문을 세 검색 방식으로 실행하고, 저장한 뒤 채점한다.

서비스 호출은 어댑터로 주입한다.
    adapter(question: str, retrieval_mode: str, request_id: str) -> Mapping  # 실행 기록
어댑터에는 질문 텍스트와 검색 방식만 전달한다. 정답·근거·expected_action은 전달하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.observability.records import RETRIEVAL_MODES, RunRecordError, normalize_run_record
from app.evaluation.graders import FaithfulnessFn, JudgeFn, grade_record, load_prompt

ServiceAdapter = Callable[[str, str, str], Mapping[str, Any]]


def failure_record(*, request_id: str, question: str, retrieval_mode: str, dataset_id: str,
                   config_hash: str, error_type: str, detail: str | None = None) -> dict[str, Any]:
    """어댑터가 실행 기록을 돌려주지 못한 요청. 미확인 값은 null로 남긴다."""
    return {
        "request_id": request_id, "dataset_id": dataset_id, "retrieval_mode": retrieval_mode,
        "config_hash": config_hash, "question": question, "retrieved_candidates": None,
        "final_context_chunks": None, "final_context_hash": None, "status": "error",
        "answer": None, "reason": None, "citations": [], "latency_ms": None, "attempts": [],
        "langfuse_trace_id": None, "error_type": error_type, "error_detail": detail,
    }


def run_comparison(questions: Sequence[Mapping[str, str]], adapter: ServiceAdapter, *,
                   experiment_id: str, dataset_id: str, config_hash: str,
                   modes: Sequence[str] = RETRIEVAL_MODES,
                   new_request_id: Callable[[], str] = lambda: str(uuid.uuid4()),
                   ) -> list[dict[str, Any]]:
    """질문 × 방식 전체를 실행한다. 실패한 요청도 error 기록으로 남긴다."""
    records = []
    for q in questions:
        for mode in modes:
            request_id = new_request_id()
            common = dict(request_id=request_id, question=q["question"], retrieval_mode=mode,
                          dataset_id=dataset_id, config_hash=config_hash)
            try:
                raw = dict(adapter(q["question"], mode, request_id))
            except Exception as exc:
                raw = failure_record(**common, error_type="adapter_exception",
                                     detail=type(exc).__name__)
            raw.update(experiment_id=experiment_id, question_id=q["id"])
            try:
                record = normalize_run_record(raw)
            except RunRecordError as exc:
                record = normalize_run_record({
                    **failure_record(**common, error_type="invalid_run_record", detail=str(exc)),
                    "experiment_id": experiment_id, "question_id": q["id"]})
            records.append(record)
    return records


def grade_all(records: Sequence[Mapping[str, Any]], references: Mapping[str, Mapping[str, Any]],
              *, k: int, judge_fn: JudgeFn | None, faithfulness_fn: FaithfulnessFn | None,
              ) -> list[dict[str, Any]]:
    """저장된 기록에 참고 정답을 연결해 채점한다. 참고 정답이 없는 문항은 오류로 알린다."""
    missing = sorted({r["question_id"] for r in records} - set(references))
    if missing:
        raise ValueError(f"참고 정답이 없는 문항: {missing}")
    template = load_prompt("judge.txt")
    return [grade_record(r, references[r["question_id"]], k=k, judge_fn=judge_fn,
                         faithfulness_fn=faithfulness_fn, judge_template=template)
            for r in records]
