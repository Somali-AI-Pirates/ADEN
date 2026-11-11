"""Judge 사례 세트의 재채점 결과를 사례 × 실행으로 나란히 보여준다(모델 호출 없음).

    python -m experimental.Damsebi.judge_case_table --source judge-cases-20261111 \
        --runs p0v2 p1-r1 p1-r2 p2-r1 p2-r2 [--details p2-r1] [--out <md>]
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


def _cell(item: dict) -> str:
    if item["status"] == "scored":
        return str(int(item["value"]))
    return {"not_applicable": "NA", "unavailable": "불가"}[item["status"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--details", nargs="*", default=[], help="판정 이유를 펼칠 실행")
    parser.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    parser.add_argument("--out")
    args = parser.parse_args()
    base = Path(args.artifacts_dir)
    cases = {}
    for record in _jsonl(base / args.source / "runs.jsonl"):
        cases[record["request_id"]] = record["judge_case"]["case"]
    lines = [f"사례 세트 `{args.source}`. 칸: 사실·조건·근거·판단(1/0/NA/불가) | 검산 진단 전체 상태(대상:상태).", "",
             "| 사례 | " + " | ".join(args.runs) + " |", "|---|" + "---|" * len(args.runs)]
    scores = {}
    for run in args.runs:
        for row in _jsonl(base / f"{args.source}-{run}" / "scores.jsonl"):
            scores[(run, row["request_id"])] = row
    for request_id, case in cases.items():
        cells = []
        for run in args.runs:
            row = scores[(run, request_id)]
            calc = row.get("calculation_checks") or {"status": "missing", "items": []}
            targets = []
            for item in calc["items"]:
                targets.append(f"{item['target']}:{item['status']}")
            four = "·".join(_cell(row["judge"][c]) for c in CRITERIA)
            cells.append(f"{four} \\| {calc['status']}" + (f" ({', '.join(targets)})" if targets else ""))
        lines.append(f"| {case} | " + " | ".join(cells) + " |")
    for run in args.details:
        lines += ["", f"### 판정 이유: {run}", ""]
        for request_id, case in cases.items():
            row = scores[(run, request_id)]
            lines.append(f"- **{case}** has_factual_claims={row['has_factual_claims']}")
            for criterion in CRITERIA:
                item = row["judge"][criterion]
                lines.append(f"  - {criterion} {_cell(item)}: {item['reason']}")
            calc = row.get("calculation_checks") or {}
            lines.append(f"  - 검산 진단 {calc.get('status')}: {calc.get('reason')}")
            for item in calc.get("items", []):
                lines.append(f"    - {item['target']} {item['status']} | 부족 {item['missing_evidence']} | "
                             f"근거 {item['supporting_source_ids']} | {item['reason']}")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
