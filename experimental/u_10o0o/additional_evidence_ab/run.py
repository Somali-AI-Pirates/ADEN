"""Prepare, then run frozen A/B retrieval 3 times without answer generation."""

import argparse
import asyncio
import io
import os
import subprocess
import sys
import tarfile
import time

from .common import (
    ROOT,
    PACKAGE,
    OUTPUT,
    RECORDS,
    RUNTIME,
    BUNDLE,
    REVISION,
    read,
    write,
    file_sha,
    digest,
)


def prepare():
    if OUTPUT.exists():
        raise FileExistsError("Experiment output exists; do not overwrite it")
    OUTPUT.mkdir(parents=True)
    archive = subprocess.check_output(
        ["git", "archive", REVISION, "app", "shared", "configs"], cwd=ROOT
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(RUNTIME, filter="data")
    source = ROOT / "data/advanced/evaluation/additional.json"
    references = read(source)
    questions = [{"id": row["id"], "question": row["question"]} for row in references]
    assert len(questions) == 30 and len({x["id"] for x in questions}) == 30
    write(OUTPUT / "questions.json", questions)
    write(OUTPUT / "references.json", references)
    rubric = {}
    for row in references:
        required = list(row["required_facts"])
        omitted = []
        if row["id"] == "AS-ADD03":
            required = [
                "취소 요청을 보냈고 회신이 없는 상태만으로 취소 완료라고 기록할 수 없다."
            ]
            omitted = ["특정 부서명을 별도 필수 요구로 강제하지 않는다."]
        elif row["id"] == "AS-ADD10":
            required = [
                "전환일 이전에 이메일로 접수 완료된 기존 건은 정정일이 전환 후라는 이유만으로 신규 포털에 등록하지 않는다. 신규 접수 경로 변경과 기존 접수 정정을 구분한다."
            ]
            omitted = [
                "별도 정정 이메일 주소나 정정 절차 전체는 질문의 필수 요구가 아니다."
            ]
        if row["id"] == "AS-ADD09":
            required[-1] += (
                "; 적용 기준은 예정 출항일이 아닌 신규 접수일과 장비 종류다."
            )
        rubric[row["id"]] = {
            "question": row["question"],
            "required": [
                {"id": f"R{i + 1}", "fact": fact} for i, fact in enumerate(required)
            ],
            "not_required": omitted,
            "forbidden": row.get("forbidden_claims", []),
            "clarification_only": row["id"] == "AS-ADD12",
            "evidence_groups": row["evidence_groups"],
        }
    write(OUTPUT / "rubric.json", rubric)
    write(
        OUTPUT / "preparation.json",
        {
            "revision": REVISION,
            "references_sha256": file_sha(source),
            "questions_sha256": file_sha(OUTPUT / "questions.json"),
            "rubric_sha256": file_sha(OUTPUT / "rubric.json"),
            "corpus_sha256": file_sha(BUNDLE / "corpus.json"),
            "authorization": "Existing .env key, no cost cap, confirmed 2026-11-13",
            "runtime_files": {
                str(p.relative_to(RUNTIME)): file_sha(p)
                for p in sorted(RUNTIME.rglob("*"))
                if p.is_file()
            },
        },
    )
    print("Prepared frozen runtime, 30 questions and rubric; no API calls", flush=True)


def activate():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing")
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(RUNTIME))
    if any(k == "app" or k.startswith("app.") for k in sys.modules):
        raise RuntimeError("Frozen app must load before any workspace app module")


def freeze():
    preparation = read(OUTPUT / "preparation.json")
    assert preparation["rubric_sha256"] == file_sha(OUTPUT / "rubric.json")
    assert preparation["questions_sha256"] == file_sha(OUTPUT / "questions.json")
    assert preparation["corpus_sha256"] == file_sha(BUNDLE / "corpus.json")
    for name, expected in preparation["runtime_files"].items():
        assert file_sha(RUNTIME / name) == expected, name
    files = {p.name: file_sha(p) for p in sorted(PACKAGE.glob("*.py"))}
    protocol = {
        **preparation,
        "experiment_files": files,
        "repeats": 3,
        "arms": ["A", "B"],
        "workers": 2,
        "device": "cpu_gpu_rerank",
        "answer_generation": False,
        "candidate_pages": 20,
        "final_pages": 8,
        "evidence_tokens": 8000,
        "max_operations": 5,
        "model": "gpt-6-luna",
        "reasoning": "medium",
        "max_planner_calls": 1080,
        "max_review_calls": 180,
        "automatic_retries": 0,
        "search_cache": False,
        "retrieval_does_not_read_rubric": True,
    }
    target = OUTPUT / "protocol.json"
    if target.exists():
        assert read(target) == protocol, "Frozen conditions changed"
    else:
        write(target, protocol)
        write(
            RECORDS / "protocol.json",
            {k: v for k, v in protocol.items() if k != "runtime_files"},
        )
    return protocol


