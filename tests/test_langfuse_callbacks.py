from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import RunnableLambda
from langfuse import Langfuse
from langfuse.types import (
    MaskOtelSpansParams,
    OtelSpanData,
    OtelSpanIdentifier,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import langfuse_masking
from app.observability.langfuse_masking import mask_langfuse_spans
from app.evaluation.scripts.http_records import _attempts
from app.observability.records import compute_context_hash
from app.observability.tracing import Tracer


def _span(attributes):
    return OtelSpanData(
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id=None,
        name="graph-state",
        instrumentation_scope_name="langfuse",
        instrumentation_scope_version="5.2.0",
        attributes=attributes,
        resource_attributes={},
    )


def _apply_patch(attributes, patch):
    result = dict(attributes)
    if patch is None:
        return result
    for key in patch.delete_attributes:
        result.pop(key, None)
    result.update(patch.set_attributes)
    return result


def _json_attr(attributes, key):
    return json.loads(attributes[key])


@pytest.fixture
def local_langfuse():
    public_key = f"pk-offline-{uuid4().hex}"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    client = Langfuse(
        public_key=public_key,
        secret_key="offline-test-secret",
        base_url="http://127.0.0.1:1",
        tracing_enabled=True,
        tracer_provider=provider,
        span_exporter=exporter,
        mask_otel_spans=mask_langfuse_spans,
        flush_at=1000,
        flush_interval=60,
        media_upload_thread_count=1,
    )
    tracer = Tracer(client, public_key=public_key)
    try:
        yield tracer, client, provider, exporter
    finally:
        client.shutdown()
        provider.shutdown()


def test_exception_scrubber_is_bounded_for_plain_and_dotted_identifiers():
    script = "\n".join(
        [
            "import json",
            "from app.observability.langfuse_masking import _scrub_string",
            "short = 'A' * 64",
            "long = 'A' * 100_000",
            "dotted = ('a.' * 49_999) + 'aa'",
            "dotted_error_candidate = ('a.' * 10_000) + 'a: x'",
            "print(json.dumps({",
            "    'short_preserved': _scrub_string(short) == short,",
            "    'dotted_error_candidate_preserved': _scrub_string(dotted_error_candidate) == dotted_error_candidate,",
            "    'dotted_preserved': _scrub_string(dotted) == dotted,",
            "    'long_preserved': _scrub_string(long) == long,",
            "    'numeric_boundary_preserved': _scrub_string('1Error: raw') == '1Error: raw',",
            "    'numeric_underscore_boundary_preserved': _scrub_string('1_Error: raw') == '1_Error: raw',",
            "    'underscore_token_redacted': _scrub_string('_Error: raw') == '[REDACTED]',",
            "    'bare_error_redacted': _scrub_string('Error: raw exception details') == '[REDACTED]',",
            "    'qualified_error_redacted': _scrub_string('module.RuntimeError: raw exception details') == '[REDACTED]',",
            "    'credential_before_exception_scrubbed': _scrub_string('api_key=sk-graph-secret-1234567890 RuntimeError: body') == 'api_key=[REDACTED] [REDACTED]',",
            "    'space_before_colon_redacted': _scrub_string('prefix RuntimeError \\n : raw') == 'prefix [REDACTED]',",
            "    'empty_error_preserved': _scrub_string('Error:') == 'Error:',",
            "}))",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "short_preserved": True,
        "dotted_error_candidate_preserved": True,
        "dotted_preserved": True,
        "long_preserved": True,
        "numeric_boundary_preserved": True,
        "numeric_underscore_boundary_preserved": True,
        "underscore_token_redacted": True,
        "bare_error_redacted": True,
        "qualified_error_redacted": True,
        "credential_before_exception_scrubbed": True,
        "space_before_colon_redacted": True,
        "empty_error_preserved": True,
    }


def test_mask_keeps_visible_payload_and_removes_reasoning_secrets_and_exception_text():
    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="b" * 16)
    input_payload = {
        "question": "visible question",
        "reasoning": "private chain of thought",
        "api_key": "sk-secret-value-1234567890",
        "provider_metadata": {"raw": "provider-private"},
        "provider_errors": [
            {"error": "sk-graph-secret-1234567890"},
            {"error": "ghp_1234567890abcdefghijklmnopqrstuv"},
            {"error": "Bearer private-bearer-value"},
        ],
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "visible message"},
                    {"type": "reasoning", "summary": "private block"},
                    {"type": "image", "data": "private image payload"},
                ],
                "additional_kwargs": {"reasoning": "private"},
                "response_metadata": {"provider": "private"},
            },
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "status": "invalid_arguments",
                        "error_class": "ValueError",
                        "error": "ValueError: private provider response body",
                        "visible_result": "kept",
                    }
                ),
            },
        ],
    }
    output_payload = {
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "visible answer"},
            {"type": "reasoning", "text": "private answer reasoning"},
        ],
    }
    attributes = {
        "langfuse.observation.input": json.dumps(input_payload),
        "langfuse.observation.output": json.dumps(output_payload),
        "langfuse.observation.status_message": "RuntimeError: raw private exception",
        "langfuse.observation.metadata.role": json.dumps("planner"),
        "langfuse.observation.metadata.attempt": "1",
        "langfuse.observation.metadata.error_type": json.dumps("ValueError"),
        "langfuse.observation.metadata.ls_provider": json.dumps("openai"),
        "langfuse.observation.metadata.api_key": json.dumps("secret"),
    }
    params = MaskOtelSpansParams(spans={identifier: _span(attributes)})

    patch = mask_langfuse_spans(params=params).span_patches[identifier]
    masked = _apply_patch(attributes, patch)
    masked_input = _json_attr(masked, "langfuse.observation.input")
    masked_output = _json_attr(masked, "langfuse.observation.output")

    assert masked_input["question"] == "visible question"
    assert masked_input["messages"][0]["content"] == [
        {"type": "text", "text": "visible message"}
    ]
    assert "reasoning" not in masked_input and "api_key" not in masked_input
    assert "provider_metadata" not in masked_input
    assert all("error" not in item for item in masked_input["provider_errors"])
    assert "additional_kwargs" not in masked_input["messages"][0]
    assert "response_metadata" not in masked_input["messages"][0]
    tool_result = json.loads(masked_input["messages"][1]["content"])
    assert tool_result == {
        "status": "invalid_arguments",
        "error_class": "ValueError",
        "visible_result": "kept",
    }
    assert masked_output["content"] == [{"type": "output_text", "text": "visible answer"}]
    assert "langfuse.observation.status_message" not in masked
    assert "langfuse.observation.metadata.ls_provider" not in masked
    assert "langfuse.observation.metadata.api_key" not in masked
    assert json.loads(masked["langfuse.observation.metadata.role"]) == "planner"
    assert json.loads(masked["langfuse.observation.metadata.error_type"]) == "ValueError"


