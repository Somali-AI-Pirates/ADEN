import asyncio
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.requests import Request
from pypdf import PdfWriter
import pytest

from app.api.chat import ChatRequest, query_stream
from app.api.main import create_app
from app.observability.run_store import RequestRunStoreError
from shared.contracts import QueryResponse
from app.rag.config import TableRagSettings
from app.observability.tracing import Tracer
from app.rag.model import GeneratedAnswer
from app.rag.models import SearchResult, SourceRef, make_document
from app.rag.service import RagService
from app.rag.admission import (
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestUnavailableError,
)


class Reservation:
    def __init__(self):
        self.release_count = 0

    def release(self):
        if self.release_count == 0:
            self.release_count = 1


class Service:
    def __init__(self, error=None, reserve_error=None):
        self.error = error
        self.reserve_error = reserve_error
        self.reservations = []

    async def readiness(self):
        return SimpleNamespace(ready=True, checks={"resources": True})

    async def reserve_request(self, request_id):
        if self.reserve_error:
            raise self.reserve_error
        reservation = Reservation()
        self.reservations.append((request_id, reservation))
        return reservation

    async def ainvoke(self, question, *, request_id, on_event=None, reservation=None, conversation_history=()):
        await on_event(
            {"type": "progress", "stage": "answering", "message": "답변 작성 중"}
        )
        await on_event({"type": "answer_delta", "delta": question})
        if self.error:
            raise self.error
        return SimpleNamespace(
            response=QueryResponse(
                request_id=request_id,
                status="answered",
                answer=question,
                reason="문서 근거 확인",
                citations=[
                    {
                        "source_id": "AS001:p0001",
                        "book_id": "AS001",
                        "page": 1,
                        "file_name": "AS001.pdf",
                    }
                ],
            )
        )

    async def aclose(self):
        pass


async def connected():
    return False


def events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_has_request_identity_and_authoritative_final():
    app = create_app(table_service_loader=lambda _: Service())
    with TestClient(app) as client:
        response = client.post("/query/stream", json={"question": " 예약 확인 "})
    values = events(response)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache, no-store, no-transform"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert [v["type"] for v in values] == ["start", "progress", "answer_delta", "final"]
    assert {v["request_id"] for v in values} == {response.headers["X-Request-ID"]}
    assert values[-1]["response"]["answer"] == "예약 확인"
    assert values[-1]["response"]["citations"][0]["page"] == 1


