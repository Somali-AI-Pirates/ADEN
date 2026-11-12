import asyncio
import json
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest

from app.evaluation.security import check_same_context
from app.rag.model import GeneratedAnswer, RagModelError
from app.rag.models import EvidenceContext, RetrievalRun
from app.rag.security import run_comparison
from app.rag.tools import FinishRequest
from tests.test_table_rag_service import _Model, _service


class AnswerModel(_Model):
    def __init__(self, fail_first=False):
        super().__init__()
        self.inputs = []
        self.fail_first = fail_first

    async def answer(self, payload, *, context_hash, calls, defense=""):
        self.inputs.append((payload, defense))
        value = json.loads(payload)
        source = value["evidence"]["pages"][0]["source_id"]
        call = {
            "attempt": len(calls.calls) + 1,
            "role": "answer",
            "model": "offline-model",
            "model_input": {
                "system": "기본 지시" + ("\n\n" + defense if defense else ""),
                "payload": payload,
                "context_hash": context_hash,
            },
            "tool_schema": None,
            "model_parameters": {"reasoning_effort": "medium"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "provider_attempts": 1,
            "status": "complete",
            "latency_ms": 1,
        }
        calls.calls.append(call)
        if self.fail_first and len(self.inputs) == 1:
            call.update(status="error", usage=None)
            raise RagModelError("simulated provider failure")
        return GeneratedAnswer(
            answer="자료로 확인했습니다.", citations=[source], status="answered"
        )


def prepared(service, question, request_id):
    sid = "AS001:p0001" if question == "첫 질문" else "AS001:p0002"
    page = service._page_lookup(sid)
    sql = ({"table_id": "demo", "columns": ["total"], "rows": [[80]]},)
    text = json.dumps(
        {"pages": [{"source_id": sid, "text": page.page_content}], "sql": list(sql)},
        ensure_ascii=False,
    )
    evidence = EvidenceContext(
        pages=(page,),
        sql_results=sql,
        text=text,
        model_input=text,
        token_count=len(text),
        context_hash=sha256(text.encode()).hexdigest(),
        page_context_hash="page-hash",
        source_ids=(sid,),
        candidate_source_ids=(sid,),
        excluded_chunk_ids=(),
    )
    return RetrievalRun(
        question=question,
        status="sufficient",
        termination_reason="finished",
        finish=FinishRequest(
            status="sufficient",
            selected_sources=[sid],
            interim_summary="",
            conflicts=[],
        ),
        evidence=evidence,
        searches=(),
        sql_attempts=sql,
        diagnostics={"request_id": request_id},
    )


def setup(model):
    service = _service(model)
    service._graph.run = AsyncMock(
        side_effect=lambda question, **kw: prepared(service, question, kw["request_id"])
    )
    return service


def test_two_questions_make_four_answers_with_identical_sql_pages_status_and_inputs():
    async def run():
        model = AnswerModel()
        service = setup(model)
        questions = [
            {
                "id": str(i),
                "question": q,
                "attack_text": "must-not-be-added",
                "expected_response": "hidden-answer",
            }
            for i, q in enumerate(["첫 질문", "둘째 질문"])
        ]
        try:
            records = await run_comparison(
                service,
                questions,
                experiment_id="offline-security",
                defense="방어 지시",
            )
            assert service._graph.run.await_count == 2
            assert len(records) == len(model.inputs) == 4
            assert len({r["request_id"] for r in records}) == 4
            for pair in [records[:2], records[2:]]:
                assert [r["prompt_variant"] for r in pair] == ["base", "defense"]
                assert pair[0]["answer_payload"] == pair[1]["answer_payload"]
                assert pair[0]["model_context_hash"] == pair[1]["model_context_hash"]
                assert (
                    pair[0]["final_context_chunks"] == pair[1]["final_context_chunks"]
                )
                payload = json.loads(pair[0]["answer_payload"])
                assert payload["retrieval_status"] == "sufficient"
                assert payload["evidence"]["sql"][0]["rows"] == [[80]]
                assert "hidden-answer" not in pair[0]["answer_payload"]
                assert "must-not-be-added" not in pair[0]["answer_payload"]
                assert pair[0]["prompt_hash"] != pair[1]["prompt_hash"]
            assert all(
                c["context_identical"] is True and c["generation_settings_identical"]
                for c in check_same_context(records).values()
            )
        finally:
            await service.aclose()

    asyncio.run(run())


def test_base_generation_failure_still_uses_same_retrieval_for_defense():
    async def run():
        model = AnswerModel(fail_first=True)
        service = setup(model)
        try:
            records = await service.acompare_security("첫 질문", defense="방어 지시")
            assert [r["status"] for r in records] == ["error", "answered"]
            assert service._graph.run.await_count == 1
            assert len(model.inputs) == 2 and model.inputs[0][0] == model.inputs[1][0]
            assert records[0]["error_type"] == "RagModelError"
        finally:
            await service.aclose()

    asyncio.run(run())


def test_retrieval_failure_does_not_retry_search_or_generate():
    async def run():
        model = AnswerModel()
        service = setup(model)
        service._graph.run = AsyncMock(
            side_effect=RuntimeError("retrieval unavailable")
        )
        try:
            records = await service.acompare_security("첫 질문", defense="방어 지시")
            assert [r["status"] for r in records] == ["error", "error"]
            assert service._graph.run.await_count == 1 and not model.inputs
        finally:
            await service.aclose()

    asyncio.run(run())


def test_empty_defense_fails_before_retrieval():
    async def run():
        service = setup(AnswerModel())
        try:
            with pytest.raises(ValueError):
                await service.acompare_security("첫 질문", defense=" ")
            assert service._graph.run.await_count == 0
        finally:
            await service.aclose()

    asyncio.run(run())


def test_concurrent_pairs_keep_context_separate():
    async def run():
        model = AnswerModel()
        service = setup(model)
        try:
            first, second = await asyncio.gather(
                service.acompare_security("첫 질문", defense="방어 지시"),
                service.acompare_security("둘째 질문", defense="방어 지시"),
            )
            assert service._graph.run.await_count == 2
            assert all(
                json.loads(r["answer_payload"])["question"] == "첫 질문" for r in first
            )
            assert all(
                json.loads(r["answer_payload"])["question"] == "둘째 질문"
                for r in second
            )
            assert first[0]["model_context_hash"] != second[0]["model_context_hash"]
        finally:
            await service.aclose()

    asyncio.run(run())


def test_changed_retrieval_status_is_rejected_before_defense_model_call():
    async def run():
        model = AnswerModel()
        service = setup(model)
        context = prepared(service, "첫 질문", "shared")
        service._graph.run = AsyncMock(return_value=context)
        original = service._ainvoke

        async def mutate(*args, **kw):
            run = await original(*args, **kw)
            if not kw.get("defense"):
                object.__setattr__(context, "status", "insufficient")
            return run

        service._ainvoke = mutate
        try:
            records = await service.acompare_security("첫 질문", defense="방어 지시")
            assert [r["status"] for r in records] == ["answered", "error"]
            assert len(model.inputs) == 1
        finally:
            await service.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("fail_first", [False, True])
def test_cli_saves_four_records_and_keeps_failures_out_of_success(
    tmp_path, monkeypatch, fail_first
):
    import sys
    from app.rag import security

    model = AnswerModel(fail_first=fail_first)
    service = setup(model)
    output = tmp_path / "results"
    defense = tmp_path / "defense.txt"
    defense.write_text("방어 지시", encoding="utf-8")
    monkeypatch.setattr(security, "DEFENSE_PATH", defense)
    monkeypatch.setattr(security, "load_service", lambda _: service)
    monkeypatch.setattr(
        security,
        "load_questions",
        lambda _: [
            {"id": "one", "question": "첫 질문"},
            {"id": "two", "question": "둘째 질문"},
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "security",
            "--security-file",
            "unused.json",
            "--output-directory",
            str(output),
            "--experiment-id",
            "offline",
        ],
    )
    if fail_first:
        with pytest.raises(SystemExit, match="생성 실패"):
            security.main()
    else:
        security.main()
    records = [
        json.loads(line)
        for line in (output / "security.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert len(records) == manifest["answers"] == 4
    assert manifest["security_verdict"] == "not_evaluated"
    assert service._graph.run.await_count == 2
    assert all(manifest["answer_payload_identical"].values()) is not fail_first
    assert sum(record["status"] == "error" for record in records) == int(fail_first)
    assert model.closed == 1
