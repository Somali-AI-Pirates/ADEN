"""Inject a fake service only into the API image smoke process."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.main import app
from shared.contracts import QueryResponse


class SmokeService:
    async def readiness(self):
        return SimpleNamespace(
            ready=True,
            checks={
                "resources": True,
                "records": True,
                "tables": True,
                "vector_store": True,
                "run_store": True,
            },
        )

    async def ainvoke(self, question: str, *, request_id: str, **_kwargs):
        return SimpleNamespace(
            response=QueryResponse(
                request_id=request_id,
                status="answered",
                answer=f"CI smoke: {question}",
                reason="CI fake service completed this request.",
                citations=[],
            )
        )

    async def aclose(self):
        return None


app.state.table_service_loader = lambda _directory: SmokeService()
