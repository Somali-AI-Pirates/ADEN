"""Service-owned FIFO admission and cancellation-safe request ownership."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from app.rag.lifecycle import await_to_completion


class RequestBusyError(RuntimeError):
    retry_after = 10


class RequestUnavailableError(RuntimeError):
    pass


class RequestDeadlineExceeded(TimeoutError):
    pass


@dataclass(eq=False)
class RequestReservation:
    """A slot is held until the execution coroutine and its workers finish."""

    request_id: str
    arrived_at: float
    admitted_at: float
    queued_at: float
    deadline: float
    _controller: RequestAdmission = field(repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def queue_ms(self) -> float:
        return (self.admitted_at - self.queued_at) * 1000

    def claim(self, controller: RequestAdmission, request_id: str) -> None:
        if controller is not self._controller or request_id != self.request_id:
            raise ValueError("Request reservation belongs to a different request")
        if self._released or not controller.accepting:
            raise RequestUnavailableError("TableRAG service is closed")
        if self._task is not None:
            raise ValueError("Request reservation has already been used")
        self._task = asyncio.current_task()

    def release(self) -> None:
        """Release an unused response reservation; running work owns its slot."""
        if self._task is None:
            self._controller.complete(self)


@dataclass
class _Waiting:
    request_id: str
    arrived_at: float
    queued_at: float
    future: asyncio.Future[RequestReservation]


class RequestAdmission:
    """One event loop's bounded FIFO; not a cross-process request queue."""

    def __init__(
        self,
        *,
        max_active: int = 1,
        max_waiting: int = 2,
        queue_timeout: float = 10,
        request_timeout: float = 300,
    ) -> None:
        self.max_active = max_active
        self.max_waiting = max_waiting
        self.queue_timeout = queue_timeout
        self.request_timeout = request_timeout
        self.accepting = True
        self._active: set[RequestReservation] = set()
        self._waiting: deque[_Waiting] = deque()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def waiting_count(self) -> int:
        return len(self._waiting)

    def _grant(
        self, request_id: str, arrived_at: float, queued_at: float
    ) -> RequestReservation:
        reservation = RequestReservation(
            request_id=request_id,
            arrived_at=arrived_at,
            admitted_at=asyncio.get_running_loop().time(),
            queued_at=queued_at,
            deadline=arrived_at + self.request_timeout,
            _controller=self,
        )
        self._active.add(reservation)
        return reservation

    async def acquire(
        self, request_id: str, *, arrived_at: float | None = None
    ) -> RequestReservation:
        loop = asyncio.get_running_loop()
        arrived_at = loop.time() if arrived_at is None else arrived_at
        if not self.accepting:
            raise RequestUnavailableError("TableRAG service is closed")
        remaining = arrived_at + self.request_timeout - loop.time()
        if remaining <= 0:
            raise RequestDeadlineExceeded("Request processing budget exceeded")
        if not self._waiting and len(self._active) < self.max_active:
            return self._grant(request_id, arrived_at, loop.time())
        if len(self._waiting) >= self.max_waiting:
            raise RequestBusyError("The request queue is full")
        waiter = _Waiting(request_id, arrived_at, loop.time(), loop.create_future())
        self._waiting.append(waiter)
        transferred = False
        try:
            reservation = await asyncio.wait_for(
                asyncio.shield(waiter.future),
                timeout=min(self.queue_timeout, remaining),
            )
            transferred = True
            return reservation
        except TimeoutError as error:
            if loop.time() >= arrived_at + self.request_timeout:
                raise RequestDeadlineExceeded(
                    "Request processing budget exceeded while waiting"
                ) from error
            raise RequestBusyError("Request queue wait exceeded its limit") from error
        finally:
            if waiter in self._waiting:
                self._waiting.remove(waiter)
            # If cancellation/timeout raced with promotion, return the unused
            # slot. Successful await transfers ownership to the caller.
            if waiter.future.done() and not waiter.future.cancelled():
                exception = waiter.future.exception()
                if exception is None and not transferred:
                    waiter.future.result().release()
            else:
                waiter.future.cancel()

    def complete(self, reservation: RequestReservation) -> None:
        if reservation._released:
            return
        if reservation._controller is not self:
            raise ValueError("Cannot release another service's reservation")
        reservation._released = True
        self._active.discard(reservation)
        if not self.accepting:
            return
        now = asyncio.get_running_loop().time()
        while self._waiting and len(self._active) < self.max_active:
            waiter = self._waiting.popleft()
            if waiter.future.done():
                continue
            if now >= waiter.arrived_at + self.request_timeout:
                waiter.future.set_exception(
                    RequestDeadlineExceeded("Request processing budget exceeded")
                )
                continue
            if now >= waiter.queued_at + self.queue_timeout:
                waiter.future.set_exception(
                    RequestBusyError("Request queue wait exceeded its limit")
                )
                continue
            waiter.future.set_result(
                self._grant(waiter.request_id, waiter.arrived_at, waiter.queued_at)
            )

    async def aclose(self, grace_seconds: float) -> None:
        """Reject queued work, then drain owners before shared resources close."""
        self.accepting = False
        while self._waiting:
            waiter = self._waiting.popleft()
            if not waiter.future.done():
                waiter.future.set_exception(
                    RequestUnavailableError("TableRAG service is closing")
                )
        for reservation in tuple(self._active):
            reservation.release()
        tasks = {
            reservation._task
            for reservation in self._active
            if reservation._task is not None and not reservation._task.done()
        }
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            _, cancelled = await await_to_completion(
                asyncio.gather(*pending, return_exceptions=True)
            )
            if cancelled:
                raise asyncio.CancelledError
