# Timeout path: inner coroutine still pending when the deadline fires.
# `wait_for` must cancel the inner, pump it to completion, and raise
# `TimeoutError` (a builtin, propagated through the @native
# `tpy::TimeoutError` exception type).
import asyncio


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 42


async def main_coro() -> None:
    try:
        v = await asyncio.wait_for(slow(), 0.01)
        print("not reached")
        print(v)
    except TimeoutError:
        print("timed out")


def main() -> None:
    asyncio.run(main_coro())


main()
