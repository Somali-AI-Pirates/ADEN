"""Diagnose saved official retrieval misses without changing the search policy."""

import argparse
import os
from collections import Counter
from pathlib import Path

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_search import hybrid_candidates
from experimental.u_10o0o.context_evidence import _normalize
from experimental.u_10o0o.embeding import load_model
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    page_key,
    score_item,
    validate_saved,
)
from experimental.u_10o0o.search import load_index

TOPICS = {
    "AS-DEV02": "신규 예약의 접수 정보·창구·확정·제출 마감",
    "AS-DEV03": "예약 검토 상태와 확정 상태의 구분",
    "AS-DEV04": "서류에 적힌 화물 중량 항목의 차이",
    "AS-DEV06": "처음 제출할 자료와 서류별 중량 구분",
    "AS-DEV08": "초안 승인과 실제 적재 완료의 구분",
    "AS-DEV09": "출항 전 중량 정정 마감과 변경 절차",
    "AS-DEV16": "반출 정정 후 비용 재산정",
    "AS-DEV20": "실제 반납 기준 유료 일수와 합계",
    "AS-DEV22": "무료 기간 연장의 적용 범위",
    "AS-DEV24": "반납 지연료 면제와 보관료 합산",
    "AS-DEV26": "보관료 정액 감액의 적용 단계",
    "AS-ADD01": "항로·화물·장비의 적용 범위",
    "AS-ADD02": "예정 출항 변경 후 확정과 제출 마감",
    "AS-ADD03": "예약 취소 요청과 취소 완료의 구분",
    "AS-ADD05": "출항 일정에 따른 제출 마감과 도착 통지",
    "AS-ADD06": "최초 제출 중량과 포장·화물 서류의 구분",
    "AS-ADD07": "예약 확정과 반입 허가·배차의 구분",
    "AS-ADD09": "봉인 번호 변경과 반입 예약 처리",
    "AS-ADD10": "이메일 정정과 신규 포털 접수의 구분",
    "AS-ADD13": "비용 중 확정할 항목과 보류할 항목",
    "AS-ADD17": "규격별 요율 선택과 보관료 계산",
    "AS-ADD19": "전체 비용과 지급 주체의 구분",
    "AS-ADD21": "반납 지연료 상한과 보관료 합산",
    "AS-ADD23": "기사 유예일과 무료 기간의 계산",
    "AS-ADD25": "연장 승인 적용 시점과 비용 합산",
    "AS-ADD29": "일부 요율 개정의 적용 범위",
}


