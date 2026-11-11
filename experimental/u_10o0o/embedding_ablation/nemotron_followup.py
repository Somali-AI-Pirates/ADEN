"""Run the frozen KURE-v2 follow-up conditions with Nemotron embeddings."""

import argparse
import shutil
from collections import Counter
from pathlib import Path
from time import perf_counter

import numpy as np
import torch

from experimental.u_10o0o import advanced_search
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    append_json,
    check_runtime,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
    batch_questions,
    loaded_runtime,
)
from experimental.u_10o0o.pattern_retrieval.established_score import differences
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info

from . import kure_followup as common
from . import kure_followup_report as scoring
from .__main__ import (
    DEFAULT_RUN,
    encode,
    log,
    model_loader,
    validate_assets,
    validate_inputs,
    validate_results,
)
from .assets import MODELS

OUTPUT = ROOT / "artifacts/u_10o0o/advanced-nemotron-followup-20261111"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-nemotron-followup-20261111"
ARMS = common.ARMS


def prepare(run, output):
    frozen = validate_inputs(run)
    validate_results(run, "nemotron", frozen)
    common.validate_saved(run, common.OUTPUT)
    common.prepare(run, output)
    # Preserve the same reviewed sensitivity criteria, without changing gold.
    shutil.copyfile(common.OUTPUT / "source-audit.json", output / "source-audit.json")
    manifest = read_json(output / "manifest.json")
    manifest.update(
        model="nemotron",
        model_id=MODELS["nemotron"][0],
        revision=MODELS["nemotron"][1],
        audit_sha256=file_sha(output / "source-audit.json"),
        comparison_manifest_sha256=file_sha(common.OUTPUT / "manifest.json"),
        nemotron_protocol_sha256=file_sha(RECORDS / "protocol.md"),
    )
    write_json(output / "manifest.json", manifest)
    log("Prepared Nemotron with the same conditions, audit and timing sample")


def check(run, output):
    frozen, manifest = common.check_prepared(run, output)
    if manifest.get("model") != "nemotron":
        raise ValueError("Expected a Nemotron run")
    for path, expected in [
        (common.OUTPUT / "manifest.json", manifest["comparison_manifest_sha256"]),
        (RECORDS / "protocol.md", manifest["nemotron_protocol_sha256"]),
        (output / "source-audit.json", manifest["audit_sha256"]),
    ]:
        if file_sha(path) != expected:
            raise ValueError(f"Frozen comparison changed: {path.name}")
    previous = read_json(common.OUTPUT / "manifest.json")
    for field in (
        "questions_sha256",
        "audit_sha256",
        "timing_ids",
        "timing_repeats",
        "arms",
    ):
        if manifest[field] != previous[field]:
            raise ValueError(f"The two models differ on {field}")
    return frozen, manifest


def document_vectors(run):
    assets = validate_assets(run, "nemotron")
    return np.load(ROOT / assets["document_path"], allow_pickle=False)


