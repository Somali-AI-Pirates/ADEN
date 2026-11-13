"""Score saved reranking results against the established Advanced references."""

import argparse
import json
from collections import defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from replay import QUESTIONS, write_json

from app.evaluation.dataset import load_questions, load_references
from app.rag.corpus import load_corpus
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.pattern_retrieval.established_retrieve import question_sources
from experimental.u_10o0o.pattern_retrieval.established_score import (
    official_metrics,
    page_key,
)

ARMS = ("fp32", "bf16")
BUNDLE = Path("artifacts/advanced-table-rag")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def checked_rows(replay):
    rows = {row["id"]: row for row in replay["rows"]}
    questions = {
        row["id"]: row["question"] for row in json.loads(QUESTIONS.read_bytes())
    }
    if (
        replay["references_opened"] is not False
        or replay["questions_sha256"] != digest(QUESTIONS)
        or replay["snapshot_sha256"] != digest(BUNDLE / "corpus.json")
        or len(replay["rows"]) != 280
        or rows.keys() != questions.keys()
    ):
        raise ValueError("Saved replay does not match the frozen questions and bundle")
    for question_id, row in rows.items():
        if len(set(row["candidates"])) != 20:
            raise ValueError(f"Invalid input candidates for {question_id}")
        if row["fp32"]["work"] != row["bf16"]["work"]:
            raise ValueError(f"Unequal inputs for {question_id}")
        for arm in ARMS:
            side = row[arm]
            if (
                set(side["rank"]) != set(row["candidates"])
                or len(side["rank"]) != 20
                or len(side["final_sources"]) > 8
                or side["context_tokens"] > 8000
            ):
                raise ValueError(f"Invalid replay output for {question_id}")
    return rows, questions


def score_pages(suite, reference, pages):
    if suite == "negative_control":
        return {"status": "outside_corpus"}
    if suite.startswith("official_"):
        metrics = official_metrics(reference, pages)
        if metrics is None:
            return {"status": "no_page_reference"}
        found = {page_key(page) for page in pages}
        return {
            "status": "scored",
            "complete": bool(metrics["all_groups"]),
            "hit": metrics["matched_groups"],
            "required": metrics["required_groups"],
            "page_recall": metrics["page_recall"],
            "matched_requirements": [
                index
                for index, group in enumerate(reference["evidence_groups"], 1)
                if {page_key(evidence) for evidence in group} & found
            ],
        }
    metrics = fact_coverage(reference, pages)
    if not metrics["required_facts"]:
        raise ValueError("Category reference has no required facts")
    return {
        "status": "scored",
        "complete": bool(metrics["all_facts"]),
        "hit": metrics["required_facts_hit"],
        "required": metrics["required_facts"],
        "matched_requirements": [
            index
            for index, requirement in enumerate(reference["evidence_requirements"], 1)
            if fact_coverage({"evidence_requirements": [requirement]}, pages)[
                "all_facts"
            ]
        ],
    }


def group_for(suite, reference):
    if suite == "scenarios":
        return f"scenario_{reference['scenario']}"
    return suite


def aggregate(rows):
    result = {
        "n": len(rows),
        "rank_changed": sum(row["rank_changed"] for row in rows),
        "top1_changed": sum(row["top1_changed"] for row in rows),
        "final_order_changed": sum(row["final_order_changed"] for row in rows),
        "final_set_changed": sum(row["final_set_changed"] for row in rows),
        "table_ids_changed": sum(row["table_ids_changed"] for row in rows),
    }
    scored = [row for row in rows if row["fp32"]["status"] == "scored"]
    result["scored"] = len(scored)
    for arm in ARMS:
        recall = [
            row[arm]["page_recall"] for row in scored if "page_recall" in row[arm]
        ]
        result[arm] = {
            "complete": sum(row[arm]["complete"] for row in scored),
            "hit": sum(row[arm]["hit"] for row in scored),
            "required": sum(row[arm]["required"] for row in scored),
            "mean_page_recall": sum(recall) / len(recall) if recall else None,
        }
    result["complete_gains"] = [
        row["id"] for row in scored if row["bf16"]["complete"] > row["fp32"]["complete"]
    ]
    result["complete_losses"] = [
        row["id"] for row in scored if row["bf16"]["complete"] < row["fp32"]["complete"]
    ]
    result["partial_gains"] = [
        row["id"]
        for row in scored
        if set(row["bf16"]["matched_requirements"])
        - set(row["fp32"]["matched_requirements"])
    ]
    result["partial_losses"] = [
        row["id"]
        for row in scored
        if set(row["fp32"]["matched_requirements"])
        - set(row["bf16"]["matched_requirements"])
    ]
    return result


def run(replay_path, output):
    replay = json.loads(replay_path.read_bytes())
    rows, questions = checked_rows(replay)
    # References are intentionally first opened after both arms exist and validate.
    reference_files = {}
    references = {}
    for suite, (question_path, reference_path) in question_sources().items():
        dataset = {row["id"]: row["question"] for row in load_questions(question_path)}
        expected = {
            name: question
            for name, question in questions.items()
            if name.startswith(suite + ":")
        }
        if expected != {
            f"{suite}:{name}": question for name, question in dataset.items()
        }:
            raise ValueError(f"Question set changed for {suite}")
        references[suite] = load_references(reference_path)
        reference_files[str(reference_path.relative_to(Path.cwd()))] = digest(
            reference_path
        )

    groups = defaultdict(list)
    with closing(load_corpus(BUNDLE)) as corpus:
        for question_id, row in rows.items():
            suite, name = question_id.split(":", 1)
            reference = references[suite][name]
            arm_pages = {arm: row[arm]["final_sources"] for arm in ARMS}
            scored = {}
            for arm, source_ids in arm_pages.items():
                pages = [
                    {
                        "book_id": page.metadata["book_id"],
                        "page": page.metadata["page"],
                        "text": page.page_content,
                    }
                    for source_id in source_ids
                    for page in (corpus.pages_by_source[source_id],)
                ]
                scored[arm] = score_pages(suite, reference, pages)
            before, after = arm_pages["fp32"], arm_pages["bf16"]
            compared = {
                "id": question_id,
                "group": group_for(suite, reference),
                "rank_changed": row["fp32"]["rank"] != row["bf16"]["rank"],
                "top1_changed": row["fp32"]["rank"][0] != row["bf16"]["rank"][0],
                "final_order_changed": before != after,
                "final_set_changed": set(before) != set(after),
                "table_ids_changed": row["fp32"]["table_ids"]
                != row["bf16"]["table_ids"],
                "removed": sorted(set(before) - set(after)),
                "added": sorted(set(after) - set(before)),
                "final_sources": arm_pages,
                **scored,
            }
            groups[compared["group"]].append(compared)
            groups[
                "official" if suite.startswith("official_") else "categories"
            ].append(compared)
            groups["all"].append(compared)
    result = {
        "replay_sha256": digest(replay_path),
        "reference_sha256": reference_files,
        "summary": {group: aggregate(items) for group, items in groups.items()},
        "per_question": groups["all"],
    }
    write_json(output, result)
    for key, group in result["summary"].items():
        print(key, group)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.replay, args.output)
