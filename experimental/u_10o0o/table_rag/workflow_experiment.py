"""Frozen paired experiment; labels are read only after all 280 outputs exist."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copyfile

import numpy as np

from experimental.u_10o0o.advanced_is_lookup_ab import BASELINE
from experimental.u_10o0o.advanced_structured_luna import StructuredLunaInterpreter
from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    file_sha,
    object_sha,
    check_runtime,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    score_item,
    aggregates,
    differences,
    page_key,
    official_metrics,
    page_metrics,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.prepare import CACHE_PATH
from experimental.u_10o0o.table_rag.benchmark import (
    saved_results as previous_results,
    runtime,
)
from experimental.u_10o0o.table_rag.retrieval import Retriever, json_payload
from experimental.u_10o0o.table_rag.report import PairJudgment, JUDGE_INSTRUCTIONS
from experimental.u_10o0o.table_rag.workflow import (
    Model,
    execute,
    answer,
    MODEL_ID,
    EFFORT,
)

PREVIOUS = ROOT / "artifacts/u_10o0o/advanced-table-rag-20261111/v5"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-workflow-20261111"
ARMS = ("baseline", "workflow")


def baseline_manifest():
    """Validate immutable saved inputs and their original code, not today's code."""
    manifest = read_json(BASELINE / "manifest.json")
    if (
        manifest["status"] != "complete"
        or manifest["trace_count"] != 280
        or manifest["references_opened"] is not False
    ):
        raise ValueError("Incomplete baseline")
    for name, suffix in (
        ("questions", "json"),
        ("traces", "jsonl"),
        ("contexts", "jsonl"),
    ):
        if file_sha(BASELINE / f"{name}.{suffix}") != manifest[f"{name}_sha256"]:
            raise ValueError("Saved baseline data changed")
    for item in manifest["inputs"] + manifest["question_sources"]:
        if file_sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("Baseline input changed")
    for item in manifest["runtime_files"]:
        current = ROOT / item["path"]
        archive = PREVIOUS / "executed-code" / item["path"]
        if not any(
            p.exists() and file_sha(p) == item["sha256"] for p in (current, archive)
        ):
            raise ValueError("Original baseline code is not available for verification")
    return manifest


def baseline_evidence(pages):
    return {
        "pages": [
            {**p, "source_id": f"{p['book_id']}:p{p['page']:04d}"} for p in pages
        ],
        "requirements": [],
        "intermediate_answers": [],
        "supporting_evidence": {},
        "sql_operations": [],
        "coverage_is_model_judgment": True,
    }


