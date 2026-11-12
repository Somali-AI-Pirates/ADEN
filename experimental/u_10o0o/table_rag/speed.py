"""Offline v5 replay: identical models/candidates, lock scope and GPU batch only."""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from shutil import copyfile
from threading import Lock, Event, Thread
from time import perf_counter, sleep
import subprocess

import numpy as np

from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    object_sha,
    file_sha,
    check_runtime,
)
from experimental.u_10o0o.pattern_retrieval.legacy_baseline import _source_info
from experimental.u_10o0o.table_rag.benchmark import saved_results, runtime
from experimental.u_10o0o.table_rag.retrieval import (
    Retriever,
    encode,
    weighted_rrf,
    distinct_page_units,
    merge_pages,
    complete_sources,
    page_key,
    source_id,
    json_payload,
)

V5 = ROOT / "artifacts/u_10o0o/advanced-table-rag-20261111/v5"
RECORDS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-speed-20261111"
ARMS = {"A": ("coarse", 1), "B": ("split", 1), "C": ("split", 4), "D": ("split", 8)}
GROUPS = (
    "official_development",
    "official_additional",
    "scenarios",
    "semantic_queries",
    "work_rules",
)


def gpu_state():
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@contextmanager
def timed_lock(lock, times, name):
    start = perf_counter()
    with lock:
        times[name] = perf_counter() - start
        yield


class ProfiledRetriever(Retriever):
    """Same v5 search operations and ordering, with measured resource boundaries."""

    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.embedding_lock = Lock()
        self.reranking_lock = Lock()
        self.configure("A")

    def configure(self, arm):
        self.arm = arm
        self.lock_scope, self.batch_size = ARMS[arm]

    def search(self, question):
        started = perf_counter()
        times = {}
        outer = (
            timed_lock(self.lock, times, "outer_wait")
            if self.lock_scope == "coarse"
            else nullcontext()
        )
        with outer:
            embedding_guard = (
                timed_lock(self.embedding_lock, times, "embedding_wait")
                if self.lock_scope == "split"
                else nullcontext()
            )
            with embedding_guard:
                tick = perf_counter()
                query = encode(self.embedder, [question], kind="query", batch_size=1)[0]
                times["embedding"] = perf_counter() - tick
            tick = perf_counter()
            dense_scores = self.vectors @ query
            indices = np.argsort(-dense_scores, kind="stable")[:50]
            dense = [(self.units[i], float(dense_scores[i])) for i in indices]
            times["dense"] = perf_counter() - tick
            tick = perf_counter()
            sparse = self.bm25.fielded(question, top_k=50)
            times["bm25"] = perf_counter() - tick
            tick = perf_counter()
            candidates = distinct_page_units(
                weighted_rrf(sparse, dense, (0.5, 0.5), top_k=100), 20
            )
            times["fusion"] = perf_counter() - tick
            reranker_guard = (
                timed_lock(self.reranking_lock, times, "reranker_wait")
                if self.lock_scope == "split"
                else nullcontext()
            )
            with reranker_guard:
                tick = perf_counter()
                input_ids = self.reranker.tokenizer(
                    [question] * len(candidates),
                    [p["text"] for p, _ in candidates],
                    truncation=False,
                    verbose=False,
                )["input_ids"]
                work = {
                    "rerank_pairs": len(candidates),
                    "raw_pair_tokens": sum(map(len, input_ids)),
                    "truncated_pairs": sum(len(t) > 2048 for t in input_ids),
                }
                times["diagnostic_tokenization"] = perf_counter() - tick
                tick = perf_counter()
                scores = self.reranker.predict(
                    [(question, p["text"]) for p, _ in candidates],
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
                times["rerank"] = perf_counter() - tick
        tick = perf_counter()
        reranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: (
                -float(pair[1]),
                pair[0][0]["file_name"],
                pair[0][0]["page"],
                pair[0][0].get("chunk_id", ""),
            ),
        )
        units = [(p, float(score)) for ((p, _), score) in reranked]
        ordered = merge_pages(
            [[(self.page_map[page_key(p)], score) for p, score in units]]
        )
        _, chosen, route = complete_sources(
            question, ordered, self.engine.lookup, self.engine.storage, 8000
        )
        ranked, _, _, source_route, _ = chosen
        table_ids = []
        for unit, _ in units:
            ids = (
                [unit["table_id"]]
                if "table_id" in unit
                else self.store.by_page.get(page_key(unit), [])
            )
            for tid in ids:
                if tid not in table_ids:
                    table_ids.append(tid)
        for page, _ in ranked[:8]:
            for tid in self.store.by_page.get(page_key(page), []):
                if tid not in table_ids:
                    table_ids.append(tid)
        times["hg_and_sources"] = perf_counter() - tick
        return {
            "query": question,
            "work": work,
            "ranked": ranked[:20],
            "tables": table_ids[:6],
            "unit_candidates": [
                {"source_id": source_id(p), "table_id": p.get("table_id")}
                for p, _ in candidates
            ],
            "seconds": perf_counter() - started,
            "hg_route": route,
            "source_route": source_route,
            "stage_seconds": times,
        }