def diagnose(run_dir, output):
    manifest, traces, contexts = validate_saved(run_dir)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    questions = read_json(Path(run_dir) / "questions.json")
    official_ids = {
        r["id"] for r in manifest["items"] if r["suite"].startswith("official_")
    }
    official = [r for r in questions if r["id"] in official_ids]
    pages, vectors = load_index()
    index = PatternIndex(pages)
    model = load_model()
    fielded = [index.fielded(row["question"], top_k=20) for row in official]
    embeddings = model.encode_query(
        [r["question"] for r in official],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    dense, hybrid = hybrid_candidates(pages, vectors, fielded, embeddings)
    branches = {}
    for question, bm25, vector, combined in zip(
        official, fielded, dense, hybrid, strict=True
    ):

        def locations(ranked):
            return [
                {"book_id": page_key(p)[0], "page": page_key(p)[1]} for p, _ in ranked
            ]

        rid = question["id"]
        if [page_key(p) for p, _ in combined] != [
            page_key(r) for r in traces[rid]["pool"]
        ]:
            raise ValueError("Replayed hybrid candidates differ from the saved run.")
        branches[rid] = {"bm25": locations(bm25), "dense": locations(vector)}
    write_json(output / "official-branches.json", branches)
    del model
    # No reference has been read until all replayed branch lists are saved.
    references = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in manifest["question_sources"]
    }
    page_map = {page_key(p): p for p in pages}
    scores = [
        score_item(
            meta,
            references[meta["suite"]][meta["question_id"]],
            traces[meta["id"]],
            contexts[meta["id"]],
            page_map,
        )
        for meta in manifest["items"]
    ]
    role_by_source = {}
    case_file = next(
        ROOT / r["path"]
        for r in manifest["inputs"]
        if r["path"].endswith("/case_sources.jsonl")
    )
    for row in read_jsonl(case_file):
        book, number = row["source_id"].removeprefix("advanced:").rsplit(":p", 1)
        role_by_source[book, int(number)] = row["record_role"]
    private, public = [], []
    totals = Counter()
    query_map = {r["id"]: r["question"] for r in questions}
    for row in scores:
        if (
            not row["suite"].startswith("official_")
            or not row["scored"]
            or row["stages"]["final"]["all_groups"]
        ):
            continue
        rid = row["id"]
        ref = references[row["suite"]][row["question_id"]]
        trace, context = traces[rid], contexts[rid]
        final = {page_key(p) for p in context["pages"]}
        initial = {page_key(p) for p in trace["pool"]}
        ordered = {page_key(p): i + 1 for i, p in enumerate(trace["ranked"])}
        kure = {page_key(p): i + 1 for i, p in enumerate(trace["reranked_pool"])}
        expected = {page_key(e) for group in ref["evidence_groups"] for e in group}
        bm25 = {page_key(p) for p in branches[rid]["bm25"]}
        dense_keys = {page_key(p) for p in branches[rid]["dense"]}
        detail = []
        for key in sorted(expected - final):
            citations = [
                e
                for group in ref["evidence_groups"]
                for e in group
                if page_key(e) == key
            ]
            valid_quotes = [
                _normalize(e["quote"])
                for e in citations
                if _normalize(e.get("quote", ""))
            ]
            quote_elsewhere = bool(valid_quotes) and all(
                any(quote in _normalize(p["text"]) for p in context["pages"])
                for quote in valid_quotes
            )
            cause = (
                "final_selection"
                if key in ordered
                else "rrf_cutoff"
                if key in bm25 or key in dense_keys
                else "outside_both_branches"
            )
            totals[cause] += 1
            totals["missing_pages"] += 1
            totals["in_kure_top8"] += int(key in kure and kure[key] <= 8)
            totals["same_quotes_elsewhere"] += int(quote_elsewhere)
            detail.append(
                {
                    "book_id": key[0],
                    "page": key[1],
                    "role": role_by_source.get(key),
                    "in_initial": key in initial,
                    "bm25_top20": key in bm25,
                    "dense_top20": key in dense_keys,
                    "selected_position": ordered.get(key),
                    "kure_position": kure.get(key),
                    "cause": cause,
                    "same_quotes_elsewhere": quote_elsewhere,
                    "citations": citations,
                }
            )
        absent = sum(d["selected_position"] is None for d in detail)
        late = len(detail) - absent
        category = "both" if absent and late else "candidate" if absent else "selection"
        totals[f"questions_{category}"] += 1
        totals["failed_questions"] += 1
        missing_roles = sorted({d["role"] for d in detail if d["role"]})
        entry = {
            "question_id": row["question_id"],
            "topic": TOPICS.get(row["question_id"], "未分類"),
            "category": category,
            "missing_pages": len(detail),
            "missing_candidate_pages": absent,
            "late_positions": sorted(
                d["selected_position"] for d in detail if d["selected_position"]
            ),
            "missing_roles": missing_roles,
            "routes": trace["routes"],
            "final_pages": len(context["pages"]),
            "context_tokens": trace["context_tokens"],
            "branch_failures": dict(Counter(d["cause"] for d in detail)),
        }
        public.append(entry)
        private.append({**entry, "question": query_map[rid], "missing": detail})
    write_json(output / "private-missing-evidence.json", private)
    write_json(
        output / "summary.json", {"groups": aggregates(scores), "misses": dict(totals)}
    )
    write_json(output / "missing-questions.json", public)
    write_json(output / "per-question.json", scores)
    write_json(
        output / "manifest.json",
        {
            "run_manifest_sha256": file_sha(Path(run_dir) / "manifest.json"),
            "analysis_code_sha256": file_sha(Path(__file__)),
            "branch_replay_matches_all_60": True,
            "reference_files": [
                {"path": s["references"], "sha256": file_sha(ROOT / s["references"])}
                for s in manifest["question_sources"]
            ],
        },
    )
    print(dict(totals))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    diagnose(args.run_dir, args.output_dir)


if __name__ == "__main__":
    main()