def test_email_follow_up_searches_for_current_document_evidence():
    topic = "한빛물류 예약 담당 연락처를 확인해 줘."
    formal = "한빛물류 예약 담당 이메일이 어떻게 됩니까?"
    informal = "한빛물류 예약 담당 이메일이 뭐야?"
    follow_up = "이메일이 뭐야?"
    ambiguous = "이메일이 어떻게 됩니까?"
    legacy_clarification = "연락처가 뭐야?"
    contact = "booking@example.com"
    document = make_document(
        f"한빛물류 예약 담당 이메일: {contact}",
        SourceRef(book_id="AS001", file_name="AS001.pdf", page=2),
        "AS001:p0002:page",
    )
    source_id = document.metadata["source_id"]
    searches = []
    document_available = True

    def search(query):
        searches.append(query)
        has_candidate = "한빛물류" in query or query in (ambiguous, legacy_clarification)
        pages = (document,) if document_available and has_candidate else ()
        return SearchResult(pages, (), {})

    class Model:
        def planner_overhead(self, count_tokens):
            return 0

        async def plan(self, messages, *, finish_only, calls):
            initial = json.loads(messages[0].content)
            if len(messages) > 1:
                evidence = json.loads(messages[-1].content)
            else:
                evidence = initial["initial_evidence"][0]
                prior = initial.get("conversation_history", [])
                if len(initial["initial_evidence"]) > 1:
                    evidence = initial["initial_evidence"][-1]
                    assert evidence["kind"] == "conversation_sources"
                elif initial["question"] == follow_up and prior and prior[-1]["question"] == topic:
                    return AIMessage(content="", tool_calls=[{
                        "name": "search_evidence", "id": "email-search",
                        "args": {"query": "한빛물류 예약 담당 이메일", "kind": "documents"},
                    }])
            asks_for_clarification = initial["question"] in (ambiguous, legacy_clarification)
            sources = [] if asks_for_clarification else evidence["candidate_sources"]
            status = "insufficient" if len(messages) > 1 else "needs_clarification"
            return AIMessage(content="", tool_calls=[{
                "name": "finish", "id": "finish-email",
                "args": {
                    "status": "sufficient" if sources else status,
                    "selected_sources": sources, "interim_summary": "", "conflicts": [],
                },
            }])

        async def answer(self, payload, *, context_hash, calls):
            value = json.loads(payload)
            pages = value["evidence"]["pages"]
            if value["question"] == legacy_clarification:
                assert not calls.answer_guidance
                assert pages[0]["source_id"] == source_id
                return GeneratedAnswer(
                    status="needs_clarification", answer="어느 연락처를 찾으시나요?", citations=[source_id],
                )
            if not pages:
                if value["retrieval_status"] == "insufficient":
                    return GeneratedAnswer(status="insufficient", answer="이메일 근거가 없습니다.", citations=[])
                return GeneratedAnswer(
                    status="needs_clarification", answer="어느 업체의 이메일을 찾으시나요?", citations=[],
                )
            assert contact in pages[0]["text"]
            if value["question"] == follow_up:
                assert value["conversation_history"][-1]["question"] == topic
                assert "old@example.com" not in pages[0]["text"]
                assert calls.answer_guidance
            else:
                assert not calls.answer_guidance
            return GeneratedAnswer(status="answered", answer=contact, citations=[pages[0]["source_id"]])

        async def aclose(self):
            pass

    service = RagService(
        search=search,
        page_lookup=lambda identifier: document if document_available and identifier == source_id else None,
        table_store=SimpleNamespace(schema=lambda _: []),
        model=Model(), tracer=Tracer(), manifest={"policy": "table_rag_a"},
    )
    app = create_app(table_service_loader=lambda _: service)
    with TestClient(app) as client:
        def ask(question, **options):
            response = client.post("/query/stream", json={"question": question, **options})
            assert response.status_code == 200
            assert events(response)[-1]["type"] == "final"
            return events(response)[-1]["response"]

        for question in (topic, formal, informal):
            assert ask(question)["answer"] == contact
        assert ask(follow_up)["status"] == "needs_clarification"
        clarification = ask(ambiguous)
        assert clarification["status"] == "needs_clarification"
        assert clarification["citations"] == []
        assert contact not in clarification["answer"]
        assert ask(legacy_clarification)["citations"][0]["source_id"] == source_id
        legacy = client.post("/query", json={"question": legacy_clarification})
        assert legacy.status_code == 200
        assert legacy.json()["citations"][0]["source_id"] == source_id
        previous = [{"question": topic, "answer": "이전 안내: old@example.com"}]
        answer = ask(follow_up, conversation_history=previous)
        assert answer["status"] == "answered"
        assert answer["answer"] == contact
        assert [citation["source_id"] for citation in answer["citations"]] == [source_id]
        assert searches[-2:] == [follow_up, "한빛물류 예약 담당 이메일"]
        assert ask(follow_up)["status"] == "needs_clarification"
        previous[0]["source_ids"] = ["AS999:p9999", source_id]
        reread = ask(follow_up, conversation_history=previous)
        assert reread["answer"] == contact
        assert [citation["source_id"] for citation in reread["citations"]] == [source_id]
        assert searches[-1] == follow_up
        document_available = False
        unsupported = ask(follow_up, conversation_history=previous)
        assert unsupported["status"] == "abstained"
        assert unsupported["citations"] == []
        assert "old@example.com" not in unsupported["answer"]


@pytest.mark.parametrize(
    "error", [RequestRunStoreError("private path"), RuntimeError("secret")]
)
def test_error_after_partial_answer_never_completes(error):
    app = create_app(table_service_loader=lambda _: Service(error))
    with TestClient(app) as client:
        response = client.post("/query/stream", json={"question": "확인"})
    values = events(response)
    assert values[-1]["type"] == "error"
    assert all(v["type"] != "final" for v in values)
    assert "private" not in response.text and "secret" not in response.text


def test_stream_validation_and_unready():
    def unavailable(_):
        raise FileNotFoundError()

    with TestClient(create_app(table_service_loader=unavailable)) as client:
        turn = {"question": "업체 확인", "answer": "업체는 한빛물류입니다."}
        for body in [
            {"question": " "},
            {"question": "예약", "retrieval_mode": None},
            {"question": "예약", "conversation_history": [turn] * 7},
            {"question": "예약", "conversation_history": [{**turn, "answer": "가" * 2001}]},
            {"question": "예약", "conversation_history": [{**turn, "question": " "}]},
            {"question": "예약", "conversation_history": [{**turn, "source_ids": ["../bad"]}]},
        ]:
            response = client.post("/query/stream", json=body)
            assert response.status_code == 422
            assert response.headers["X-Request-ID"] == response.json()["request_id"]
        assert (
            client.post("/query/stream", json={"question": "예약"}).status_code == 503
        )


