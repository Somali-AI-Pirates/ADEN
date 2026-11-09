"""Compare four Advanced search and evidence patterns on the local PDF corpus."""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.evaluation.metrics import retrieval_metrics
from app.generation.context import build_context
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    PREDICT_BATCH_SIZE,
    RECORDS_DIR,
    evaluation_files,
    evaluation_questions,
    fingerprint,
    rerank_variants,
    retain_reranked_head,
    score_question,
)
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _normalize,
    _quote_groups,
)
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

PILOT_DIR = Path(__file__).with_name("evaluation") / "advanced_coverage_v2"
CURATED = PROJECT_ROOT / "artifacts/u_10o0o/pattern-curation/advanced"
ARCHIVED_BASE = RECORDS_DIR / "advanced-factor-ablation-20261108/selected-results.json"
ARMS = ("baseline", "fielded", "table_view", "roles", "role_followup", "validity")
LATENCY_COUNT = 16


def location(page):
    return Path(page["file_name"]).stem, page["page"]


def packed_context(rows):
    return build_context(
        _context_candidates(rows[:FINAL_K]), FINAL_K, CONTEXT_TOKENS, _count_tokens
    )


def variants(question, index, baseline, fielded, expanded, tables, validity):
    valid_view, diagnostics = validity.render(question, baseline[:FINAL_K])
    return {
        "baseline": (baseline, baseline, {}),
        "fielded": (fielded, fielded, {}),
        "table_view": (baseline, tables.render(question, baseline[:FINAL_K]), {}),
        "roles": (index.role_order(question, baseline), None, {}),
        "role_followup": (index.role_order(question, expanded), None, {}),
        "validity": (baseline, valid_view, diagnostics),
    }


def score_variant(question, ranked, context, reference):
    evidence = reference["evidence_groups"]
    groups, expected = _quote_groups(evidence)
    sources = [
        {"book_id": Path(page["file_name"]).stem, "page": page["page"]}
        for page, _ in ranked
    ]
    result = {
        "id": question["id"],
        "split": question["split"],
        "metrics": None,
        "context_tokens": context.token_count,
        "context_chunks": len(context.final_chunks),
        "context_sha256": context.context_hash,
        "sources": [
            {"book_id": cite.book_id, "page": cite.page}
            for cite in context.source_map.values()
        ],
    }
    if not any(evidence):
        return result
    found = set()
    for item in context.final_chunks:
        chunk = item.chunk
        text = _normalize(chunk.text)
        found.update(
            quote
            for quote in expected
            if quote[:2] == (chunk.book_id, chunk.page) and quote[2] in text
        )
    result["metrics"] = {
        **{
            f"doc_recall@{k}": retrieval_metrics(reference, sources, k)["recall_at_k"][
                "value"
            ]
            for k in (3, FINAL_K, CANDIDATES)
        },
        **{
            f"page_recall@{k}": score_question(
                question["id"], ranked, evidence, top_k=k
            )["metrics"]["recall"]
            for k in (FINAL_K, CANDIDATES)
        },
        "quote_recall": len(found) / len(expected) if expected else None,
        "all_quote_groups": int(all(group & found for group in groups))
        if groups and all(groups)
        else None,
        "quotes_found": len(found),
        "quotes_expected": len(expected),
    }
    return result


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["split"]].append(row)
        if row["split"] in {"development", "additional"}:
            groups["official"].append(row)
    output = {}
    for name, values in groups.items():
        scored = [row["metrics"] for row in values if row["metrics"] is not None]
        output[name] = {
            "questions": len(values),
            "scored": len(scored),
            "changed_context": sum(row["changed_context"] for row in values),
            "context_tokens": mean(row["context_tokens"] for row in values),
            **{
                key: mean(numbers)
                for key in (scored[0] if scored else ())
                if (numbers := [row[key] for row in scored if row[key] is not None])
            },
        }
    return output