def search_signature(result, *, stored=False):
    ranked = (
        result["ranked"]
        if stored
        else [_source_info(p, s) for p, s in result["ranked"]]
    )
    return {
        "ranked_pages": [page_key(p) for p in ranked],
        "tables": result["tables"],
        "unit_candidates": result["unit_candidates"],
        "hg_route": result["hg_route"],
        "source_route": result["source_route"],
        "work": result["work"],
    }


def semantic_sql(result):
    return {k: v for k, v in result.items() if k != "seconds"}


def replay(retriever, saved):
    """Repeat local operations; freeze LLM choices, never load reference labels."""
    started = perf_counter()
    groups, searches, sql = [], [], []
    sql_index = 0
    sql_equal = True

    def search(index):
        old = saved["searches"][index]
        value = retriever.search(old["query"])
        identity = search_signature(value)
        observed_scores = [score for _, score in value["ranked"]]
        prior_scores = [p["score"] for p in old["ranked"]]
        same = identity == search_signature(old, stored=True)
        searches.append(
            {
                "index": index,
                "query_sha256": object_sha(old["query"]),
                "same_saved_search": same,
                "signature_sha256": object_sha(identity),
                "identity": identity,
                "seconds": value["seconds"],
                "stage_seconds": value.get("stage_seconds", {}),
                "scores": observed_scores,
                "max_score_delta_saved": max(
                    (
                        abs(a - b)
                        for a, b in zip(observed_scores, prior_scores, strict=True)
                    ),
                    default=0,
                )
                if len(observed_scores) == len(prior_scores) and same
                else None,
            }
        )
        groups.append(value["ranked"])

    search(0)
    index = 1
    sql_seconds = 0.0
    for turn in saved["turns"]:
        if turn.get("tool") != "solve_subquery" or turn.get("status") != "executed":
            continue
        search(index)
        index += 1
        args = turn["arguments"]
        if args.get("sql"):
            actual = retriever.store.query(args["sql"], args["table_ids"])
            old_sql = saved["sql_attempts"][sql_index]
            sql_index += 1
            equal = semantic_sql(actual) == semantic_sql(old_sql)
            sql_equal &= equal
            sql_seconds += actual.get("seconds", 0.0)
            # v5 includes seconds in SQL serialization. Freeze that non-semantic
            # field so timing precision cannot change the 8000-token budget.
            if equal and "seconds" in old_sql:
                actual["seconds"] = old_sql["seconds"]
            sql.append(actual)
            if actual["status"] == "ok":
                groups.append(
                    [(retriever.page_map[page_key(p)], 1.0) for p in actual["sources"]]
                )
    if index != len(saved["searches"]) or sql_index != len(saved["sql_attempts"]):
        raise ValueError("Saved tool trace and search/SQL sequence disagree")
    tick = perf_counter()
    ranked, context, kept_sql = retriever.context(
        groups, saved["finish"]["selected_sources"], sql
    )
    packing_seconds = perf_counter() - tick
    tokens = context.token_count + _count_tokens(json_payload(kept_sql))
    final_identity = {
        "ranked": [source_id(p) for p, _ in ranked],
        "context_sha256": context.context_hash,
        "kept_sql": [semantic_sql(s) for s in kept_sql],
        "tokens": tokens,
    }
    return {
        "id": saved["id"],
        "arm": retriever.arm,
        "seconds": perf_counter() - started,
        "searches": searches,
        "sql_seconds": sql_seconds,
        "packing_seconds": packing_seconds,
        "sql_equal": sql_equal,
        "same_saved_searches": all(s["same_saved_search"] for s in searches),
        "same_saved_context": context.context_hash
        == saved["traces"]["table_rag"]["context_sha256"],
        "same_saved_sql": [semantic_sql(s) for s in kept_sql]
        == [semantic_sql(s) for s in saved["sql_results"]],
        "context_sha256": context.context_hash,
        "final_signature_sha256": object_sha(final_identity),
        "final_pages": [(c.chunk.book_id, c.chunk.page) for c in context.final_chunks],
        "tokens": tokens,
    }


