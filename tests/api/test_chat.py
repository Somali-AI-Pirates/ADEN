import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

from app.api.chat import ChatRequest, query_stream
from app.api.main import create_app
from app.observability.run_store import RequestRunStoreError
from shared.contracts import QueryResponse
from app.rag.config import TableRagSettings


class Service:
    def __init__(self, error=None):
        self.error = error

    async def ainvoke(self, question, *, request_id, on_event=None):
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
    assert response.headers["X-Accel-Buffering"] == "no"
    assert [v["type"] for v in values] == ["start", "progress", "answer_delta", "final"]
    assert {v["request_id"] for v in values} == {response.headers["X-Request-ID"]}
    assert values[-1]["response"]["answer"] == "예약 확인"
    assert values[-1]["response"]["citations"][0]["page"] == 1


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
        for body in [{"question": " "}, {"question": "예약", "retrieval_mode": None}]:
            response = client.post("/query/stream", json=body)
            assert response.status_code == 422
            assert response.headers["X-Request-ID"] == response.json()["request_id"]
        assert (
            client.post("/query/stream", json={"question": "예약"}).status_code == 503
        )


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
            async def ainvoke(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        request = SimpleNamespace(
            state=SimpleNamespace(request_id="a" * 32),
            app=SimpleNamespace(state=SimpleNamespace(table_service=Pending())),
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
