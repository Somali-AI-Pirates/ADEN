"""Reproduce retriever ranks, then audit saved evidence attribution offline."""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
ARCHIVE = ROOT / "artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ab-v1"
RECORD = Path(__file__).resolve().parent
DEFAULT = ROOT / "artifacts/u_10o0o/advanced-retriever-contribution-20261111/run-v1"
CLASSES = ("bm25_only", "vector_only", "both", "neither")
STAGES = ("bm25", "dense", "union", "rrf20", "after_hg", "available", "final")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s]


def write(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def key(row):
    return row.get("book_id") or Path(row["file_name"]).stem, int(row["page"])


def keys(rows):
    return list(dict.fromkeys(key(r) for r in rows))


def four(a, b):
    return (
        "both" if a and b else "bm25_only" if a else "vector_only" if b else "neither"
    )


def assert_archive():
    manifest = read(ARCHIVE / "manifest.json")
    assert manifest["status"] == "complete" and manifest["results"] == 640
    for name in ("traces", "contexts"):
        assert sha(ARCHIVE / f"{name}.jsonl") == manifest[f"{name}_sha256"]
    return manifest


def replay(output):
    from experimental.u_10o0o import advanced_field_roles, embeding
    from experimental.u_10o0o.hybrid import weighted_rrf
    from experimental.u_10o0o.pattern_retrieval.established_retrieve import (
        load_inputs,
        loaded_runtime,
    )
    from experimental.u_10o0o.search import load_index, rank_pages

    archive = assert_archive()
    output.mkdir(parents=True, exist_ok=False)
    questions, metadata, sources, _ = load_inputs()
    questions = [
        q for q in questions if q["id"].startswith(("official_", "semantic_queries:"))
    ]
    assert len(questions) == 100 and len({q["id"] for q in questions}) == 100
    ids = {q["id"] for q in questions}
    metadata = [m for m in metadata if m["id"] in ids]
    sources = [
        s
        for s in sources
        if s["suite"].startswith("official_") or s["suite"] == "semantic_queries"
    ]
    archive_hashes = {r["path"]: r["sha256"] for r in archive["inputs"]}
    tracked_paths = [ROOT / s["path"] for s in sources] + [
        embeding.CACHE_PATH,
        embeding.VECTORS_PATH,
    ]
    for p in tracked_paths:
        assert sha(p) == archive_hashes[str(p.relative_to(ROOT))], str(p)
    stamps = [
        {"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in tracked_paths
    ]
    traces = {
        r["id"]: r
        for r in lines(ARCHIVE / "traces.jsonl")
        if r["experiment"] == "regression" and r["arm"] == "control" and r["id"] in ids
    }
    assert len(traces) == 100
    pages, vectors = load_index()
    advanced_field_roles.TOKENIZER_NAME = "bigram-id"
    index = advanced_field_roles.PatternIndex(pages)
    embeding.CPU_THREADS = 4
    model = embeding.load_model("cpu")
    runtime = loaded_runtime()
    rows = []
    for prefix in ("official_", "semantic_queries:"):
        batch = [q for q in questions if q["id"].startswith(prefix)]
        embeddings = model.encode_query(
            [q["question"] for q in batch],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        for q, vector in zip(batch, embeddings, strict=True):
            qsha = hashlib.sha256(q["question"].encode()).hexdigest()
            assert qsha == traces[q["id"]]["question_sha256"]
            sparse = index.fielded(q["question"], top_k=20)
            dense = rank_pages(vector, vectors, pages, top_k=20)
            rrf = weighted_rrf(sparse, dense, (0.5, 0.5), top_k=20)

            def info(ranked):
                return [
                    {"book_id": key(p)[0], "page": key(p)[1], "score": float(s)}
                    for p, s in ranked
                ]

            row = {
                "id": q["id"],
                "question_sha256": qsha,
                "bm25": info(sparse),
                "dense": info(dense),
                "rrf20": info(rrf),
            }
            row["archived_pool_order_match"] = keys(row["rrf20"]) == keys(
                traces[q["id"]]["pool"]
            )
            rows.append(row)
        print(f"Local CPU ranks saved in memory {len(rows)}/100", flush=True)
    write(output / "ranks.json", rows)
    matched = sum(r["archived_pool_order_match"] for r in rows)
    for stamp in stamps + runtime:
        assert sha(ROOT / stamp["path"]) == stamp["sha256"]
    manifest = {
        "status": "ranks_complete" if matched == 100 else "rank_mismatch",
        "questions": 100,
        "archived_pool_orders_matched": matched,
        "frozen_bm25_tokenizer": "bigram-id",
        "top_k_each_retriever": 20,
        "embedding_model": embeding.MODEL_ID,
        "embedding_revision": embeding.MODEL_REVISION,
        "device": "cpu",
        "threads": 4,
        "dtype": "float32",
        "external_api_calls": 0,
        "new_kure_calls": 0,
        "references_read_before_ranks_saved": False,
        "archive_manifest_sha256": sha(ARCHIVE / "manifest.json"),
        "archive_request_ranks_sha256": sha(ARCHIVE / "request-ranks.jsonl"),
        "ranks_sha256": sha(output / "ranks.json"),
        "inputs": stamps,
        "runtime": runtime,
        "script_sha256": sha(Path(__file__)),
        "items": metadata,
        "reference_sources": sources,
    }
    write(output / "manifest.json", manifest)
    assert matched == 100, f"Archive mismatch: {100 - matched}; scoring blocked."
    print(
        "100/100 archived RRF orders reproduced. References remain unopened.",
        flush=True,
    )


def score(output):
    from app.evaluation.dataset import load_references
    from experimental.u_10o0o.advanced_full_metrics import fact_coverage
    from experimental.u_10o0o.pattern_retrieval.established_score import (
        official_metrics,
    )

    archive = assert_archive()
    manifest = read(output / "manifest.json")
    assert sha(ARCHIVE / "manifest.json") == manifest["archive_manifest_sha256"]
    assert manifest["status"] in ("ranks_complete", "complete")
    assert manifest["archived_pool_orders_matched"] == 100
    assert sha(output / "ranks.json") == manifest["ranks_sha256"]
    assert (
        sha(ARCHIVE / "request-ranks.jsonl") == manifest["archive_request_ranks_sha256"]
    )
    for stamp in manifest["inputs"]:
        assert sha(ROOT / stamp["path"]) == stamp["sha256"]
    ranks = {r["id"]: r for r in read(output / "ranks.json")}
    assert len(ranks) == 100 and all(
        r["archived_pool_order_match"] for r in ranks.values()
    )
    reference_hashes = {
        r["path"]: r["sha256"] for r in archive["reference_fingerprints"]
    }
    for source in manifest["reference_sources"]:
        assert (
            sha(ROOT / source["references"]) == reference_hashes[source["references"]]
        )
    refs = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in manifest["reference_sources"]
    }
    pages_path = next(
        ROOT / s["path"]
        for s in manifest["inputs"]
        if s["path"].endswith("advanced_restored_pages.json")
    )
    pages = {key(p): p for p in read(pages_path)}
    traces = {
        (r["experiment"], r["id"]): r
        for r in lines(ARCHIVE / "traces.jsonl")
        if r["arm"] == "control"
    }
    contexts = {
        (r["experiment"], r["id"]): r
        for r in lines(ARCHIVE / "contexts.jsonl")
        if r["arm"] == "control"
    }
    request_ranks = defaultdict(list)
    for r in lines(ARCHIVE / "request-ranks.jsonl"):
        if r["arm"] == "control":
            request_ranks[r["id"]].append(r)
    assert len(request_ranks) == 40
    per_question, private_evidence = [], []
    expected_ids = set()
    for experiment in ("regression", "semantic_planned"):
        for meta in manifest["items"]:
            qid, suite = meta["id"], meta["suite"]
            if experiment == "semantic_planned" and suite != "semantic_queries":
                continue
            reference = refs[suite][meta["question_id"]]
            trace = traces[experiment, qid]
            if experiment == "regression":
                sparse, dense = keys(ranks[qid]["bm25"]), keys(ranks[qid]["dense"])
                count = 1
            else:
                qs = request_ranks[qid]
                assert len({q["query_index"] for q in qs}) == len(qs)
                sparse = list(dict.fromkeys(p for q in qs for p in keys(q["bm25"])))
                dense = list(dict.fromkeys(p for q in qs for p in keys(q["dense"])))
                count = len(qs)
            materials = {
                "bm25": sparse,
                "dense": dense,
                "union": list(dict.fromkeys([*sparse, *dense])),
                "rrf20": keys(trace["pool"]),
                "after_hg": keys(trace["after_hg_pool"]),
                "available": keys(trace["ranked"]),
                "final": keys(trace["final_pages"]),
            }
            actual_final = contexts[experiment, qid]["pages"]
            assert keys(actual_final) == materials["final"]
            official = suite.startswith("official_")
            expected = (
                set(key(e) for g in reference.get("evidence_groups", []) for e in g)
                if official
                else set(
                    key(e)
                    for req in reference["evidence_requirements"]
                    for e in req["primary"]
                )
            )
            counts = Counter({c: 0 for c in CLASSES})
            survival = {c: Counter({s: 0 for s in STAGES[3:]}) for c in CLASSES}
            for source in sorted(expected):
                origin = four(source in sparse, source in dense)
                counts[origin] += 1
                presence = {
                    s: int(source in sources) for s, sources in materials.items()
                }
                for stage in STAGES[3:]:
                    survival[origin][stage] += presence[stage]
                private_evidence.append(
                    {
                        "experiment": experiment,
                        "id": qid,
                        "book_id": source[0],
                        "page": source[1],
                        "origin": origin,
                        "presence": presence,
                    }
                )
            assert sum(counts.values()) == len(expected)
            complete, unit_hits, unit_counts = {}, {}, {}
            for stage, source_keys in materials.items():
                text = (
                    actual_final
                    if stage == "final"
                    else [pages[p] for p in source_keys]
                )
                if official:
                    result = official_metrics(
                        reference,
                        text,
                        8 if stage == "final" else max(20, len(source_keys)),
                    )
                    complete[stage] = result["all_groups"] if result else None
                    unit_hits[stage] = result["matched_groups"] if result else 0
                    unit_counts[stage] = result["required_groups"] if result else 0
                else:
                    result = fact_coverage(reference, text)
                    complete[stage] = result["all_facts"]
                    unit_hits[stage] = result["required_facts_hit"]
                    unit_counts[stage] = result["required_facts"]
            row = {
                "experiment": experiment,
                "id": qid,
                "category": suite if official else reference["variant"],
                "query_count": count,
                "scored": bool(expected),
                "required_pages": len(expected),
                "page_classes": dict(counts),
                "class_survival": {c: dict(v) for c, v in survival.items()},
                "matched_pages": {
                    s: len(expected.intersection(v)) for s, v in materials.items()
                },
                "macro_page_recall": {
                    s: len(expected.intersection(v)) / len(expected)
                    if expected
                    else None
                    for s, v in materials.items()
                },
                "complete": complete,
                "required_units": unit_counts["final"],
                "matched_units": unit_hits,
                "whole_question_class": four(
                    bool(complete["bm25"]), bool(complete["dense"])
                )
                if expected
                else None,
                "complementary_union_complete": bool(
                    expected
                    and complete["union"]
                    and not complete["bm25"]
                    and not complete["dense"]
                ),
                "hg_route": trace["routes"]["selection"].split("/")[0],
                "hg_added_pages": len(
                    set(materials["after_hg"]) - set(materials["rrf20"])
                ),
                "hg_dropped_pages": len(
                    set(materials["rrf20"]) - set(materials["after_hg"])
                ),
                "hg_added_gold": len(
                    expected.intersection(
                        set(materials["after_hg"]) - set(materials["rrf20"])
                    )
                ),
                "hg_dropped_gold": len(
                    expected.intersection(
                        set(materials["rrf20"]) - set(materials["after_hg"])
                    )
                ),
                "all_lookup_added_gold": len(
                    expected.intersection(
                        set(materials["available"]) - set(materials["rrf20"])
                    )
                ),
                "all_lookup_dropped_gold": len(
                    expected.intersection(
                        set(materials["rrf20"]) - set(materials["available"])
                    )
                ),
            }
            assert (
                len(materials["rrf20"]) <= 20
                and len(materials["available"]) <= 20
                and len(materials["final"]) <= 8
            )
            per_question.append(row)
            expected_ids.add((experiment, qid))
    assert len(per_question) == len(expected_ids) == 140
    # Verify against the frozen published results, including actual final texts.
    saved = read(ARCHIVE / "per-question.json")
    saved_lookup = {
        (exp, r["id"]): r for exp, arms in saved.items() for r in arms["control"]
    }
    for r in per_question:
        old = saved_lookup[r["experiment"], r["id"]]
        assert r["scored"] == old["scored"]
        if r["scored"]:
            for stage, old_stage in [
                ("rrf20", "initial"),
                ("after_hg", "after_hg"),
                ("available", "available"),
                ("final", "final"),
            ]:
                assert (
                    r["complete"][stage]
                    == old["stages"][old_stage][old["complete_metric"]]
                )
                assert (
                    r["matched_pages"][stage]
                    == old["stages"][old_stage]["matched_pages"]
                )
    summaries = {}
    for experiment in ("regression", "semantic_planned"):
        groups = defaultdict(list)
        for row in per_question:
            if row["experiment"] != experiment:
                continue
            groups[row["category"]].append(row)
            groups[
                "official_all" if row["id"].startswith("official_") else "semantic_all"
            ].append(row)
        summaries[experiment] = {
            group: summarize(rows) for group, rows in groups.items()
        }
    write(output / "evidence-private.json", private_evidence)
    write(output / "per-question.json", per_question)
    write(output / "summary.json", summaries)
    write(RECORD / "per-question.json", per_question)
    write(RECORD / "summary.json", summaries)
    manifest.update(
        status="complete",
        scored_comparisons=140,
        references_opened_after_saved_ranks=True,
        reference_fingerprints=[
            {"path": s["references"], "sha256": sha(ROOT / s["references"])}
            for s in manifest["reference_sources"]
        ],
    )
    write(output / "manifest.json", manifest)
    write(RECORD / "manifest.json", manifest)
    write_question_table(per_question)
    manifest["analysis_script_sha256"] = sha(Path(__file__))
    if (output / "replay-script.py").exists():
        assert sha(output / "replay-script.py") == manifest["script_sha256"]
        manifest["replay_script_snapshot"] = str(
            (output / "replay-script.py").relative_to(ROOT)
        )
    write(output / "manifest.json", manifest)
    write(RECORD / "manifest.json", manifest)
    report(summaries, per_question)
    print(
        json.dumps(
            {
                exp: {
                    k: v
                    for k, v in sums.items()
                    if k in ("official_all", "semantic_all")
                }
                for exp, sums in summaries.items()
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def summarize(rows):
    scored = [r for r in rows if r["scored"]]
    count = len(scored)
    return {
        "total": len(rows),
        "scored": count,
        "pages": sum(r["required_pages"] for r in scored),
        "page_classes": {c: sum(r["page_classes"][c] for r in scored) for c in CLASSES},
        "class_survival": {
            c: {s: sum(r["class_survival"][c][s] for r in scored) for s in STAGES[3:]}
            for c in CLASSES
        },
        "matched_pages": {
            s: sum(r["matched_pages"][s] for r in scored) for s in STAGES
        },
        "page_recall_macro": {
            s: sum(r["macro_page_recall"][s] for r in scored) / count for s in STAGES
        },
        "complete": {s: sum(r["complete"][s] for r in scored) for s in STAGES},
        "required_units": sum(r["required_units"] for r in scored),
        "matched_units": {
            s: sum(r["matched_units"][s] for r in scored) for s in STAGES
        },
        "whole_question_classes": {
            c: sum(r["whole_question_class"] == c for r in scored) for c in CLASSES
        },
        "complementary_union_complete": sum(
            r["complementary_union_complete"] for r in scored
        ),
        "hg_routes": dict(Counter(r["hg_route"] for r in rows)),
        **{
            k: sum(r[k] for r in rows)
            for k in [
                "hg_added_pages",
                "hg_dropped_pages",
                "hg_added_gold",
                "hg_dropped_gold",
                "all_lookup_added_gold",
                "all_lookup_dropped_gold",
            ]
        },
        "query_count": sum(r["query_count"] for r in rows),
    }


def write_question_table(rows):
    text = [
        "# 문항별 검색기 기여",
        "",
        "주 분석은 원 질문 100개, 보조 분석은 같은 일상 표현의 요청별 검색 40개다. 표의 네 분류는 정답 페이지 건수다. 단계별 전체 충족은 0/1이며, 공식의 근거 없는 2문항은 —로 표시한다.",
        "",
    ]
    for exp in ("regression", "semantic_planned"):
        text += [
            f"## {exp}",
            "",
            "| ID | 유형 | 페이지 | BM25만 | 벡터만 | 둘 다 | 둘 다 놓침 | BM25 전체 | 벡터 전체 | 합집합 전체 | 후보20 전체 | HG후 전체 | 최종 전체 | HG 경로 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for r in rows:
            if r["experiment"] != exp:
                continue
            complete = [
                r["complete"][s] if r["scored"] else "—"
                for s in ("bm25", "dense", "union", "rrf20", "after_hg", "final")
            ]
            text.append(
                f"| {r['id']} | {r['category']} | {r['required_pages']} | "
                + " | ".join(str(r["page_classes"][c]) for c in CLASSES)
                + " | "
                + " | ".join(map(str, complete))
                + f" | {r['hg_route']} |"
            )
        text += [""]
    (RECORD / "per-question.md").write_text("\n".join(text), encoding="utf-8")


def report(summaries, rows):
    selected = [
        ("원 질문 일상 표현", summaries["regression"]["semantic_all"]),
        ("원 질문 공식", summaries["regression"]["official_all"]),
        ("요청별 일상 표현", summaries["semantic_planned"]["semantic_all"]),
    ]
    text = [
        "# BM25·벡터 검색의 근거 기여와 HG 조회 전후",
        "",
        "[결과 해석과 HG 세부 분석](interpretation.md) · [검사 기록](validation.json)",
        "",
        "직전 실험의 보관된 `bigram-id` + 제목 보너스 BM25·Snowflake-ko 기준선을 분석했다. 최신 코드 정규화 구성과 IS 창 벡터는 포함하지 않는다. 기본 실행·색인·이전 실험은 변경하지 않았다.",
        "",
        "## 정답 페이지의 출처별 분류",
        "",
        "각 질문에 필요한 정답 `book_id+page`를 한 건으로 센다. 동일 페이지가 여러 질문에 필요하면 각각 센다. 일상 표현은 주 근거 페이지, 공식은 근거 그룹에 열거된 페이지가 분모다. 실제 필수 사실·허용 대체 근거 충족은 뒤의 문항 표에서 별도로 채점한다.",
        "",
        "| 검색 경로 | 정답 페이지 건수 | BM25만 | 벡터만 | 둘 다 | 둘 다 놓침 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, r in selected:
        text.append(
            f"| {label} | {r['pages']} | "
            + " | ".join(str(r["page_classes"][c]) for c in CLASSES)
            + " |"
        )
    text += [
        "",
        "원 질문은 같은 질문에서 각 검색기 상위 20쪽이다. 요청별 경로는 원 질문과 실행된 각 요청의 상위 20쪽을 검색기별로 합친다. 검색 호출량이 다르므로 동등한 단일 Top20로 비교하지 않는다.",
        "",
        "## 단계별 전체 근거 충족 문항",
        "",
        "| 경로 | 채점/전체 | BM25 | 벡터 | 두 검색기 합집합 | 병합 후보20 | HG 직후20 | 모든 원문 조회 후20 | 최종8 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, r in selected:
        text.append(
            f"| {label} | {r['scored']}/{r['total']} | "
            + " | ".join(f"{r['complete'][s]}/{r['scored']}" for s in STAGES)
            + " |"
        )
    text += [
        "",
        "공식은 모든 근거 그룹에서 하나 이상의 정확한 페이지를 찾는 조건이다. 일상 표현은 모든 필수 사실의 주 근거 묶음 또는 허용 대체 묶음이 실제 문맥에 있어야 한다. 공식 2문항은 참고 페이지가 없어 검색은 재생했지만 성공률에서 제외한다.",
        "",
        "## HG와 추가 원문 조회의 기여",
        "",
        "| 경로 | 명시 HG 경로 문항 | HG 신규 정답 페이지 | HG 탈락 정답 페이지 | 모든 조회 신규 정답 페이지 | 모든 조회 탈락 정답 페이지 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, r in selected:
        text.append(
            f"| {label} | {r['hg_routes'].get('explicit_hg', 0)} | {r['hg_added_gold']} | {r['hg_dropped_gold']} | {r['all_lookup_added_gold']} | {r['all_lookup_dropped_gold']} |"
        )
    text += [
        "",
        "후보 집합의 새 페이지 보충과 순서 승격은 다르다. 명시 HG 경로가 실행됐어도 관련 페이지가 이미 검색 후보에 있으면 신규 페이지는 0이다. HG를 끄고 최종 선택을 다시 실행하지 않았으므로, 이 표의 0을 순서 배치까지 포함한 HG의 인과적 효과가 0이라는 뜻으로 해석하지 않는다.",
        "",
        "## 검색기가 찾은 정답 페이지의 후속 생존",
        "",
        "| 경로 | 최초 출처 분류 | 찾은 페이지 | 후보20 | HG 직후20 | 모든 조회 후20 | 최종8 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    labels = dict(
        zip(CLASSES, ("BM25만", "벡터만", "둘 다", "둘 다 놓침"), strict=True)
    )
    for label, r in selected:
        for c in CLASSES:
            text.append(
                f"| {label} | {labels[c]} | {r['page_classes'][c]} | "
                + " | ".join(str(r["class_survival"][c][s]) for s in STAGES[3:])
                + " |"
            )
    text += [
        "",
        "둘 다 놓침 행의 최초 수는 미발견 건수다. 이후 조회로 회복했다면 그 단계에 별도로 나타나며, 이를 BM25 또는 벡터가 찾았다고 바꾸지 않는다.",
        "",
        "## 세부 유형",
        "",
        "| 경로/유형 | 정답 페이지 | BM25만 | 벡터만 | 둘 다 | 둘 다 놓침 | BM25 전체 충족 | 벡터 전체 충족 | 최종 전체 충족 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for exp, groups in summaries.items():
        for label, r in groups.items():
            if label in ("semantic_all", "official_all"):
                continue
            text.append(
                f"| {exp}/{label} | {r['pages']} | "
                + " | ".join(str(r["page_classes"][c]) for c in CLASSES)
                + f" | {r['complete']['bm25']}/{r['scored']} | {r['complete']['dense']}/{r['scored']} | {r['complete']['final']}/{r['scored']} |"
            )
    text += [
        "",
        "## 해석 범위와 재현",
        "",
        "이 분석은 저장된 경로에서의 관측 기여다. BM25 또는 벡터를 제거한 뒤 리랭킹·HG·문맥 조립을 모두 다시 실행한 절제 실험은 아니다. 40개 일상 표현은 원 질문과 요청별 경로에 중복되므로 고유 질문은 100개, 경로별 분석은 140개다. 새 외부 API·KURE·GPU 실행은 없으며 CPU에서 원 질문의 누락된 순위만 재생했다.",
        "",
        "문항별 전체 분류와 단계별 수치는 [per-question.json](per-question.json), 설정·입력 지문은 [manifest.json](manifest.json), 과정은 [process.md](process.md)에 있다. 원문·정답 인용·페이지별 ID는 로컬 artifacts에만 보관한다.",
        "",
        "```bash",
        "PYTHONPATH=. .venv/bin/python experimental/u_10o0o/records/advanced-retriever-contribution-20261111/analyze.py replay --run-dir artifacts/u_10o0o/advanced-retriever-contribution-20261111/run-new",
        "PYTHONPATH=. .venv/bin/python experimental/u_10o0o/records/advanced-retriever-contribution-20261111/analyze.py score --run-dir artifacts/u_10o0o/advanced-retriever-contribution-20261111/run-new",
        "```",
        "",
        "재생은 새 경로에서 실행해야 한다. 기존 실행의 재채점은 같은 경로에 `score`만 실행한다. 검색 참고 근거는 새 순위를 저장하고 보관 순서 일치 검사를 통과한 후 읽는다.",
        "",
    ]
    text += [
        "## 문항 전체 충족 기준의 네 분류",
        "",
        "여기서 둘 다 미충족은 두 검색기 모두 질문에 필요한 전체 근거를 혼자 확보하지 못했다는 뜻이다. 부분 근거를 하나도 못 찾았다는 뜻은 아니다. 상호 보완 열은 두 검색기 각각은 미충족이지만 합치면 전체 근거가 갖춰지는 문항 수다.",
        "",
        "| 경로 | 채점 문항 | BM25만 전체 충족 | 벡터만 전체 충족 | 둘 다 전체 충족 | 둘 다 미충족 | 합치면 충족 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, r in selected:
        text.append(
            f"| {label} | {r['scored']} | "
            + " | ".join(str(r["whole_question_classes"][c]) for c in CLASSES)
            + f" | {r['complementary_union_complete']} |"
        )
    text += [
        "",
        "전체 문항을 읽기 쉬운 표로 정리한 [문항별 기록](per-question.md)과 [수치 JSON](per-question.json)을 함께 남겼다.",
        "",
    ]
    (RECORD / "report.md").write_text("\n".join(text), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("replay", "score"))
    parser.add_argument("--run-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    assert [
        four(a, b)
        for a, b in [(True, False), (False, True), (True, True), (False, False)]
    ] == list(CLASSES)
    {"replay": replay, "score": score}[args.action](args.run_dir)