def latency_probe(index, reranker, tables, validity, questions, log):
    sample = sorted(
        (q for q in questions if q["split"] in {"development", "additional"}),
        key=lambda q: sha256(q["id"].encode()).hexdigest(),
    )[:LATENCY_COUNT]
    if not sample:
        raise ValueError("공식 질문이 필요합니다.")

    def single(arm, q):
        text = q["question"]
        sparse = index.fielded(text) if arm == "fielded" else index.base(text)
        if arm == "role_followup":
            sparse = index.followup(text, sparse)
        reranked = rerank_variants(
            reranker, [q], {arm: [sparse]}, lambda _message: None
        )[arm][0]
        order = retain_reranked_head(sparse, reranked)
        if arm in {"roles", "role_followup"}:
            order = index.role_order(text, order)
        if arm == "table_view":
            order = tables.render(text, order[:FINAL_K])
        if arm == "validity":
            order, _ = validity.render(text, order[:FINAL_K])
        packed_context(order)

    for arm in ARMS:
        single(arm, sample[0])
    rows = {arm: [] for arm in ARMS}
    for i, q in enumerate(sample):
        for arm in ARMS[i % len(ARMS) :] + ARMS[: i % len(ARMS)]:
            started = perf_counter()
            single(arm, q)
            rows[arm].append((perf_counter() - started) * 1000)
    log(f"워밍업 검색·KURE·문맥 지연 측정 완료 · {len(sample)}문항 × {len(ARMS)}조건")
    return {
        "question_ids": [q["id"] for q in sample],
        "arms": {
            arm: {
                "p50_ms": median(values),
                "p95_ms": sorted(values)[ceil(0.95 * len(values)) - 1],
            }
            for arm, values in rows.items()
        },
    }


