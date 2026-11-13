"""Build private Langfuse links for completed browser requests."""

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.rag.config import TableRagSettings

logger = logging.getLogger(__name__)


async def load_trace_link_prefix(settings: TableRagSettings) -> str | None:
    credentials = settings.credentials
    if not (
        settings.trace_enabled
        and credentials.langfuse_public_key
        and credentials.langfuse_secret_key
    ):
        return None
    base_url = (credentials.langfuse_base_url or "https://cloud.langfuse.com").rstrip("/")
    try:
        async with asyncio.timeout(2):
            async with httpx.AsyncClient(
                auth=(credentials.langfuse_public_key, credentials.langfuse_secret_key),
            ) as client:
                response = await client.get(f"{base_url}/api/public/projects")
                response.raise_for_status()
                projects = response.json()["data"]
        if isinstance(projects, list) and len(projects) == 1 and isinstance(projects[0], dict):
            project_id = projects[0]["id"]
            if isinstance(project_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
                return f"{base_url}/project/{project_id}/traces"
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError, KeyError, TypeError) as error:
        logger.info("Langfuse trace links unavailable (%s)", type(error).__name__)
    return None


def completed_trace_url(prefix: str | None, trace: Mapping[str, Any]) -> str | None:
    trace_id = trace.get("langfuse_trace_id")
    if (
        prefix
        and trace.get("langfuse_status") == "recorded_unverified"
        and isinstance(trace_id, str)
        and re.fullmatch(r"[a-f0-9]{32}", trace_id)
    ):
        return f"{prefix}/{trace_id}"
    return None
