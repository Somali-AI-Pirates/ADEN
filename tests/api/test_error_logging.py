"""실패 응답의 요청 ID와 원인 종류를 원문 노출 없이 연결한다."""
import logging
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from shared.exceptions import ServiceError
from tests.pipeline_fakes import make_query_resources

SECRET = "PRIVATE-DATA-MUST-NOT-APPEAR"


@pytest.mark.parametrize("failure,status,error_type", [
    ("evidence", 503, "KeyError"),
    ("unexpected", 500, "RuntimeError"),
    ("invalid", 422, "RequestValidationError"),
    ("unready", 503, "ServiceError"),
])
def test_error_log_matches_http_request_without_sensitive_details(caplog, failure, status, error_type):
    resources = make_query_resources()
    if failure == "evidence":
        resources.evidence_provider = Mock(side_effect=KeyError(SECRET))
    loader = Mock(return_value=resources)
    if failure == "unready":
        loader.side_effect = ServiceError("준비 중", 503)
    with (
        patch("app.api.lifespan.load_dotenv"),
        caplog.at_level(logging.WARNING, logger="app.api.errors"),
        TestClient(create_app(resources_loader=loader), raise_server_exceptions=False) as client,
    ):
        question = " " if failure == "invalid" else SECRET
        if failure == "unexpected":
            with patch("app.api.routes.answer_query", side_effect=RuntimeError(SECRET)):
                response = client.post("/query", json={"question":question})
        else:
            response = client.post("/query", json={"question":question}, headers={"Authorization":"Bearer "+SECRET})
    assert response.status_code == status
    records = [r for r in caplog.records if r.name == "app.api.errors"]
    assert len(records) == 1
    record = records[0]
    assert record.request_id == response.json()["request_id"] == response.headers["X-Request-ID"]
    assert record.error_type == error_type and record.status_code == status
    assert record.exc_info is None
    assert SECRET not in caplog.text + response.text