def retrieve(run, output):
    frozen, manifest = check(run, output)
    if any((output / a).exists() for a in ARMS):
        raise FileExistsError("Use a new output directory")
    for a in ARMS:
        (output / a).mkdir()
    questions = read_json(output / "questions.json")
    documents = document_vectors(run)
    queries = np.load(run / "nemotron/question-vectors.npy", allow_pickle=False)
    if queries.shape != (len(questions), documents.shape[1]):
        raise ValueError("Query/document vector dimensions differ")
    started = perf_counter()
    # Match the original per-query FP32 matrix-vector operation, not a new dtype.
    scores = np.stack([documents @ vector for vector in queries])
    np.save(output / "dense-scores.npy", scores, allow_pickle=False)
    engine, scope = common.make_engine()
    runtime = loaded_runtime()
    old, _ = validate_results(run, "nemotron", frozen)
    old = {t["id"]: t for t in old}
    _, kure = common.validate_saved(run, common.OUTPUT)
    kure = {a: {t["id"]: t for t in saved[0]} for a, saved in kure.items()}
    positions = {q["id"]: i for i, q in enumerate(questions)}
    replay = []
    matched_sparse_scope = 0
    unique_pairs = 0
    count = 0
    for batch in batch_questions(questions, frozen["items"]):
        pools = {a: [] for a in ARMS}
        details = {a: [] for a in ARMS}
        for q in batch:
            for scoped in (False, True):
                pool, sparse, dense, decision = common.candidates(
                    engine,
                    scope,
                    q["question"],
                    scores[positions[q["id"]]],
                    scoped=scoped,
                    limit=40,
                )
                for a in ("scope20", "scope40") if scoped else ("base20", "union40"):
                    pools[a].append(pool[:20] if a.endswith("20") else pool)
                    details[a].append((sparse, dense, decision))
        unique_pairs += sum(
            len({id(p) for a in ARMS for p, _ in pools[a][i]})
            for i in range(len(batch))
        )
        ranked = advanced_search.rerank_variants(engine.reranker, batch, pools, log)
        for a in ARMS:
            for i, q in enumerate(batch):
                sparse, dense, decision = details[a][i]
                trace, context = common.pack(
                    engine, q, a, pools[a][i], ranked[a][i], decision
                )
                trace["sparse_top20"] = [_source_info(p, s) for p, s in sparse]
                trace["dense_top20"] = [_source_info(p, s) for p, s in dense]
                counterpart = kure[a][q["id"]]
                if (
                    trace["scope"] != counterpart["scope"]
                    or trace["sparse_top20"] != counterpart["sparse_top20"]
                ):
                    raise ValueError("BM25 or scope differs between embedding models")
                matched_sparse_scope += 1
                if a == "base20":
                    previous = old[q["id"]]
                    for field in ("pool", "ranked", "final_pages"):
                        if [common.key(p) for p in trace[field]] != [
                            common.key(p) for p in previous[field]
                        ]:
                            replay.append({"id": q["id"], "field": field})
                    if trace["context_sha256"] != previous["context_sha256"]:
                        replay.append({"id": q["id"], "field": "context_hash"})
                append_json(output / a / "traces.jsonl", trace)
                append_json(output / a / "contexts.jsonl", context)
        count += len(batch)
        log(f"Saved Nemotron four conditions for {count}/280 questions")
    check_runtime(runtime)
    check(run, output)
    manifest.update(
        status="retrieved",
        runtime_files=runtime,
        wall_seconds=perf_counter() - started,
        page_kind_counts=dict(Counter(scope.kinds)),
        baseline_replay_differences=replay,
        rerank_unique_pairs_computed=unique_pairs,
        sparse_and_scope_equal_to_kure=matched_sparse_scope,
        results={
            a: {f: file_sha(output / a / f"{f}.jsonl") for f in ("traces", "contexts")}
            for a in ARMS
        },
        references_read_during_retrieval=False,
    )
    write_json(output / "manifest.json", manifest)
    log(
        f"Retrieval complete; replay differences {len(replay)}; matched sparse/scope {matched_sparse_scope}"
    )


