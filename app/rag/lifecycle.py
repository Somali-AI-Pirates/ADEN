"""Cancellation-safe completion for tasks that own external resources."""

from __future__ import annotations

import asyncio
from functools import partial

from anyio import to_thread
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


async def close_async_resource(callback, *args) -> None:
    _, cancelled = await await_to_completion(callback(*args))
    if cancelled:
        raise asyncio.CancelledError


async def close_sync_resource(callback, *args) -> None:
    _, cancelled = await await_to_completion(
        to_thread.run_sync(partial(callback, *args))
    )
    if cancelled:
        raise asyncio.CancelledError


async def run_sync_acquiring(function, *args, on_acquired=None, **kwargs):
    """Create a sync resource in a worker and register it before cancellation propagates."""

    async def create_and_register():
        resource = await to_thread.run_sync(partial(function, *args, **kwargs))
        if on_acquired is not None:
            on_acquired(resource)
        return resource

    resource, cancelled = await await_to_completion(create_and_register())
    if cancelled:
        raise asyncio.CancelledError
    return resource
