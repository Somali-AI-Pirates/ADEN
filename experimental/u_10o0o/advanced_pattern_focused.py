"""Answer A/B for table changes and a final-rule offline validity check."""

import argparse
import json
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_patterns import (
    ARCHIVED_BASE,
    CURATED,
    FINAL_K,
    KST,
    evaluation_files,
    evaluation_questions,
    fingerprint,
    location,
    packed_context,
    score_variant,
    summarize,
    write_json,
)
from experimental.u_10o0o.advanced_table_views import TableViews
from experimental.u_10o0o.advanced_validity import ValidityEvidence
from experimental.u_10o0o.prepare import CACHE_PATH

TABLE_SAMPLE = 16


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--resume-from", type=Path, help="완료한 답변 쌍이 담긴 이전 JSONL"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    files = evaluation_files()
    inputs = {
        name: {"questions": fingerprint(q), "references": fingerprint(r)}
        for name, (q, r) in files.items()
    }
    archive = json.loads(ARCHIVED_BASE.read_bytes())
    pages_input = fingerprint(CACHE_PATH)
    if (
        archive["config"]["inputs"] != inputs
        or archive["config"]["pages"] != pages_input
    ):
        raise ValueError("보관된 순위와 현재 입력의 지문이 다릅니다.")
    questions = evaluation_questions(files)
    ordered = archive["results"]["bigram_id_kure_top3_bm25"]["records"]
    pages = {location(row): row for row in json.loads(CACHE_PATH.read_bytes())}
    table = TableViews(args.curated)
    validity = ValidityEvidence(args.curated)
    contexts, rankings, changed_tables, diagnostics = [], [], [], []
    for i, (q, saved) in enumerate(zip(questions, ordered, strict=True)):
        if (q["id"], q["split"]) != (saved["id"], saved["split"]):
            raise ValueError("질문과 순위의 순서가 다릅니다.")
        rows = [
            (pages[(item["book_id"], item["page"])], 0.0) for item in saved["ranked"]
        ]
        original = packed_context(rows)
        table_context = packed_context(table.render(q["question"], rows[:FINAL_K]))
        valid_view, info = validity.render(q["question"], rows[:FINAL_K])
        contexts.append((original, table_context, packed_context(valid_view)))
        rankings.append(rows)
        diagnostics.append(info)
        if (
            q["split"] in {"development", "additional"}
            and original.context_hash != table_context.context_hash
        ):
            changed_tables.append(i)

    selected = sorted(
        changed_tables,
        key=lambda i: sha256(
            f"table-changed-v1:{questions[i]['id']}".encode()
        ).hexdigest(),
    )[:TABLE_SAMPLE]
    if len(selected) != TABLE_SAMPLE:
        raise ValueError("표 문맥이 바뀐 공식 문항이 표본 수보다 적습니다.")
    refs = {name: load_references(path) for name, (_, path) in files.items()}
    validity_rows = []
    for i, (q, order, (base, _table, valid)) in enumerate(
        zip(questions, rankings, contexts, strict=True)
    ):
        row = score_variant(q, order, valid, refs[q["split"]][q["id"]])
        row["changed_context"] = base.context_hash != valid.context_hash
        row["diagnostics"] = diagnostics[i]
        validity_rows.append(row)
    totals = Counter()
    for row in validity_rows:
        totals.update(row["diagnostics"])
    write_json(
        args.output / "config.json",
        {
            "inputs": inputs,
            "pages": pages_input,
            "archive": fingerprint(ARCHIVED_BASE),
            "curation_manifest_sha256": sha256(
                (args.curated / "manifest.json").read_bytes()
            ).hexdigest(),
            "code": {
                name: fingerprint(Path(__file__).with_name(name + ".py"))
                for name in (
                    "advanced_pattern_focused",
                    "advanced_table_views",
                    "advanced_validity",
                    "advanced_pattern_answers",
                )
            },
            "table_changed_official_candidates": len(changed_tables),
            "table_answer_sample": [questions[i]["id"] for i in selected],
            "max_generation_calls": 2 * TABLE_SAMPLE if args.live else 0,
            "max_judge_calls": 2 * TABLE_SAMPLE if args.live else 0,
        },
    )
    write_json(
        args.output / "validity.json",
        {
            "summary": summarize(validity_rows),
            "diagnostics": dict(totals),
            "rows": validity_rows,
        },
    )
    log(f"최종 유효 시점 규칙 재검증 완료 · {len(validity_rows)}문항")
    if args.live:
        from experimental.u_10o0o.advanced_pattern_answers import _summary, run_answers

        arms = ("baseline", "table_view")
        prior = (
            [
                json.loads(line)
                for line in args.resume_from.read_text(encoding="utf-8").splitlines()
            ]
            if args.resume_from
            else []
        )
        expected = {
            (questions[i]["split"], questions[i]["id"], arm): contexts[i][
                index
            ].context_hash
            for i in selected
            for index, arm in enumerate(arms)
        }
        completed = set()
        for row in prior:
            key = row["split"], row["question_id"], row["variant"]
            if key in completed or expected.get(key) != row["context_sha256"]:
                raise ValueError("이전 답변 기록과 현재 표본·문맥이 다릅니다.")
            completed.add(key)
        remaining = [
            i
            for i in selected
            if not all(
                (questions[i]["split"], questions[i]["id"], arm) in completed
                for arm in arms
            )
        ]
        if len(completed) != 2 * (len(selected) - len(remaining)):
            raise ValueError("재개에는 두 조건이 모두 기록된 문항이 필요합니다.")
        if prior:
            (args.output / "answers.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in prior),
                encoding="utf-8",
            )
        log(
            f"표 답변 A/B · 이전 {len(selected) - len(remaining)}문항 · 신규 {len(remaining)}문항"
        )
        new_rows = (
            run_answers(
                [questions[i] for i in remaining],
                {
                    arm: [contexts[i][index] for i in remaining]
                    for index, arm in enumerate(arms)
                },
                refs,
                args.output,
                log,
            )
            if remaining
            else []
        )
        write_json(
            args.output / "answer_summary.json", _summary(prior + new_rows, arms)
        )


if __name__ == "__main__":
    main()