async def run(limit=None):
    protocol = freeze()
    activate()
    from app.rag.corpus import load_corpus
    from app.rag.graph import TableRagGraph
    from app.rag.model import load_model, ModelCalls, TableModel
    from app.rag.records import RecordIndex
    from app.rag.retrieval import load_retriever
    from app.rag.service import _retrieval_record
    from app.rag.evidence import ENCODING, token_count
    from .policy import configure_model, with_previews

    started = time.perf_counter()
    corpus = load_corpus(BUNDLE)
    retriever = load_retriever(corpus, device=protocol["device"])
    index = RecordIndex(BUNDLE)
    model_a = load_model()
    model_b = TableModel(model_a.model)
    configure_model(model_b)
    models = {"A": model_a, "B": model_b}
    graphs = {
        arm: TableRagGraph(
            search=retriever.search,
            page_lookup=corpus.pages_by_source.get,
            table_store=corpus.table_store,
            record_index=index,
            plan_overhead_tokens=model.planner_overhead(token_count),
        )
        for arm, model in models.items()
    }
    write(
        OUTPUT / "runtime-manifest.json",
        {
            "retrieval": retriever.manifest,
            "prompts": {k: m.plan_prompt for k, m in models.items()},
            "model_load_seconds": time.perf_counter() - started,
            "module_path": str(sys.modules["app.rag.graph"].__file__),
        },
    )
    questions = read(OUTPUT / "questions.json")
    # Warm model allocation only; actual question searches below are never cached.
    await asyncio.to_thread(retriever.search, "지원 업무와 문서의 적용 범위")
    tasks = [
        (repeat, arm, q)
        for repeat in range(1, 4)
        for i, q in enumerate(questions)
        for arm in (("A", "B") if (repeat + i) % 2 else ("B", "A"))
    ]
    if limit:
        tasks = tasks[:limit]
    semaphore = asyncio.Semaphore(2)
    stop = asyncio.Event()
    progress = 0

    async def one(repeat, arm, question):
        nonlocal progress
        key = f"r{repeat}-{arm}-{question['id']}"
        path = OUTPUT / "runs" / (key + ".json")
        if path.exists():
            existing = read(path)
            assert existing["protocol_sha256"] == file_sha(OUTPUT / "protocol.json")
            return existing
        marker = path.with_suffix(".started.json")
        if marker.exists():
            raise RuntimeError(
                "Interrupted paid run requires explicit inspection: " + key
            )
        async with semaphore:
            if stop.is_set():
                return None
            write(marker, {"key": key, "question_sha256": digest(question["question"])})
            calls = ModelCalls(None)
            previews = []
            clock = time.perf_counter()

            async def plan(history, *, finish_only):
                if arm == "B":
                    history, audit = with_previews(
                        history, corpus.pages_by_source.get, ENCODING
                    )
                    previews.append(audit)
                return await models[arm].plan(
                    history, finish_only=finish_only, calls=calls
                )

            row = {
                "key": key,
                "repeat": repeat,
                "arm": arm,
                "question_id": question["id"],
                "question": question["question"],
                "protocol_sha256": file_sha(OUTPUT / "protocol.json"),
            }
            try:
                result = await graphs[arm].run(
                    question["question"],
                    planner=plan,
                    request_id=key,
                )
                candidates = tuple(
                    corpus.pages_by_source[s]
                    for s in result.evidence.candidate_source_ids
                )
                row.update(
                    _retrieval_record(
                        result, candidates, token_count(result.evidence.model_input)
                    )
                )
                row["status"] = result.status
                row["termination_reason"] = result.termination_reason
                row["finish"] = result.finish.model_dump()
                row["search_queries"] = [
                    o.get("subquery")
                    for o in result.diagnostics.get("observations", [])
                    if o.get("subquery")
                ]
                assert len(result.evidence.pages) <= 8
                assert len(candidates) <= 20 and result.evidence.token_count <= 8000
            except Exception as exc:
                row.update(status="execution_error", error_class=type(exc).__name__)
            row.update(
                latency_seconds=time.perf_counter() - clock,
                usage=calls.summary(),
                model_calls=calls.calls,
                preview_audit=previews,
            )
            assert len(calls.calls) <= 6
            write(path, row)
            progress += 1
            print(
                f"Saved {progress}/{len(tasks)} {key}: {row['status']} calls={len(calls.calls)}",
                flush=True,
            )
            if row["status"] == "execution_error":
                stop.set()
            for call in calls.calls:
                if call.get("error_class") in {
                    "AuthenticationError",
                    "PermissionDeniedError",
                    "RateLimitError",
                }:
                    stop.set()
            return row

    try:
        rows = await asyncio.gather(*(one(*task) for task in tasks))
    finally:
        await model_a.aclose()
        index.close()
    rows = [row for row in rows if row]
    count = len(list((OUTPUT / "runs").glob("r*-?-AS-ADD??.json")))
    write(
        OUTPUT / "run-status.json",
        {
            "status": "complete" if count == 180 and not stop.is_set() else "partial",
            "saved_count": count,
            "expected_count": 180,
            "references_read_by_retrieval": False,
        },
    )
    print("Retrieval saved:", count, "of 180", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    else:
        asyncio.run(run(args.limit))


if __name__ == "__main__":
    main()
