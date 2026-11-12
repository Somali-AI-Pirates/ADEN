"""Compare the product graph with saved A(v5) searches and planner decisions."""

import argparse
import asyncio
import json
from collections import Counter, deque
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
from langchain_core.messages import AIMessage

from app.rag.corpus import load_corpus
from app.rag.graph import TableRagGraph
from app.rag.models import SearchResult


def _read(path):
    return json.loads(path.read_bytes())


def _digest(path):
    return sha256(path.read_bytes()).hexdigest()


def _source_id(page):
    return f"{page['book_id']}:p{page['page']:04d}"


def _sql_values(results):
    return [
        {key: value for key, value in result.items() if key != "seconds"}
        for result in results
    ]


class Replay:
    def __init__(self, corpus, live_search=None):
        self.pages = corpus.pages_by_source
        self.live_search = live_search
        self.searches = deque()
        self.turns = deque()
        self.rank_matches = []
        self.graph = TableRagGraph(
            search=self.search,
            table_store=corpus.table_store,
            page_lookup=self.pages.get,
        )

    def search(self, question):
        expected = self.searches.popleft()
        if question != expected["query"]:
            raise ValueError("Replayed search question differs from the saved query")
        sources = [page["source_id"] for page in expected["ranked"]]
        if self.live_search is not None:
            result = self.live_search(question)
            self.rank_matches.append(
                {
                    "ranked_pages": [doc.metadata["source_id"] for doc in result.pages]
                    == sources,
                    "table_ids": list(result.table_ids) == expected["tables"],
                    "rrf_units": [
                        {key: unit.get(key) for key in ("source_id", "table_id")}
                        for unit in result.diagnostics["rrf_candidates"]
                    ]
                    == expected["unit_candidates"],
                    "hg_route": result.diagnostics["hg"]["route"]
                    == expected["hg_route"],
                    "source_route": result.diagnostics["hg"]["source_route"]
                    == expected["source_route"],
                    "diagnostics": result.diagnostics,
                }
            )
            return result
        return SearchResult(
            pages=tuple(self.pages[source] for source in sources),
            table_ids=tuple(expected["tables"]),
            diagnostics={},
        )

    async def plan(self, history, *, finish_only):
        index, turn = self.turns.popleft()
        name = turn.get("tool") or "solve_subquery"
        if finish_only and name != "finish":
            raise ValueError("Saved run violates the forced finish boundary")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": f"replay-{index}",
                    "name": name,
                    "args": turn.get("arguments") or {},
                }
            ],
        )

    async def case(self, question, saved):
        self.searches = deque(saved["searches"])
        self.turns = deque(enumerate(saved["turns"]))
        self.rank_matches = []
        started = perf_counter()
        try:
            result = await self.graph.run(
                question, request_id=saved["id"], planner=self.plan
            )
        except (ValueError, IndexError) as error:
            if self.live_search is None:
                raise
            return {
                "id": saved["id"],
                "termination": type(error).__name__,
                "all_steps_consumed": False,
                "rank_comparisons": self.rank_matches,
            }
        latency_ms = (perf_counter() - started) * 1000
        trace = saved["traces"]["table_rag"]
        evidence = result.evidence
        return {
            "id": saved["id"],
            "termination": result.termination_reason,
            "latency_ms": latency_ms,
            "all_steps_consumed": not self.searches and not self.turns,
            "status": result.status == saved["finish"]["status"],
            "candidate_order": list(evidence.candidate_source_ids)
            == [page["source_id"] for page in trace["ranked"]],
            "final_pages": list(evidence.source_ids)
            == [_source_id(page) for page in saved["contexts"]["table_rag"]],
            "page_context_hash": evidence.page_context_hash == trace["context_sha256"],
            "sql_results": _sql_values(evidence.sql_results)
            == _sql_values(saved["sql_results"]),
            "sql_attempts": _sql_values(result.sql_attempts)
            == _sql_values(saved["sql_attempts"]),
            "rank_comparisons": self.rank_matches,
        }


async def _run(args):
    corpus = load_corpus(args.corpus)
    retriever = None
    model_load_ms = None
    if args.live_search:
        from app.rag.retrieval import load_retriever

        started = perf_counter()
        retriever = load_retriever(corpus, device=args.device)
        model_load_ms = (perf_counter() - started) * 1000
        retriever.search("예약 접수번호를 받으면 확정된 건가요?")
    replay = Replay(corpus, retriever.search if retriever else None)
    reference = _read(args.reference / "run.json")
    question_path = args.reference / "questions.json"
    protocol_path = args.reference / "protocol.json"
    protocol = _read(protocol_path)
    if (
        reference["status"] != "complete"
        or reference["protocol_sha256"] != _digest(protocol_path)
        or protocol["questions_sha256"] != _digest(question_path)
    ):
        raise ValueError("Saved A(v5) run and question fingerprints differ")
    questions = {row["id"]: row["question"] for row in _read(question_path)}
    selected = set(args.ids.split(",")) if args.ids else set(questions)
    if not selected <= questions.keys():
        raise ValueError("Unknown replay question IDs")
    rows = []
    for relative, expected in reference["files"].items():
        path = args.reference / relative
        if _digest(path) != expected:
            raise ValueError(f"Saved reference fingerprint differs: {path.name}")
        saved = _read(path)
        if saved["id"] not in selected:
            continue
        question = questions[saved["id"]]
        question_hash = sha256(
            json.dumps(question, ensure_ascii=False).encode()
        ).hexdigest()
        if question_hash != saved["question_sha256"]:
            raise ValueError(f"Question fingerprint differs: {saved['id']}")
        rows.append(await replay.case(question, saved))
    if len(rows) != len(selected):
        raise ValueError("Replay did not cover the selected question IDs")
    checks = Counter(key for row in rows for key, value in row.items() if value is True)
    search_comparisons = [row for case in rows for row in case["rank_comparisons"]]
    rank_matches = Counter(
        key for row in search_comparisons for key, value in row.items() if value is True
    )
    search_latency_ms = {}
    if search_comparisons:
        stages = [row["diagnostics"]["stages"] for row in search_comparisons]
        for key in stages[0]:
            samples = [stage[key] for stage in stages]
            search_latency_ms[key] = {
                "p50": float(np.percentile(samples, 50)),
                "p95": float(np.percentile(samples, 95)),
            }
    return {
        "reference_run_sha256": _digest(args.reference / "run.json"),
        "reference_protocol_sha256": _digest(args.reference / "protocol.json"),
        "questions_sha256": _digest(question_path),
        "corpus_sha256": _digest(args.corpus / "corpus.json"),
        "search_mode": "local_model" if retriever else "saved_rankings",
        "retrieval": retriever.manifest if retriever else None,
        "model_load_ms": model_load_ms,
        "search_latency_ms": search_latency_ms,
        "search_count": len(search_comparisons),
        "search_matches": dict(rank_matches),
        "questions": len(rows),
        "matches": dict(checks),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", help="Optional comma-separated saved question IDs")
    parser.add_argument(
        "--live-search", action="store_true", help="Recompute local search ranks"
    )
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    result = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {key: result[key] for key in ("questions", "matches")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
