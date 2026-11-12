"""Cancellation-safe completion for tasks that own external resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def await_to_completion(awaitable: Awaitable[T]) -> tuple[T, bool]:
    """Wait for an owned operation to finish despite repeated caller cancellation.

    The operation runs in its own task and is shielded from cancellation. The
    returned flag lets the caller register or clean up an acquired resource
    before re-raising cancellation.
    """
    task = asyncio.ensure_future(awaitable)
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
    return task.result(), cancellation_requested
