import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.observability.run_store import RequestRunStoreError
from app.rag.service import RagContextBudgetError, RagModelError
from shared.contracts import QueryResponse
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, load_settings


class FakeTableRagService:
    def __init__(self) -> None:
        self.requests = []
        self.close_count = 0
        self.barrier = None
        self.error = None

    async def ainvoke(self, question: str, *, request_id: str):
        self.requests.append((question, request_id))
        if self.error is not None:
            raise self.error
        if self.barrier is not None:
            await asyncio.to_thread(self.barrier.wait, timeout=5)
        response = QueryResponse(
            request_id=request_id,
            status="answered",
            answer=f"검색 결과입니다. ({question})",
            reason="검색 근거가 있습니다.",
            citations=[],
        )
        return SimpleNamespace(response=response)

    async def aclose(self) -> None:
        self.close_count += 1


class TestTableRagApi:
    def setup_method(self) -> None:
        self.settings = load_settings(DEFAULT_CONFIG)

    def test_default_loads_table_service_and_preserves_request_id(self) -> None:
        service = FakeTableRagService()
        resources_loader = Mock(side_effect=AssertionError("legacy loader called"))
        loader = Mock(return_value=service)
        app = create_app(table_service_loader=loader)
        with (
            patch.dict("os.environ", {"ADEN_TABLE_RAG_DIR": ""}),
            patch("app.api.lifespan.load_resources", resources_loader),
        ):
            with TestClient(app) as client:
                health = client.get("/health").json()
                response = client.post("/query", json={"question": "예약 확인"})

        assert health["dataset"] == "advanced"
        assert health["query_ready"] is True
        assert response.status_code == 200
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert service.requests == [("예약 확인", response.headers["X-Request-ID"])]
        loader.assert_called_once_with(
            PROJECT_ROOT / "artifacts/advanced-table-rag"
        )
        resources_loader.assert_not_called()
        assert service.close_count == 1

    def test_explicit_legacy_mode_is_unavailable_in_table_rag(self) -> None:
        service = FakeTableRagService()
        app = create_app(table_service_loader=lambda _path: service)
        with (
            patch.dict(
                "os.environ",
                {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"},
            ),
            TestClient(app) as client,
        ):
            rejected = client.post(
                "/query", json={"question": "예약 확인", "retrieval_mode": "naive"}
            )
            assert rejected.status_code == 501
            assert rejected.json()["request_id"] == rejected.headers["X-Request-ID"]
            assert service.requests == []

    def test_same_app_restarts_and_closes_each_table_service(self) -> None:
        services = [FakeTableRagService(), FakeTableRagService()]
        loader = Mock(side_effect=services)
        app = create_app(table_service_loader=loader)
        with patch.dict(
            "os.environ", {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"}
        ):
            for service in services:
                with TestClient(app) as client:
                    response = client.post("/query", json={"question": "예약 확인"})
                    assert response.status_code == 200
                    assert client.get("/health").json()["query_ready"] is True
                assert service.close_count == 1
                assert app.state.table_service is None

        assert loader.call_count == 2

    def test_concurrent_table_requests_keep_request_ids_separate(self) -> None:
        service = FakeTableRagService()
        service.barrier = Barrier(2)
        app = create_app(table_service_loader=lambda _path: service)
        with (
            patch.dict(
                "os.environ",
                {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"},
            ),
            TestClient(app) as client,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            responses = list(
                pool.map(
                    lambda question: client.post("/query", json={"question": question}),
                    ["첫 번째 질문", "두 번째 질문"],
                )
            )

        request_ids = {response.headers["X-Request-ID"] for response in responses}
        assert [response.status_code for response in responses] == [200, 200]
        assert len(request_ids) == 2
        assert {response.json()["request_id"] for response in responses} == request_ids
        assert {request_id for _, request_id in service.requests} == request_ids
        assert {question for question, _ in service.requests} == {
            "첫 번째 질문",
            "두 번째 질문",
        }

    def test_non_advanced_dataset_does_not_load_table_service(self) -> None:
        settings = self.settings.model_copy(update={"dataset": "basic"})
        loader = Mock()
        app = create_app(table_service_loader=loader)
        with (
            patch.dict(
                "os.environ",
                {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"},
            ),
            patch("app.api.lifespan.load_settings", return_value=settings),
            TestClient(app) as client,
        ):
            assert client.get("/health").json()["query_ready"] is False
            response = client.post("/query", json={"question": "예약 확인"})

        assert response.status_code == 503
        loader.assert_not_called()

    def test_model_timeout_and_context_errors_keep_public_status(self) -> None:
        cases = [
            (RequestRunStoreError("private disk failure"), 503),
            (RagContextBudgetError("private planner details"), 502),
            (RagModelError("private provider details"), 502),
            (TimeoutError("private timeout details"), 504),
        ]
        for error, expected_status in cases:
            service = FakeTableRagService()
            service.error = error
            app = create_app(table_service_loader=lambda _path: service)
            with (
                patch.dict(
                    "os.environ",
                    {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"},
                ),
                TestClient(app, raise_server_exceptions=False) as client,
            ):
                response = client.post("/query", json={"question": "예약 확인"})

            assert response.status_code == expected_status
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
            assert "private" not in response.text
            assert service.close_count == 1


    def test_missing_bundle_keeps_swagger_and_readiness_available(self):
        loader = Mock(side_effect=FileNotFoundError("bundle not prepared"))
        app = create_app(table_service_loader=loader)
        with patch.dict("os.environ", {"ADEN_TABLE_RAG_DIR": ""}), TestClient(app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
            assert client.get("/health").json()["query_ready"] is False
            response = client.post("/query", json={"question": "예약 확인"})
        assert response.status_code == 503
        assert "prepare_table_rag build" in response.json()["reason"]
        assert response.json()["request_id"] == response.headers["X-Request-ID"]



def test_swagger_default_body_uses_only_question():
    from app.api.main import create_app

    schema = create_app().openapi()
    example = schema["paths"]["/query"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]["question"]["value"]
    assert set(example) == {"question"}
    assert example["question"].strip()