def test_mask_redacts_payload_when_filtering_fails_and_fails_closed_if_fallback_fails(
    monkeypatch, local_langfuse
):
    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="b" * 16)
    attributes = {
        "langfuse.observation.input": '{"visible":"secret"}',
        "langfuse.observation.output": '{"visible":"secret"}',
        "langfuse.observation.metadata.role": '"planner"',
        "gen_ai.prompt.0.content": "secret prompt",
    }
    params = MaskOtelSpansParams(spans={identifier: _span(attributes)})

    def fail_filter(_value):
        raise ValueError("private malformed payload")

    monkeypatch.setattr(langfuse_masking, "_sanitize_payload", fail_filter)
    patch = mask_langfuse_spans(params=params).span_patches[identifier]
    masked = _apply_patch(attributes, patch)
    assert masked["langfuse.observation.input"] == "[REDACTED]"
    assert masked["langfuse.observation.output"] == "[REDACTED]"
    assert "langfuse.observation.metadata.role" not in masked
    assert "gen_ai.prompt.0.content" not in masked

    def fail_fallback(_attributes):
        raise RuntimeError("private fallback failure")

    monkeypatch.setattr(langfuse_masking, "_fallback_deletes", fail_fallback)
    with pytest.raises(RuntimeError, match="Langfuse masking failed") as raised:
        mask_langfuse_spans(params=params)
    assert raised.value.__cause__ is None

    # The SDK export boundary absorbs the mask failure by dropping the batch;
    # the request still finishes and no unmasked span reaches the exporter.
    tracer, _client, _provider, exporter = local_langfuse
    trace = tracer.start_request(
        "mask-failure-request",
        question="visible question",
        retrieval_mode="table_rag",
        dataset_id="test",
        config_hash="config",
    )
    trace.finish(
        status="answered", answer="visible answer", reason="visible", citations=[], latency_ms=1
    )
    assert tracer.flush() == {"status": "flushed_unverified"}
    assert exporter.get_finished_spans() == ()


