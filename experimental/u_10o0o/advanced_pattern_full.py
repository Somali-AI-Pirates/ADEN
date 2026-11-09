"""Compare the fixed Advanced retrieval and context arms on all 6,500 new items."""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_questions, load_references
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_patterns import (
    ARCHIVED_BASE,
    ARMS,
    CURATED,
    packed_context,
    score_variant,
    write_json,
)
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    PREDICT_BATCH_SIZE,
    fingerprint,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_table_views import TableViews
from experimental.u_10o0o.advanced_validity import ValidityEvidence
from experimental.u_10o0o.prepare import CACHE_PATH

DATA = Path(__file__).with_name("evaluation") / "advanced_full_coverage"
BATCH_SIZE = 160


def input_files():
    manifest = json.loads((DATA / "manifest.json").read_bytes())
    files = {}
    for name in ("questions", "references", "blind_related_question_ids"):
        path = DATA / f"{name}.json"
        details = fingerprint(path)
        if details["sha256"] != manifest["output_sha256"][path.name]:
            raise ValueError(f"입력 파일 지문이 다릅니다. {path.name}")
        files[name] = details
    files["manifest"] = fingerprint(DATA / "manifest.json")
    questions = [
        {**q, "split": "full"} for q in load_questions(DATA / "questions.json")
    ]
    if len(questions) != manifest["questions"] or len(questions) != 6500:
        raise ValueError("증강 6,500문항을 모두 읽어야 합니다.")
    return questions, files


def rank_batch(index, reranker, questions, log):
    baseline = [index.base(q["question"]) for q in questions]
    fielded = [index.fielded(q["question"]) for q in questions]
    expanded = [
        index.followup(q["question"], rows, strict_roles=True)
        for q, rows in zip(questions, baseline, strict=True)
    ]
    search = {"baseline": baseline, "fielded": fielded, "role_followup": expanded}
    ranked = rerank_variants(reranker, questions, search, log)
    return {
        arm: [
            retain_reranked_head(sparse, reranked)
            for sparse, reranked in zip(rows, ranked[arm], strict=True)
        ]
        for arm, rows in search.items()
    }, [
        [id(page) for page, _ in base] != [id(page) for page, _ in followup]
        for base, followup in zip(baseline, expanded, strict=True)
    ]


def pack_batch(index, tables, validity, questions, ranked):
    for i, q in enumerate(questions):
        text = q["question"]
        base = ranked["baseline"][i]
        fielded = ranked["fielded"][i]
        roles = index.role_order(text, base, strict_roles=True)
        followup = index.role_order(text, ranked["role_followup"][i], strict_roles=True)
        table = tables.render(text, base[:FINAL_K])
        valid, diagnostics = validity.render(text, base[:FINAL_K])
        yield {
            "baseline": (base, packed_context(base), {}),
            "fielded": (fielded, packed_context(fielded), {}),
            "table_view": (base, packed_context(table), {}),
            "roles": (roles, packed_context(roles), {}),
            "role_followup": (followup, packed_context(followup), {}),
            "validity": (base, packed_context(valid), diagnostics),
        }


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups["overlap" if row["blind_related"] else "disjoint"].append(row)
        groups[f"type:{row['question_type']}"].append(row)
        groups[f"identifier:{row['identifier_mode']}"].append(row)
    result = {}
    for group, items in groups.items():
        arm_summary = {}
        for arm in ARMS:
            entries = [row["arms"][arm] for row in items]
            counts = {
                key: sum(entry[key] for entry in entries)
                for key in entries[0]
                if all(isinstance(entry[key], (float, int)) for entry in entries)
            }
            arm_summary[arm] = {
                "sum": counts,
                "mean": {key: value / len(items) for key, value in counts.items()},
            }
        diagnostics = defaultdict(int)
        for row in items:
            for key, value in row["validity"].items():
                diagnostics[key] += value
        result[group] = {
            "questions": len(items),
            "followup_candidate_changes": sum(
                row["followup_candidate_change"] for row in items
            ),
            "validity": dict(diagnostics),
            "arms": arm_summary,
        }
    return result


