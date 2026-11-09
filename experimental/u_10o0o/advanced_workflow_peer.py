"""Replay the teammate's no-LLM graph preset on a workflow question set.

The teammate searches p800 chunks. This adapter keeps that candidate generation
and ranking, collapses ranked chunks to unique PDF pages, then supplies restored
full-page text to the common 8-page / 4,000-token context builder. The adaptation
is recorded in the output because it is not a byte-for-byte replay of the
teammate's chunk context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

import torch
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.evaluation.dataset import load_references
from app.evaluation.metrics import retrieval_metrics
from app.generation.context import build_context
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_search import CONTEXT_TOKENS, FINAL_K, KST
from experimental.u_10o0o.advanced_search import score_question as score_pages
from experimental.u_10o0o.context_evidence import _count_tokens, score_context
from experimental.u_10o0o.prepare import DATA_DIR, PROJECT_ROOT
from shared.contracts import Chunk, PageDocument, RetrievedChunk

TEAM_DIR = PROJECT_ROOT / "experimental/ysl727727/overnight"
TEAM_ARCHIVE = (
    PROJECT_ROOT
    / "experimental/u_10o0o/records/advanced-industry-patterns-20261108/peer-comparison/config.json"
)
RESTORED_PAGES = PROJECT_ROOT / "artifacts/u_10o0o/advanced_restored_pages.json"
INDEX_DIR = PROJECT_ROOT / "artifacts/u_10o0o/advanced-workflow-peer-index-20261109"
PRESET_NAME = "team_graph"
CONTEXT_ADAPTATION = "rank p800 chunks -> collapse to unique (book_id,page) -> restore complete source pages -> common context builder"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_peer_modules(index_dir: Path):
    """Import teammate modules with all generated files redirected to index_dir."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["OV_DEVICE"] = "cuda"
    sys.path.insert(0, str(TEAM_DIR))
    import common

    common.OUT = index_dir
    common.HERE = index_dir
    common.LEVELS["adv"]["docs"] = [DATA_DIR / "documents"]
    import index
    import prepare
    import run

    return common, index, prepare, run


def _load_archive() -> dict[str, Any]:
    archive = json.loads(TEAM_ARCHIVE.read_text(encoding="utf-8"))
    expected = archive["team_sources"]
    for source in expected:
        path = PROJECT_ROOT / source["path"]
        if _sha256(path) != source["sha256"]:
            raise ValueError(f"동료 소스 지문 불일치: {source['path']}")
    return archive