def timing(run, output):
    _, manifest = check(run, output)
    if (output / "timing.json").exists():
        raise FileExistsError("Never overwrite timing")
    load_started = perf_counter()
    engine, scope = common.make_engine()
    documents = document_vectors(run)
    model = model_loader("nemotron")
    setup_seconds = perf_counter() - load_started
    questions = {q["id"]: q for q in read_json(output / "questions.json")}
    sample = [questions[id] for id in manifest["timing_ids"]]
    samples = []

    def once(q, arm):
        torch.cuda.synchronize()
        start = perf_counter()
        vector = encode(model, [q["question"]], kind="query", batch_size=1)[0]
        torch.cuda.synchronize()
        encoded = perf_counter()
        values = documents @ vector
        searched = perf_counter()
        pool, _, _, decision = common.candidates(
            engine,
            scope,
            q["question"],
            values,
            scoped=arm.startswith("scope"),
            limit=20 if arm.endswith("20") else 40,
        )
        fused = perf_counter()
        scores = engine.reranker.predict(
            [(q["question"], p["text"]) for p, _ in pool],
            batch_size=32,
            show_progress_bar=False,
        )
        ranked = sorted(
            zip([p for p, _ in pool], map(float, scores), strict=True),
            key=lambda x: -x[1],
        )
        torch.cuda.synchronize()
        reranked = perf_counter()
        trace, context = common.pack(engine, q, arm, pool, ranked, decision)
        end = perf_counter()
        return {
            "id": q["id"],
            "arm": arm,
            "encode_ms": (encoded - start) * 1000,
            "dense_ms": (searched - encoded) * 1000,
            "sparse_scope_fusion_ms": (fused - searched) * 1000,
            "rerank_ms": (reranked - fused) * 1000,
            "context_ms": (end - reranked) * 1000,
            "total_ms": (end - start) * 1000,
            "pairs": len(pool),
            "context_sha256": trace["context_sha256"],
            "final_pages": [common.key(p) for p in context["pages"]],
        }

    for q in sample[:3]:
        for a in ARMS:
            once(q, a)
    log("Nemotron timing warmup finished")
    for repeat in range(manifest["timing_repeats"]):
        for i, q in enumerate(sample):
            offset = (repeat + i) % len(ARMS)
            for a in ARMS[offset:] + ARMS[:offset]:
                samples.append({**once(q, a), "repeat": repeat})
            if (i + 1) % 5 == 0:
                log(f"Nemotron timing repeat {repeat + 1}/3: {i + 1}/30 questions")
    fields = (
        "encode_ms",
        "dense_ms",
        "sparse_scope_fusion_ms",
        "rerank_ms",
        "context_ms",
        "total_ms",
    )
    metrics = {
        a: {
            f: {
                "p50": float(
                    np.percentile([r[f] for r in samples if r["arm"] == a], 50)
                ),
                "p95": float(
                    np.percentile([r[f] for r in samples if r["arm"] == a], 95)
                ),
            }
            for f in fields
        }
        for a in ARMS
    }
    check(run, output)
    write_json(
        output / "timing.json",
        {
            "scope": "warm batch-1 encode through context; no query cache; excludes load",
            "setup_seconds": setup_seconds,
            "warmup_per_arm": 3,
            "repeats": 3,
            "samples": samples,
            "metrics": metrics,
            "runtime_files": loaded_runtime(),
            "embedding_placement": "BF16 CUDA/CPU offload, configured 5GiB GPU / 19GiB CPU",
            "dense_search": "CPU float32 exact cosine",
        },
    )
    log("Nemotron full-request timing saved")


def score(run, output):
    check(run, output)
    scoring.score(run, output)


