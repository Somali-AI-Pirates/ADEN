"""저장된 채점 결과를 다시 채점하지 않고 그대로 다시 게시한다. 개인 검증 도구다.

    python experimental/Damsebi/republish_scores.py <채점 실험 폴더> [<원본 runs.jsonl>]

같은 결과를 다시 보내도 점수 ID가 같아 서버 건수가 늘지 않는지 확인할 때, 그리고 게시가 중단돼
manifest에 `incomplete`로 남은 실험을 마저 게시할 때 쓴다. 평가 세트와 지표군은 저장된 게시 기록을 따른다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.evaluation.publishing import publish_evaluation_scores  # noqa: E402
from app.evaluation.report import write_artifacts  # noqa: E402
from app.evaluation.scripts._common import load_env  # noqa: E402
from app.observability.tracing import Tracer  # noqa: E402


def main():
    exp_dir = Path(sys.argv[1])
    read = lambda p: [json.loads(line) for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads((exp_dir / "manifest.json").read_text(encoding="utf-8"))
    if not (manifest.get("langfuse_scores") or {}).get("evaluation_set"):
        raise SystemExit("평가 세트로 게시한 기록이 없다(--publish-langfuse-scores로 채점한 실험만 다시 게시한다)")
    runs = read(sys.argv[2] if len(sys.argv) > 2 else exp_dir / "runs.jsonl")
    scores = read(exp_dir / "scores.jsonl")
    before = [row.get("langfuse_score_ids") for row in scores]
    load_env()
    tracer = Tracer.from_env()
    try:
        manifest["langfuse_scores"] = publish_evaluation_scores(tracer, runs, scores, manifest)
    finally:
        tracer.shutdown()
    write_artifacts(exp_dir, manifest, scores=scores)
    same = before == [row.get("langfuse_score_ids") for row in scores]
    print(json.dumps(manifest["langfuse_scores"], ensure_ascii=False))
    print("점수 ID:", "이전 게시와 같음" if same else "이전 게시와 다름(중단된 게시를 마저 보냈거나 새로 게시됨)")


if __name__ == "__main__":
    main()
