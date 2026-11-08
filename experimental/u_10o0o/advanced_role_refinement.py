"""Offline strict-role follow-up probe using archived Advanced rankings."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from statistics import fmean

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_field_roles import (
    MAX_FOLLOWUP_ADDITIONS,
    PatternIndex,
)
from experimental.u_10o0o.advanced_patterns import packed_context, score_variant
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KURE,
    MAX_TOKENS,
    MODELS,
    evaluation_files,
    evaluation_questions,
)
from experimental.u_10o0o.evaluate import fingerprint
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

RECORDS = PROJECT_ROOT / "experimental/u_10o0o/records"
ARCHIVE_DIR = RECORDS / "advanced-factor-ablation-20261108"
PERSONAL_CONTROLS = ARCHIVE_DIR / "personal-controls.json"
SELECTED_RESULTS = ARCHIVE_DIR / "selected-results.json"
PILOT_DIR = PROJECT_ROOT / "experimental/u_10o0o/evaluation/advanced_coverage_v2"
OUTPUT_DIR = RECORDS / "advanced-industry-patterns-20261108/role-refinement"
PILOT_RANKINGS_PATH = OUTPUT_DIR / "pilot_baseline_rankings.json"
MAX_PAIRS_PER_BATCH = 1600


def source_key(page: dict) -> tuple[str, int]:
    return Path(page["file_name"]).stem, int(page["page"])


def source_value(page: dict) -> dict[str, str | int]:
    book_id, page_number = source_key(page)
    return {"book_id": book_id, "page": page_number}


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _rows_from_archive(record: dict, page_by_source: dict) -> list[tuple[dict, float]]:
    rows = []
    for rank, item in enumerate(record["ranked"], start=1):
        source = (item["book_id"], int(item["page"]))
        rows.append((page_by_source[source], float(len(record["ranked"]) - rank + 1)))
    return rows


def _source_list(rows: list[tuple[dict, float]]) -> list[dict[str, str | int]]:
    return [source_value(page) for page, _ in rows]


def _summarize(rows_by_arm: dict[str, list[dict]]) -> dict:
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for arm, rows in rows_by_arm.items():
        for row in rows:
            groups[row["split"]][arm].append(row)
            groups["all_380"][arm].append(row)
            if row["split"] != "pilot":
                groups["old_340"][arm].append(row)
            if row["split"] in {"development", "additional"}:
                groups["official"][arm].append(row)
    output = {}
    for split, arms in groups.items():
        output[split] = {}
        for arm, values in arms.items():
            scored = [row["metrics"] for row in values if row["metrics"]]
            metric_keys = tuple(scored[0]) if scored else ()
            output[split][arm] = {
                "questions": len(values),
                "scored": len(scored),
                "changed_context": sum(row["changed_context"] for row in values),
                **{
                    key: fmean(
                        metric[key] for metric in scored if metric.get(key) is not None
                    )
                    for key in metric_keys
                    if any(metric.get(key) is not None for metric in scored)
                },
            }
    return output


def _archive_maps() -> tuple[dict, dict, dict]:
    personal = json.loads(PERSONAL_CONTROLS.read_bytes())
    selected = json.loads(SELECTED_RESULTS.read_bytes())
    sparse_records = {
        (row["split"], row["id"]): row
        for row in personal["results"]["bigram_id_bm25"]["records"]
    }
    kure_records = {
        (row["split"], row["id"]): row
        for row in personal["results"]["bigram_id_kure"]["records"]
    }
    selected_records = {
        (row["split"], row["id"]): row
        for row in selected["results"]["bigram_id_kure_top3_bm25"]["records"]
    }
    if len(sparse_records) != 340 or set(sparse_records) != set(kure_records):
        raise ValueError("보관된 340개 baseline sparse/KURE 순위가 일치하지 않습니다.")
    if set(sparse_records) != set(selected_records):
        raise ValueError("보관된 baseline 선택 순위와 원본 쿼리 집합이 다릅니다.")
    for key, kure_row in kure_records.items():
        if kure_row["ranked"][:3] != selected_records[key]["ranked"][:3]:
            raise ValueError(f"보관된 KURE 상위 3 순위가 바뀌었습니다: {key}")
    return personal, sparse_records, selected_records


def _group_summary_markdown(summary: dict) -> str:
    parts = [
        "# Strict role refinement",
        "",
        (
            "Offline comparison against the archived selected baseline. Metrics "
            "use the same page evidence and 4,000-token context packer."
        ),
        "",
    ]
    for split, arms in summary.items():
        parts.extend(
            [
                f"## {split}",
                "",
                "| Arm | Scored | Changed contexts | Page recall@8 | Quote recall | "
                "Doc recall@8 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for arm in ("baseline", "strict_roles", "strict_followup"):
            if arm not in arms:
                continue
            row = arms[arm]
            parts.append(
                f"| {arm} | {row['scored']} | {row['changed_context']} | "
                f"{row.get('page_recall@8', 0):.4f} | "
                f"{row.get('quote_recall', 0):.4f} | "
                f"{row.get('doc_recall@8', 0):.4f} |"
            )
        parts.append("")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size는 양수여야 합니다.")
    if not torch.cuda.is_available():
        raise SystemExit("로컬 KURE reranker 실행에 CUDA가 필요합니다.")
    if args.output.exists():
        raise SystemExit(f"출력 디렉터리가 이미 있습니다: {args.output}")
    args.output.mkdir(parents=True)

    def log(message: str) -> None:
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    files = evaluation_files()
    files["pilot"] = (
        PILOT_DIR / "pilot_questions.json",
        PILOT_DIR / "pilot_references.json",
    )
    questions = evaluation_questions(files)
    if len(questions) != 380:
        raise ValueError(f"예상 문항 수와 다릅니다: {len(questions)} != 380")

    personal, sparse_archive, selected_archive = _archive_maps()
    archive_config = personal["config"]
    for split in ("holdout", "development", "additional"):
        questions_path, references_path = files[split]
        archived = archive_config["inputs"][split]
        if fingerprint(questions_path) != archived["questions"]:
            raise ValueError(f"보관 당시 {split} 질문 파일과 다릅니다.")
        if fingerprint(references_path) != archived["references"]:
            raise ValueError(f"보관 당시 {split} 정답 파일과 다릅니다.")
    pilot_manifest = json.loads((PILOT_DIR / "manifest.json").read_bytes())
    page_fingerprint = fingerprint(CACHE_PATH)
    if page_fingerprint["sha256"] != archive_config["pages"]["sha256"]:
        raise ValueError("페이지 캐시가 baseline 보관 당시 입력과 다릅니다.")

    pages = json.loads(CACHE_PATH.read_bytes())
    page_by_source = {source_key(page): page for page in pages}
    if len(page_by_source) != len(pages):
        raise ValueError("페이지 캐시의 source key가 중복됩니다.")

    index = PatternIndex(pages)
    old_questions = [q for q in questions if q["split"] != "pilot"]
    pilot_questions = [q for q in questions if q["split"] == "pilot"]
    if len(old_questions) != 340 or len(pilot_questions) != 40:
        raise ValueError("340개 보관 세트와 40개 파일럿 세트를 확인할 수 없습니다.")

    baseline_rows: dict[str, list[tuple[dict, float]]] = {}
    role_rows: dict[str, list[tuple[dict, float]]] = {}
    followup_rows: dict[str, list[tuple[dict, float]]] = {}
    changed_old: dict[str, dict] = {}
    changed_pilot: dict[str, dict] = {}

    for q in old_questions:
        key = (q["split"], q["id"])
        sparse = _rows_from_archive(sparse_archive[key], page_by_source)
        selected = _rows_from_archive(selected_archive[key], page_by_source)
        baseline_rows[q["id"]] = selected
        role_rows[q["id"]] = index.role_order(
            q["question"], selected, strict_roles=True
        )
        expanded = index.followup(
            q["question"], sparse, top_k=CANDIDATES, strict_roles=True
        )
        followup_rows[q["id"]] = expanded
        sparse_ids = {id(page) for page, _ in sparse}
        expanded_ids = {id(page) for page, _ in expanded}
        if sparse_ids != expanded_ids:
            added = [page for page, _ in expanded if id(page) not in sparse_ids]
            removed = [page for page, _ in sparse if id(page) not in expanded_ids]
            changed_old[q["id"]] = {
                "question_id": q["id"],
                "split": q["split"],
                "added": _source_list([(page, 0.0) for page in added]),
                "removed": _source_list([(page, 0.0) for page in removed]),
            }

    # Pilot baseline needs a local KURE pass. Old questions use the archived KURE
    # top three; only changed old candidate pools are rescored as complete pools.
    for q in pilot_questions:
        sparse = index.base(q["question"], top_k=CANDIDATES)
        followup = index.followup(
            q["question"], sparse, top_k=CANDIDATES, strict_roles=True
        )
        baseline_rows[q["id"]] = sparse
        followup_rows[q["id"]] = followup
        role_rows[q["id"]] = []  # Filled after the local baseline rerank.
        sparse_ids = {id(page) for page, _ in sparse}
        followup_ids = {id(page) for page, _ in followup}
        if sparse_ids != followup_ids:
            added = [page for page, _ in followup if id(page) not in sparse_ids]
            removed = [page for page, _ in sparse if id(page) not in followup_ids]
            changed_pilot[q["id"]] = {
                "question_id": q["id"],
                "added": _source_list([(page, 0.0) for page in added]),
                "removed": _source_list([(page, 0.0) for page in removed]),
            }

    reordered_old = sum(
        role_rows[q["id"]] != baseline_rows[q["id"]] for q in old_questions
    )
    log(
        "candidate decisions fixed · "
        f"strict role reorder={reordered_old}/340 · "
        f"follow-up changed={len(changed_old)}/340 old + "
        f"{len(changed_pilot)}/40 pilot"
    )

    # Predict only what cannot be recovered from stored ordinal ranks: all pilot
    # baseline candidates, additions to changed pilot pools, and changed old pools.
    question_by_id = {q["id"]: q for q in questions}
    score_cache: dict[tuple[str, int], float] = {}
    pairs: list[tuple[str, str]] = []
    pair_keys: list[tuple[str, int]] = []
    queued_keys: set[tuple[str, int]] = set()

    def queue_score(question_id: str, page: dict) -> None:
        cache_key = (question_id, id(page))
        if cache_key in score_cache or cache_key in queued_keys:
            return
        question_text = question_by_id[question_id]["question"]
        pairs.append((question_text, page["text"]))
        pair_keys.append(cache_key)
        queued_keys.add(cache_key)

    for q in pilot_questions:
        for page, _ in baseline_rows[q["id"]]:
            queue_score(q["id"], page)
    for qid, change in changed_pilot.items():
        added_keys = {(row["book_id"], row["page"]) for row in change["added"]}
        for page, _ in followup_rows[qid]:
            if source_key(page) in added_keys:
                queue_score(qid, page)
    for qid in changed_old:
        protected_ids = {id(page) for page, _ in baseline_rows[qid][:3]}
        for page, _ in followup_rows[qid]:
            if id(page) not in protected_ids:
                queue_score(qid, page)

    if pairs:
        reranker = CrossEncoder(
            KURE,
            revision=MODELS[KURE],
            device="cuda",
            max_length=MAX_TOKENS,
            local_files_only=True,
            model_kwargs={"dtype": torch.float16},
        )
        log(f"로컬 KURE 점수 계산 · {len(pairs)} pairs")
        for start in range(0, len(pairs), MAX_PAIRS_PER_BATCH):
            end = min(start + MAX_PAIRS_PER_BATCH, len(pairs))
            values = reranker.predict(
                pairs[start:end],
                batch_size=args.batch_size,
                show_progress_bar=False,
            )
            for key, score in zip(pair_keys[start:end], values, strict=True):
                score_cache[key] = float(score)
            log(f"KURE {end}/{len(pairs)}")
        del reranker
        torch.cuda.empty_cache()
    else:
        score_cache = {}

    # Construct pilot selected rankings from their KURE top three, and update
    # changed candidate pools while keeping those baseline top-three pages fixed.
    for q in pilot_questions:
        qid = q["id"]
        sparse = baseline_rows[qid]
        baseline_scored = sorted(
            ((page, score_cache[(qid, id(page))]) for page, _ in sparse),
            key=lambda row: -row[1],
        )
        selected = baseline_scored[:3]
        selected_ids = {id(page) for page, _ in selected}
        baseline_final = selected + [
            (page, score) for page, score in sparse if id(page) not in selected_ids
        ]
        baseline_rows[qid] = baseline_final
        role_rows[qid] = index.role_order(
            q["question"], baseline_final, strict_roles=True
        )

        followup = followup_rows[qid]
        if idset(followup) == idset(sparse):
            followup_final = baseline_final
        else:
            followup_scores = [
                (
                    page,
                    score_cache.get((qid, id(page)), 0.0),
                )
                for page, _ in followup
            ]
            protected_ids = {id(page) for page, _ in baseline_final[:3]}
            followup_final = baseline_final[:3] + sorted(
                (row for row in followup_scores if id(row[0]) not in protected_ids),
                key=lambda row: -row[1],
            )
        followup_rows[qid] = index.role_order(
            q["question"], followup_final, strict_roles=True
        )

    # For changed old candidate pools, score the changed pool locally, but keep
    # the archived KURE top three in their exact positions.
    for q in old_questions:
        qid = q["id"]
        if qid not in changed_old:
            followup_rows[qid] = role_rows[qid]
            continue
        selected_head = baseline_rows[qid][:3]
        protected_ids = {id(page) for page, _ in selected_head}
        scored = sorted(
            (
                (page, score_cache[(qid, id(page))])
                for page, _ in followup_rows[qid]
                if id(page) not in protected_ids
            ),
            key=lambda row: -row[1],
        )
        reranked_tail = selected_head + scored
        followup_rows[qid] = index.role_order(
            q["question"], reranked_tail, strict_roles=True
        )

    pilot_baseline = [
        {
            "id": q["id"],
            "ranked": _source_list(baseline_rows[q["id"]]),
        }
        for q in pilot_questions
    ]
    write_json(
        args.output / "pilot_baseline_rankings.json",
        {
            "description": (
                "Full 20-source selected baseline ranking; KURE top three "
                "followed by BM25 order."
            ),
            "questions": pilot_baseline,
        },
    )

    # Evaluation references are loaded only after every retrieval decision and
    # reranked candidate list has been fixed.
    references = {name: load_references(path) for name, (_, path) in files.items()}
    arms = {
        "baseline": baseline_rows,
        "strict_roles": role_rows,
        "strict_followup": followup_rows,
    }
    scored_rows: dict[str, list[dict]] = {arm: [] for arm in arms}
    baseline_contexts = {}
    for q in questions:
        context = packed_context(baseline_rows[q["id"]])
        baseline_contexts[q["id"]] = context.context_hash
        reference = references[q["split"]][q["id"]]
        for arm, order_by_id in arms.items():
            order = order_by_id[q["id"]]
            packed = context if arm == "baseline" else packed_context(order)
            scored = score_variant(q, order, packed, reference)
            scored["changed_context"] = (
                packed.context_hash != baseline_contexts[q["id"]]
            )
            scored_rows[arm].append(scored)

    summary = _summarize(scored_rows)
    write_json(
        args.output / "results.json",
        {
            "summary": summary,
            "counts": {
                "archived_questions": len(old_questions),
                "pilot_questions": len(pilot_questions),
                "strict_role_reordered_archived": sum(
                    role_rows[q["id"]] != baseline_rows[q["id"]] for q in old_questions
                ),
                "strict_followup_candidate_changes_archived": len(changed_old),
                "strict_followup_additions_archived": sum(
                    len(row["added"]) for row in changed_old.values()
                ),
                "strict_followup_candidate_changes_pilot": len(changed_pilot),
                "strict_followup_additions_pilot": sum(
                    len(row["added"]) for row in changed_pilot.values()
                ),
                "locally_reranked_pairs": len(pairs),
                "max_additions": MAX_FOLLOWUP_ADDITIONS,
            },
            "candidate_changes": {
                "archived": list(changed_old.values()),
                "pilot": list(changed_pilot.values()),
            },
            "rows": scored_rows,
        },
    )

    input_fingerprints = {
        split: {
            "questions": fingerprint(question_path),
            "references": fingerprint(reference_path),
        }
        for split, (question_path, reference_path) in files.items()
    }
    source_files = {
        "field_roles": Path(__file__).with_name("advanced_field_roles.py"),
        "refinement_runner": Path(__file__),
        "pattern_scorer": Path(__file__).with_name("advanced_patterns.py"),
        "selected_search": Path(__file__).with_name("advanced_search.py"),
        "context_packer": PROJECT_ROOT / "app/generation/context.py",
        "context_evidence": Path(__file__).with_name("context_evidence.py"),
        "retrieval_metrics": PROJECT_ROOT / "app/evaluation/metrics.py",
        "personal_controls": PERSONAL_CONTROLS,
        "selected_results": SELECTED_RESULTS,
    }
    config = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": input_fingerprints,
        "pilot_manifest": fingerprint(PILOT_DIR / "manifest.json"),
        "pilot_manifest_status": pilot_manifest.get("status"),
        "page_cache": page_fingerprint,
        "archives": {
            "personal_controls": fingerprint(PERSONAL_CONTROLS),
            "selected_results": fingerprint(SELECTED_RESULTS),
        },
        "candidate_count": CANDIDATES,
        "final_k": FINAL_K,
        "context_budget": CONTEXT_TOKENS,
        "context_tokenizer": "o200k_base",
        "reranker": {
            "model": KURE,
            "revision": MODELS[KURE],
            "max_tokens": MAX_TOKENS,
            "batch_size": args.batch_size,
            "dtype": "float16",
            "device": torch.cuda.get_device_name(),
            "local_files_only": True,
        },
        "retrieval": index.config,
        "strict_roles_enabled": True,
        "strict_role_top3_protected": True,
        "locally_reranked_pairs": len(pairs),
        "code": {name: fingerprint(path) for name, path in source_files.items()},
        "note": (
            "The retrieval config hash differs from the first full-pattern run "
            "because strict role rules were added as an opt-in branch; default "
            "behavior remains unchanged."
        ),
    }
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
    config["config_sha256"] = sha256(canonical).hexdigest()
    write_json(args.output / "config.json", config)
    (args.output / "report.md").write_text(
        _group_summary_markdown(summary) + "\n",
        encoding="utf-8",
    )
    log(
        f"complete · old changes={len(changed_old)}/340 · "
        f"pilot changes={len(changed_pilot)}/40 · local pairs={len(pairs)} · "
        f"config_sha256={config['config_sha256']}"
    )


def idset(rows: list[tuple[dict, float]]) -> set[int]:
    return {id(page) for page, _ in rows}


if __name__ == "__main__":
    main()