def report(run, output):
    _, manifest = check(run, output)
    _, saved = common.validate_saved(run, output)
    _, kure_saved = common.validate_saved(run, common.OUTPUT)
    results = {
        "KURE-v2": read_json(common.OUTPUT / "scores.json"),
        "Nemotron": read_json(output / "scores.json"),
    }
    timers = {
        "KURE-v2": read_json(common.OUTPUT / "timing.json"),
        "Nemotron": read_json(output / "timing.json"),
    }
    labels = {
        "base20": "기존 20쪽",
        "union40": "합집합 최대40",
        "scope20": "범위 필터+20",
        "scope40": "범위 필터+최대40",
    }
    lines = [
        "# Nemotron과 KURE-v2 동일 조건 비교",
        "",
        "공식 60문항·카테고리 220문항을 실행하고 공식 58개·카테고리 200개를 근거 채점했다. 원래 채점과 동일 검수 패킷의 민감도를 구분한다. 기존에 본 문항의 개발·회귀 결과다. [조건](protocol.md) · [과정](process.md) · [문항별 차이](changes.md) · [결과 JSON](results.json)",
        "",
        "| 모델 | 조건 | 공식 | 카테고리 유효 | 일상 표현 | 업무 규정 | 전체 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for model, data in results.items():
        for a in ARMS:
            groups = data["original"][a]
            values = [
                scoring.rate(groups[g])
                for g in (
                    "official_all",
                    "categories_positive",
                    "semantic_all",
                    "work_rules_all",
                )
            ]
            total = (
                groups["official_all"]["stages"]["final"]["complete"]
                + groups["categories_positive"]["stages"]["final"]["complete"]
            )
            lines.append(
                f"| {model} | {labels[a]} | " + " | ".join(values) + f" | {total}/258 |"
            )
    lines += [
        "",
        "| 후보 전체 근거 충족 | 조건 | 공식 | 일상 표현 |",
        "|---|---|---:|---:|",
    ]
    for model, data in results.items():
        for a in ARMS:
            groups = data["original"][a]
            lines.append(
                f"| {model} | {labels[a]} | {scoring.rate(groups['official_all'], 'initial')} | {scoring.rate(groups['semantic_all'], 'initial')} |"
            )
    lines += [
        "",
        "| 최종 정확한 페이지 Recall@8 | 조건 | 공식 | 일상 표현 |",
        "|---|---|---:|---:|",
    ]
    for model, data in results.items():
        for a in ARMS:
            groups = data["original"][a]
            lines.append(
                f"| {model} | {labels[a]} | {groups['official_all']['stages']['final']['page_recall']:.2%} | {groups['semantic_all']['stages']['final']['page_recall']:.2%} |"
            )
    lines += [
        "",
        "| 일상 표현의 검수 민감도 | 조건 | 대체 출처 인정 | 범위 모호 문항 제외 |",
        "|---|---|---:|---:|",
    ]
    for model, data in results.items():
        for a in ARMS:
            alt = scoring.rate(data["audited_sensitivity"][a]["semantic_all"])
            clear = scoring.rate(data["scope_clear_sensitivity"][a]["semantic_all"])
            lines.append(f"| {model} | {labels[a]} | {alt} | {clear} |")
    lines += [
        "",
        "SEM-40의 대체 출처와 SEM-32의 범위 모호 판정은 앞선 KURE-v2 실험에서 고정한 것을 수정 없이 사용했다. 원래 정답은 변경하지 않았으며 새 모델의 결과에 맞춘 출처 추가는 없다. 독립·사람 검수는 수행하지 않았다.",
        "",
        "| 모델 | 조건 | 전체 p50(ms) | 전체 p95(ms) | 인코딩 p50(ms) | 리랭킹 p50(ms) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for model, timer in timers.items():
        for a in ARMS:
            m = timer["metrics"][a]
            lines.append(
                f"| {model} | {labels[a]} | {m['total_ms']['p50']:.1f} | {m['total_ms']['p95']:.1f} | {m['encode_ms']['p50']:.1f} | {m['rerank_ms']['p50']:.1f} |"
            )
    lines += [
        "",
        "지연은 같은 PC의 같은 30문항·3회 반복·배치 1·조건 순환에서 질문 인코딩부터 최종 문맥 조립까지 측정했다. 모델/색인 준비와 답변 생성은 제외했다. KURE-v2는 앞선 측정 기록이며 두 모델을 동시에 교차 실행한 결과는 아니다. Nemotron은 8GB GPU에서 CPU/GPU 분산, KURE-v2는 CUDA 인코딩/MaxSim이다. 이 장비에서의 배포 구성을 비교하며 동일 GPU 상주 조건의 모델 속도로 해석하지 않는다.",
        "",
        f"기존 Nemotron 재현 차이는 {len(manifest['baseline_replay_differences'])}건이며, KURE-v2와 BM25 순위·범위 판정이 같았던 비교는 {manifest['sparse_and_scope_equal_to_kure']}/1120건이다.",
        "",
    ]
    cross = {
        a: differences(
            {
                "baseline": results["KURE-v2"]["original_rows"][a],
                "A": results["Nemotron"]["original_rows"][a],
            }
        )
        for a in ARMS
    }
    changes = [
        "# 문항별 비교",
        "",
        "모델 간 비교는 같은 조건에서 KURE-v2→Nemotron이다. 모델 내 비교는 Nemotron base20→변경 조건이다.",
        "",
        "| 비교 | 문항 | 변화 | 후보 전→후 | 최종 전→후 | 실패 위치 전→후 |",
        "|---|---|---|---|---|---|",
    ]
    for label, rows in {
        **{f"모델간/{a}": v for a, v in cross.items()},
        **{f"Nemotron내/{a}": v for a, v in results["Nemotron"]["diffs"].items()},
    }.items():
        for x in rows:
            if x["status"] not in ("improved", "regressed"):
                continue
            changes.append(
                f"| {label} | {x['id']} | {x['status']} | {x['baseline_initial']}→{x['A_initial']} | {x['baseline_complete']}→{x['A_complete']} | {x['baseline_failure_stage']}→{x['A_failure_stage']} |"
            )
    work = {}
    batch_diffs = {}
    for model, rows in [("KURE-v2", kure_saved), ("Nemotron", saved)]:
        work[model] = {}
        batch_diffs[model] = {}
        for a, (traces, _) in rows.items():
            work[model][a] = {
                field: sum(t["work"][field] for t in traces)
                for field in traces[0]["work"]
            }
            by_id = {t["id"]: t for t in traces}
            changed = [
                r
                for r in timers[model]["samples"]
                if r["arm"] == a
                and r["context_sha256"] != by_id[r["id"]]["context_sha256"]
            ]
            batch_diffs[model][a] = {
                "runs": len(changed),
                "ids": sorted({r["id"] for r in changed}),
            }
    lines += [
        "## 실행 검증",
        "",
        f"온라인 배치 1과 품질 배치의 문맥 지문 차이: `{batch_diffs}`. 상세 출처와 반복별 기록은 로컬 timing.json에 저장했다.",
        "",
        "## 재현",
        "",
        "```bash",
    ]
    for command in ("prepare", "retrieve", "score", "timing", "report"):
        lines.append(
            f".venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup {command}"
        )
    lines += [
        "```",
        "",
        "다른 출력 경로는 --output으로 지정한다. 기존 경로를 덮어쓰지 않는다. 원문·질문·참고 인용은 artifacts 아래에만 보관한다.",
        "",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (RECORDS / "changes.md").write_text("\n".join(changes) + "\n", encoding="utf-8")
    write_json(
        RECORDS / "results.json",
        {
            "original": {m: d["original"] for m, d in results.items()},
            "audited_sensitivity": {
                m: d["audited_sensitivity"] for m, d in results.items()
            },
            "scope_clear_sensitivity": {
                m: d["scope_clear_sensitivity"] for m, d in results.items()
            },
            "within_nemotron_diffs": results["Nemotron"]["diffs"],
            "cross_model_diffs": cross,
            "timing_metrics": {m: t["metrics"] for m, t in timers.items()},
            "work": work,
            "timing_context_differences": batch_diffs,
            "manifest_sha256": file_sha(output / "manifest.json"),
            "replay_differences": manifest["baseline_replay_differences"],
            "sparse_scope_equal": manifest["sparse_and_scope_equal_to_kure"],
            "local_artifacts": str(output.relative_to(ROOT)),
            "report_code_sha256": file_sha(Path(__file__)),
        },
    )
    log("Saved same-condition cross-model report")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "command", choices=("prepare", "retrieve", "score", "timing", "report")
    )
    args = parser.parse_args()
    torch.set_num_threads(8)
    {
        "prepare": prepare,
        "retrieve": retrieve,
        "score": score,
        "timing": timing,
        "report": report,
    }[args.command](args.run, args.output)


if __name__ == "__main__":
    main()
