"""B/C 고정 근거 실행 결과를 문항별로 펼쳐 본다(모델 호출 없음): 상태·시도·답변·Judge·검산 진단·RAGAS·trace·게시.

    python -m experimental.Damsebi.bc_result_view <실험 ID> [<실험 ID> ...] [--answers]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRITERIA = ("fact", "condition", "evidence", "decision")


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiments", nargs="+")
    parser.add_argument("--answers", action="store_true")
    args = parser.parse_args()
    for experiment in args.experiments:
        folder = ROOT / "artifacts" / "experiments" / experiment
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        scores = {}
        for row in _jsonl(folder / "scores.jsonl"):
            scores[row["request_id"]] = row
        bench = manifest["benchmark"]
        print(f"## {experiment} commit={manifest.get('git_commit', '')[:7]} dirty={manifest.get('git_dirty')} "
              f"judge_prompt={str(bench.get('judge_prompt'))[7:19]} schema={bench.get('judge_schema')} "
              f"langfuse={bench.get('langfuse_verification')} scores={manifest.get('langfuse_scores', {}).get('status')} "
              f"{manifest.get('langfuse_scores', {}).get('score_status_counts')}")
        for run in _jsonl(folder / "runs.jsonl"):
            score = scores[run["request_id"]]
            judge = "·".join(
                str(int(score["judge"][c]["value"])) if score["judge"][c]["status"] == "scored"
                else score["judge"][c]["status"] for c in CRITERIA)
            calc = score.get("calculation_checks") or {}
            items = [f"{i['target']}:{i['status']}" for i in calc.get("items", [])]
            faith = score["faithfulness"]
            attempts = [(a["attempt"], a.get("error"), a.get("output_tokens")) for a in run["attempts"]]
            print(f"- {run['question_id']} {run['status']} provided_context={run.get('provided_context')} "
                  f"chunks={run.get('final_context_chunk_count')} attempts={attempts} latency={run['latency_ms']}ms "
                  f"| judge {judge} | calc {calc.get('status')} {items} | RAGAS {faith['value']} {faith['status']} "
                  f"| trace {str(run.get('langfuse_trace_id'))[:12]} {run.get('langfuse_verification')}")
            if args.answers:
                print("  ANSWER:", (run.get("answer") or "").replace("\n", " / "))
                print("  REASON:", (run.get("reason") or "").replace("\n", " / "))
                for criterion in CRITERIA:
                    print(f"  {criterion}: {score['judge'][criterion]['reason']}")
                for item in calc.get("items", []):
                    print(f"  calc {item['target']} {item['status']} 부족={item['missing_evidence']} "
                          f"근거={item['supporting_source_ids']}: {item['reason']}")


if __name__ == "__main__":
    main()