def test_official_callback_parents_graph_and_two_generations_under_one_request(local_langfuse):
    tracer, _client, _provider, exporter = local_langfuse
    trace = tracer.start_request(
        "callback-graph-request",
        question="visible user question",
        retrieval_mode="table_rag",
        dataset_id="test-dataset",
        config_hash="config-hash",
    )
    handler = trace.langchain_handler()
    assert handler is not None
    assert trace.langchain_handler() is handler

    final_chunk = {
        "chunk_id": "chunk-1",
        "source_id": "source-1",
        "book_id": "book-1",
        "file_name": "book.pdf",
        "page": 1,
        "text": "visible source text",
    }
    candidates = [{**final_chunk, "rank": 1, "score": 0.9, "retriever": "hybrid"}]
    context_text = "[S1] book.pdf p.1\nvisible source text"
    trace.record_retrieval(
        retrieved_candidates=candidates,
        final_context_chunks=[final_chunk],
        final_context_hash=compute_context_hash([final_chunk]),
        model_context_text=context_text,
        model_context_hash=sha256(context_text.encode()).hexdigest(),
        model_context_token_count=8,
    )

    original_content = [
        {"type": "text", "text": "visible planner prompt"},
        {"type": "reasoning", "summary": "private reasoning stays in model input"},
    ]
    messages = [HumanMessage(content=original_content)]
    graph_state = {
        "question": "visible graph question",
        "messages": messages,
        "reasoning": "private graph reasoning",
        "credentials": {"api_key": "sk-graph-secret-1234567890"},
        "provider_metadata": {"provider": "private"},
        "error": "RuntimeError: raw graph exception",
    }
    graph_node = RunnableLambda(lambda state: state["messages"]).with_config(
        {"run_name": "graph-state"}
    )
    planner = FakeListChatModel(responses=["visible planner result"]).with_config(
        {
            "run_name": "planner-1",
            "metadata": {
                "role": "planner",
                "attempt": 1,
                "tool_schema": [{"name": "lookup_records"}],
                "ls_model_name": "fake-planner",
            },
        }
    )
    graph = graph_node | planner
    graph.invoke(graph_state, config={"callbacks": [handler]})

    answer_model = FakeListChatModel(responses=["visible answer result"]).with_config(
        {
            "run_name": "answer-2",
            "metadata": {"role": "answer", "attempt": 2, "ls_model_name": "fake-answer"},
        }
    )
    answer_model.invoke(
        [HumanMessage(content="visible answer prompt")],
        config={"callbacks": [handler]},
    )
    assert messages[0].content == original_content
    trace.finish(
        status="answered",
        answer="visible final answer",
        reason="visible final reason",
        citations=[],
        latency_ms=42,
    )

    assert tracer.flush() == {"status": "flushed_unverified"}
    spans = exporter.get_finished_spans()
    span_by_name = {span.name: span for span in spans}
    assert sum(span.name == "planner-1" for span in spans) == 1
    assert sum(span.name == "answer-2" for span in spans) == 1
    root = span_by_name["rag-query"]
    root_id = root.context.span_id
    trace_id = format(root.context.trace_id, "032x")
    assert trace_id == trace.trace_id
    spans_by_id = {span.context.span_id: span for span in spans}
    graph_parent = spans_by_id[span_by_name["graph-state"].parent.span_id]
    assert graph_parent.parent.span_id == root_id
    assert span_by_name["answer-2"].parent.span_id == root_id
    assert all(format(span.context.trace_id, "032x") == trace_id for span in spans)

    graph_input = _json_attr(
        dict(span_by_name["graph-state"].attributes), "langfuse.observation.input"
    )
    graph_output = _json_attr(
        dict(span_by_name["graph-state"].attributes), "langfuse.observation.output"
    )
    serialized_graph_payload = json.dumps([graph_input, graph_output], ensure_ascii=False)
    assert "private graph reasoning" not in serialized_graph_payload
    assert "private reasoning stays in model input" not in serialized_graph_payload
    assert "sk-graph-secret" not in serialized_graph_payload
    assert "provider_metadata" not in serialized_graph_payload
    assert "RuntimeError: raw graph exception" not in serialized_graph_payload

    planner_span = span_by_name["planner-1"]
    planner_input = _json_attr(dict(planner_span.attributes), "langfuse.observation.input")
    assert planner_input[0]["content"] == [{"type": "text", "text": "visible planner prompt"}]
    assert json.loads(planner_span.attributes["langfuse.observation.metadata.role"]) == "planner"
    assert json.loads(planner_span.attributes["langfuse.observation.metadata.tool_schema"]) == [
        {"name": "lookup_records"}
    ]
    assert _json_attr(dict(root.attributes), "langfuse.observation.output")["answer"] == (
        "visible final answer"
    )
    assert _json_attr(dict(span_by_name["retrieval"].attributes), "langfuse.observation.output")[
        "candidates"
    ] == candidates
    assert _json_attr(
        dict(span_by_name["final-context"].attributes), "langfuse.observation.output"
    )["final_context_chunks"] == [final_chunk]

    def http_observation(span):
        attributes = dict(span.attributes)
        metadata = {}
        for key, value in attributes.items():
            prefix = "langfuse.observation.metadata."
            if key.startswith(prefix):
                metadata[key.removeprefix(prefix)] = json.loads(value)
        usage = attributes.get("langfuse.observation.usage_details")
        observation = {
            "model": attributes.get("langfuse.observation.model.name"),
            "input": attributes.get("langfuse.observation.input"),
            "output": attributes.get("langfuse.observation.output"),
            "metadata": metadata,
        }
        if usage:
            observation["usageDetails"] = json.loads(usage)
        return observation

    restored = _attempts(
        {
            name: http_observation(span)
            for name, span in span_by_name.items()
            if name in {"planner-1", "answer-2"}
        }
    )
    assert [item["role"] for item in restored] == ["planner", "answer"]
    assert [item["model"] for item in restored] == ["fake-planner", "fake-answer"]
    assert restored[0]["model_input"] == {
        "messages": [{"role": "user", "content": "visible planner prompt"}]
    }
    assert restored[0]["tool_schema"] == [{"name": "lookup_records"}]
    assert restored[0]["model_output"] == {
        "role": "assistant", "content": "visible planner result"
    }
    assert restored[1]["model_input"] == {
        "messages": [{"role": "user", "content": "visible answer prompt"}]
    }
    assert restored[1]["model_output"] == {
        "role": "assistant", "content": "visible answer result"
    }
    assert [(item["input_tokens"], item["output_tokens"]) for item in restored] == [
        (None, None),
        (None, None),
    ]