def prepare_index(index_dir: Path, log) -> dict[str, Any]:
    archive = _load_archive()
    common, index, prepare, run = _load_peer_modules(index_dir)
    extraction_report = index_dir / "adv/extract_report.json"
    if extraction_report.exists():
        extraction = json.loads(extraction_report.read_text(encoding="utf-8"))
        log("기존 scratch PDF 추출을 재사용합니다.")
    else:
        log("동료 원본 파이프라인으로 Advanced PDF 120개를 scratch에 추출합니다.")
        prepare.main(["adv"])
        extraction = json.loads(extraction_report.read_text(encoding="utf-8"))
    page_count = sum(item["pages"] for item in extraction["files"])
    if (
        extraction["failed"]
        or len(extraction["files"]) != archive["pdfs"]
        or page_count != archive["pages"]
    ):
        raise ValueError("보관된 동료 평가와 같은 120개 PDF·18,000쪽이 아닙니다.")

    cfg = archive["presets"][PRESET_NAME]
    if not torch.cuda.is_available():
        raise RuntimeError("동료 기준과 같은 CUDA FP16 실행 환경이 필요합니다.")
    # Pin the archived revision before Index builds any document vectors.
    dense = SentenceTransformer(
        archive["dense"]["model"],
        revision=archive["dense"]["revision"],
        device="cuda",
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    dense.max_seq_length = index.MODELS[cfg["emb"]][3]
    index._loaded[cfg["emb"]] = dense
    ix = run.get_index("adv", cfg)
    if len(ix.rows) != archive["chunks"]:
        raise ValueError("보관된 동료 평가와 p800 청크 수가 다릅니다.")
    rr_model = CrossEncoder(
        archive["reranker"]["model"],
        revision=archive["reranker"]["revision"],
        max_length=archive["reranker"]["max_tokens"],
        device="cuda",
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    rr = run.Reranker("adv")
    rr.models[cfg["rerank"]] = rr_model

    # Persist only provenance and counts; query/reference text is not part of index metadata.
    manifest = {
        "prepared_at": datetime.now(KST).isoformat(),
        "source_code": archive["team_sources"],
        "archive_sha256": _sha256(TEAM_ARCHIVE),
        "dataset": "data/advanced/documents/*.pdf",
        "pdf_count": len(extraction["files"]),
        "page_count": page_count,
        "p800_chunk_count": len(ix.rows),
        "dense": archive["dense"],
        "reranker": archive["reranker"],
        "preset_name": PRESET_NAME,
        "preset": cfg,
        "device": torch.cuda.get_device_name(),
        "candidate_chunks": cfg["pool"],
        "llm_calls": 0,
        "index_dir": str(index_dir.relative_to(PROJECT_ROOT)),
    }
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "workflow-peer-index.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(
        f"scratch 색인 준비 완료 · PDF {len(extraction['files']):,}개 · "
        f"페이지 {page_count:,}개 · p800 청크 {len(ix.rows):,}개"
    )
    return {
        "archive": archive,
        "common": common,
        "index": index,
        "run": run,
        "ix": ix,
        "rr": rr,
        "cfg": cfg,
        "manifest": manifest,
    }


def _read_questions(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = value.get("questions", value.get("items"))
    if not isinstance(value, list):
        raise TypeError(
            "질문 파일은 JSON 배열 또는 questions/items 배열을 포함해야 합니다."
        )
    questions = []
    for position, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise TypeError(f"질문 {position}에 문자열 question 필드가 없습니다.")
        qid = item.get("id", item.get("question_id", f"q{position + 1:03}"))
        questions.append({"id": str(qid), "question": item["question"]})
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("질문 ID가 중복됩니다.")
    return questions


def _page_map(path: Path) -> dict[tuple[str, int], PageDocument]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise TypeError("복원 페이지 캐시는 JSON 배열이어야 합니다.")
    pages = {}
    for row in values:
        page = PageDocument(
            book_id=Path(row["file_name"]).stem,
            file_name=row["file_name"],
            page=row["page"],
            text=row["text"],
        )
        pages[(page.book_id, page.page)] = page
    return pages


def _rank_unique_pages(
    ix, chunk_indices: list[int], pages: dict[tuple[str, int], PageDocument]
):
    ranked: list[tuple[PageDocument, float]] = []
    seen = set()
    missing = 0
    for chunk_rank, chunk_index in enumerate(chunk_indices, start=1):
        row = ix.rows[chunk_index]
        key = (row["book_id"], int(row["page"]))
        if key in seen:
            continue
        seen.add(key)
        page = pages.get(key)
        if page is None:
            missing += 1
            continue
        ranked.append((page, -float(chunk_rank)))
    return ranked[:20], missing


def _assemble_context(ranked, question_index: int):
    candidates = []
    for rank, (page, score) in enumerate(ranked[:FINAL_K], start=1):
        chunk = Chunk(
            chunk_id=f"teammate-page:{question_index}:{page.book_id}:{page.page}",
            book_id=page.book_id,
            file_name=page.file_name,
            page=page.page,
            text=page.text,
            heading="",
        )
        candidates.append(
            RetrievedChunk(
                chunk=chunk,
                rank=rank,
                score=score,
                retriever="teammate-p800-page-adapter",
            )
        )
    return build_context(candidates, FINAL_K, CONTEXT_TOKENS, _count_tokens)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row["metrics"] for row in rows if row["metrics"] is not None]
    if not scored:
        return {"questions": len(rows), "scored": 0}
    metric_keys = sorted({key for row in scored for key in row})
    return {
        "questions": len(rows),
        "scored": len(scored),
        **{
            key: fmean(row[key] for row in scored if row.get(key) is not None)
            for key in metric_keys
        },
        "mean_unique_pages_from_20_chunks": fmean(
            row["candidate_unique_pages"] for row in rows
        ),
        "mean_context_pages": fmean(row["context_pages"] for row in rows),
        "questions_with_missing_restored_pages": sum(
            row["missing_restored_pages"] > 0 for row in rows
        ),
    }


def evaluate(args, output: Path, log) -> None:
    questions = _read_questions(args.questions)
    provenance = prepare_index(args.index_dir, log)
    pages = _page_map(args.restored_pages)
    ix, index, run, cfg, rr = (
        provenance[key] for key in ("ix", "index", "run", "cfg", "rr")
    )
    output.mkdir(parents=True, exist_ok=False)

    question_file_sha = _sha256(args.questions)
    restored_file_sha = _sha256(args.restored_pages)
    rankings = []
    query_times = []
    dense_pairs = 0
    for start in range(0, len(questions), 32):
        batch = questions[start : start + 32]
        vectors = index.embed_queries([q["question"] for q in batch], cfg["emb"])
        dense_pairs += len(batch) * cfg["cand_d"]
        for offset, (question, vector) in enumerate(
            zip(batch, vectors, strict=True), start=start
        ):
            tick = perf_counter()
            chunk_order = run.retrieve(
                ix,
                rr,
                cfg,
                question["question"],
                vector,
                out_n=cfg["pool"],
            )
            query_times.append((perf_counter() - tick) * 1000)
            ranked, missing = _rank_unique_pages(ix, chunk_order, pages)
            context = _assemble_context(ranked, offset)
            rankings.append(
                {
                    "id": question["id"],
                    "candidate_chunks": [
                        {
                            "chunk_id": ix.rows[i]["chunk_id"],
                            "book_id": ix.rows[i]["book_id"],
                            "page": ix.rows[i]["page"],
                        }
                        for i in chunk_order
                    ],
                    "ranked_pages": [
                        {
                            "book_id": page.book_id,
                            "file_name": page.file_name,
                            "page": page.page,
                        }
                        for page, _ in ranked
                    ],
                    "context_pages": [
                        {
                            "book_id": item.chunk.book_id,
                            "file_name": item.chunk.file_name,
                            "page": item.chunk.page,
                        }
                        for item in context.final_chunks
                    ],
                    "candidate_unique_pages": len(ranked),
                    "context_pages_count": len(context.final_chunks),
                    "context_tokens": context.token_count,
                    "missing_restored_pages": missing,
                    "context_hash": context.context_hash,
                    "_context": context,
                    "_ranked": ranked,
                }
            )
        log(
            f"검색 완료 {min(start + len(batch), len(questions)):,}/{len(questions):,}문항"
        )

    # Persist retrieval-only output before opening evaluation labels.
    public_rankings = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rankings
    ]
    (output / "rankings.json").write_text(
        json.dumps(public_rankings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    refs = load_references(args.references)
    if set(refs) != {q["id"] for q in questions}:
        raise ValueError("질문과 참고 근거의 ID 집합이 일치하지 않습니다.")
    metrics_rows = []
    for question, retrieved in zip(questions, rankings, strict=True):
        reference = refs[question["id"]]
        ranked = retrieved["_ranked"]
        context = retrieved["_context"]
        sources = [
            {"book_id": page.book_id, "file_name": page.file_name, "page": page.page}
            for page, _ in ranked
        ]
        evidence = reference.get("evidence_groups", [])
        metric = None
        if any(evidence):
            context_sources = [
                {
                    "book_id": item.chunk.book_id,
                    "file_name": item.chunk.file_name,
                    "page": item.chunk.page,
                }
                for item in context.final_chunks
            ]
            doc8 = retrieval_metrics(reference, context_sources, FINAL_K)
            doc20 = retrieval_metrics(reference, sources, 20)
            context_ranked = [
                (
                    {
                        "book_id": item.chunk.book_id,
                        "file_name": item.chunk.file_name,
                        "page": item.chunk.page,
                    },
                    item.score,
                )
                for item in context.final_chunks
            ]
            # Page Recall and quote coverage use exactly the pages admitted to context.
            page8 = score_pages(question, context_ranked, evidence, top_k=FINAL_K)[
                "metrics"
            ]
            ranked_pages = [
                (
                    {
                        "book_id": page.book_id,
                        "file_name": page.file_name,
                        "page": page.page,
                        "text": page.text,
                    },
                    score,
                )
                for page, score in ranked
            ]
            quotes = score_context(
                ranked_pages,
                evidence,
                token_budget=CONTEXT_TOKENS,
                top_k=FINAL_K,
            )
            facts = (
                fact_coverage(reference, context)
                if isinstance(reference.get("evidence_requirements"), list)
                else {}
            )
            metric = {
                "doc_hit@8": doc8["hit_at_k"]["value"],
                "doc_recall@8": doc8["recall_at_k"]["value"],
                "candidate_doc_recall@20": doc20["recall_at_k"]["value"],
                "page_recall@8": page8["recall"],
                "quote_recall": quotes["found_quotes"] / quotes["expected_quotes"]
                if quotes["expected_quotes"]
                else None,
                "evidence_groups_any_quote": quotes["groups_with_any_found_quote"]
                / quotes["evidence_groups"]
                if quotes["evidence_groups"]
                else None,
                **facts,
                "context_tokens": context.token_count,
                "context_pages": len(context.final_chunks),
            }
        metrics_rows.append(
            {
                "id": question["id"],
                "metrics": metric,
                "candidate_unique_pages": retrieved["candidate_unique_pages"],
                "context_pages": len(context.final_chunks),
                "context_tokens": context.token_count,
                "missing_restored_pages": retrieved["missing_restored_pages"],
            }
        )
    summary = _summarize(metrics_rows)
    summary["runtime"] = {
        "median_retrieval_ms": sorted(query_times)[(len(query_times) - 1) // 2]
        if query_times
        else None,
        "p95_retrieval_ms": sorted(query_times)[
            max(0, (95 * len(query_times) - 1) // 100)
        ]
        if query_times
        else None,
        "dense_candidate_pairs": dense_pairs,
        "reranker_pairs": rr.dirty,
    }
    summary["question_results"] = metrics_rows
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    config = {
        "created_at": datetime.now(KST).isoformat(),
        "questions": {
            "path": str(args.questions),
            "sha256": question_file_sha,
            "count": len(questions),
        },
        "references": {
            "path": str(args.references),
            "sha256": _sha256(args.references),
        },
        "restored_pages": {
            "path": str(args.restored_pages),
            "sha256": restored_file_sha,
            "page_count": len(pages),
        },
        "team_archive": {
            "path": str(TEAM_ARCHIVE.relative_to(PROJECT_ROOT)),
            "sha256": _sha256(TEAM_ARCHIVE),
        },
        "team_source": provenance["manifest"]["source_code"],
        "preset_name": PRESET_NAME,
        "preset": cfg,
        "retrieval": {
            "candidate_chunks": cfg["pool"],
            "candidate_dense": cfg["cand_d"],
            "candidate_bm25": cfg["cand_b"],
            "distinct_page_deduplication": "first occurrence in teammate final chunk rank",
            "context_page_limit": FINAL_K,
            "context_token_limit": CONTEXT_TOKENS,
            "context_source": "restored complete page text",
            "context_adaptation": CONTEXT_ADAPTATION,
            "llm_calls": 0,
        },
        "models": {
            "dense": provenance["manifest"]["dense"],
            "reranker": provenance["manifest"]["reranker"],
            "device": provenance["manifest"]["device"],
        },
        "index_dir": str(args.index_dir),
    }
    (output / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rr.save()
    log(
        "평가 완료 · "
        f"Recall@8 {summary.get('doc_recall@8', 0):.1%} · "
        f"페이지 Recall@8 {summary.get('page_recall@8', 0):.1%} · "
        f"필수 사실 Recall {summary.get('fact_recall', 0):.1%} · LLM 0회"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--restored-pages", type=Path, default=RESTORED_PAGES)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    output = args.output
    log_path = output / "run.log"
    output.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    if not torch.cuda.is_available():
        raise SystemExit("동료 기준의 로컬 CUDA 실행 환경이 필요합니다.")
    started = perf_counter()
    log("동료 no-LLM workflow 재현 시작 · Advanced · team_graph")
    prepare_index(args.index_dir, log)
    if not args.prepare_only:
        # The output directory must not be re-used for a run with different inputs.
        run_dir = output / "results"
        evaluate(args, run_dir, log)
    log(f"작업 종료 · {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