def live_sample(questions, references):
    groups = defaultdict(list)
    for i, q in enumerate(questions):
        if q["split"] == "pilot":
            groups[references["pilot"][q["id"]]["question_type"]].append(i)
    selected = []
    for key, group in sorted(groups.items()):
        # Six from each 10-question answer category and three from each 5-question control.
        selected.extend(
            sorted(
                group,
                key=lambda i: sha256(
                    f"patterns-v1:{questions[i]['id']}".encode()
                ).hexdigest(),
            )[: 6 if len(group) >= 10 else 3]
        )
    if len(selected) != 24:
        raise ValueError("파일럿 유형별 24문항 추출 조건과 다릅니다.")
    return selected


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--live", action="store_true", help="GPT-6 Luna 답변·채점 24문항을 추가한다."
    )
    parser.add_argument("--skip-latency", action="store_true")
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 KURE FP16 CUDA 실행이 필요합니다.")
    now = datetime.now(KST)
    output = (
        args.output or RECORDS_DIR / f"advanced-industry-patterns-{now:%Y%m%d-%H%M%S}"
    )
    output.mkdir(parents=True, exist_ok=True)
    if (output / "retrieval.json").exists() or (output / "answers.jsonl").exists():
        raise SystemExit("기존 실행 결과가 있습니다. 다른 출력 경로를 지정하세요.")
    log_path = output / "run.log"
    try:
        with log_path.open("x", encoding="utf-8"):
            pass
    except FileExistsError as error:
        raise SystemExit(
            "이미 시작한 실행입니다. 다른 출력 경로를 지정하세요."
        ) from error

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    files = evaluation_files()
    files["pilot"] = (
        PILOT_DIR / "pilot_questions.json",
        PILOT_DIR / "pilot_references.json",
    )
    questions = evaluation_questions(files)
    assert len(questions) == 380
    inputs = {
        name: {"questions": fingerprint(q), "references": fingerprint(r)}
        for name, (q, r) in files.items()
    }
    pages_input = fingerprint(CACHE_PATH)
    archive = json.loads(ARCHIVED_BASE.read_bytes())
    if pages_input != archive["config"]["pages"] or any(
        inputs[key] != value for key, value in archive["config"]["inputs"].items()
    ):
        raise ValueError("보관된 기준선과 현재 개발 세트·페이지 캐시가 다릅니다.")
    pilot_manifest = json.loads((PILOT_DIR / "manifest.json").read_bytes())
    for name, entry in inputs["pilot"].items():
        filename = Path(entry["path"]).name
        if pilot_manifest["file_sha256"].get(filename) != entry["sha256"]:
            raise ValueError(f"파일럿 {name} 입력의 SHA-256이 다릅니다.")
    manifest_bytes = (args.curated / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["identity"]["dataset"] != "advanced":
        raise ValueError("Advanced 로컬 정제 스냅샷이 필요합니다.")
    log(
        f"개인 Advanced 비교 시작 · {len(questions)}문항 · {torch.cuda.get_device_name()}"
    )
    pages = json.loads(CACHE_PATH.read_bytes())
    index = PatternIndex(pages)
    log(f"페이지 색인 준비 완료 · {len(pages):,}쪽")
    config = {
        "started_at": now.isoformat(),
        "inputs": inputs,
        "pages": pages_input,
        "curation_manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "candidate_count": CANDIDATES,
        "final_k": FINAL_K,
        "context_budget": CONTEXT_TOKENS,
        "context_tokenizer": "o200k_base",
        "kure": {
            "model": KURE,
            "revision": MODELS[KURE],
            "max_tokens": MAX_TOKENS,
            "batch_size": PREDICT_BATCH_SIZE,
            "dtype": "float16",
        },
        "gpu": torch.cuda.get_device_name(),
        "arms": ARMS,
        "retrieval": index.config,
    }
    write_json(output / "config.json", config)

    baseline = [index.base(q["question"]) for q in questions]
    fielded = [index.fielded(q["question"]) for q in questions]
    expanded = [
        index.followup(q["question"], rows)
        for q, rows in zip(questions, baseline, strict=True)
    ]
    search = {"baseline": baseline, "fielded": fielded, "role_followup": expanded}
    log("세 후보 집합 준비 완료")
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    ranked = rerank_variants(reranker, questions, search, log)
    final = {
        arm: [
            retain_reranked_head(sparse, reranked)
            for sparse, reranked in zip(search[arm], ranked[arm], strict=True)
        ]
        for arm in search
    }
    historical = archive["results"]["bigram_id_kure_top3_bm25"]["records"]
    baseline_reproduced = sum(
        [location(page) for page, _ in now_rows]
        == [(p["book_id"], p["page"]) for p in old_row["ranked"]]
        for now_rows, old_row in zip(
            final["baseline"][: len(historical)], historical, strict=True
        )
    )
    log(f"개발 세트 보관 순위 일치 · {baseline_reproduced}/{len(historical)}문항")
    from experimental.u_10o0o.advanced_table_views import TableViews
    from experimental.u_10o0o.advanced_validity import ValidityEvidence

    tables, validity = TableViews(args.curated), ValidityEvidence(args.curated)
    config["code"] = {
        name: fingerprint(Path(__file__).with_name(name + ".py"))
        for name in (
            "advanced_patterns",
            "advanced_field_roles",
            "advanced_table_views",
            "advanced_validity",
        )
    }
    write_json(output / "config.json", config)
    contexts = {arm: [] for arm in ARMS}
    orders = {arm: [] for arm in ARMS}
    diagnostics_by_question = []
    for q, base, fields, extra in zip(
        questions,
        final["baseline"],
        final["fielded"],
        final["role_followup"],
        strict=True,
    ):
        bundle = variants(q["question"], index, base, fields, extra, tables, validity)
        diagnostics_by_question.append(bundle["validity"][2])
        for arm, (order, rendered, _) in bundle.items():
            contexts[arm].append(
                packed_context(order if rendered is None else rendered)
            )
            orders[arm].append(order)
    log("여섯 조건의 문맥 구성 완료")

    # References are opened only after all search and rendering decisions are fixed.
    references = {name: load_references(path) for name, (_, path) in files.items()}
    result = {}
    for arm in ARMS:
        rows = []
        for i, (q, order, context) in enumerate(
            zip(questions, orders[arm], contexts[arm], strict=True)
        ):
            row = score_variant(q, order, context, references[q["split"]][q["id"]])
            row["changed_context"] = (
                context.context_hash != contexts["baseline"][i].context_hash
            )
            if arm == "validity":
                row["diagnostics"] = diagnostics_by_question[i]
            rows.append(row)
        result[arm] = {"summary": summarize(rows), "rows": rows}
    write_json(
        output / "retrieval.json",
        {
            "archived_baseline_rank_matches": baseline_reproduced,
            "followup_changed_candidates": sum(
                x != y for x, y in zip(baseline, expanded, strict=True)
            ),
            "arms": result,
        },
    )
    log("검색·문맥 평가 기록 저장")
    if not args.skip_latency:
        write_json(
            output / "latency.json",
            latency_probe(index, reranker, tables, validity, questions, log),
        )
    if args.live:
        from experimental.u_10o0o.advanced_pattern_answers import run_answers

        selected = live_sample(questions, references)
        write_json(
            output / "answer_sample.json",
            {
                "model": "gpt-6-luna",
                "question_ids": [questions[i]["id"] for i in selected],
                "max_generation_calls": len(selected) * len(ARMS),
                "max_judge_calls": len(selected) * len(ARMS),
            },
        )
        run_answers(
            [questions[i] for i in selected],
            {arm: [v[i] for i in selected] for arm, v in contexts.items()},
            references,
            output,
            log,
        )
    log(f"비교 완료 · {output}")


if __name__ == "__main__":
    main()