def test_stream_callback_exports_measured_usage_and_missing_usage_stays_null(local_langfuse):
    tracer, _client, _provider, exporter = local_langfuse
    trace = tracer.start_request(
        "stream-usage-request",
        question="visible question",
        retrieval_mode="table_rag",
        dataset_id="test",
        config_hash="config",
    )
    handler = trace.langchain_handler()

    class UsageStream(FakeListChatModel):
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="visible ", chunk_position=None)
            )
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="answer",
                    usage_metadata={"input_tokens": 13, "output_tokens": 5, "total_tokens": 18},
                    chunk_position="last",
                )
            )

    streamed = UsageStream(responses=[]).with_config(
        {"run_name": "answer-1", "metadata": {"role": "answer", "attempt": 1}}
    )
    output = "".join(
        chunk.content
        for chunk in streamed.stream(
            [HumanMessage(content="visible answer prompt")],
            config={"callbacks": [handler]},
        )
    )
    assert output == "visible answer"

    unknown_usage = FakeListChatModel(responses=["no usage provided"]).with_config(
        {"run_name": "planner-2", "metadata": {"role": "planner", "attempt": 2}}
    )
    unknown_usage.invoke(
        [HumanMessage(content="visible planner prompt")],
        config={"callbacks": [handler]},
    )
    trace.finish(
        status="answered", answer="visible answer", reason="visible", citations=[], latency_ms=1
    )
    tracer.flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    measured = json.loads(spans["answer-1"].attributes["langfuse.observation.usage_details"])
    assert measured == {"input": 13, "output": 5, "total": 18}
    assert "langfuse.observation.usage_details" not in spans["planner-2"].attributes

    attempts = _attempts(
        {
            "planner-2": {
                "model": "fake-model",
                "input": '[{"role":"user","content":"visible planner prompt"}]',
                "output": '{"role":"assistant","content":"no usage provided"}',
                "metadata": {"role": "planner", "attempt": 2},
            },
            "answer-1": {
                "model": "fake-model",
                "input": '[{"role":"user","content":"visible answer prompt"}]',
                "output": '{"role":"assistant","content":"visible answer"}',
                "usageDetails": {"input": 13, "output": 5, "total": 18},
                "metadata": {
                    "role": "answer",
                    "attempt": 1,
                    "usage_known": {"input": True, "output": True},
                },
            },
        }
    )
    assert [(item["input_tokens"], item["output_tokens"]) for item in attempts] == [
        (13, 5),
        (None, None),
    ]


