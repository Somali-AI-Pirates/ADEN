import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.rag.config import load_table_rag_settings
from app.api import lifespan as lifespan_module
from app.observability.run_store import RequestRunStoreError
from shared.contracts import QueryResponse


SECRET = "PRIVATE-DATA-MUST-NOT-APPEAR"


class FailingTableRagService:
    def __init__(self, error=None):
        self.error = error

    async def readiness(self):
        return SimpleNamespace(ready=True, checks={"resources": True})

    async def ainvoke(self, question, *, request_id):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            response=QueryResponse(
                request_id=request_id,
                status="answered",
                answer="ok",
                reason="evidence",
                citations=[],
            )
        )

    async def aclose(self):
        pass


def install_safe_settings(monkeypatch, tmp_path):
    config = tmp_path / "advanced.yaml"
    config.write_text(
        "dataset: advanced\ndocuments_dir: documents\n"
        "retrieval:\n  default_mode: naive\n  k: 8\n  context_token_budget: 8000\n",
        encoding="utf-8",
    )

    def load_settings():
        return load_table_rag_settings(
            config_path=config,
            env={},
            env_file=tmp_path / "missing.env",
            project_root=tmp_path,
        )

    monkeypatch.setattr(lifespan_module, "load_table_rag_settings", load_settings)


@pytest.mark.parametrize(
    "failure,status,error_type",
    [
        ("store", 503, "RequestRunStoreError"),
        ("unexpected", 500, "RuntimeError"),
        ("invalid", 422, "RequestValidationError"),
        ("unready", 503, "ServiceError"),
    ],
)
def test_error_log_matches_table_rag_http_request_without_sensitive_details(
    caplog, monkeypatch, tmp_path, failure, status, error_type
):
    install_safe_settings(monkeypatch, tmp_path)
    service = FailingTableRagService()
    if failure == "store":
        service.error = RequestRunStoreError(SECRET)
    elif failure == "unexpected":
        service.error = RuntimeError(SECRET)

    def loader(_path):
        if failure == "unready":
            raise FileNotFoundError(SECRET)
        return service

    with (
        caplog.at_level(logging.WARNING, logger="app.api.errors"),
        TestClient(
            create_app(table_service_loader=loader), raise_server_exceptions=False
        ) as client,
    ):
        question = " " if failure == "invalid" else SECRET
        response = client.post(
            "/query",
            json={"question": question},
            headers={"Authorization": "Bearer " + SECRET},
        )

    assert response.status_code == status
    records = [record for record in caplog.records if record.name == "app.api.errors"]
    assert len(records) == 1
    record = records[0]
    assert (
        record.request_id
        == response.json()["request_id"]
        == response.headers["X-Request-ID"]
    )
    assert record.error_type == error_type and record.status_code == status
    assert record.exc_info is None
    assert SECRET not in caplog.text + response.text
