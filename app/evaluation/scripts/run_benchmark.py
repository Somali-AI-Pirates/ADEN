"""일반 평가: 선택 질문 × naive/hybrid/hybrid_rerank 실행 → 저장 → 채점 → 결과 파일.

샘플(가상, API 키 불필요):
    python -m app.evaluation.scripts.run_benchmark --sample
실제:
    python -m app.evaluation.scripts.run_benchmark --questions data/basic/evaluation/<파일>.json \
        --adapter <B모듈>:<어댑터> --ids Q1,Q2 --config configs/basic.yaml \
        --judge-model <모델> --ragas-model <모델> --selection-reason "..."
Langfuse 저장 확인:
    python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id> [<trace_id> ...]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from shared.settings import Settings, load_settings

from app.observability.records import RETRIEVAL_MODES
from app.observability.tracing import Tracer
from app.evaluation.dataset import load_questions, load_references
from app.evaluation.graders import PROMPTS_DIR, make_openai_judge, make_ragas_faithfulness
from app.evaluation.report import write_artifacts, write_report
from app.evaluation.publishing import expected_observations, publish_evaluation_scores
from app.evaluation.runner import grade_all, run_comparison
from app.evaluation.scripts._common import (ROOT, add_publish_options, base_manifest, guard_overwrite,
                                      load_callable, load_env, now_id, publication_plan, sha256_file)

SAMPLE_K = 8  # 가상 샘플 전용 기본값. 실제 실행은 설정 파일이나 --k로 정한다.


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--publish-langfuse-scores", action="store_true", help="채점 후 trace에 수치 점수 게시")
    add_publish_options(p)
    p.add_argument("--sample", action="store_true", help="가상 샘플·모의 평가기로 실행")
    p.add_argument("--questions", help="제공 질문 파일(id·question만 실행에 사용)")
    p.add_argument("--references", help="참고 정답 파일(기본: --questions와 같은 파일)")
    p.add_argument("--adapter", help="서비스 어댑터 '모듈:이름' (question, mode, request_id) -> 실행 기록")
    p.add_argument("--ids", help="선택 질문 ID, 쉼표 구분(기본: 전체)")
    p.add_argument("--selection-reason", default="")
    p.add_argument("--experiment-id")
    p.add_argument("--dataset-id", help="데이터셋 이름(기본: 설정 파일, 미지정 시 advanced)")
    # 둘을 함께 주면 기록(해시)과 실제 실행(파일)이 다른 설정을 가리킬 수 있어 하나만 받는다
    config_group = p.add_mutually_exclusive_group()
    config_group.add_argument("--config", help="설정 파일. 표준 bootstrap의 실행 설정과 config_hash에 적용")
    config_group.add_argument("--config-hash", help="설정 파일이 없는 어댑터의 설정 해시(--config와 함께 쓰지 않음)")
    p.add_argument("--k", type=int,
                   help="Hit/Recall 평가의 상위 청크 수. --config가 있으면 그 retrieval.k와 같아야 하고"
                        "(생략 시 그 값), --config 없이 실행하면 반드시 지정")
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


def resolve_k(k: int | None, config: Settings | None) -> int:
    """채점 k를 실제 검색 설정과 맞춘다. 설정 파일이 없으면 추정하지 않고 명시값을 요구한다."""
    if k is not None and k <= 0:
        raise SystemExit(f"--k는 양수여야 한다: {k}")
    if config is not None:
        if k is not None and k != config.retrieval.k:
            raise SystemExit(f"--k {k}가 설정 파일의 retrieval.k {config.retrieval.k}와 다르다. "
                             "채점 k는 실제 검색 설정과 같아야 하므로 --k를 빼거나 같은 값을 지정한다")
        return config.retrieval.k
    if k is None:
        raise SystemExit("--config 없이 실행하면 --k로 서비스가 실제로 쓰는 retrieval.k를 지정해야 한다")
    return k


def main() -> None:
    args = parse_args()
    if args.verify_langfuse:
        load_env()
        tracer = Tracer.from_env()
        for trace_id in args.verify_langfuse:
            print(trace_id, json.dumps(tracer.verify_trace(trace_id), ensure_ascii=False))
        return

    config_path = (ROOT / args.config).resolve() if args.config and not args.sample else None
    config = load_settings(config_path) if config_path is not None else None
    if args.dataset_id is None:
        args.dataset_id = config.dataset if config else "advanced"

    modes = []
    for mode in args.modes.split(","):
        if mode:
            modes.append(mode)
    if args.sample:
        from app.evaluation.samples import (SAMPLE_DATASET_ID, SAMPLE_EVAL, FakeLangfuseClient,
                                        MockJudge, SampleServiceAdapter, mock_faithfulness)
        args.k = resolve_k(SAMPLE_K if args.k is None else args.k, None)
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
        args.k = resolve_k(args.k, config)
        experiment_id = args.experiment_id or now_id(args.dataset_id)
        questions_path = references_path = args.questions
        if args.references:
            references_path = args.references
        dataset_id = args.dataset_id
        config_hash = args.config_hash or sha256_file(ROOT / args.config)
        adapter = load_callable(args.adapter, config_path=config_path)
        tracer = getattr(adapter, "tracer", None)
        judge_fn = make_openai_judge(args.judge_model) if args.judge_model else None
        faith_fn = make_ragas_faithfulness(args.ragas_model) if args.ragas_model else None
        judge_desc, faith_desc = args.judge_model, args.ragas_model
        reports_dir = args.reports_dir or str(ROOT / "reports")

    report_dir = Path(reports_dir) / experiment_id
    exp_dir = Path(args.artifacts_dir) / experiment_id
    guard_overwrite(report_dir / "comparison.csv", args.overwrite)

    ids = None
    if args.ids:
        ids = []
        for requested_id in args.ids.split(","):
            ids.append(requested_id.strip())
    questions = load_questions(questions_path, ids)  # id·question만

    manifest = base_manifest(experiment_id=experiment_id, dataset_id=dataset_id,
                             config_hash=config_hash, sample=args.sample)
    question_ids = []
    for question in questions:
        question_ids.append(question["id"])
    manifest["benchmark"] = {
        "k": args.k, "modes": modes, "question_ids": question_ids,
        "selection_reason": args.selection_reason or ("가상 샘플 시나리오" if args.sample else ""),
        "references_sha256": sha256_file(Path(references_path)),
        "questions_file": Path(questions_path).name, "judge": judge_desc,
        "faithfulness": faith_desc, "judge_prompt": sha256_file(PROMPTS_DIR / "judge.txt"),
    }
    publication = publication_plan(args, manifest)  # 서비스·평가 호출 전에 평가 세트 충돌을 확인한다

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
                                timeout_s=max(0.0, deadline - time.monotonic()),
                                expected_observations=expected_observations(r))["status"]
            if isinstance(tracer, Tracer) and tracer.enabled else None)
    verified = Counter()
    for run in runs:
        verified[("mock:" if args.sample else "") + str(run["langfuse_verification"])] += 1
    manifest["benchmark"].update(langfuse_flush=("mock:" if args.sample else "") + flush["status"],
                                 langfuse_verification=dict(verified))
    write_artifacts(exp_dir, manifest, runs=runs)

    # 2) 채점: 저장된 답변·최종 문맥에 정답·채점 기준을 연결
    scores = grade_all(runs, load_references(references_path), k=args.k,
                       judge_fn=judge_fn, faithfulness_fn=faith_fn)
    if publication:
        manifest["langfuse_scores"] = publication
    write_artifacts(exp_dir, manifest, scores=scores)  # 게시 전에 저장해 게시 중 오류·중단에도 남긴다
    if publication:
        manifest["langfuse_scores"] = publish_evaluation_scores(tracer, runs, scores, manifest)
        write_artifacts(exp_dir, manifest, scores=scores)
    write_report(report_dir, manifest, runs=runs, scores=scores)
    print(f"실행 기록: {exp_dir}\n제출 결과: {report_dir}")
    if args.sample:
        print("※ 가상 샘플 결과다. 실제 Basic 성능이 아니다.")


if __name__ == "__main__":
    main()