def test_official_callbacks_isolate_concurrent_requests_by_trace(local_langfuse):
    tracer, _client, _provider, exporter = local_langfuse

    def invoke(index):
        request_id = f"concurrent-{index}"
        trace = tracer.start_request(
            request_id,
            question=f"question-{index}",
            retrieval_mode="table_rag",
            dataset_id="test",
            config_hash="config",
        )
        handler = trace.langchain_handler()
        model = FakeListChatModel(responses=[f"answer-{index}"]).with_config(
            {"run_name": "answer-1", "metadata": {"role": "answer", "attempt": 1}}
        )
        model.invoke(
            [HumanMessage(content=f"prompt-{index}")],
            config={"callbacks": [handler]},
        )
        trace.finish(
            status="answered",
            answer=f"answer-{index}",
            reason="visible",
            citations=[],
            latency_ms=1,
        )
        return trace.trace_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        trace_ids = list(pool.map(invoke, (1, 2)))
    assert len(set(trace_ids)) == 2
    assert tracer.flush() == {"status": "flushed_unverified"}

    grouped = {}
    for span in exporter.get_finished_spans():
        trace_id = format(span.context.trace_id, "032x")
        grouped.setdefault(trace_id, []).append(span)
    assert set(grouped) == set(trace_ids)
    for index, trace_id in enumerate(trace_ids, start=1):
        spans = grouped[trace_id]
        roots = [span for span in spans if span.name == "rag-query"]
        generations = [span for span in spans if span.name == "answer-1"]
        assert len(roots) == len(generations) == 1
        root = roots[0]
        generation = generations[0]
        assert generation.parent.span_id == root.context.span_id
        assert _json_attr(dict(root.attributes), "langfuse.observation.input")["question"] == (
            f"question-{index}"
        )
        assert _json_attr(dict(generation.attributes), "langfuse.observation.input")[0][
            "content"
        ] == f"prompt-{index}"
        assert _json_attr(dict(generation.attributes), "langfuse.observation.output")[
            "content"
        ] == f"answer-{index}"


def test_callback_factory_failure_is_request_local_and_export_failure_is_unverified(
    monkeypatch,
    local_langfuse,
):
    tracer, _client, _provider, _exporter = local_langfuse

    assert Tracer().start_request(
        "disabled",
        question="q",
        retrieval_mode="table_rag",
        dataset_id="test",
        config_hash="config",
    ).langchain_handler() is None

    class Root:
        id = "b" * 16

        def update(self, **kwargs):
            return self

        def end(self):
            return self

    fake_client = SimpleNamespace(
        create_trace_id=lambda seed: "c" * 32,
        start_observation=lambda **kwargs: Root(),
    )
    import langfuse.langchain

    calls = []

    def broken_handler(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private callback initialization detail")

    monkeypatch.setattr(langfuse.langchain, "CallbackHandler", broken_handler)
    broken_trace = Tracer(fake_client, public_key="public-test").start_request(
        "callback-failure",
        question="q",
        retrieval_mode="table_rag",
        dataset_id="test",
        config_hash="config",
    )
    assert broken_trace.langchain_handler() is None
    assert broken_trace.langchain_handler() is None
    assert len(calls) == 1
    assert broken_trace.status == "sdk_error"
    assert broken_trace.errors == ["langchain_handler:RuntimeError"]
    assert "private" not in repr(broken_trace.errors)
    done = broken_trace.finish(
        status="answered", answer="answer", reason="visible", citations=[], latency_ms=1
    )
    assert done["langfuse_status"] == "sdk_error"

    class FailedExporter(InMemorySpanExporter):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def export(self, spans):
            self.calls += 1
            return SpanExportResult.FAILURE

    failure_exporter = FailedExporter()
    failure_provider = TracerProvider()
    public_key = f"pk-offline-failure-{uuid4().hex}"
    failure_client = Langfuse(
        public_key=public_key,
        secret_key="offline-test-secret",
        base_url="http://127.0.0.1:1",
        tracing_enabled=True,
        tracer_provider=failure_provider,
        span_exporter=failure_exporter,
        mask_otel_spans=mask_langfuse_spans,
        flush_at=1000,
        flush_interval=60,
        media_upload_thread_count=1,
    )
    failure_tracer = Tracer(failure_client, public_key=public_key)
    failed_trace = failure_tracer.start_request(
        "export-failure",
        question="q",
        retrieval_mode="table_rag",
        dataset_id="test",
        config_hash="config",
    )
    failed_trace.finish(
        status="answered", answer="a", reason="visible", citations=[], latency_ms=1
    )
    result = failure_tracer.flush()
    assert result["status"] == "flushed_unverified"
    assert result["status"] != "verified"
    assert failure_exporter.calls > 0
    failure_client.shutdown()
    failure_provider.shutdown()
