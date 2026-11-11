"""저장된 일반 평가 기록을 답변 재생성 없이 다시 채점한다.

채점 기준(Judge 프롬프트)이나 Judge 모델만 바꿔 같은 답변의 점수를 비교할 때 쓴다.
- 원본 `artifacts/experiments/<원본 ID>/runs.jsonl`의 답변·최종 문맥을 그대로 읽는다. 서비스는 호출하지 않는다.
- 검색 지표는 원본 k로 다시 계산한다(평가 호출 없음).
- Faithfulness는 --ragas-model이 없으면 원본 점수를 그대로 옮긴다(답변·문맥이 같으므로).
- 원본 폴더는 수정하지 않고 새 실험 ID로 저장한다.
- --publish-langfuse-scores는 새로 계산한 지표군만 게시한다(Judge, --ragas-model이 있으면 Faithfulness도).
  다른 지표군은 --publish-groups로 고른다. 점수 이름은 `<평가 세트>.<지표>`이고 세트 기본값은 실험 ID다.

샘플(가상, 모의 Judge):
    python -m app.evaluation.scripts.regrade --source sample-demo \
        --references app/evaluation/samples/sample_eval.json
실제:
    python -m app.evaluation.scripts.regrade --source <원본 실험 ID> --references <정답 파일> \
        --judge-model <모델> [--judge-prompt <프롬프트 파일>] [--ragas-model <모델>]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evaluation.dataset import load_references
from app.evaluation.graders import PROMPTS_DIR, make_openai_judge, make_ragas_faithfulness
from app.evaluation.metrics import score
from app.evaluation.report import write_artifacts, write_report
from app.evaluation.publishing import publish_evaluation_scores
from app.evaluation.runner import grade_all
from app.evaluation.scripts._common import (ROOT, add_publish_options, base_manifest, guard_overwrite,
                                      load_env, now_id, publication_plan, sha256_file)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--publish-langfuse-scores", action="store_true", help="재채점 점수를 원본 trace에 게시")
    add_publish_options(p)
    p.add_argument("--source", required=True, help="원본 실험 ID(--artifacts-dir 아래 폴더 이름)")
    p.add_argument("--references", required=True, help="참고 정답 파일")
    p.add_argument("--judge-model", help="실제 기록에서 필수. 샘플 기록은 모의 Judge를 쓴다")
    p.add_argument("--judge-prompt", default=str(PROMPTS_DIR / "judge.txt"),
                   help="Judge 프롬프트 파일(기본: app/evaluation/prompts/judge.txt)")
    p.add_argument("--ragas-model", help="지정하면 Faithfulness도 다시 계산. 생략 시 원본 점수 재사용")
    p.add_argument("--experiment-id", help="기본: <원본 ID>-regrade-<시각>")
    p.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    p.add_argument("--reports-dir", help="기본: reports/ (샘플은 artifacts/sample-reports/)")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def reuse_faithfulness(scores: list[dict[str, Any]], source_scores: list[dict[str, Any]]) -> None:
    """원본 Faithfulness를 request_id로 옮긴다. 새 평가 호출은 없으므로 사용량은 null.
    shortcut: 원본 점수는 원본 Judge의 has_factual_claims로 적용 여부가 정해졌다. 새 Judge 판단과
    달라질 수 있으면 --ragas-model로 다시 계산한다."""
    old = {}
    for source_score in source_scores:
        old[source_score["request_id"]] = source_score["faithfulness"]
    for s in scores:
        s["faithfulness"] = old.get(s["request_id"]) or score(
            None, "unavailable", "원본 Faithfulness 점수 없음")
        s["grading_usage"]["faithfulness"] = None


def main() -> None:
    args = parse_args()
    src_dir = Path(args.artifacts_dir) / args.source
    if not (src_dir / "runs.jsonl").exists():
        raise SystemExit(f"{src_dir / 'runs.jsonl'}가 없다. run_benchmark로 만든 원본 실험 ID를 지정한다")
    src_manifest = json.loads((src_dir / "manifest.json").read_text(encoding="utf-8"))
    bench = src_manifest.get("benchmark")
    if not bench:
        raise SystemExit(f"{args.source}: 일반 평가(benchmark) 기록이 아니다")
    sample = bool(src_manifest.get("sample"))

    experiment_id = args.experiment_id or now_id(f"{args.source}-regrade")
    if experiment_id == args.source:
        raise SystemExit("원본과 같은 실험 ID에는 저장하지 않는다")
    if sample:
        if args.judge_model or args.ragas_model:
            raise SystemExit("샘플 기록은 모의 Judge로만 재채점한다(--judge-model·--ragas-model 제외)")
        if not experiment_id.startswith("sample-"):
            raise SystemExit("샘플 실험 ID는 'sample-'로 시작해야 한다")
        from app.evaluation.samples import MockJudge
        judge_fn, judge_desc, faith_fn = MockJudge(), "MockJudge(가상)", None
        reports_dir = args.reports_dir or str(ROOT / "artifacts" / "sample-reports")
    else:
        if not args.judge_model:
            raise SystemExit("--judge-model이 필요하다")
        load_env()  # Judge·Langfuse 키. 셸에 이미 있는 값이 우선
        judge_fn, judge_desc = make_openai_judge(args.judge_model), args.judge_model
        if args.ragas_model:
            faith_fn = make_ragas_faithfulness(args.ragas_model)
        else:
            faith_fn = None
        reports_dir = args.reports_dir or str(ROOT / "reports")

    report_dir = Path(reports_dir) / experiment_id
    guard_overwrite(report_dir / "comparison.csv", args.overwrite)

    prompt_path = Path(args.judge_prompt)
    runs = _read_jsonl(src_dir / "runs.jsonl")
    if faith_fn:
        faith_desc = args.ragas_model
    else:
        faith_desc = f"원본 재사용({bench.get('faithfulness')})"
    manifest = base_manifest(experiment_id=experiment_id, dataset_id=src_manifest["dataset_id"],
                             config_hash=src_manifest["config_hash"], sample=sample)
    manifest["benchmark"] = {**bench, "judge": judge_desc, "faithfulness": faith_desc,
                             "judge_prompt": sha256_file(prompt_path),
                             "references_sha256": sha256_file(Path(args.references))}
    if faith_fn:
        faithfulness_status = "recomputed"
    else:
        faithfulness_status = "reused"
    manifest["regrade"] = {
        "source_experiment_id": args.source, "source_git_commit": src_manifest.get("git_commit"),
        "source_judge": bench.get("judge"), "source_judge_prompt": bench.get("judge_prompt"),
        "judge_prompt_file": prompt_path.name, "references_file": Path(args.references).name,
        "faithfulness": faithfulness_status,
    }
    # 채점 호출 전에 평가 세트 충돌을 확인한다. 옮겨 온 Faithfulness와 그대로인 검색 지표는 기본 게시에서 뺀다
    request_ids = []
    for run in runs:
        request_ids.append(run["request_id"])
    if faith_fn:
        default_groups = ["judge", "faithfulness"]
    else:
        default_groups = ["judge"]
    publication = publication_plan(args, manifest, request_ids=request_ids,
                                   default_groups=default_groups)
    # k는 원본 실행의 검색 설정에 묶여 있으므로 바꾸지 않는다
    scores = grade_all(runs, load_references(args.references), k=bench["k"], judge_fn=judge_fn,
                       faithfulness_fn=faith_fn,
                       judge_template=prompt_path.read_text(encoding="utf-8"))
    if faith_fn is None:
        src_scores = src_dir / "scores.jsonl"
        if src_scores.exists():
            source_scores = _read_jsonl(src_scores)
        else:
            source_scores = []
        reuse_faithfulness(scores, source_scores)

    exp_dir = Path(args.artifacts_dir) / experiment_id
    if publication:
        manifest["langfuse_scores"] = publication
    # 게시 전에 저장해 게시가 실패하거나 중단돼도 채점 결과가 남는다. 답변·문맥은 원본 runs.jsonl에만 둔다
    write_artifacts(exp_dir, manifest, scores=scores)
    if publication:
        from app.observability.tracing import Tracer
        tracer = None
        try:
            if sample:
                from app.evaluation.samples import FakeLangfuseClient
                tracer = Tracer(FakeLangfuseClient())
            else:
                tracer = Tracer.from_env()
            manifest["langfuse_scores"] = publish_evaluation_scores(tracer, runs, scores, manifest)
        except Exception as exc:
            manifest["langfuse_scores"] = {**publication, "status": "failed", "error": type(exc).__name__}
        finally:
            try:
                if tracer is not None:
                    tracer.shutdown()
            finally:  # 게시 상태와 보낸 점수 ID. 중단되면 그때까지 보낸 ID와 incomplete가 남는다
                write_artifacts(exp_dir, manifest, scores=scores)
    write_report(report_dir, manifest, runs=runs, scores=scores)
    print(f"원본: {src_dir}\n재채점 기록: {exp_dir}\n제출 결과: {report_dir}")
    if sample:
        print("※ 가상 샘플 결과다. 실제 Basic 성능이 아니다.")


if __name__ == "__main__":
    main()