def main():
    from experimental.u_10o0o.advanced_full_metrics import fact_coverage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--curated", type=Path, default=CURATED)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 KURE FP16 CUDA 실행이 필요합니다.")
    args.output.mkdir(parents=True, exist_ok=False)
    log_path = args.output / "run.log"

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    started = perf_counter()
    questions, files = input_files()
    page_fingerprint = fingerprint(CACHE_PATH)
    archive = json.loads(ARCHIVED_BASE.read_bytes())
    if page_fingerprint != archive["config"]["pages"]:
        raise ValueError("보관된 기준선과 현재 페이지 캐시가 다릅니다.")
    curated_manifest = (args.curated / "manifest.json").read_bytes()
    if json.loads(curated_manifest)["identity"]["dataset"] != "advanced":
        raise ValueError("Advanced 로컬 정제 스냅샷이 필요합니다.")
    log(
        f"증강 전체 평가 시작 · {len(questions):,}문항 · {torch.cuda.get_device_name()}"
    )
    pages = json.loads(CACHE_PATH.read_bytes())
    index = PatternIndex(pages)
    index.base_index = FastBM25(index.base_index)
    if index.heading_index is not None:
        index.heading_index = FastBM25(index.heading_index)
    tables = TableViews(args.curated)
    validity = ValidityEvidence(args.curated)
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    write_json(
        args.output / "config.json",
        {
            "started_at": datetime.now(KST).isoformat(),
            "inputs": files,
            "pages": page_fingerprint,
            "curation_manifest_sha256": sha256(curated_manifest).hexdigest(),
            "code_sha256": {
                name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "advanced_pattern_full.py",
                    "advanced_patterns.py",
                    "advanced_search.py",
                    "advanced_full_metrics.py",
                    "advanced_fast_bm25.py",
                    "advanced_field_roles.py",
                    "advanced_table_views.py",
                    "advanced_validity.py",
                )
            },
            "candidate_count": CANDIDATES,
            "reranked_head": 3,
            "final_k": FINAL_K,
            "context_tokens": CONTEXT_TOKENS,
            "context_tokenizer": "o200k_base",
            "kure": {
                "model": KURE,
                "revision": MODELS[KURE],
                "max_tokens": MAX_TOKENS,
                "batch_size": PREDICT_BATCH_SIZE,
                "dtype": "float16",
                "gpu": torch.cuda.get_device_name(),
            },
            "question_batch_size": BATCH_SIZE,
            "retrieval": index.config,
            "strict_roles": True,
            "arms": ARMS,
            "generation_calls": 0,
        },
    )
    log(f"색인과 KURE 준비 완료 · {len(pages):,}쪽")
    references = None
    overlap = set()
    rows = []
    with (args.output / "questions.jsonl").open("x", encoding="utf-8") as stream:
        for start in range(0, len(questions), BATCH_SIZE):
            batch = questions[start : start + BATCH_SIZE]
            ranked, changed = rank_batch(index, reranker, batch, log)
            packed = list(pack_batch(index, tables, validity, batch, ranked))
            if references is None:
                references = load_references(DATA / "references.json")
                overlap = set(
                    json.loads((DATA / "blind_related_question_ids.json").read_bytes())[
                        "ids"
                    ]
                )
                if (
                    set(references) != {q["id"] for q in questions}
                    or len(overlap) != 397
                    or overlap - references.keys()
                ):
                    raise ValueError("참고 파일과 별도 집계 ID를 확인할 수 없습니다.")
            for q, contexts, change in zip(batch, packed, changed, strict=True):
                qid = q["id"]
                reference = references[qid]
                arm_rows = {}
                base_hash = contexts["baseline"][1].context_hash
                for arm, (order, context, _) in contexts.items():
                    result = score_variant(q, order, context, reference)
                    arm_rows[arm] = {
                        **result["metrics"],
                        **fact_coverage(reference, context),
                        "context_tokens": context.token_count,
                        "context_chunks": len(context.final_chunks),
                        "context_changed": context.context_hash != base_hash,
                    }
                row = {
                    "id": qid,
                    "blind_related": qid in overlap,
                    "question_type": reference["question_type"],
                    "identifier_mode": reference["identifier_mode"],
                    "followup_candidate_change": change,
                    "arms": arm_rows,
                    "validity": contexts["validity"][2],
                }
                rows.append(row)
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()
            log(
                f"증강 {len(rows):,}/{len(questions):,}문항 저장 · 경과 {perf_counter() - started:.1f}초"
            )
    write_json(args.output / "summary.json", summarize(rows))
    log(f"증강 {len(rows):,}문항 완료 · API 호출 0회")


if __name__ == "__main__":
    main()
