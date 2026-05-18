# Cancellation propagating through a CFG-based finally body. The
# task is cancelled while suspended in the try body; the cancel
# delivers `CancelledError` to the resume case's catch-all, which
# saves it to `__finally_exc_<n>` and transitions to the finally
# entry. The finally body runs to completion before AsyncFinallyExit
# rethrows the saved CancelledError; the awaiting parent catches it.
import asyncio


async def cleanup() -> None:
    print("cleanup-ran")


async def coro() -> int:
    try:
        await asyncio.sleep(60.0)
        return 99
    finally:
        await cleanup()


async def main_coro() -> None:
    task = asyncio.create_task(coro())
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("got-cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
