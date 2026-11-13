"""Apply the two controlled selectors to exactly the baseline's saved candidates."""

import argparse
import asyncio
import copy
import time
from .common import OUT, read, write, file_sha, activate, check_frozen, key
from .paid import ask
from .selection import PROMPT, Selection, observed_payload, validate_and_select
from .run import tasks


async def run():
    check_frozen()
    activate()
    from openai import AsyncOpenAI
    from app.rag.evidence import ENCODING, assemble_evidence
    from app.rag.models import make_document, SourceRef, RetrievalRun
    from app.rag.tools import FinishRequest
    from app.rag.service import _retrieval_record

    client = AsyncOpenAI(max_retries=0, timeout=90)
    semaphore = asyncio.Semaphore(4)
    count = 0

    async def one(q, n, arm):
        nonlocal count
        k = key(n, arm, q["id"])
        target = OUT / "runs" / (k + ".json")
        if target.exists():
            return
        source = OUT / "runs" / (key(n, "A", q["id"]) + ".json")
        if not source.exists():
            return
        baseline = read(source)
        if baseline["status"] == "execution_error":
            raise RuntimeError("Baseline failed: " + source.stem)
        payload = observed_payload(baseline, focused=arm == "V", encoding=ENCODING)
        async with semaphore:
            tick = time.perf_counter()
            selection, paid = await ask(
                client,
                ("select-literal-v2-" if arm == "V" else "select-") + k,
                PROMPT,
                payload,
                Selection,
                max_tokens=6500,
            )
            preferred, status, audit = validate_and_select(
                selection, payload, baseline["finish"]["selected_sources"]
            )
            docs = tuple(
                make_document(
                    p["text"],
                    SourceRef(
                        book_id=p["book_id"], file_name=p["file_name"], page=p["page"]
                    ),
                    p["chunk_id"],
                )
                for p in baseline["retrieved_candidates"]
            )
            lookup = {p.metadata["source_id"]: p for p in docs}
            evidence = assemble_evidence(
                q["question"],
                [docs],
                preferred,
                baseline.get("included_sql_results", []),
                lookup.get,
            )
            missing = set(preferred) - set(evidence.source_ids)
            if missing:
                status = "insufficient"
            finish = FinishRequest(
                status=status,
                selected_sources=preferred,
                interim_summary=selection.summary[:1500],
                conflicts=[],
            )
            result = RetrievalRun(
                q["question"],
                status,
                "finished",
                finish,
                evidence,
                (),
                tuple(baseline.get("sql_results", [])),
                baseline["diagnostics"],
            )
            row = copy.deepcopy(baseline)
            row.update(_retrieval_record(result, docs, evidence.token_count))
            row["diagnostics"]["searches"] = baseline["diagnostics"]["searches"]
            row.update(
                key=k,
                arm=arm,
                status=status,
                finish=finish.model_dump(),
                selection=selection.model_dump(),
                selection_audit={**audit, "sources_lost_to_budget": sorted(missing)},
                baseline_source_sha256=file_sha(source),
                input_candidate_source_ids=baseline["candidate_source_ids"],
                observed_payload_sha256=paid["fingerprint"],
                new_api_calls=1,
                selector_call={
                    k: v
                    for k, v in paid.items()
                    if k not in {"payload", "prompt", "schema"}
                },
                selector_latency_seconds=paid["seconds"],
                latency_seconds=baseline["latency_seconds"] + paid["seconds"],
                latency_kind="baseline_plus_postprocessing_not_live_workflow",
                selection_compute_seconds=time.perf_counter() - tick - paid["seconds"],
            )
            row.pop("reused_from", None)
            row.pop("reused_sha256", None)
            assert set(row["candidate_source_ids"]) == set(
                baseline["candidate_source_ids"]
            )
            assert len(evidence.pages) <= 8 and evidence.token_count <= 8000
            write(target, row)
            count += 1
            print("Selected", count, k, status, flush=True)

    try:
        await asyncio.gather(*(one(q, n, a) for q, n, a in tasks(("S", "V"))))
    finally:
        await client.close()
    count = sum(
        (OUT / "runs" / (key(n, a, q["id"]) + ".json")).exists()
        for q, n, a in tasks(("S", "V"))
    )
    write(
        OUT / "selector-status.json",
        {"count": count, "expected": 760, "complete": count == 760},
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["run"])
    p.parse_args()
    asyncio.run(run())
