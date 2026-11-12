"""The default run must use the selected policy, including its actual output."""

from types import SimpleNamespace as NS

import pytest

from app.generation.context import build_context
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.advanced_is_lookup_ab import context_pages
from experimental.u_10o0o.pattern_retrieval.common import (
    read_json,
    write_json,
    object_sha,
)
from experimental.u_10o0o.table_rag import benchmark
from experimental.u_10o0o.table_rag.agent import ANSWER_INSTRUCTIONS, TOOLS
from experimental.u_10o0o.table_rag.retrieval import Retriever
from experimental.u_10o0o.table_rag.test_table_rag import page, response


def test_default_run_retains_selected_source_beyond_merged_top20(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    out = tmp_path / "run"
    out.mkdir()
    q = {"id": "fixture:1", "question": "Find the second source", "split": "fixture"}
    write_json(source / "questions.json", [q])
    for path in [source / "manifest.json", out / "store.json", out / "vectors.json"]:
        write_json(path, {})
    original = build_context(
        _context_candidates([(page(1), 1)]), 8, 8000, _count_tokens
    )
    trace = {
        "ranked": [{"book_id": "TEST", "page": 1, "score": 1}],
        "context_sha256": original.context_hash,
    }
    monkeypatch.setattr(benchmark, "BASELINE", source)
    monkeypatch.setattr(
        benchmark,
        "validate_saved",
        lambda _: ({}, {q["id"]: trace}, {q["id"]: {"pages": context_pages(original)}}),
    )
    retriever = object.__new__(Retriever)
    retriever.page_map = {("TEST", n): page(n) for n in range(1, 41)}
    retriever.store = NS(schema=lambda _: [])
    retriever.search = lambda question: {
        "query": question,
        "ranked": [
            (page(n), 1 / n)
            for n in (range(1, 21) if question == q["question"] else range(21, 41))
        ],
        "tables": [],
    }
    monkeypatch.setattr(benchmark, "Retriever", lambda *a, **kw: retriever)
    client = NS(close=lambda: None)
    monkeypatch.setattr(
        benchmark, "StructuredLunaInterpreter", lambda: NS(client=client)
    )
    monkeypatch.setattr(
        benchmark, "cached_answer", lambda *a, **kw: {"status": "fixture"}
    )
    responses = [
        response(
            "solve_subquery",
            {"subquery": "second", "interim_summary": "", "table_ids": [], "sql": None},
            "1",
        ),
        response(
            "finish",
            {
                "status": "sufficient",
                "selected_sources": ["TEST:p0040"],
                "interim_summary": "found",
                "conflicts": [],
            },
            "2",
        ),
    ]
    instructions = []

    def api(*args, **kwargs):
        instructions.append(kwargs["instructions"])
        return responses.pop(0), {"status": "complete"}

    monkeypatch.setattr("experimental.u_10o0o.table_rag.agent.api_call", api)
    benchmark.run(out)
    result = read_json(out / "results" / (object_sha(q["id"]) + ".json"))
    assert 40 in {p["page"] for p in result["contexts"]["table_rag"]}
    assert result["selection_audit"]["not_retained"] == []
    assert result["selection_audit"]["policy"] == "preserve"
    assert result["finish"]["status"] == "sufficient"
    protocol = read_json(out / "protocol.json")
    assert protocol["prompt_sha256"] == object_sha(
        [instructions[0], ANSWER_INSTRUCTIONS, TOOLS]
    )
    assert len(set(instructions)) == 1
    assert read_json(out / "run.json")["status"] == "complete"


def test_legacy_inheritance_rejected_before_creating_new_protocol(tmp_path):
    write_json(tmp_path / "reuse-proof.json", {"rows": []})
    with pytest.raises(ValueError, match="Legacy inherited retrievals"):
        benchmark.freeze(tmp_path, "cpu")
    assert not (tmp_path / "protocol.json").exists()
