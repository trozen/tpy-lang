# Outer cancellation of a `wait_for` task propagates through to the
# inner coroutine. When `task.cancel()` runs and the wait_for task is
# polled, the resume cancel-check propagates the cancel into the
# in-flight `_WaitForFuture` (and recursively into the user coro),
# letting `slow()` observe `CancelledError` in its `try/except` and
# do its cleanup before the cancellation surfaces to the outer caller.
import asyncio


async def slow() -> int:
    try:
        await asyncio.sleep(1.0)
        return 42
    except asyncio.CancelledError:
        print("inner-cancelled")
        raise


async def main_coro() -> None:
    task = asyncio.create_task(asyncio.wait_for(slow(), 5.0))
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("outer-cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
