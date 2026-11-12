"""B 수정 전후 답변의 채점 결과를 같은 비교 항목으로 나란히 정리한다(읽기 전용, 모델 호출 없음).

인자는 `라벨=채점 실험 ID`다. 같은 답변을 여러 번 채점했으면 `라벨=ID1+ID2+ID3`처럼 묶는다. 그러면 Judge
항목은 채점 회차의 다수결과 일치 수(예: `0 (2/3)`)로 보이고, 동률이면 `split`으로 남긴다(유리한 회차를 고르지 않음).
묶은 회차는 같은 답변 원본·같은 Judge 프롬프트·스키마여야 한다.

    python -m experimental.Damsebi.fix_compare --references <정답 파일> --out <md> \
        before=<수정 전 채점 ID> after=<수정 후 채점 ID1>+<ID2>+<ID3>

TableRAG 실행(`run_table_rag run`)의 채점은 `--table-rag-run <실행 폴더>`를 주고 ID에 그 폴더 `grades/` 아래의
채점 ID를 쓴다. 묶은 회차는 같은 runs.jsonl(`runs_sha256`)을 채점한 것이어야 한다.

    python -m experimental.Damsebi.fix_compare --references <정답 파일> --table-rag-run <실행 폴더> \
        additional=<채점 ID1>+<ID2>+<ID3> --out <md>

비교 항목: 조건(코드 버전·생성 프롬프트·평가 기준), Judge 항목별 점수, RAGAS Faithfulness, 출처 정확성(문맥 안 인용,
참고 근거 쪽 인용), 상태·오류·재시도, 서비스·HTTP 시간, 토큰, 그리고 해당 없음·평가 불가·누락 건수.
회차를 묶으면 Judge 항목이 회차마다 같지 않은 요청을 사람 검수 목록으로 따로 보인다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from app.evaluation.dataset import load_references
from app.evaluation.scripts._common import ROOT

CRITERIA = ("fact", "condition", "evidence", "decision")
KO = {"fact": "사실", "condition": "조건", "evidence": "근거", "decision": "판단"}
SAME_GRADING = ("judge_prompt", "judge_schema", "judge", "references_sha256")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_group(base: Path, ids: list[str], run_dir: Path | None = None) -> dict:
    """base/<채점 ID>(regrade·일반 평가) 또는 run_dir/grades/<채점 ID>(TableRAG)의 회차를 묶는다."""
    grade_dirs = [run_dir / "grades" / i for i in ids] if run_dir else [base / i for i in ids]
    manifests = [json.loads((d / "manifest.json").read_text(encoding="utf-8")) for d in grade_dirs]
    if run_dir:
        runs_sha = "sha256:" + hashlib.sha256((run_dir / "runs.jsonl").read_bytes()).hexdigest()
        if any(m.get("runs_sha256") != runs_sha for m in manifests):
            raise SystemExit(f"{ids}: 채점 회차의 runs_sha256이 현재 {run_dir / 'runs.jsonl'}와 다르다")
        source, source_dir = run_dir.name, run_dir
    else:
        sources = {(m.get("regrade") or {}).get("source_experiment_id") or m["experiment_id"] for m in manifests}
        if len(sources) != 1:
            raise SystemExit(f"{ids}: 묶은 채점 회차의 답변 원본이 다르다 {sorted(sources)}")
        source = sources.pop()
        source_dir = base / source
    for key in SAME_GRADING:
        if len({str(m["benchmark"].get(key)) for m in manifests}) != 1:
            raise SystemExit(f"{ids}: 묶은 채점 회차의 {key}가 다르다")
    source_manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    runs = _jsonl(source_dir / "runs.jsonl")
    scores = {}
    for grade_dir in grade_dirs:
        for row in _jsonl(grade_dir / "scores.jsonl"):
            scores.setdefault(row["request_id"], []).append(row)
    http = source_manifest.get("http_records") or {}
    return {"ids": ids, "manifest": manifests[0], "source": source, "source_manifest": source_manifest,
            "runs": runs, "scores": scores, "rounds": len(ids), "unlinked": http.get("unlinked", []),
            "requests": http.get("requests", len(runs))}


def vote(rows: list[dict], criterion: str) -> tuple[str, float | None, str]:
    """(표시, 다수결 값, 상태). scored 회차만 투표하고 해당 없음·평가 불가는 따로 센다."""
    items = [row["judge"][criterion] for row in rows]
    scored = [item["value"] for item in items if item["status"] == "scored"]
    if not scored:
        status = Counter(item["status"] for item in items).most_common(1)[0][0]
        return ("NA" if status == "not_applicable" else "UN"), None, status
    top = Counter(scored).most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return f"split ({'/'.join(str(int(v)) for v in scored)})", None, "split"
    value, count = top[0]
    shown = str(int(value)) if len(rows) == 1 else f"{int(value)} ({count}/{len(rows)})"
    return shown, value, "scored"


def citation_check(run: dict, reference: dict) -> tuple[int, int, int]:
    """(인용 수, 최종 문맥에 있는 인용, 참고 근거 쪽과 같은 인용). 참고 근거 쪽은 진단이며 대체 근거는 Judge 근거가 본다."""
    context = {(c["source_id"], c["book_id"], c["page"]) for c in run.get("final_context_chunks") or []}
    pages = set()
    for group in reference.get("evidence_groups") or []:
        for item in group:
            pages.add((item["book_id"], item["page"]))
    cites = run.get("citations") or []
    in_context = sum((c.get("source_id"), c.get("book_id"), c.get("page")) in context for c in cites)
    on_reference = sum((c.get("book_id"), c.get("page")) in pages for c in cites)
    return len(cites), in_context, on_reference


def _median(values: list) -> str:
    values = [v for v in values if v is not None]
    return f"{statistics.median(values):.0f}" if values else "-"


def _short(value, n: int = 8) -> str:
    return str(value).removeprefix("sha256:")[:n]


def _retried(run: dict) -> bool:
    """재시도한 요청. TableRAG의 attempts에는 planner·answer 호출이 모두 있어 호출 수로 세지 않는다."""
    roles = [attempt.get("role") for attempt in run["attempts"]]
    if any(roles):
        usage = run.get("usage") or {}
        return roles.count("answer") > 1 or (usage.get("provider_attempts") or 0) > (usage.get("logical_calls") or 0)
    return len(run["attempts"]) > 1


def _mark(item: dict) -> str:
    if item["status"] == "scored":
        return str(int(item["value"]))
    return "NA" if item["status"] == "not_applicable" else "UN"


def render(groups: dict[str, dict], references: dict) -> str:
    lines = ["## 조건", "", "| 라벨 | 채점 실험(회차) | 답변 원본 | 답변 생성 코드 | 생성 프롬프트 | 설정 해시 | "
             "Judge 프롬프트·스키마 | Judge·Faithfulness 모델 | 정답 파일 |", "|---|---|---|---|---|---|---|---|---|"]
    for label, g in groups.items():
        b, sm = g["manifest"]["benchmark"], g["source_manifest"]
        http = sm.get("http_records") or {}
        commit = http.get("source_git_commit") or sm.get("git_commit")
        dirty = http.get("source_git_dirty") if http else sm.get("git_dirty")
        service_prompts = (sm.get("service") or {}).get("prompt_sha256") or {}
        prompts = ([p[:8] for p in http.get("server_prompt_hashes") or []]
                   or [f"{name} {value[:8]}" for name, value in service_prompts.items()] or ["(기록 없음)"])
        lines.append(f"| {label} | {' + '.join(g['ids'])} | {g['source']} | `{str(commit)[:8]}` 미커밋 {dirty} | "
                     f"{', '.join(prompts)} | `{_short(g['manifest']['config_hash'])}` | "
                     f"`{_short(b.get('judge_prompt'))}` {b.get('judge_schema')} | {b.get('judge')} · "
                     f"{b.get('faithfulness')} | `{_short(b.get('references_sha256'))}` |")
    head = ["요청(연결)", "상태", "오류", "재시도 요청"] + [KO[c] for c in CRITERIA] + [
        "Faithfulness", "검산 진단", "인용(문맥 안/참고 쪽/전체)", "서비스 ms 중앙값", "HTTP ms 중앙값",
        "토큰 입력/출력(사용량 완전/부분/미확인)", "누락"]
    lines += ["", "## 요약", "", "Judge 칸은 1점 수/평가 대상 (해당 없음 NA, 평가 불가 UN, 다수결 동률 split). "
              "Faithfulness는 평균 (대상/NA/UN). 누락은 HTTP 미연결 요청과 채점 기록이 없는 요청이다.", "",
              "| 라벨 | " + " | ".join(head) + " |", "|---|" + "---|" * len(head)]
    rows_out = []
    for label, g in groups.items():
        runs, statuses = g["runs"], Counter(r["status"] for r in g["runs"])
        judge = {c: Counter() for c in CRITERIA}
        faith, faith_na, faith_un, calc, cites, missing = [], 0, 0, Counter(), [0, 0, 0], 0
        usage = Counter(r["usage"]["status"] for r in runs)
        for run in runs:
            rows = g["scores"].get(run["request_id"])
            if not rows:
                missing += 1
                continue
            cells = []
            for c in CRITERIA:
                shown, value, status = vote(rows, c)
                judge[c]["one" if value == 1 else ("zero" if status == "scored" else status)] += 1
                cells.append(shown)
            values = [r["faithfulness"]["value"] for r in rows if r["faithfulness"]["status"] == "scored"]
            if values:
                faith.append(statistics.fmean(values))
            elif rows[0]["faithfulness"]["status"] == "not_applicable":
                faith_na += 1
            else:
                faith_un += 1
            calc_status = "/".join(sorted({(r.get("calculation_checks") or {}).get("status", "missing")
                                           for r in rows}))
            calc[calc_status] += 1
            n, in_ctx, on_ref = citation_check(run, references.get(run["question_id"], {}))
            cites = [cites[0] + in_ctx, cites[1] + on_ref, cites[2] + n]
            rows_out.append((label, run, rows, cells, values, calc_status, (in_ctx, on_ref, n)))
        judge_cells = []
        for c in CRITERIA:
            j = judge[c]
            judge_cells.append(f"{j['one']}/{j['one'] + j['zero']} (NA {j['not_applicable']}, UN {j['unavailable']}"
                               + (f", split {j['split']}" if j["split"] else "") + ")")
        tokens_in = sum(r["usage"]["input_tokens"] or 0 for r in runs)
        tokens_out = sum(r["usage"]["output_tokens"] or 0 for r in runs)
        cells = [f"{g['requests']} ({len(runs)})", ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())),
                 str(statuses.get("error", 0)), str(sum(_retried(r) for r in runs)), *judge_cells,
                 f"{statistics.fmean(faith):.2f} ({len(faith)}/{faith_na}/{faith_un})" if faith
                 else f"- (0/{faith_na}/{faith_un})",
                 ", ".join(f"{k} {v}" for k, v in sorted(calc.items())) or "-",
                 f"{cites[0]}/{cites[1]}/{cites[2]}", _median([r["latency_ms"] for r in runs]),
                 _median([r.get("http_latency_ms") for r in runs]),
                 f"{tokens_in}/{tokens_out} ({usage['complete']}/{usage['partial']}/{usage['unknown']})",
                 f"HTTP 미연결 {len(g['unlinked'])}, 채점 없음 {missing}"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## 요청별", "", "| 라벨 | 문항 | 방식 | request_id | 상태 | 모델 호출 | 사실·조건·근거·판단 | "
              "Faithfulness | 검산 | 인용(문맥 안/참고 쪽/전체) | 서비스 ms | HTTP ms | 토큰 입력/출력 | 문맥 해시 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, run, _, cells, values, calc_status, (in_ctx, on_ref, n) in rows_out:
        faith = "/".join(f"{v:.2f}" for v in values) or "-"
        lines.append(f"| {label} | {run['question_id']} | {run['retrieval_mode']} | `{run['request_id'][:12]}` | "
                     f"{run['status']} | {len(run['attempts'])} | {' · '.join(cells)} | {faith} | {calc_status} | "
                     f"{in_ctx}/{on_ref}/{n} | {run['latency_ms']} | {run.get('http_latency_ms') or '-'} | "
                     f"{run['usage']['input_tokens']}/{run['usage']['output_tokens']} | "
                     f"`{_short(run.get('final_context_hash'), 12)}` |")
    for label, g in groups.items():
        for item in g["unlinked"]:
            lines.append(f"| {label} | {item['question_id']} | {item['retrieval_mode']} | "
                         f"`{str(item.get('request_id'))[:12]}` | 미연결({item['outcome']}) | - | {item['reason']} "
                         "| - | - | - | - | - | - | - |")
    review = []
    for label, run, rows, cells, *_ in rows_out:
        for index, c in enumerate(CRITERIA):
            items = [row["judge"][c] for row in rows]
            marks = [_mark(item) for item in items]
            if len(rows) > 1 and len(set(marks)) > 1:
                reasons = " / ".join(f"{n}회: " + str(item.get("reason") or "").replace("|", "\\|").replace("\n", " ")
                                     for n, item in enumerate(items, 1))
                review.append(f"| {label} | {run['question_id']} | {KO[c]} | {'/'.join(marks)} | {cells[index]} | "
                              f"{reasons} |")
    if any(g["rounds"] > 1 for g in groups.values()):
        lines += ["", "## 사람 검수 대상", "", "Judge 항목의 회차별 값이 모두 같지 않은 요청이다(해당 없음 NA·평가 불가 UN "
                  "회차 포함). 다수결 값을 쓰되 점수를 덮어쓰지 않고 사람이 사유를 검수한다.", ""]
        lines += (["| 라벨 | 문항 | 항목 | 회차별 값 | 다수결 | 회차별 사유 |", "|---|---|---|---|---|---|", *review]
                  if review else ["없음(모든 Judge 항목이 회차마다 같다)"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("groups", nargs="+", help="라벨=채점 실험 ID[+ID...]")
    parser.add_argument("--references", required=True, help="참고 정답 파일(출처 정확성 진단에만 사용)")
    parser.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    parser.add_argument("--table-rag-run", help="TableRAG 실행 폴더. 주면 ID는 그 폴더 grades/ 아래 채점 ID다")
    parser.add_argument("--out", help="Markdown 출력 파일(생략 시 화면만)")
    args = parser.parse_args()
    run_dir = Path(args.table_rag_run).resolve() if args.table_rag_run else None
    groups = {}
    for spec in args.groups:
        label, _, ids = spec.partition("=")
        groups[label] = load_group(Path(args.artifacts_dir), ids.split("+"), run_dir)
    text = render(groups, load_references(args.references))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