def select_sample(saved):
    result = []
    for group in GROUPS:
        ids = [rid for rid in saved if rid.split(":", 1)[0] == group]
        result.extend(sorted(ids, key=object_sha)[:3])
    if len(result) != 15:
        raise ValueError("Expected fixed five-group timing sample")
    return result


def blocks(ids, count=20):
    return [ids[i : i + count] for i in range(0, len(ids), count)]


def run_group(retriever, saved, ids, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda rid: replay(retriever, saved[rid]), ids))


def freeze(output, saved):
    output.mkdir(parents=True, exist_ok=False)
    records = runtime()
    for r in records:
        destination = output / "executed-code" / r["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / r["path"], destination)
    config = {
        "v5_run_sha256": file_sha(V5 / "run.json"),
        "arms": ARMS,
        "questions": len(saved),
        "searches": sum(len(r["searches"]) for r in saved.values()),
        "ordered_ids": sorted(saved, key=object_sha),
        "timing_ids": select_sample(saved),
        "quality_workers": 4,
        "timing_workers": [1, 4],
        "timing_repeats": 3,
        "gpu_fraction": 0.12,
        "query_device": "cpu",
        "query_dtype": "bfloat16",
        "reranker_device": "cuda",
        "reranker_dtype": "float32",
        "cpu_threads": 2,
        "api_calls": 0,
        "references_read": False,
        "sql_timing_metadata": "Executed values verified; saved seconds used only in evidence packing",
        "runtime_files": records,
        "gpu_start": gpu_state(),
    }
    write_json(output / "protocol.json", config)
    return config


def inherit_quality(directory, saved):
    """Reuse only completed equivalence checks; never inherit interrupted timing."""
    failure = read_json(directory / "interrupted.json")
    old_protocol = read_json(directory / "protocol.json")
    if failure["failed_arm"] not in ARMS or not failure["timing_not_started"]:
        raise ValueError("Unsupported interrupted replay")
    if old_protocol["v5_run_sha256"] != file_sha(V5 / "run.json"):
        raise ValueError("Saved v5 changed")
    path = Path(__file__).relative_to(ROOT)
    archived = directory / "executed-code" / path

    def definitions(text):
        return {
            n.name: ast.dump(n, include_attributes=False)
            for n in ast.parse(text).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }

    before, after = (
        definitions(archived.read_text()),
        definitions(Path(__file__).read_text()),
    )
    for name in ("ProfiledRetriever", "replay", "search_signature", "semantic_sql"):
        if before[name] != after[name]:
            raise ValueError("Search/replay algorithm changed")
    for item in old_protocol["runtime_files"]:
        if (
            item["path"] != str(path)
            and file_sha(ROOT / item["path"]) != item["sha256"]
        ):
            raise ValueError("Source pipeline changed")
    inherited = {}
    hashes = {}
    for arm in ARMS:
        if arm == failure["failed_arm"]:
            continue
        source = directory / f"quality-{arm}.json"
        rows = read_json(source)
        expected_ids = old_protocol["ordered_ids"][: len(rows)]
        if [r["id"] for r in rows] != expected_ids:
            raise ValueError("Partial checkpoint order changed")
        for r in rows:
            if r["arm"] != arm:
                raise ValueError("Partial checkpoint arm changed")
            expected = saved[r["id"]]["searches"]
            if len(r["searches"]) != len(expected) or any(
                s["query_sha256"] != object_sha(e["query"])
                for s, e in zip(r["searches"], expected, strict=True)
            ):
                raise ValueError("Saved query sequence differs")
        inherited[arm] = rows
        hashes[str(source.relative_to(ROOT))] = file_sha(source)
    return inherited, {
        "directory": str(directory.relative_to(ROOT)),
        "protocol_sha256": file_sha(directory / "protocol.json"),
        "files": hashes,
        "failure": failure,
        "quality_only": True,
        "timings_inherited": False,
    }


def wait_gpu_available():
    # Our model/context fits below this threshold; higher usage indicated another
    # GPU task during v1. This is a shared-host heuristic, not exclusive ownership.
    while float(gpu_state().split(",")[2]) > 2200:
        print("WAIT other GPU memory usage above 2200 MiB", flush=True)
        sleep(10)


def measured_group(retriever, saved, ids, workers):
    wait_gpu_available()
    stop, samples = Event(), []

    def monitor():
        while not stop.is_set():
            samples.append(gpu_state())
            stop.wait(2)

    thread = Thread(target=monitor, daemon=True)
    thread.start()
    started = perf_counter()
    try:
        rows = run_group(retriever, saved, ids, workers)
        elapsed = perf_counter() - started
    finally:
        stop.set()
        thread.join()
    return rows, elapsed, samples


def run(output, resume_from=None):
    import torch

    saved = saved_results(V5)
    base = Retriever(V5, device="cpu_gpu_rerank")
    profiler = ProfiledRetriever(base)
    inherited, reuse = (
        inherit_quality(resume_from, saved) if resume_from else ({}, None)
    )
    protocol = freeze(output, saved)
    protocol["inherited"] = reuse
    protocol["gpu_contention_threshold_mib"] = 2200
    write_json(output / "protocol.json", protocol)
    ids = protocol["ordered_ids"]
    # Instrumentation fidelity: compare live original and A, including tables.
    checks = []
    for rid in ids[:6]:
        query = saved[rid]["searches"][0]["query"]
        original = base.search(query)
        measured = profiler.search(query)
        checks.append(search_signature(original) == search_signature(measured))
    write_json(
        output / "instrumentation-check.json",
        {"passed": all(checks), "count": len(checks)},
    )
    if not all(checks):
        raise ValueError("Profiled A differs from original Retriever")
    # Warm up every batch on fixed high-token searches, independent of labels.
    longest = sorted(
        [s for r in saved.values() for s in r["searches"]],
        key=lambda s: (-s["work"]["raw_pair_tokens"], object_sha(s["query"])),
    )[:3]
    supported, failures = [], {}
    if reuse:
        failures[reuse["failure"]["failed_arm"]] = reuse["failure"]["reason"]
    for arm in ARMS:
        if arm in failures:
            continue
        profiler.configure(arm)
        try:
            for s in longest:
                profiler.search(s["query"])
            supported.append(arm)
        except torch.cuda.OutOfMemoryError:
            failures[arm] = (
                "GPU OOM during fixed high-token preflight at 0.12 memory fraction"
            )
            torch.cuda.empty_cache()
    if "A" not in supported:
        raise RuntimeError("Baseline does not fit the existing GPU memory budget")
    write_json(
        output / "preflight.json",
        {"supported": supported, "failures": failures, "gpu": gpu_state()},
    )
    quality = {a: list(inherited.get(a, [])) for a in supported}
    for arm, rows in quality.items():
        write_json(output / f"quality-{arm}.json", rows)
    gpu_samples = []
    for number, block in enumerate(blocks(ids)):
        start = number % len(supported)
        order = supported[start:] + supported[:start]
        for arm in order:
            completed = {r["id"] for r in quality[arm]}
            pending = [rid for rid in block if rid not in completed]
            if not pending:
                continue
            profiler.configure(arm)
            wait_gpu_available()
            before = gpu_state()
            tick = perf_counter()
            values = run_group(profiler, saved, pending, 4)
            elapsed = perf_counter() - tick
            quality[arm].extend(values)
            write_json(output / f"quality-{arm}.json", quality[arm])
            gpu_samples.append(
                {
                    "block": number,
                    "arm": arm,
                    "before": before,
                    "after": gpu_state(),
                    "wall_seconds": elapsed,
                }
            )
            write_json(output / "block-timing.json", gpu_samples)
            print(
                f"QUALITY {arm} {len(quality[arm])}/280 wall={elapsed:.2f}s", flush=True
            )
    timing = []
    excluded = []
    for workers in (1, 4):
        for repeat in range(3):
            start = (repeat + workers) % len(supported)
            order = supported[start:] + supported[:start]
            for arm in order:
                profiler.configure(arm)
                while True:
                    values, wall, gpu_trace = measured_group(
                        profiler, saved, protocol["timing_ids"], workers
                    )
                    trial = {
                        "arm": arm,
                        "workers": workers,
                        "repeat": repeat,
                        "wall_seconds": wall,
                        "rows": values,
                        "gpu_samples": gpu_trace,
                    }
                    if any(float(state.split(",")[2]) > 2200 for state in gpu_trace):
                        excluded.append(trial)
                        write_json(output / "timing-excluded.json", excluded)
                        print(
                            f"TIMING EXCLUDED {arm}: concurrent GPU usage", flush=True
                        )
                        continue
                    timing.append(trial)
                    break
                write_json(output / "timing.json", timing)
                print(
                    f"TIMING {arm} workers={workers} repeat={repeat + 1} wall={wall:.2f}s",
                    flush=True,
                )
    check_runtime(protocol["runtime_files"])
    write_json(
        output / "run.json",
        {
            "status": "complete",
            "protocol_sha256": file_sha(output / "protocol.json"),
            "files": {p.name: file_sha(p) for p in sorted(output.glob("*.json"))},
            "api_calls": 0,
            "references_read": False,
            "gpu_end": gpu_state(),
        },
    )


def dist(values):
    return {
        "n": len(values),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
    }


def report(output):
    run_record = read_json(output / "run.json")
    if run_record["status"] != "complete":
        raise ValueError("Incomplete run")
    for name, sha in run_record["files"].items():
        if file_sha(output / name) != sha:
            raise ValueError("Replay output changed")
    preflight = read_json(output / "preflight.json")
    quality = {
        a: read_json(output / f"quality-{a}.json") for a in preflight["supported"]
    }
    baseline = {r["id"]: r for r in quality["A"]}
    summary = {}
    mismatches = []
    for arm, rows in quality.items():
        if len(rows) != 280 or sum(len(r["searches"]) for r in rows) != 494:
            raise ValueError("Expected all v5 questions/searches")
        comparisons = []
        for r in rows:
            a = baseline[r["id"]]
            same_searches = all(
                s["signature_sha256"] == old["signature_sha256"]
                for s, old in zip(r["searches"], a["searches"], strict=True)
            )
            same_final = r["final_signature_sha256"] == a["final_signature_sha256"]
            comparisons.append((same_searches, same_final))
            if (
                not same_searches
                or not same_final
                or not r["same_saved_context"]
                or not r["sql_equal"]
            ):
                mismatches.append(
                    {
                        "id": r["id"],
                        "arm": arm,
                        "same_A_searches": same_searches,
                        "same_A_final": same_final,
                        "same_saved_context": r["same_saved_context"],
                        "sql_equal": r["sql_equal"],
                        "changed_search_indices": [
                            s["index"]
                            for s, old in zip(r["searches"], a["searches"], strict=True)
                            if s["signature_sha256"] != old["signature_sha256"]
                        ],
                    }
                )
        searches = [s for r in rows for s in r["searches"]]
        stages = sorted({k for s in searches for k in s["stage_seconds"]})
        summary[arm] = {
            "questions": len(rows),
            "searches": len(searches),
            "same_saved_searches": sum(s["same_saved_search"] for s in searches),
            "same_saved_context": sum(r["same_saved_context"] for r in rows),
            "same_A_search_questions": sum(c[0] for c in comparisons),
            "same_A_final": sum(c[1] for c in comparisons),
            "same_saved_sql": sum(r["same_saved_sql"] and r["sql_equal"] for r in rows),
            "request_seconds": dist([r["seconds"] for r in rows]),
            "search_seconds": dist([s["seconds"] for s in searches]),
            "stages": {
                k: dist([s["stage_seconds"].get(k, 0) for s in searches])
                for k in stages
            },
            "stage_totals": {
                k: sum(s["stage_seconds"].get(k, 0) for s in searches) for k in stages
            },
        }
    timing = read_json(output / "timing.json")
    latency = {}
    for workers in (1, 4):
        latency[str(workers)] = {}
        for arm in quality:
            runs = [r for r in timing if r["arm"] == arm and r["workers"] == workers]
            values = [r for run in runs for r in run["rows"]]
            latency[str(workers)][arm] = {
                "question_seconds": dist([r["seconds"] for r in values]),
                "round_wall_seconds": [r["wall_seconds"] for r in runs],
                "same_A_final": sum(
                    r["final_signature_sha256"]
                    == baseline[r["id"]]["final_signature_sha256"]
                    for r in values
                ),
                "n": len(values),
            }
    RECORDS.mkdir(exist_ok=True)
    write_json(
        RECORDS / "summary.json",
        {
            "quality": summary,
            "latency": latency,
            "unsupported": preflight["failures"],
            "run_sha256": file_sha(output / "run.json"),
            "protocol": read_json(output / "protocol.json"),
            "external_api_calls": 0,
            "reference_labels_read": False,
        },
    )
    write_json(RECORDS / "differences.json", mismatches)
    lines = [
        "# TableRAG v5 로컬 검색 최적화 비교",
        "",
        "기존 280문항·494개 검색과 SQL을 재생했다. 질문 계획·종료 선택은 저장본으로 고정했고 외부 API와 정답 채점은 실행하지 않았다. "
        "원본 모델·검색 후보·HG 조회·문맥 예산을 유지하며 잠금 범위와 GPU 배치만 비교했다.",
        "",
        "| 조건 | 저장 v5와 검색 일치 | 저장 v5와 최종 문맥 일치 | 현재 A와 최종 입력 일치 |",
        "|---|---:|---:|---:|",
    ]
    names = {
        "A": "전체 잠금·배치1",
        "B": "잠금 분리·배치1",
        "C": "잠금 분리·배치4",
        "D": "잠금 분리·배치8",
    }
    for a, v in summary.items():
        lines.append(
            f"| {a}: {names[a]} | {v['same_saved_searches']}/494 | {v['same_saved_context']}/280 | {v['same_A_final']}/280 |"
        )
    for workers, arms in latency.items():
        lines += [
            "",
            f"## 동시 요청 {workers}개: 고정 15문항 × 3회",
            "",
            "| 조건 | 문항별 로컬 시간 p50 | p95 | A와 최종 입력 일치 |",
            "|---|---:|---:|---:|",
        ]
        for a, v in arms.items():
            d = v["question_seconds"]
            lines.append(
                f"| {a}: {names[a]} | {d['p50']:.3f}초 | {d['p95']:.3f}초 | {v['same_A_final']}/{v['n']} |"
            )
    lines += [
        "",
        "## 해석 범위",
        "",
        "이 시간은 API 대기를 제외한 로컬 재생 시간이다. 기존 12.31/33.53초에서 단순히 빼거나 새 서비스 지연이라고 부르지 않는다. "
        "API 없이 4개 요청을 계속 실행하는 부하이며 공유 GPU 상태도 기록했다. 반복 표본은 정답/성능을 보지 않고 세트별 ID 해시로 고정했다.",
        "검색 일치는 후보 쪽 순서·표 후보·조회 경로·입력 길이 정보다. 점수의 미세 수치 차이는 허용하되 순위와 최종 입력은 별도로 검사한다. "
        "SQL은 실제 실행하고 값/출처가 동일한지 확인한 뒤, v5에서 근거 JSON에 포함했던 시간 메타데이터만 저장값으로 고정했다.",
        "수치가 같아도 새 질문 전체에서 정확도가 보장됐다는 뜻은 아니다. 이번에는 기존 평가 입력과 모델 호출을 바꾸지 않는 로컬 실행 최적화를 검사했다.",
        f"미지원 조건: {preflight['failures'] or '없음'}.",
        "독립 에이전트는 부대 대화에서 금지되어 자체 검토했다. 기본 실행·기준선은 변경하지 않았다.",
        "",
        "[실행 과정](work-log.md) · [집계](summary.json) · [문항별 차이](differences.json)",
        "",
    ]
    (RECORDS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Speed report saved", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("run", "report"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--resume-from", type=Path)
    args = p.parse_args()
    if args.command == "run":
        run(
            args.output.resolve(),
            args.resume_from.resolve() if args.resume_from else None,
        )
    else:
        if args.resume_from:
            p.error("--resume-from is only for run")
        report(args.output.resolve())
