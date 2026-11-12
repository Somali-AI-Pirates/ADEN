"""일반 평가: 선택 질문 × naive/hybrid/hybrid_rerank 실행 → 저장 → 채점 → 결과 파일.

샘플(가상, API 키 불필요):
    python -m app.evaluation.scripts.run_benchmark --sample
실제:
    python -m app.evaluation.scripts.run_benchmark --questions data/basic/evaluation/<파일>.json \
        --adapter <B모듈>:<어댑터> --ids Q1,Q2 --config configs/basic.yaml \
        --judge-model <모델> --ragas-model <모델> --selection-reason "..."
    --config를 생략하면 ADEN_CONFIG, 없으면 configs/advanced.yaml을 쓴다. 데이터셋·채점 k·설정 해시는 그 파일에서 정한다.
Langfuse 저장 확인:
    python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id> [<trace_id> ...]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from app.observability.records import RETRIEVAL_MODES
from app.observability.tracing import Tracer
from app.evaluation.dataset import load_questions, load_references
from app.evaluation.graders import (JUDGE_SCHEMA_VERSION, PROMPTS_DIR, make_openai_judge,
                                    make_ragas_faithfulness)
from app.evaluation.report import write_artifacts, write_report
from app.evaluation.publishing import (expected_observations, expected_payload_observations,
                                       publish_evaluation_scores)
from app.evaluation.runner import grade_all, run_comparison
from app.evaluation.scripts._common import (ROOT, add_publish_options, base_manifest, guard_overwrite,
                                      load_callable, load_config, load_env, now_id, publication_plan,
                                      sha256_file)

SAMPLE_K = 8  # 가상 샘플 고정값. 실제 실행은 설정 파일의 retrieval.k를 쓴다.


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
    p.add_argument("--config", help="설정 파일(저장소 루트 기준). 생략 시 ADEN_CONFIG, 없으면 configs/advanced.yaml. "
                                    "데이터셋·채점 k·설정 해시와 어댑터 설정이 이 파일에서 정해진다(샘플은 무시)")
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
        load_env()
        tracer = Tracer.from_env()
        for trace_id in args.verify_langfuse:
            print(trace_id, json.dumps(tracer.verify_trace(trace_id), ensure_ascii=False))
        return

    modes = []
    for mode in args.modes.split(","):
        if mode:
            modes.append(mode)
    if args.sample:
        from app.evaluation.samples import (SAMPLE_DATASET_ID, SAMPLE_EVAL, FakeLangfuseClient,
                                        MockJudge, SampleServiceAdapter, mock_faithfulness)
        k = SAMPLE_K
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
        config_path, settings = load_config(args.config)  # 어댑터·평가기 준비 전에 설정을 확정한다
        dataset_id, k, config_hash = settings.dataset, settings.retrieval.k, sha256_file(config_path)
        experiment_id = args.experiment_id or now_id(dataset_id)
        questions_path = references_path = args.questions
        if args.references:
            references_path = args.references
        adapter = load_callable(args.adapter, config_path=config_path)
        tracer = getattr(adapter, "tracer", None)
        if args.judge_model:
            judge_fn = make_openai_judge(args.judge_model)
        else:
            judge_fn = None
        if args.ragas_model:
            faith_fn = make_ragas_faithfulness(args.ragas_model)
        else:
            faith_fn = None
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
    selection_reason = args.selection_reason
    if not selection_reason:
        if args.sample:
            selection_reason = "가상 샘플 시나리오"
        else:
            selection_reason = ""
    manifest["benchmark"] = {
        "k": k, "modes": modes, "question_ids": question_ids,
        "selection_reason": selection_reason,
        "references_sha256": sha256_file(Path(references_path)),
        "questions_file": Path(questions_path).name, "judge": judge_desc,
        "faithfulness": faith_desc, "judge_prompt": sha256_file(PROMPTS_DIR / "judge.txt"),
        "judge_schema": JUDGE_SCHEMA_VERSION,
    }
    publication = publication_plan(args, manifest)  # 서비스·평가 호출 전에 평가 세트 충돌을 확인한다

    # 1) 실행·저장: 정답 파일은 아직 읽지 않는다
    runs = run_comparison(questions, adapter, experiment_id=experiment_id,
                          dataset_id=dataset_id, config_hash=config_hash, modes=modes)
    if isinstance(tracer, Tracer):
        flush = tracer.flush()
    else:
        flush = {"status": "adapter_managed"}
    # flush·trace ID만으로 저장 완료로 보지 않고 실제 관측을 조회한다(수집 지연은 전체 한도로 기다림)
    deadline = time.monotonic() + args.langfuse_verify_timeout
    for r in runs:
        r.setdefault("langfuse_status", None)
        r["langfuse_verification"] = (
            tracer.verify_trace(r.get("langfuse_trace_id"),
                                timeout_s=max(0.0, deadline - time.monotonic()),
                                expected_observations=expected_observations(r),
                                require_payload_names=expected_payload_observations(r))["status"]
            if isinstance(tracer, Tracer) and tracer.enabled else None)
    verified = Counter()
    for run in runs:
        if args.sample:
            verification_prefix = "mock:"
        else:
            verification_prefix = ""
        verified[verification_prefix + str(run["langfuse_verification"])] += 1
    if args.sample:
        flush_prefix = "mock:"
    else:
        flush_prefix = ""
    manifest["benchmark"].update(langfuse_flush=flush_prefix + flush["status"],
                                 langfuse_verification=dict(verified))
    write_artifacts(exp_dir, manifest, runs=runs)

    # 2) 채점: 저장된 답변·최종 문맥에 정답·채점 기준을 연결
    scores = grade_all(runs, load_references(references_path), k=k,
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