@pytest.mark.parametrize(
    "error,status,headers",
    [
        (RequestBusyError("private"), 429, {"Retry-After": "10"}),
        (RequestUnavailableError("private"), 503, {}),
        (RequestDeadlineExceeded("private"), 504, {}),
    ],
)
def test_stream_admission_errors_are_http_errors_before_stream_start(
    error, status, headers
):
    service = Service(reserve_error=error)
    app = create_app(table_service_loader=lambda _: service)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/query/stream", json={"question": "예약 확인"})

    assert response.status_code == status
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    for name, value in headers.items():
        assert response.headers[name] == value
    assert "private" not in response.text


def test_stream_started_then_error_has_no_final_and_releases_reservation():
    service = Service(error=RuntimeError("private"))
    app = create_app(table_service_loader=lambda _: service)
    with TestClient(app) as client:
        response = client.post("/query/stream", json={"question": "예약 확인"})

    values = events(response)
    assert [value["type"] for value in values][-1] == "error"
    assert all(value["type"] != "final" for value in values)
    assert service.reservations[0][1].release_count == 1


def test_reserved_response_releases_if_asgi_fails_before_iterator_starts():
    async def scenario():
        service = Service()
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="b" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=service)),
            is_disconnected=connected,
        )
        response = await query_stream(ChatRequest(question="예약"), request)
        reservation = service.reservations[0][1]

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.start":
                raise RuntimeError("socket failed")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/query/stream",
            "raw_path": b"/query/stream",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
        with pytest.raises(RuntimeError, match="socket failed"):
            await response(scope, receive, send)
        assert reservation.release_count == 1

    asyncio.run(scenario())


def test_response_send_failure_after_stream_start_cancels_and_joins_service_task():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Pending(Service):
            async def ainvoke(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        service = Pending()
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="c" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=service)),
            is_disconnected=connected,
        )
        response = await query_stream(ChatRequest(question="예약"), request)
        reservation = service.reservations[0][1]

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise RuntimeError("socket failed after stream start")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/query/stream",
            "raw_path": b"/query/stream",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
        with pytest.raises(RuntimeError, match="after stream start"):
            await response(scope, receive, send)
        assert started.is_set()
        assert cancelled.is_set()
        assert reservation.release_count == 1

    asyncio.run(scenario())


def test_stream_disconnect_during_queue_wait_cancels_admission():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        disconnected = False

        class WaitingAdmission:
            async def reserve_request(self, request_id):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        async def receive():
            if disconnected:
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/query/stream",
            "raw_path": b"/query/stream",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
        scope["app"] = SimpleNamespace(
            state=SimpleNamespace(table_service=WaitingAdmission())
        )
        request = Request(scope, receive)
        request.state.request_id = "d" * 32
        pending = asyncio.create_task(
            query_stream(ChatRequest(question="예약"), request)
        )
        await started.wait()
        disconnected = True
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_real_service_shutdown_during_stream_emits_terminal_error():
    from tests.test_table_rag_service import _Model, _service

    async def scenario():
        class WaitingModel(_Model):
            def __init__(self):
                super().__init__()
                self.planning = asyncio.Event()

            async def plan(self, *args, **kwargs):
                self.planning.set()
                await asyncio.Event().wait()

        service = _service(
            WaitingModel(),
            settings=TableRagSettings(shutdown_grace_seconds=0),
        )
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="e" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=service)),
            is_disconnected=connected,
        )
        response = await query_stream(ChatRequest(question="첫 질문"), request)
        iterator = response.body_iterator
        assert '"type": "start"' in await anext(iterator)
        await service._model.planning.wait()
        await service.aclose()

        emitted = []
        while True:
            event = json.loads((await asyncio.wait_for(anext(iterator), 1))[6:])
            emitted.append(event)
            if event["type"] in {"final", "error"}:
                break
        assert emitted[-1]["type"] == "error"
        assert all(event["type"] != "final" for event in emitted)
        assert emitted[-1]["request_id"] == "e" * 32
        assert emitted[-1]["message"]
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(scenario())


