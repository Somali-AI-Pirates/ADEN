import asyncio
from threading import Event

from app.rag.lifecycle import await_to_completion


def test_await_to_completion_joins_after_repeated_cancellation():
    async def run():
        started = Event()
        release = Event()

        async def owned_operation():
            started.set()
            await asyncio.to_thread(release.wait)
            return "resource-ready"

        owner = asyncio.create_task(await_to_completion(owned_operation()))
        await asyncio.to_thread(started.wait)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        release.set()

        result, cancellation_requested = await owner
        assert result == "resource-ready"
        assert cancellation_requested is True

    asyncio.run(run())


def test_await_to_completion_propagates_operation_errors():
    async def fail():
        raise RuntimeError("factory failed")

    async def run():
        try:
            await await_to_completion(fail())
        except RuntimeError as error:
            assert str(error) == "factory failed"
        else:
            raise AssertionError("operation error was swallowed")

    asyncio.run(run())
