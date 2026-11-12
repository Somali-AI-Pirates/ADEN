import logging
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api import lifespan as lifespan_module
from app.rag.config import load_table_rag_settings
from shared.contracts import QueryResponse


class FakeTableRagService:
    def __init__(self):
        self.requests = []
        self.error = None
        self.close_count = 0
        self.readiness_status = SimpleNamespace(ready=True, checks={"resources": True})
        self.readiness_calls = 0

    async def readiness(self):
        self.readiness_calls += 1
        return self.readiness_status

    async def ainvoke(self, question, *, request_id):
        self.requests.append((question, request_id))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            response=QueryResponse(
                request_id=request_id,
                status="answered",
                answer=f"확인했습니다. ({question})",
                reason="검색 근거가 있습니다.",
                citations=[
                    {
                        "source_id": "AS001:p0001",
                        "book_id": "AS001",
                        "file_name": "AS001.pdf",
                        "page": 1,
                    }
                ],
            )
        )

    async def aclose(self):
        self.close_count += 1


def install_config_loader(tmp_path, monkeypatch, content, *, env_overrides=None):
    config = tmp_path / "advanced.yaml"
    config.write_text(content, encoding="utf-8")
    environment = {"ADEN_CONFIG": str(config), **(env_overrides or {})}

    def load_settings():
        return load_table_rag_settings(
            env=environment,
            env_file=tmp_path / "missing.env",
            project_root=tmp_path,
        )

    monkeypatch.setattr(lifespan_module, "load_table_rag_settings", load_settings)
    return config


def basic_config():
    return """dataset: advanced
documents_dir: documents
retrieval:
  default_mode: naive
  k: 8
  context_token_budget: 8000
"""


def test_json_query_uses_table_rag_service_and_preserves_request_id(
    tmp_path, monkeypatch
):
    install_config_loader(tmp_path, monkeypatch, basic_config())
    service = FakeTableRagService()
    loader = Mock(return_value=service)
    app = create_app(table_service_loader=loader)

    with TestClient(app) as client:
        health = client.get("/health").json()
        response = client.post("/query", json={"question": " 예약 확인 "})

    assert health == {"status": "ok", "dataset": "advanced", "query_ready": True}
    assert response.status_code == 200
    assert response.json()["answer"] == "확인했습니다. (예약 확인)"
    assert response.json()["citations"][0]["book_id"] == "AS001"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert service.requests == [("예약 확인", response.headers["X-Request-ID"])]
    assert service.close_count == 1
    assert not hasattr(app.state, "resources")


def test_liveness_is_local_and_readiness_returns_component_checks(
    tmp_path, monkeypatch
):
    install_config_loader(tmp_path, monkeypatch, basic_config())
    service = FakeTableRagService()
    app = create_app(table_service_loader=lambda _path: service)

    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert service.readiness_calls == 0

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ready": True, "checks": {"resources": True}}
        assert service.readiness_calls == 1

        service.readiness_status = SimpleNamespace(
            ready=False, checks={"resources": True, "vector_store": False}
        )
        unready = client.get("/health/ready")

    assert unready.status_code == 503
    assert unready.json() == {
        "ready": False,
        "checks": {"resources": True, "vector_store": False},
    }
    assert unready.headers["X-Request-ID"]


def test_uninitialized_readiness_has_safe_component_status(tmp_path, monkeypatch):
    install_config_loader(tmp_path, monkeypatch, basic_config())
    app = create_app(
        table_service_loader=lambda _path: (_ for _ in ()).throw(
            FileNotFoundError("private path")
        )
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False, "checks": {"service": False}}
    assert "private path" not in response.text


def test_http_logs_cover_success_validation_service_failure_and_unready(
    tmp_path, monkeypatch, caplog
):
    install_config_loader(tmp_path, monkeypatch, basic_config())
    service = FakeTableRagService()
    app = create_app(table_service_loader=lambda _path: service)
    with caplog.at_level(logging.INFO, logger="app.api.middleware"):
        with TestClient(app, raise_server_exceptions=False) as client:
            success = client.post("/query", json={"question": "예약 문의"})
            invalid = client.post("/query", json={"question": " "})
            service.error = RuntimeError("private failure")
            failed = client.post("/query", json={"question": "예약 문의"})

        unavailable = create_app(
            table_service_loader=lambda _path: (_ for _ in ()).throw(
                FileNotFoundError("private bundle path")
            )
        )
        with TestClient(unavailable, raise_server_exceptions=False) as client:
            unready = client.post("/query", json={"question": "예약 문의"})

    responses = [success, invalid, failed, unready]
    assert [response.status_code for response in responses] == [200, 422, 500, 503]
    records = [
        record for record in caplog.records if record.name == "app.api.middleware"
    ]
    assert len(records) == len(responses)
    by_id = {record.request_id: record for record in records}
    for response in responses:
        request_id = response.headers["X-Request-ID"]
        assert response.json()["request_id"] == request_id
        record = by_id[request_id]
        assert record.status_code == response.status_code
        assert record.path == "/query"
        assert record.latency_scope == "http"
        assert record.latency_ms >= 0
    assert "private failure" not in failed.text
    assert "private bundle path" not in unready.text


def test_http_logs_omit_question_query_and_auth_header(tmp_path, monkeypatch, caplog):
    install_config_loader(tmp_path, monkeypatch, basic_config())
    secret = "private-diagnostic-value"
    app = create_app(table_service_loader=lambda _path: FakeTableRagService())
    with caplog.at_level(logging.INFO, logger="app.api.middleware"):
        with TestClient(app) as client:
            response = client.post(
                "/query?token=" + secret,
                json={"question": secret},
                headers={"Authorization": "Bearer " + secret},
            )
            unknown = client.get("/" + secret)

    assert response.status_code == 200
    assert unknown.status_code == 404
    assert secret not in caplog.text
    records = [
        record for record in caplog.records if record.name == "app.api.middleware"
    ]
    assert records[-1].path == "<unmatched>"


def test_api_uses_resolved_table_rag_yaml_and_environment(tmp_path, monkeypatch):
    config = """dataset: advanced
documents_dir: configured-documents
retrieval:
  default_mode: naive
  k: 8
  context_token_budget: 8000
table_rag:
  data_directory: configured-bundle
"""
    install_config_loader(
        tmp_path,
        monkeypatch,
        config,
        env_overrides={"ADEN_TABLE_RAG_DIR": "environment-bundle"},
    )
    service = FakeTableRagService()
    loader = Mock(return_value=service)
    app = create_app(table_service_loader=loader)

    with TestClient(app) as client:
        response = client.post("/query", json={"question": "예약 확인"})
        settings = app.state.settings
        assert settings.dataset == "advanced"
        assert settings.documents_dir == (tmp_path / "configured-documents").resolve()

    assert response.status_code == 200
    loader.assert_called_once_with((tmp_path / "environment-bundle").resolve())


def test_invalid_table_rag_yaml_stops_before_service_load(tmp_path, monkeypatch):
    install_config_loader(tmp_path, monkeypatch, "[]\n")
    loader = Mock()
    app = create_app(table_service_loader=loader)

    with pytest.raises(ValueError, match="YAML mapping"), TestClient(app):
        pass

    loader.assert_not_called()
