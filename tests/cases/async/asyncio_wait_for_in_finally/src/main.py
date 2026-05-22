# `await asyncio.wait_for(...)` inside a `finally` body. The outer
# try suspends on its own sleep; on cancellation the finally runs and
# itself awaits a wait_for-bounded coroutine. Exercises the
# CFG-decomposed finally interacting with a wait_for sub-coro whose
# inner has its own state machine -- the cancel-propagation in the
# outer-finally path must not implicitly re-cancel the wait_for body.
import asyncio


async def quick() -> int:
    await asyncio.sleep(0.001)
    return 11


async def go() -> None:
    try:
        await asyncio.sleep(60.0)
    finally:
        v = await asyncio.wait_for(quick(), 5.0)
        print("cleanup:", v)


async def main_coro() -> None:
    task = asyncio.create_task(go())
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("got-cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