def freeze(directory, device):
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / "executed-code"
    inputs = runtime()
    protocol = {
        "version": 1,
        "model": MODEL_ID,
        "effort": EFFORT,
        "device": device,
        "reranker_batch_size": 4,
        "search_lock_scope": "split" if device == "cpu_gpu_rerank" else "coarse",
        "assets": str(PREVIOUS.relative_to(ROOT)),
        "asset_store_sha256": file_sha(PREVIOUS / "store.json"),
        "asset_vectors_sha256": file_sha(PREVIOUS / "vectors.json"),
        "previous_run_sha256": file_sha(PREVIOUS / "run.json"),
        "baseline_manifest_sha256": file_sha(BASELINE / "manifest.json"),
        "questions_sha256": file_sha(BASELINE / "questions.json"),
        "max_steps": 5,
        "raw_candidates": 20,
        "raw_pages": 8,
        "total_evidence_tokens": 8000,
        "workers": 4,
        "source_references_can_exceed_eight": True,
        "references_opened": False,
        "runtime_files": [
            {
                "path": str((snapshot / item["path"]).relative_to(ROOT)),
                "original_path": item["path"],
                "sha256": item["sha256"],
            }
            for item in inputs
        ],
    }
    path = directory / "protocol.json"
    if path.exists():
        if read_json(path) != protocol:
            raise ValueError("Frozen conditions changed; use a new output directory")
        check_runtime(protocol["runtime_files"])
    else:
        for item, captured in zip(inputs, protocol["runtime_files"], strict=True):
            target = ROOT / captured["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(ROOT / item["path"], target)
        check_runtime(protocol["runtime_files"])
        write_json(path, protocol)
        write_json(directory / "questions.json", read_json(BASELINE / "questions.json"))
    return protocol


def run(directory, device="cpu_gpu_rerank", limit=None):
    directory = Path(directory).resolve()
    baseline_manifest()
    old = previous_results(PREVIOUS)
    retriever = Retriever(PREVIOUS, device=device)
    protocol = freeze(directory, device)
    questions = read_json(directory / "questions.json")
    result_dir = directory / "results"
    result_dir.mkdir(exist_ok=True)
    client = StructuredLunaInterpreter().client
    pending = [
        q
        for q in questions
        if not (result_dir / (object_sha(q["id"]) + ".json")).exists()
    ]
    if limit is not None:
        pending = pending[:limit]

    def one(q):
        name = object_sha(q["id"])
        marker = result_dir / (name + ".started.json")
        if marker.exists():
            raise ValueError("Unfinished paid request; do not automatically resend")
        write_json(
            marker,
            {"id": q["id"], "protocol_sha256": file_sha(directory / "protocol.json")},
        )

        def checkpoint(data):
            write_json(result_dir / (name + ".progress.json"), data)

        model = Model(client, checkpoint)
        result = execute(q["question"], retriever, model)
        baseline = old[q["id"]]
        final = result["final"]
        ranked = [_source_info(p, s) for p, s in final["ranked"]]
        initial = [_source_info(p, s) for p, s in result["initial"]["ranked"]]
        trace = {
            **baseline["traces"]["baseline"],
            "arm": "workflow",
            "pool": initial,
            "after_hg_pool": initial,
            "ranked": ranked,
            "context_tokens": final["tokens"],
            "context_sha256": object_sha(final["payload"]),
            "final_pages": [
                {"book_id": p["book_id"], "page": p["page"]}
                for p in final["payload"]["pages"]
            ],
            "routes": {"workflow": result["finish"]["status"]},
        }
        saved = {
            "id": q["id"],
            "question_sha256": object_sha(q["question"]),
            "trace": trace,
            "evidence": final["payload"],
            "finish": result["finish"],
            "coverage_before_packing": result["coverage_before_packing"],
            "complete": final["complete"],
            "dropped_claims": final["dropped_claims"],
            "provenance_sources": final["provenance_sources"],
            "steps": result["steps"],
            "invalid_supports": result["invalid_supports"],
            "calls": result["calls"],
            "retrieval_seconds": result["seconds"],
            "searches": [
                {**s, "ranked": [_source_info(p, score) for p, score in s["ranked"]]}
                for s in result["searches"]
            ],
            "answers": {},
        }
        write_json(result_dir / (name + ".retrieved.json"), saved)
        evidence = {
            "baseline": baseline_evidence(baseline["contexts"]["baseline"]),
            "workflow": saved["evidence"],
        }
        assert all(_count_tokens(json_payload(e)) <= 8000 for e in evidence.values())
        order = ARMS if int(name[0], 16) % 2 else tuple(reversed(ARMS))
        for arm in order:
            response_model = Model(client, checkpoint)
            saved["answers"][arm] = answer(response_model, q["question"], evidence[arm])
            write_json(result_dir / (name + ".answers-progress.json"), saved["answers"])
        write_json(result_dir / (name + ".json"), saved)
        return len(result["steps"]), len(result["calls"]), result["finish"]["status"]

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (steps, calls, status) in enumerate(pool.map(one, pending), 1):
                print(
                    f"Saved {i}/{len(pending)} steps={steps} calls={calls} finish={status}",
                    flush=True,
                )
    finally:
        client.close()
        complete = [result_dir / (object_sha(q["id"]) + ".json") for q in questions]
        existing = [p for p in complete if p.exists()]
        write_json(
            directory / "run.json",
            {
                "status": "complete" if len(existing) == 280 else "partial",
                "expected": 280,
                "count": len(existing),
                "references_opened": False,
                "protocol_sha256": file_sha(directory / "protocol.json"),
                "files": {str(p.relative_to(directory)): file_sha(p) for p in existing},
            },
        )
    check_runtime(protocol["runtime_files"])


def saved_results(directory):
    directory = Path(directory).resolve()
    run_record = read_json(directory / "run.json")
    if (
        run_record["status"] != "complete"
        or run_record["count"] != 280
        or run_record["references_opened"] is not False
    ):
        raise ValueError("All 280 retrieval/answer outputs are required before labels")
    if file_sha(directory / "protocol.json") != run_record["protocol_sha256"]:
        raise ValueError("Protocol changed")
    check_runtime(read_json(directory / "protocol.json")["runtime_files"])
    for name, sha in run_record["files"].items():
        if file_sha(directory / name) != sha:
            raise ValueError("Output changed")
    return {
        r["id"]: r
        for r in (read_json(directory / name) for name in run_record["files"])
    }


def quoted_pages(evidence):
    """Only retained literal quotations, never whole pages credited from SQL IDs."""
    pages = {page_key(p): dict(p) for p in evidence["pages"]}
    for proof in evidence["supporting_evidence"].values():
        if proof["kind"] != "document":
            continue
        for sid in proof["source_ids"]:
            book, page = sid.rsplit(":p", 1)
            key = book, int(page)
            row = pages.setdefault(
                key, {"book_id": book, "page": int(page), "text": ""}
            )
            if proof["quote"] not in row["text"]:
                row["text"] += "\n" + proof["quote"]
    return list(pages.values())


def score(directory):
    directory = Path(directory).resolve()
    saved = saved_results(directory)  # Must precede loading labels.
    from app.evaluation.dataset import load_references

    manifest = baseline_manifest()
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    old = previous_results(PREVIOUS)
    pages = {page_key(p): p for p in read_json(CACHE_PATH)}
    output = {
        arm: [] for arm in ("baseline", "previous", "workflow", "workflow_quotes")
    }
    flow = []
    for meta in manifest["items"]:
        rid = meta["id"]
        record = saved[rid]
        ref = refs[meta["suite"]][meta["question_id"]]
        for arm, old_arm in (("baseline", "baseline"), ("previous", "table_rag")):
            output[arm].append(
                score_item(
                    meta,
                    ref,
                    old[rid]["traces"][old_arm],
                    {"pages": old[rid]["contexts"][old_arm]},
                    pages,
                )
            )
        for arm, context in (
            ("workflow", record["evidence"]["pages"]),
            ("workflow_quotes", quoted_pages(record["evidence"])),
        ):
            output[arm].append(
                score_item(
                    meta,
                    ref,
                    {**record["trace"], "arm": arm},
                    {"pages": context},
                    pages,
                )
            )
            measured = output[arm][-1]
            if arm == "workflow_quotes" and measured["scored"]:
                if meta["suite"].startswith("official_"):
                    measured["stages"]["final"] = official_metrics(
                        ref, context, max(8, len(context))
                    )
                else:
                    expected = {
                        page_key(e)
                        for req in ref["evidence_requirements"]
                        for e in req["primary"]
                    }
                    measured["stages"]["final"].update(
                        page_metrics(expected, context, max(8, len(context)))
                    )
        measured = output["workflow_quotes"][-1]
        actual = (
            bool(measured["stages"]["final"][measured["complete_metric"]])
            if measured["scored"]
            else None
        )
        flow.append(
            {
                **meta,
                "runtime_complete": record["complete"],
                "offline_retained_quotes_complete": actual,
                "runtime_complete_but_reference_missing": record["complete"]
                and actual is False,
                "finish": record["finish"]["status"],
                "steps": len(record["steps"]),
                "model_calls": len(record["calls"]),
                "search_calls": len(record["searches"]),
                "sql_calls": sum(s["sql"] is not None for s in record["steps"]),
                "sql_status": dict(
                    Counter(
                        s["sql"]["status"]
                        for s in record["steps"]
                        if s["sql"] is not None
                    )
                ),
                "raw_pages": len(record["evidence"]["pages"]),
                "all_provenance_sources": len(
                    set(record["provenance_sources"])
                    | {p["source_id"] for p in record["evidence"]["pages"]}
                ),
                "tokens": record["trace"]["context_tokens"],
                "invalid_supports": len(record["invalid_supports"]),
                "dropped_claims": record["dropped_claims"],
                "coverage": dict(
                    Counter(r["status"] for r in record["evidence"]["requirements"])
                ),
            }
        )
    write_json(directory / "per-question.json", output)
    write_json(
        directory / "summary.json",
        {arm: aggregates(rows) for arm, rows in output.items()},
    )
    write_json(
        directory / "differences.json",
        {
            **{
                arm: differences({"baseline": output["baseline"], "A": output[arm]})
                for arm in ("workflow", "workflow_quotes")
            },
            **{
                "previous_to_" + arm: differences(
                    {"baseline": output["previous"], "A": output[arm]}
                )
                for arm in ("workflow", "workflow_quotes")
            },
        },
    )
    write_json(directory / "flow.json", flow)
    write_json(
        directory / "score-manifest.json",
        {
            "run_sha256": file_sha(directory / "run.json"),
            "references_read_after_all_outputs": True,
            "score_code_sha256": file_sha(Path(__file__)),
            "reference_hashes": {
                r["references"]: file_sha(ROOT / r["references"])
                for r in manifest["question_sources"]
            },
        },
    )
    print("Workflow retrieval scored after all outputs", flush=True)


def grade(directory):
    directory = Path(directory).resolve()
    saved = saved_results(directory)
    from app.evaluation.dataset import load_references

    manifest = baseline_manifest()
    refs = {
        r["suite"]: load_references(ROOT / r["references"])
        for r in manifest["question_sources"]
    }
    old = previous_results(PREVIOUS)
    questions = {
        q["id"]: q["question"] for q in read_json(directory / "questions.json")
    }
    target = directory / "grades"
    target.mkdir(exist_ok=True)
    client = StructuredLunaInterpreter().client

    def one(meta):
        rid = meta["id"]
        name = object_sha(rid)
        path = target / (name + ".json")
        if path.exists():
            return
        marker = target / (name + ".started.json")
        if marker.exists():
            raise ValueError("Unfinished paid judge; refusing duplicate request")
        record = saved[rid]
        evidence = {
            "baseline": baseline_evidence(old[rid]["contexts"]["baseline"]),
            "workflow": record["evidence"],
        }
        order = ARMS if int(name[0], 16) % 2 else tuple(reversed(ARMS))
        payload = {
            "question": questions[rid],
            "reference": refs[meta["suite"]][meta["question_id"]],
        }
        for label, arm in zip(("X", "Y"), order, strict=True):
            payload[label] = {
                "answer": record["answers"][arm]["answer"],
                "evidence": evidence[arm],
            }
        write_json(marker, {"id": rid, "payload_sha256": object_sha(payload)})
        model = Model(client)
        result = model.request(
            "grade",
            payload,
            instructions=JUDGE_INSTRUCTIONS
            + """
追加 규칙: supporting_evidence의 실제 인용/SQL 셀과 원본 출처를 사용해 중간 주장을 대조한다.
원문 8쪽 밖의 검증된 SQL 결과도 증거다. runtime coverage가 supported라고 해서 정답으로 보지 않는다.
대상·요청 누락, 잘못된 전체 모집단 집계, 근거 없는 중간 주장은 실패다.""",
            schema=PairJudgment,
        )
        write_json(
            path,
            {
                "id": rid,
                "usage": model.calls[-1],
                "judgments": {
                    arm: getattr(result, label).model_dump()
                    for label, arm in zip(("X", "Y"), order, strict=True)
                }
                if result
                else None,
            },
        )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, _ in enumerate(pool.map(one, manifest["items"]), 1):
                if i % 20 == 0:
                    print(f"Judged {i}/280", flush=True)
    finally:
        client.close()


def distribution(values):
    return {
        "n": len(values),
        "p50": float(np.percentile(values, 50)) if values else None,
        "p95": float(np.percentile(values, 95)) if values else None,
    }


def report(directory):
    directory = Path(directory).resolve()
    saved = saved_results(directory)
    manifest = baseline_manifest()
    summary = read_json(directory / "summary.json")
    flow = read_json(directory / "flow.json")
    calls = [c for r in saved.values() for c in r["calls"]]
    stages = sorted({c["stage"] for c in calls})
    telemetry = {
        "stages": {
            s: {
                "calls": sum(c["stage"] == s for c in calls),
                "status": dict(Counter(c["status"] for c in calls if c["stage"] == s)),
                "seconds": distribution(
                    [c["seconds"] for c in calls if c["stage"] == s]
                ),
            }
            for s in stages
        },
        "retrieval_seconds": distribution(
            [r["retrieval_seconds"] for r in saved.values()]
        ),
        "retrieval_plus_answer_seconds": distribution(
            [
                r["retrieval_seconds"] + r["answers"]["workflow"]["usage"]["seconds"]
                for r in saved.values()
            ]
        ),
        "calls": len(calls),
        "retrieval_api_calls_per_question": len(calls) / len(saved),
        "api_seconds_per_question": distribution(
            [sum(c["seconds"] for c in r["calls"]) for r in saved.values()]
        ),
        "local_search_seconds_per_question_including_lock_queue": distribution(
            [sum(s["seconds"] for s in r["searches"]) for r in saved.values()]
        ),
        "searches": sum(len(r["searches"]) for r in saved.values()),
        "sql": dict(sum((Counter(r["sql_status"]) for r in flow), Counter())),
        "usage": {
            k: sum(c.get(k, 0) for c in calls)
            for k in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cached_input_tokens",
            )
        },
        "answer_status": {
            a: dict(Counter(r["answers"][a]["usage"]["status"] for r in saved.values()))
            for a in ARMS
        },
        "runtime_complete": sum(r["runtime_complete"] for r in flow),
        "runtime_complete_but_reference_missing": sum(
            r["runtime_complete_but_reference_missing"] for r in flow
        ),
        "more_than_eight_provenance_pages": sum(
            r["all_provenance_sources"] > 8 for r in flow
        ),
        "dropped_claims": sum(r["dropped_claims"] for r in flow),
        "invalid_supports": sum(r["invalid_supports"] for r in flow),
    }
    grades, status = [], Counter()
    for meta in manifest["items"]:
        p = directory / "grades" / (object_sha(meta["id"]) + ".json")
        if p.exists():
            g = read_json(p)
            grades.append({**meta, **g})
            status[g["usage"]["status"]] += 1
    answer_summary = {"status": dict(status), "human_reviewed": False, "groups": {}}
    for label, selected in {
        "official": [r for r in grades if r["suite"].startswith("official_")],
        "categories_positive": [
            r
            for r in grades
            if not r["suite"].startswith("official_")
            and r["suite"] != "negative_control"
        ],
        "negative_control": [r for r in grades if r["suite"] == "negative_control"],
        **{
            f"S{i:02}": [
                r
                for r in grades
                if r["suite"] == "scenarios"
                and r["question_id"].startswith(f"AS-S{i:02}-")
            ]
            for i in range(1, 8)
        },
        "semantic_queries": [r for r in grades if r["suite"] == "semantic_queries"],
        "work_rules": [r for r in grades if r["suite"] == "work_rules"],
    }.items():
        paired = [
            r
            for r in selected
            if r["usage"]["status"] == "complete"
            and r["judgments"]
            and all(r["judgments"][a]["judgeable"] for a in ARMS)
        ]
        answer_summary["groups"][label] = {
            a: {
                "n": len(paired),
                "eligible": len(selected),
                "excluded": len(selected) - len(paired),
                "complete": sum(r["judgments"][a]["complete"] for r in paired),
                "unsupported": sum(r["judgments"][a]["unsupported"] for r in paired),
            }
            for a in ARMS
        }
    RECORDS.mkdir(exist_ok=True)
    exports = {
        "summary.json": summary,
        "differences.json": read_json(directory / "differences.json"),
        "flow.json": flow,
        "telemetry.json": telemetry,
        "answer-summary.json": answer_summary,
        "run.json": {
            "path": str(directory.relative_to(ROOT)),
            "run_sha256": file_sha(directory / "run.json"),
            "protocol": read_json(directory / "protocol.json"),
            "source_score_sha256": file_sha(directory / "score-manifest.json"),
        },
    }
    for name, value in exports.items():
        write_json(RECORDS / name, value)
    labels = [
        ("official_all", "공식"),
        ("official_complex_groups", "공식 복합"),
        ("categories_positive", "카테고리 양성"),
        *[
            (f"S{i:02}", label)
            for i, label in enumerate(
                (
                    "번호로 단일 기록",
                    "번호 없는 단일 사건",
                    "한 사건의 여러 기록",
                    "두 사건 비교",
                    "날짜·승인·금액",
                    "모호한 두 후보",
                    "확인 자료 부족",
                ),
                1,
            )
        ],
        ("semantic_all", "일상 표현"),
        ("work_rules_all", "업무 규정"),
    ]
    lines = [
        "# TableRAG 단계 분리·중간 근거 보존 결과",
        "",
        "하위 질문 검색 뒤 현재 스키마로 SQL을 생성하고, 별도 중간 답변의 인용을 검증해 다음 계획에 도구 결과로 전달했다. "
        "질문별 필요 근거의 상태를 실행 중 관리하고 최종 포장 뒤 다시 확인했다. 기존 기준선과 이전 v5 결과는 보존했다.",
        "",
        "**새 구성을 기준선으로 채택하지 않는다.** 이전 v5보다 원문 근거 충족이 낮고 지연이 늘었다. "
        "기존 기준선 대비 답변 개선은 있었지만 모호한 두 후보의 해석은 해결되지 않았다. [판정과 해석](conclusion.md)",
        "",
        "## 최종 원문 근거 비교",
        "",
        "공식 60문항 중 페이지 근거가 있는 58개, 카테고리 220문항 중 범위 밖 대조 20개를 뺀 200개를 채점했다. "
        "공식은 book_id+page로 허용 근거 그룹 적중을, 카테고리는 필수 사실과 대체 근거를 검사한다. "
        "공식 페이지 적중만으로 답변의 의미적 완전성을 보장하지 않는다.",
        "",
        "원문은 최대 8쪽, 답변용 근거 JSON은 총 8,000토큰이다. 중간 답변의 검증 인용과 SQL 결과는 출처 참조로 추가 보존한다. "
        "전체 출처 수는 8개를 넘을 수 있으므로 원문 8쪽 점수와 추가 인용 반영 점수를 분리한다. "
        "추가 인용 점수에는 SQL 출처 ID만 보고 원문 전체를 확보했다고 가산하지 않는다.",
        "",
        "| 세트 | 기존 기준선 | 이전 v5 | 새 흐름 원문 8쪽 | 새 흐름＋보존 인용 |",
        "|---|---:|---:|---:|---:|",
    ]
    for group, label in labels:
        values = []
        for arm in ("baseline", "previous", "workflow", "workflow_quotes"):
            row = summary[arm][group]
            metric = row["stages"]["final"]
            values.append(
                f"{metric['complete']}/{row['scored']} ({metric['complete_rate']:.1%})"
            )
        lines.append("| " + " | ".join([label, *values]) + " |")
    delta = read_json(directory / "differences.json")
    for arm, label in (("workflow", "원문"), ("workflow_quotes", "보존 인용 포함")):
        counts = Counter(r["status"] for r in delta[arm])
        lines += [
            "",
            f"{label}: 기존 기준선 대비 개선 {counts['improved']}개, 하락 {counts['regressed']}개.",
        ]
        counts = Counter(r["status"] for r in delta["previous_to_" + arm])
        lines.append(
            f"이전 v5 대비 개선 {counts['improved']}개, 하락 {counts['regressed']}개."
        )
    lines += [
        "",
        "## 실행 중 근거 확인",
        "",
        f"실행 중 모두 충족으로 판단한 문항은 {telemetry['runtime_complete']}개다. "
        f"그중 사후 참고 근거가 충족되지 않은 문항은 {telemetry['runtime_complete_but_reference_missing']}개다. "
        "이 차이는 요구 항목의 누락·의미 판단 오류 또는 참고 근거와 표현의 차이를 포함하며, 기계적 출처 확인만으로 완전성을 증명하지 않는다.",
        f"인용/셀 검증에서 제외한 참조 또는 무근거 충족 선언은 {telemetry['invalid_supports']}건이다. "
        f"예산으로 빠진 중간 주장은 {telemetry['dropped_claims']}건이며, 이 경우 최종 상태도 낮춘다.",
        "",
        "## 호출과 시간",
        "",
        "| 단계 | 호출 수 | p50 | p95 |",
        "|---|---:|---:|---:|",
    ]
    for stage, item in telemetry["stages"].items():
        lines.append(
            f"| {stage} | {item['calls']} | {item['seconds']['p50']:.2f}초 | {item['seconds']['p95']:.2f}초 |"
        )
    for key, label in (
        ("api_seconds_per_question", "문항별 검색 API 누적"),
        (
            "local_search_seconds_per_question_including_lock_queue",
            "문항별 로컬 검색 누적",
        ),
        ("retrieval_seconds", "전체 검색"),
        ("retrieval_plus_answer_seconds", "검색＋답변"),
    ):
        d = telemetry[key]
        lines += [
            "",
            f"{label}: p50 {d['p50']:.2f}초, p95 {d['p95']:.2f}초. 공유 장비·4개 동시 요청 대기가 포함된 관측값이다.",
        ]
    lines += [
        "",
        f"검색 {telemetry['searches']}회, SQL 상태 {telemetry['sql']}. 자동 재시도는 없다.",
        f"검색용 API는 문항당 평균 {telemetry['retrieval_api_calls_per_question']:.2f}회이며 답변 생성은 여기에 1회가 추가된다. "
        "별도 대조군 답변과 사후 채점은 서비스 호출량에 포함하지 않는다.",
        f"출처 참조가 8개를 넘은 문항은 {telemetry['more_than_eight_provenance_pages']}개다. "
        "API 사용량, 원문 인용/SQL 셀 대조 및 전체 예산 확인은 [실행 감사](audit.json)에 기록했다.",
        "",
        "## 답변 비교",
        "",
        "두 조건에 같은 새 답변 지침과 Luna medium을 사용했다. "
        "별도 호출이 X/Y 순서를 가려 판정했으며 사람 검수가 아니다. 양쪽 모두 판정 가능한 문항만 같은 분모로 비교한다.",
        "",
        "| 세트 | 기준선 완전 답변 | 새 흐름 완전 답변 | 근거 없는 주장 기준선→새 흐름 |",
        "|---|---:|---:|---:|",
    ]
    answer_labels = {
        **dict(labels),
        "official": "공식",
        "categories_positive": "카테고리 양성",
        "negative_control": "범위 밖 대조",
        "semantic_queries": "일상 표현",
        "work_rules": "업무 규정",
    }
    for group, arms in answer_summary["groups"].items():
        a, b = arms["baseline"], arms["workflow"]
        lines.append(
            f"| {answer_labels.get(group, group)} | {a['complete']}/{a['n']} | {b['complete']}/{b['n']} | {a['unsupported']}→{b['unsupported']} |"
        )
    lines += [
        "",
        "## 해석 범위",
        "",
        "논문의 검색→SQL→중간 답변 순서를 맞추고 ADEN용 필요 근거 확인을 추가했다. "
        "SQLite·하이브리드·HG 조회·IS 추가 표 색인·한국어 모델·원문 예산은 프로젝트의 선택이다. "
        "페이지를 넘는 논리 표 자동 복원이나 모든 기록의 모집단 완전성을 보장하지 않는다. "
        "같은 평가 세트의 개발 비교이며 미사용 검증 세트에 일반화됐다고 주장하지 않는다. "
        "단계 분리와 근거 우선순위·포장을 함께 바꾼 실험이므로 어느 하나의 단독 효과로 해석하지 않는다.",
        "",
        "출처 인용의 실제 존재와 SQL 셀 일치는 코드로 검사하지만, 요청에 맞는 근거인지의 판단은 모델이다. "
        "중간 답변을 보존해도 잘못된 해석까지 옳아지는 것은 아니므로 사후 오확정과 답변 오류를 함께 본다.",
        "",
        "사용자가 허용한 부대 대화에서 자체 검토했다. 독립 에이전트 리뷰·기본 구성 교체·커밋·푸시는 하지 않았다.",
        "",
        "## 기록과 재현",
        "",
        "[과정·범위](work-log.md) · [문항별 변화](differences.json) · [단계별 누락 분석](stage-diagnosis.md) · "
        "[흐름/근거 상태](flow.json) · [호출](telemetry.json) · [논문 대응](paper-alignment.md)",
        "",
        "[실행 명령](../../table_rag/README.md). 원문·질문·SQL·중간 답변은 로컬 artifacts에만 저장한다.",
        "",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Workflow report exported", flush=True)
