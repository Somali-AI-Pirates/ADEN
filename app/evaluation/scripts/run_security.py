"""보안 비교: 질문 2개 × 기본/방어 프롬프트 = 답변 4개. 질문별 문맥은 한 번만 만든다.

샘플(가상):
    python -m app.evaluation.scripts.run_security --sample
실제:
    python -m app.evaluation.scripts.run_security --security-file data/basic/evaluation/<보안 파일>.json \
        --retrieve <B모듈>:<retrieve_context> --generate <B모듈>:<generate_with_context> \
        --retrieval-mode hybrid_rerank --experiment-id <일반 평가와 같은 ID> --config configs/basic.yaml \
        --judge-model <모델>
    --config를 생략하면 ADEN_CONFIG, 없으면 configs/advanced.yaml을 쓴다. 데이터셋·설정 해시는 그 파일에서 정한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.scripts._common import (ROOT, base_manifest, guard_overwrite, load_callable,
                                      load_config, now_id, sha256_file)

from app.observability.records import RETRIEVAL_MODES
from app.evaluation.dataset import load_questions, load_references
from app.evaluation.graders import load_prompt
from app.evaluation.report import write_artifacts, write_report
from app.evaluation.security import (analyze_security, check_same_context,
                                 make_openai_security_judge, run_security_comparison)

DEFENSE_PROMPT_PATH = ROOT / "app" / "generation" / "prompts" / "defense.txt"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--sample", action="store_true")
    p.add_argument("--security-file")
    p.add_argument("--retrieve", help="'모듈:이름' (question, mode) -> 후보·최종 문맥")
    p.add_argument("--generate", help="'모듈:이름' (question, chunks, prompt_variant) -> 생성 결과")
    p.add_argument("--retrieval-mode", choices=RETRIEVAL_MODES)
    p.add_argument("--experiment-id")
    p.add_argument("--config", help="설정 파일(저장소 루트 기준). 생략 시 ADEN_CONFIG, 없으면 configs/advanced.yaml. "
                                    "데이터셋·설정 해시와 두 callable의 설정이 이 파일에서 정해진다(샘플은 무시)")
    p.add_argument("--judge-model", help="미지정 시 추종·정확성·거절은 미평가")
    p.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    p.add_argument("--reports-dir")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    if args.sample:
        from app.evaluation.samples import (SAMPLE_DATASET_ID, SAMPLE_SECURITY, make_sample_generate,
                                        mock_security_judge, sample_retrieve)
        experiment_id = args.experiment_id or now_id("sample")
        if not experiment_id.startswith("sample-"):
            raise SystemExit("샘플 실험 ID는 'sample-'로 시작해야 한다")
        path, dataset_id, config_hash = SAMPLE_SECURITY, SAMPLE_DATASET_ID, "sample-config"
        retrieve_fn = sample_retrieve
        generate_fn = make_sample_generate(DEFENSE_PROMPT_PATH.read_text(encoding="utf-8"))
        judge_fn, judge_desc = mock_security_judge, "mock_security_judge(가상)"
        mode = args.retrieval_mode or "hybrid"
        reports_dir = args.reports_dir or str(ROOT / "artifacts" / "sample-reports")
    else:
        if not (args.security_file and args.retrieve and args.generate and args.retrieval_mode):
            raise SystemExit("--security-file, --retrieve, --generate, --retrieval-mode가 필요하다")
        config_path, settings = load_config(args.config)  # callable·평가기 준비 전에 설정을 확정한다
        dataset_id, config_hash = settings.dataset, sha256_file(config_path)
        experiment_id = args.experiment_id or now_id(dataset_id)
        path = args.security_file
        retrieve_fn = load_callable(args.retrieve, config_path=config_path)
        generate_fn = load_callable(args.generate, config_path=config_path)
        if args.judge_model:
            judge_fn = make_openai_security_judge(args.judge_model, load_prompt("security_judge.txt"))
        else:
            judge_fn = None
        judge_desc, mode = args.judge_model, args.retrieval_mode
        reports_dir = args.reports_dir or str(ROOT / "reports")

    report_dir = Path(reports_dir) / experiment_id
    exp_dir = Path(args.artifacts_dir) / experiment_id
    guard_overwrite(report_dir / "security_comparison.csv", args.overwrite)

    questions = load_questions(path)  # id·question만. attack_text는 실행 후 분석에만 사용
    runs = run_security_comparison(questions, retrieve_fn, generate_fn,
                                   experiment_id=experiment_id, retrieval_mode=mode)
    analyzed = analyze_security(runs, load_references(path), judge_fn)
    checks = check_same_context(analyzed)

    manifest = base_manifest(experiment_id=experiment_id, dataset_id=dataset_id,
                             config_hash=config_hash, sample=args.sample)
    question_ids = []
    for question in questions:
        question_ids.append(question["id"])
    manifest["security"] = {"retrieval_mode": mode, "question_ids": question_ids,
                            "security_file": Path(path).name, "judge": judge_desc,
                            "defense_prompt": sha256_file(DEFENSE_PROMPT_PATH)}
    write_artifacts(exp_dir, manifest, security=analyzed)
    write_report(report_dir, manifest, security=analyzed, security_checks=checks)
    bad = []
    for question_id, check in checks.items():
        if check["context_identical"] is False:
            bad.append(question_id)
    unverified = []
    for question_id, check in checks.items():
        if check["context_identical"] is None:
            unverified.append(question_id)
    print(f"실행 기록: {exp_dir}\n제출 결과: {report_dir}")
    if bad:
        raise SystemExit(f"동일 문맥 재사용 실패: {bad}")
    if unverified:
        print(f"※ 동일 문맥 확인 불가(문맥 문자열·모델 입력 누락 또는 생성 실패): {unverified}")
    if args.sample:
        print("※ 가상 샘플 결과다. 실제 Basic 성능이 아니다.")


if __name__ == "__main__":
    main()
