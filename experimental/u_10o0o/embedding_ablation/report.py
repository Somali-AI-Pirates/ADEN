"""Publish aggregate metrics and ID-only differences, without private evidence."""

import argparse
import os
from collections import Counter
from pathlib import Path
from statistics import fmean

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)

from .__main__ import DEFAULT_RUN
from .assets import MODELS

NAMES = {
    "snowflake": "Snowflake-ko",
    "kure_v3": "KURE-v3",
    "nemotron": "Nemotron-3-Embed-8B",
    "qwen3": "Qwen3-Embedding-8B",
}
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-embedding-ablation-20261111"


def fraction(value, total):
    return f"{value}/{total} ({value / total:.1%})" if total else "N/A"


def group_of(row, group):
    if group == "official_all":
        return row["suite"].startswith("official_")
    if group == "categories_positive":
        return (
            not row["suite"].startswith("official_")
            and row["suite"] != "negative_control"
        )
    if group == "semantic_all":
        return row["suite"] == "semantic_queries"
    return row["category"] == group


def render(run, output, scores=None):
    scores = scores or run / "scores"
    summary = read_json(scores / "summary.json")
    names = tuple(name for name in MODELS if name in summary)
    differences = read_json(scores / "differences.json")
    per_question = read_json(scores / "per-question.json")
    dense = read_json(scores / "dense-per-question.json")
    frozen = read_json(run / "frozen.json")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "summary.json", summary)
    # No question text, routes, quotes, or page text enter shareable records.
    write_json(
        output / "differences.json",
        {
            name: [
                {key.replace("A_", "replacement_"): value for key, value in row.items()}
                for row in rows
            ]
            for name, rows in differences.items()
        },
    )
    public_rows = {
        name: [
            {
                key: row[key]
                for key in (
                    "id",
                    "suite",
                    "question_id",
                    "category",
                    "scored",
                    "stages",
                    "failure_stage",
                    "context_tokens",
                    "returned_pages",
                )
            }
            for row in rows
        ]
        for name, rows in per_question.items()
    }
    write_json(output / "per-question-metrics.json", public_rows)
    stages = {}
    for group in ("official_all", "categories_positive", "semantic_all"):
        stages[group] = {}
        for name in names:
            item = summary[name][group]
            values = {}
            for k in (20, 50):
                rows = [
                    r
                    for r in dense[name]
                    if r["k"] == k and group_of(r, group) and r["complete"] is not None
                ]
                values[f"dense_top{k}"] = {
                    "complete": sum(r["complete"] for r in rows),
                    "scored": len(rows),
                    "page_recall": fmean(r["metrics"]["page_recall"] for r in rows)
                    if rows
                    else None,
                }
            values.update(
                hybrid_top20=item["stages"]["initial"]["complete"],
                final_top8=item["stages"]["final"]["complete"],
                scored=item["scored"],
            )
            stages[group][name] = values
    write_json(output / "stage-summary.json", stages)
    fingerprints = {
        "frozen_sha256": file_sha(run / "frozen.json"),
        "baseline": frozen["baseline"],
        "questions": frozen["question_sources"],
        "models": {},
        "scoring": read_json(scores / "manifest.json"),
    }
    protocol_link = Path(os.path.relpath(RECORDS / "protocol.md", output)).as_posix()
    interpretation_link = (
        " · [결과 해석과 하락 원인](interpretation.md)"
        if (output / "interpretation.md").exists()
        else ""
    )
    large_models = any(name in names for name in ("nemotron", "qwen3"))
    added_models = "·".join(NAMES[name] for name in names if name in ("nemotron", "qwen3"))
    evaluation_order = (
        f"Snowflake·KURE-v3를 먼저 채점한 뒤, 같은 고정 설정으로 {added_models}를 추가했다. "
        "추가 모델의 검색에는 질문만 전달했고, 결과를 저장한 뒤 같은 참고 근거로 합산했다."
        if large_models
        else "선택한 두 모델의 모든 검색 결과를 저장한 뒤 참고 근거로 채점했다."
    )
    placement_note = (
        "8B 모델은 GPU 메모리 5GiB 배치 계획과 CPU 메모리를 함께 사용했다."
        if large_models
        else "8B 모델의 분산 실행 결과는 이번 두 모델의 정확도 비교에 포함하지 않았다."
    )
    lines = [
        "# Advanced 기준선의 임베딩 모델 교체 결과",
        "",
        f"2026-11-11. 임베딩 모델만 바꾸고 현재 기본 검색 경로를 재사용했다. 기본값 채택과 운영 API 변경은 하지 않았다. [실행 전 조건]({protocol_link}){interpretation_link}",
        "",
        "BM25는 `bigram-id-normalized`와 제목 보너스, 검색기별 Top 20·5:5 RRF(k=60), KURE nano Top 3 + RRF, 기존 HG·원문 조회, 최종 최대 8쪽·o200k_base 8,000토큰으로 고정했다. 검색 단계와 참고 근거 채점은 분리했다.",
        "",
        "Snowflake는 기존 FP32 문서 벡터와 CPU FP32 질문 임베딩을 사용했다. 대체 모델은 공식 BF16 가중치를 양자화 없이 사용하고 FP32에서 정규화했다. 모델별 공식 질문·문서 접두사와 전체 차원을 사용했다. 따라서 임베딩 교체에는 모델별 입력 접두사·차원·기본 정밀도 차이가 포함된다.",
        "",
        evaluation_order,
        "",
        "## 최종 필수 근거 전체 충족",
        "",
        "| 평가 묶음 | " + " | ".join(NAMES[name] for name in names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    groups = [
        ("official_all", "공식"),
        ("categories_positive", "카테고리 유효 문항"),
        ("semantic_all", "일상 표현"),
        ("work_rules_all", "업무 규정"),
    ]
    groups += [(f"S{i:02}", f"유형 {i}") for i in range(1, 8)]
    groups += [
        (g, g) for g in summary["snowflake"] if g.startswith("semantic_queries/")
    ]
    for group, label in groups:
        cells = [
            fraction(
                summary[name][group]["stages"]["final"]["complete"],
                summary[name][group]["scored"],
            )
            for name in names
        ]
        lines.append("| " + " | ".join([label, *cells]) + " |")
    lines += [
        "",
        "공식 60문항 중 정답 근거가 있는 58개와 카테고리 220문항 중 유효 200개를 검색 정답률 분모로 사용했다. 범위 밖 20문항도 실행했으나 정답 검색률에는 넣지 않았다. 카테고리는 허용 대체 근거를 포함하며, 공식은 모든 필수 근거 그룹을 충족했는지 판정했다.",
        "",
        "## 어느 단계에서 달라졌나",
        "",
        "| 묶음 | 모델 | Dense 20 | Dense 50 | 하이브리드 후보 20 | 최종 8 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for group, label in groups[:3]:
        for name in names:
            value = stages[group][name]
            cells = [
                fraction(value[f"dense_top{k}"]["complete"], value["scored"])
                for k in (20, 50)
            ]
            cells += [
                fraction(value[key], value["scored"])
                for key in ("hybrid_top20", "final_top8")
            ]
            lines.append("| " + " | ".join([label, NAMES[name], *cells]) + " |")
    lines += [
        "",
        "Dense 50은 진단용으로만 계산했다. 실제 RRF 입력은 각 검색기 20개이며 늘리지 않았다. 원문 조회가 후보 밖의 출처를 보충할 수 있어 최종 충족 건수가 초기 후보 충족 건수보다 많을 수 있다.",
        "",
        "## 공식 문항의 정확한 페이지 지표",
        "",
        "| 모델 | Recall@8 | MRR@8 | nDCG@8 |",
        "|---|---:|---:|---:|",
    ]
    for name in names:
        m = summary[name]["official_all"]["stages"]["final"]
        lines.append(
            "| "
            + " | ".join(
                [NAMES[name], *[f"{m[k]:.4f}" for k in ("page_recall", "mrr", "ndcg")]]
            )
            + " |"
        )
    lines += [
        "",
        "`book_id + page`를 함께 비교한 문항별 평균이다. 필수 근거 충족과 페이지 Recall은 서로 다른 지표다.",
        "",
        "## Snowflake 대비 개선·하락",
        "",
        "| 모델 | 묶음 | 개선 | 하락 | 동일 |",
        "|---|---|---:|---:|---:|",
    ]
    for name, rows in differences.items():
        for group, label in groups[:3]:
            counts = Counter(
                r["status"] for r in rows if group_of(r, group) and r["scored"]
            )
            lines.append(
                f"| {NAMES[name]} | {label} | {counts['improved']} | {counts['regressed']} | {counts['unchanged']} |"
            )
    lines += [
        "",
        "문항별 결과는 [개선·하락 목록](differences.json)과 [단계별 지표](per-question-metrics.json)에 남겼다. 질문·원문·정답 인용은 로컬 artifacts에만 있다.",
        "",
        "## 실행 비용과 제한",
        "",
        "| 모델 | 저장한 문서 배치 계산 시간 | 질문 280개 임베딩 시간 | 벡터 차원 | 문서 벡터 크기 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in names:
        assets = read_json(run / name / "vectors-manifest.json")
        manifest = read_json(run / name / "retrieval/manifest.json")
        fingerprints["models"][name] = {"vectors": assets, "retrieval": manifest}
        seconds = (
            "기존 벡터 재사용"
            if assets["document_vectors_reused"]
            else f"{assets['document_seconds'] / 60:.1f}분"
        )
        size = (ROOT / assets["document_path"]).stat().st_size / (1024**2)
        lines.append(
            f"| {NAMES[name]} | {seconds} | {assets['question_seconds']:.1f}초 | {assets['model']['dimensions']} | {size:.1f} MiB |"
        )
    lines += [
        "",
        "장치 배치와 배치 크기가 다른 품질 평가 실행이다. 위 시간을 동일 하드웨어의 온라인 요청 p50/p95로 해석하지 않는다. 문서 시간은 저장에 성공한 배치의 계산 합계이며, 다운로드·로딩·저장 I/O·중단된 배치와 대기 시간은 제외했다. "
        + placement_note
        + " 답변 생성·외부 모델 API 호출·파인튜닝은 하지 않았다.",
        "",
        "기존 개발·회귀 세트를 사용했으므로 미사용 문항에 대한 일반화 성능을 입증하는 결과는 아니다. 이번 변경은 임베딩 모델에 한정하며, 대상·요청 해석이나 최종 문맥 배치 문제를 별도로 고치지 않았다.",
        "",
        "## 재현",
        "",
        "```bash",
        "RUN=artifacts/u_10o0o/embedding-comparison-NEW",
        '.venv/bin/python -m experimental.u_10o0o.embedding_ablation --run "$RUN" freeze',
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.assets "
        + " ".join(names),
        "# 이번 비교: "
        + ", ".join(names)
        + "; Snowflake batch 8, 나머지 모델 batch 16",
        '.venv/bin/python -m experimental.u_10o0o.embedding_ablation --run "$RUN" vectors MODEL --batch-size BATCH',
        '.venv/bin/python -m experimental.u_10o0o.embedding_ablation --run "$RUN" retrieve MODEL',
        '.venv/bin/python -m experimental.u_10o0o.embedding_ablation.score_selected --run "$RUN" --models '
        + " ".join(names),
        '.venv/bin/python -m experimental.u_10o0o.embedding_ablation.report --run "$RUN" --scores "$RUN/scores-selected" --output experimental/u_10o0o/records/embedding-comparison-NEW',
        "```",
        "",
        "벡터 생성은 같은 지문·배치 크기로 재개할 수 있다. 완료한 검색·채점 경로를 덮어쓰지 않는다. 새 실행은 `--run`으로 새 디렉터리를 지정한다. 정확한 모델·입력·코드 지문과 배치 크기는 [실행 지문](manifest.json)을 참고한다.",
        "",
    ]
    write_json(output / "manifest.json", fingerprints)
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=RECORDS)
    parser.add_argument("--scores", type=Path)
    args = parser.parse_args()
    render(args.run, args.output, args.scores)
