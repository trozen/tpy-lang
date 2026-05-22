# Cleanup-await path: the inner coroutine has an `await` inside its
# `finally` body. After the deadline fires and `wait_for` cancels the
# inner, the cleanup `await` runs to completion before the inner
# returns -- and `wait_for` keeps pumping until then before raising
# `TimeoutError`.
import asyncio


async def slow() -> int:
    try:
        await asyncio.sleep(1.0)
        return 42
    finally:
        await asyncio.sleep(0.001)
        print("cleanup-ran")


async def main_coro() -> None:
    try:
        v = await asyncio.wait_for(slow(), 0.01)
        print("not reached")
        print(v)
    except TimeoutError:
        print("timed-out")


def main() -> None:
    asyncio.run(main_coro())


main()
