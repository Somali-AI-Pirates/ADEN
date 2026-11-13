import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.observability.run_store import RequestRunStoreError
from app.rag.model import RagContextBudgetError, RagModelError
from app.rag.admission import (
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestUnavailableError,
)
from shared.contracts import QueryResponse
from app.rag.config import TableRagSettings, load_table_rag_settings
from shared.settings import PROJECT_ROOT


class FakeTableRagService:
    def __init__(self) -> None:
        self.requests = []
        self.close_count = 0
        self.readiness_status = SimpleNamespace(ready=True, checks={"resources": True})
        self.error = None
        self.barrier = None

    async def readiness(self):
        return self.readiness_status

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
    @pytest.fixture(autouse=True)
    def isolate_root_env_file(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.config.dotenv_values", lambda *_args, **_kwargs: {}
        )

    def setup_method(self) -> None:
        self.settings = TableRagSettings()

    def test_default_loads_table_service_and_preserves_request_id(self) -> None:
        service = FakeTableRagService()
        loader = Mock(return_value=service)
        app = create_app(table_service_loader=loader)
        with patch.dict("os.environ", {"ADEN_TABLE_RAG_DIR": ""}):
            with TestClient(app) as client:
                health = client.get("/health").json()
                response = client.post("/query", json={"question": "예약 확인"})

        assert health["dataset"] == "advanced"
        assert health["query_ready"] is True
        assert response.status_code == 200
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert service.requests == [("예약 확인", response.headers["X-Request-ID"])]
        loader.assert_called_once_with(PROJECT_ROOT / "artifacts/advanced-table-rag")
        assert service.close_count == 1

    def test_default_service_receives_resolved_settings_and_credentials(
        self, monkeypatch
    ):
        from app.rag.config import TableRagSettings

        service = FakeTableRagService()
        received = []
        settings = TableRagSettings(data_directory=PROJECT_ROOT / "custom-table-rag")

        async def load_service(*, settings):
            received.append(settings)
            return service

        monkeypatch.setenv("OPENAI_API_KEY", "settings-test-openai-key")
        monkeypatch.setenv("ADEN_TABLE_RAG_DIR", "custom-table-rag")
        from app.rag import bootstrap as service_module

        monkeypatch.setattr(service_module, "load_service", load_service)
        app = create_app()
        with TestClient(app) as client:
            response = client.post("/query", json={"question": "예약 확인"})

        assert response.status_code == 200
        assert len(received) == 1
        assert received[0].data_directory == settings.data_directory
        assert received[0].credentials.openai_api_key == "settings-test-openai-key"
        assert "settings-test-openai-key" not in repr(received[0])
        assert service.close_count == 1

    def test_explicit_retrieval_mode_is_unavailable_in_table_rag(self) -> None:
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

    @pytest.mark.parametrize(
        "error,status,headers",
        [
            (RequestBusyError("private"), 429, {"Retry-After": "10"}),
            (RequestUnavailableError("private"), 503, {}),
            (RequestDeadlineExceeded("private"), 504, {}),
        ],
    )
    def test_query_maps_admission_errors_to_safe_http_responses(
        self, error, status, headers
    ) -> None:
        service = FakeTableRagService()
        service.error = error
        app = create_app(table_service_loader=lambda _path: service)
        with (
            patch.dict(
                "os.environ", {"ADEN_TABLE_RAG_DIR": "artifacts/advanced-table-rag"}
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            response = client.post("/query", json={"question": "예약 확인"})

        assert response.status_code == status
        assert response.headers["X-Request-ID"] == response.json()["request_id"]
        for name, value in headers.items():
            assert response.headers[name] == value
        assert "private" not in response.text

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

    def test_basic_dataset_config_is_rejected_before_service_load(
        self, tmp_path, monkeypatch
    ):
        config = tmp_path / "basic.yaml"
        config.write_text(
            "dataset: basic\ndocuments_dir: data/basic/documents\n",
            encoding="utf-8",
        )
        loader = Mock()
        app = create_app(table_service_loader=loader)

        def load_test_settings():
            return load_table_rag_settings(
                config_path=config,
                env={},
                env_file=tmp_path / "missing.env",
                project_root=tmp_path,
            )

        monkeypatch.setattr(
            "app.api.lifespan.load_table_rag_settings", load_test_settings
        )
        with pytest.raises(ValueError, match="advanced dataset"), TestClient(app):
            pass
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
        with (
            patch.dict("os.environ", {"ADEN_TABLE_RAG_DIR": ""}),
            TestClient(app) as client,
        ):
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
