import asyncio

import pytest

from app.rag.admission import (
    RequestAdmission,
    RequestBusyError,
    RequestDeadlineExceeded,
    RequestUnavailableError,
)


def test_fifo_bounded_queue_and_unused_reservation_release():
    async def run():
        controller = RequestAdmission()
        first = await controller.acquire("first")
        second = asyncio.create_task(controller.acquire("second"))
        await asyncio.sleep(0)
        third = asyncio.create_task(controller.acquire("third"))
        await asyncio.sleep(0)
        with pytest.raises(RequestBusyError) as failure:
            await controller.acquire("overflow")
        assert failure.value.retry_after == 10
        first.release()
        admitted_second = await second
        assert admitted_second.request_id == "second"
        assert not third.done()
        admitted_second.release()
        admitted_third = await third
        assert admitted_third.request_id == "third"
        admitted_third.release()
        admitted_third.release()
        assert controller.active_count == controller.waiting_count == 0
    asyncio.run(run())


def test_wait_timeout_and_queue_cancellation_allow_the_next_request():
    async def run():
        controller = RequestAdmission(queue_timeout=0.02)
        first = await controller.acquire("first")
        with pytest.raises(RequestBusyError):
            await controller.acquire("expired")
        cancelled = asyncio.create_task(controller.acquire("cancelled"))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        first.release()
        next_request = await controller.acquire("next")
        next_request.release()
        assert controller.active_count == controller.waiting_count == 0
    asyncio.run(run())


def test_cancel_during_promotion_does_not_lose_a_slot():
    async def run():
        controller = RequestAdmission()
        first = await controller.acquire("first")
        waiting = asyncio.create_task(controller.acquire("waiting"))
        await asyncio.sleep(0)
        first.release()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        next_request = await controller.acquire("next")
        assert controller.active_count == 1
        next_request.release()
    asyncio.run(run())


def test_request_budget_includes_time_before_and_inside_queue():
    async def run():
        controller = RequestAdmission(queue_timeout=1, request_timeout=0.02)
        with pytest.raises(RequestDeadlineExceeded):
            await controller.acquire(
                "already-expired", arrived_at=asyncio.get_running_loop().time() - 1
            )
        first = await controller.acquire("first")
        with pytest.raises(RequestDeadlineExceeded):
            await controller.acquire("queue-deadline")
        first.release()
        assert controller.active_count == controller.waiting_count == 0
    asyncio.run(run())


def test_running_reservation_cannot_be_released_by_response_cleanup():
    async def run():
        controller = RequestAdmission()
        reservation = await controller.acquire("running")
        reservation.claim(controller, "running")
        reservation.release()
        assert controller.active_count == 1
        with pytest.raises(ValueError):
            reservation.claim(controller, "running")
        controller.complete(reservation)
        assert controller.active_count == 0
    asyncio.run(run())


def test_reservation_cannot_be_claimed_by_another_request_or_service():
    async def run():
        first = RequestAdmission()
        second = RequestAdmission()
        reservation = await first.acquire("first")
        for controller, request_id in [(first, "other"), (second, "first")]:
            with pytest.raises(ValueError):
                reservation.claim(controller, request_id)
        assert first.active_count == 1
        reservation.release()
    asyncio.run(run())


def test_shutdown_rejects_waiting_and_cancels_active_after_grace():
    async def run():
        controller = RequestAdmission()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def execute():
            reservation = await controller.acquire("active")
            reservation.claim(controller, "active")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                controller.complete(reservation)
                stopped.set()

        active = asyncio.create_task(execute())
        await started.wait()
        waiting = asyncio.create_task(controller.acquire("waiting"))
        await asyncio.sleep(0)
        await controller.aclose(0)
        with pytest.raises(RequestUnavailableError):
            await waiting
        with pytest.raises(asyncio.CancelledError):
            await active
        assert stopped.is_set()
        with pytest.raises(RequestUnavailableError):
            await controller.acquire("later")
        assert controller.active_count == controller.waiting_count == 0
    asyncio.run(run())


def test_shutdown_allows_an_active_request_to_finish_during_grace():
    async def run():
        controller = RequestAdmission()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def execute():
            reservation = await controller.acquire("active")
            reservation.claim(controller, "active")
            started.set()
            try:
                await finish.wait()
                return "complete"
            finally:
                controller.complete(reservation)

        active = asyncio.create_task(execute())
        await started.wait()
        closing = asyncio.create_task(controller.aclose(1))
        await asyncio.sleep(0)
        finish.set()
        assert await active == "complete"
        await closing
        assert controller.active_count == 0
    asyncio.run(run())
