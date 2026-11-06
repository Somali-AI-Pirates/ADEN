"""일반 평가: 선택 질문 × naive/hybrid/hybrid_rerank 실행 → 저장 → 채점 → 결과 파일.

샘플(가상, API 키 불필요):
    python -m member_c.scripts.run_benchmark --sample
실제:
    python -m member_c.scripts.run_benchmark --questions data/basic/evaluation/<파일>.json \
        --adapter <B모듈>:<어댑터> --ids Q1,Q2 --config configs/basic.yaml \
        --judge-model <모델> --ragas-model <모델> --selection-reason "..."
Langfuse 저장 확인:
    python -m member_c.scripts.run_benchmark --verify-langfuse <trace_id> [<trace_id> ...]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from member_c.observability.records import RETRIEVAL_MODES
from member_c.observability.tracing import Tracer
from member_c.evaluation.dataset import load_questions, load_references
from member_c.evaluation.graders import PROMPTS_DIR, make_openai_judge, make_ragas_faithfulness
from member_c.evaluation.report import write_artifacts, write_report
from member_c.evaluation.runner import grade_all, run_comparison
from member_c.scripts._common import (ROOT, base_manifest, guard_overwrite, load_callable,
                                      now_id, sha256_file)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--sample", action="store_true", help="가상 샘플·모의 평가기로 실행")
    p.add_argument("--questions", help="제공 질문 파일(id·question만 실행에 사용)")
    p.add_argument("--references", help="참고 정답 파일(기본: --questions와 같은 파일)")
    p.add_argument("--adapter", help="서비스 어댑터 '모듈:이름' (question, mode, request_id) -> 실행 기록")
    p.add_argument("--ids", help="선택 질문 ID, 쉼표 구분(기본: 전체)")
    p.add_argument("--selection-reason", default="")
    p.add_argument("--experiment-id")
    p.add_argument("--dataset-id", default="basic")
    p.add_argument("--config", help="설정 파일. 해시를 config_hash로 사용")
    p.add_argument("--config-hash")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--modes", default=",".join(RETRIEVAL_MODES))
    p.add_argument("--judge-model", help="미지정 시 Judge는 평가 불가로 기록")
    p.add_argument("--ragas-model", help="미지정 시 Faithfulness는 평가 불가로 기록")
    p.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    p.add_argument("--reports-dir", help="기본: reports/ (샘플은 artifacts/sample-reports/)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--verify-langfuse", nargs="+", metavar="TRACE_ID")
    p.add_argument("--langfuse-verify-timeout", type=float, default=90.0,
                   help="실행 후 Langfuse 저장 확인에 쓸 전체 대기 시간(초)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.verify_langfuse:
        tracer = Tracer.from_env()
        for trace_id in args.verify_langfuse:
            print(trace_id, json.dumps(tracer.verify_trace(trace_id), ensure_ascii=False))
        return

    modes = [m for m in args.modes.split(",") if m]
    if args.sample:
        from member_c.evaluation.samples import (SAMPLE_DATASET_ID, SAMPLE_EVAL, FakeLangfuseClient,
                                        MockJudge, SampleServiceAdapter, mock_faithfulness)
        experiment_id = args.experiment_id or now_id("sample")
        if not experiment_id.startswith("sample-"):
            raise SystemExit("샘플 실험 ID는 'sample-'로 시작해야 한다")
        questions_path, references_path = SAMPLE_EVAL, SAMPLE_EVAL
        dataset_id, config_hash = SAMPLE_DATASET_ID, "sample-config"
        tracer = Tracer(FakeLangfuseClient())
        adapter = SampleServiceAdapter(tracer, config_hash=config_hash)
        judge_fn, faith_fn = MockJudge(), mock_faithfulness
        judge_desc, faith_desc = "MockJudge(가상)", "mock_faithfulness(가상, RAGAS 아님)"
        reports_dir = args.reports_dir or str(ROOT / "artifacts" / "sample-reports")
    else:
        if not (args.questions and args.adapter):
            raise SystemExit("실제 실행에는 --questions와 --adapter가 필요하다(샘플은 --sample)")
        if not (args.config or args.config_hash):
            raise SystemExit("--config 또는 --config-hash로 실험 설정을 고정해야 한다")
        experiment_id = args.experiment_id or now_id(args.dataset_id)
        questions_path = references_path = args.questions
        if args.references:
            references_path = args.references
        dataset_id = args.dataset_id
        config_hash = args.config_hash or sha256_file(ROOT / args.config)
        adapter = load_callable(args.adapter)
        tracer = getattr(adapter, "tracer", None)
        judge_fn = make_openai_judge(args.judge_model) if args.judge_model else None
        faith_fn = make_ragas_faithfulness(args.ragas_model) if args.ragas_model else None
        judge_desc, faith_desc = args.judge_model, args.ragas_model
        reports_dir = args.reports_dir or str(ROOT / "reports")

    report_dir = Path(reports_dir) / experiment_id
    exp_dir = Path(args.artifacts_dir) / experiment_id
    guard_overwrite(report_dir / "comparison.csv", args.overwrite)

    ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    questions = load_questions(questions_path, ids)  # id·question만

    # 1) 실행·저장: 정답 파일은 아직 읽지 않는다
    runs = run_comparison(questions, adapter, experiment_id=experiment_id,
                          dataset_id=dataset_id, config_hash=config_hash, modes=modes)
    flush = tracer.flush() if isinstance(tracer, Tracer) else {"status": "adapter_managed"}
    # flush·trace ID만으로 저장 완료로 보지 않고 실제 관측을 조회한다(수집 지연은 전체 한도로 기다림)
    deadline = time.monotonic() + args.langfuse_verify_timeout
    for r in runs:
        r.setdefault("langfuse_status", None)
        r["langfuse_verification"] = (
            tracer.verify_trace(r.get("langfuse_trace_id"),
                                timeout_s=max(0.0, deadline - time.monotonic()))["status"]
            if isinstance(tracer, Tracer) and tracer.enabled else None)
    verified = Counter(("mock:" if args.sample else "") + str(r["langfuse_verification"])
                       for r in runs)
    manifest = base_manifest(experiment_id=experiment_id, dataset_id=dataset_id,
                             config_hash=config_hash, sample=args.sample)
    manifest["benchmark"] = {
        "k": args.k, "modes": modes, "question_ids": [q["id"] for q in questions],
        "selection_reason": args.selection_reason or ("가상 샘플 시나리오" if args.sample else ""),
        "questions_file": Path(questions_path).name, "judge": judge_desc,
        "faithfulness": faith_desc, "judge_prompt": sha256_file(PROMPTS_DIR / "judge.txt"),
        "langfuse_flush": ("mock:" if args.sample else "") + flush["status"],
        "langfuse_verification": dict(verified),
    }
    write_artifacts(exp_dir, manifest, runs=runs)

    # 2) 채점: 저장된 답변·최종 문맥에 정답·채점 기준을 연결
    scores = grade_all(runs, load_references(references_path), k=args.k,
                       judge_fn=judge_fn, faithfulness_fn=faith_fn)
    write_artifacts(exp_dir, manifest, scores=scores)
    write_report(report_dir, manifest, runs=runs, scores=scores)
    print(f"실행 기록: {exp_dir}\n제출 결과: {report_dir}")
    if args.sample:
        print("※ 가상 샘플 결과다. 실제 Basic 성능이 아니다.")


if __name__ == "__main__":
    main()
