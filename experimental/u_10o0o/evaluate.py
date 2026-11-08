import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET, PROJECT_ROOT
from experimental.u_10o0o.search import TOP_K, search

SPLITS = {
    "basic": ("development", "additional", "expansion_validation"),
    "advanced": ("development", "additional"),
}[DATASET]
METRICS = ("hit", "recall", "precision")
RECORDS_DIR = Path(__file__).with_name("records")


def score_question(question_id, results, evidence_groups, *, top_k=TOP_K):
    retrieved = [
        {
            "rank": rank,
            "book_id": Path(page["file_name"]).stem,
            "page": page["page"],
            "score": score,
        }
        for rank, (page, score) in enumerate(results[:top_k], start=1)
    ]
    found_pages = {(item["book_id"], item["page"]) for item in retrieved}
    evidence_pages = {
        (item["book_id"], item["page"]) for group in evidence_groups for item in group
    }
    matched_pages = found_pages & evidence_pages
    metrics = None
    if evidence_pages:
        metrics = {
            "hit": int(bool(matched_pages)),
            "recall": len(matched_pages) / len(evidence_pages),
            "precision": len(matched_pages) / top_k,
        }
    return {
        "question_id": question_id,
        "retrieved_pages": retrieved,
        "evidence_pages": sorted(evidence_pages),
        "matched_pages": sorted(matched_pages),
        "metrics": metrics,
    }


def summarize(records):
    scored = [record["metrics"] for record in records if record["metrics"] is not None]
    return {
        "questions": len(records),
        "scored": len(scored),
        "excluded": len(records) - len(scored),
        **{
            metric: mean(row[metric] for row in scored) if scored else None
            for metric in METRICS
        },
    }


def fingerprint(path: Path) -> dict[str, str]:
    return {
        "path": str(path.relative_to(PROJECT_ROOT)),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def report(model_id, summaries, *, similarity="cosine", dataset=DATASET, top_k=TOP_K):
    similarity_label = {
        "cosine": "코사인 유사도",
        "maxsim": "MaxSim",
        "bm25": "BM25 점수",
        "weighted_rrf": "가중 RRF 점수",
        "cross_encoder": "CrossEncoder 관련도 점수",
    }[similarity]
    label = (
        "검색 방식"
        if similarity in ("bm25", "weighted_rrf", "cross_encoder")
        else "모델"
    )
    lines = [
        f"# {dataset.capitalize()} 페이지 검색 평가",
        "",
        f"{label} `{model_id}` · 페이지 단위 · {similarity_label} · 상위 {top_k}쪽",
        "",
        f"| 묶음 | 전체 | 채점 | 제외 | Hit@{top_k} | Recall@{top_k} | Precision@{top_k} |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split, row in summaries.items():
        values = [f"{row[key]:.4f}" if row[key] is not None else "—" for key in METRICS]
        scores = " | ".join(values)
        lines.append(
            f"| {split} | {row['questions']} | {row['scored']} | {row['excluded']} | {scores} |"
        )
    lines.extend(
        [
            "",
            "근거는 `evidence_groups`에서 중복을 제거한 (문서 ID, 쪽 번호) 집합이다.",
            "Hit은 일치 근거가 하나라도 있으면 1이다.",
            "Recall은 일치 수를 근거 쪽 수로 나눈 값이다.",
            f"Precision은 상위 {top_k}쪽 중 근거와 일치한 비율이다.",
            "근거 페이지가 있는 문항에 대해 지표별 단순 평균을 계산했다.",
            "",
            "문항별 검색 순위와 점수, 지표, 입력 파일 해시는 [results.json](results.json)에 있다.",
            "",
        ]
    )
    return "\n".join(lines)


def evaluate(
    model_id, vectors_path, search_question, *, similarity="cosine", top_k=TOP_K
):
    evaluation_dir = DATA_DIR / "evaluation"
    questions_paths = {
        split: evaluation_dir / f"{split}_questions.json" for split in SPLITS
    }
    reference_paths = {split: evaluation_dir / f"{split}.json" for split in SPLITS}
    retrieved = {}
    for split, path in questions_paths.items():
        questions = json.loads(path.read_text(encoding="utf-8"))
        retrieved[split] = [
            (item["id"], search_question(item["question"])) for item in questions
        ]

    records = {}
    for split, path in reference_paths.items():
        references = {
            item["id"]: item["evidence_groups"]
            for item in json.loads(path.read_text(encoding="utf-8"))
        }
        records[split] = [
            score_question(question_id, results, references[question_id], top_k=top_k)
            for question_id, results in retrieved[split]
        ]

    summaries = {
        "overall": summarize([record for rows in records.values() for record in rows]),
        **{split: summarize(rows) for split, rows in records.items()},
    }
    inputs: dict[str, object] = {"pages": fingerprint(CACHE_PATH)}
    if vectors_path is not None:
        inputs["vectors"] = fingerprint(vectors_path)
    inputs["evaluation"] = {
        split: {
            "questions": fingerprint(questions_paths[split]),
            "references": fingerprint(reference_paths[split]),
        }
        for split in SPLITS
    }
    return {
        "model_id": model_id,
        "dataset": DATASET,
        "run_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "retrieval_unit": "page",
        "similarity": similarity,
        "top_k": top_k,
        "evidence_basis": "unique_evidence_pages",
        "aggregation": "macro_average_over_scored_questions",
        "inputs": inputs,
        "summary": summaries,
        "records": records,
    }


def save_results(result, output_dir):
    shareable = {
        **result,
        "records": {
            split: [
                {key: row[key] for key in ("question_id", "retrieved_pages", "metrics")}
                for row in rows
            ]
            for split, rows in result["records"].items()
        },
    }
    (output_dir / "results.json").write_text(
        json.dumps(shareable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = report(
        result["model_id"],
        result["summary"],
        similarity=result["similarity"],
        dataset=result.get("dataset", "basic"),
        top_k=result["top_k"],
    )
    (output_dir / "report.md").write_text(summary, encoding="utf-8")
    return summary


def main() -> None:
    result = evaluate(MODEL_ID, VECTORS_PATH, search)
    result["revision"] = MODEL_REVISION
    run_at = datetime.fromisoformat(result["run_at"])
    run_id = (
        f"{DATASET}-{MODEL_ID.replace('/', '-')}-page-top{TOP_K}"
        f"-{run_at:%Y%m%d-%H%M%S-%f}"
    )
    output_dir = RECORDS_DIR / run_id
    output_dir.mkdir(parents=True)
    summary = save_results(result, output_dir)
    print(summary)
    print(f"저장 위치 {output_dir}")


if __name__ == "__main__":
    main()