def test_real_service_busy_is_mapped_before_json_and_sse_start():
    from app.api.routes import query
    from app.rag.config import TableRagSettings
    from shared.contracts import QueryRequest
    from shared.exceptions import ServiceError
    from tests.test_table_rag_service import _Model, _service

    async def scenario():
        class WaitingModel(_Model):
            def __init__(self):
                super().__init__()
                self.planning = asyncio.Event()

            async def plan(self, *args, **kwargs):
                self.planning.set()
                await asyncio.Event().wait()

        service = _service(
            WaitingModel(),
            settings=TableRagSettings(max_waiting_requests=0),
        )
        first = asyncio.create_task(service.ainvoke("첫 질문", request_id="f" * 32))
        await service._model.planning.wait()
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="a" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=service)),
            is_disconnected=connected,
        )

        with pytest.raises(ServiceError) as json_error:
            await query(QueryRequest(question="둘째 질문"), request)
        assert json_error.value.status_code == 429
        assert json_error.value.response_headers == {"Retry-After": "10"}

        with pytest.raises(ServiceError) as stream_error:
            await query_stream(ChatRequest(question="둘째 질문"), request)
        assert stream_error.value.status_code == 429
        assert stream_error.value.response_headers == {"Retry-After": "10"}

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await service.aclose()

    asyncio.run(scenario())


def test_send_failure_joins_real_service_sync_worker_before_releasing_slot():
    from tests.test_table_rag_service import _Model, _service

    async def scenario():
        worker_started = Event()
        release_worker = Event()
        worker_finished = Event()

        def block_search(_question):
            worker_started.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("test worker release timeout")
            worker_finished.set()

        service = _service(_Model(), before_search=block_search)
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="f" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=service)),
            is_disconnected=connected,
        )
        response = await query_stream(ChatRequest(question="첫 질문"), request)

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                if not await asyncio.to_thread(worker_started.wait, 2):
                    raise AssertionError("RAG sync worker did not start")
                raise RuntimeError("socket failed after stream start")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/query/stream",
            "raw_path": b"/query/stream",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
        call = asyncio.create_task(response(scope, receive, send))
        assert await asyncio.to_thread(worker_started.wait, 2)
        await asyncio.sleep(0.02)
        assert not call.done()
        assert service._admission.active_count == 1

        release_worker.set()
        with pytest.raises(RuntimeError, match="after stream start"):
            await call
        assert worker_finished.is_set()
        assert service._admission.active_count == 0
        await service.aclose()

    asyncio.run(scenario())


def test_pdf_is_inline_range_capable_and_confined_to_originals(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(root / "AS001.pdf")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"private")
    (root / "AS002.pdf").symlink_to(secret)
    settings = TableRagSettings(documents_dir=root)
    app = create_app(table_service_loader=lambda _: Service())
    with (
        patch("app.api.lifespan.load_table_rag_settings", return_value=settings),
        TestClient(app) as client,
    ):
        response = client.get("/documents/AS001")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline")
        partial = client.get("/documents/AS001", headers={"Range": "bytes=0-9"})
        assert partial.status_code == 206
        assert partial.content == response.content[:10]
        for name in ("AS002", "unknown", "..", "AS001.pdf", "%2e%2e%5csecret"):
            assert client.get("/documents/" + name).status_code == 404


def test_stream_disconnect_cancels_only_its_service_task():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Pending:
            async def reserve_request(self, request_id):
                reservation = Reservation()
                self.reservation = reservation
                return reservation

            async def ainvoke(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        request = SimpleNamespace(
            state=SimpleNamespace(request_id="a" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=Pending())),
            is_disconnected=connected,
        )
        response = await query_stream(ChatRequest(question="예약"), request)
        iterator = response.body_iterator
        assert '"start"' in await anext(iterator)
        await started.wait()
        await iterator.aclose()
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_concurrent_streams_do_not_mix_answer_deltas_or_request_ids():
    from concurrent.futures import ThreadPoolExecutor

    class ConcurrentService(Service):
        def __init__(self):
            super().__init__()
            self.started = 0
            self.both_started = asyncio.Event()

        async def ainvoke(self, question, **kwargs):
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), timeout=5)
            return await super().ainvoke(question, **kwargs)

    service = ConcurrentService()
    app = create_app(table_service_loader=lambda _: service)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda question: client.post(
                    "/query/stream", json={"question": question}
                ),
                ["첫 질문", "둘째 질문"],
            )
        )
    ids = set()
    for question, response in zip(["첫 질문", "둘째 질문"], responses):
        values = events(response)
        ids.add(response.headers["X-Request-ID"])
        assert (
            "".join(v["delta"] for v in values if v["type"] == "answer_delta")
            == question
        )
        assert {v["request_id"] for v in values} == {response.headers["X-Request-ID"]}
        assert values[-1]["response"]["answer"] == question
    assert len(ids) == 2
