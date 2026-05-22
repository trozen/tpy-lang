# Non-positive timeout: triggers the deadline check on the first poll
# (matches CPython). The inner sleep never gets to register its timer
# before wait_for cancels it.
import asyncio


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 42


async def zero_timeout() -> None:
    try:
        await asyncio.wait_for(slow(), 0.0)
        print("not reached")
    except TimeoutError:
        print("zero")


async def main_coro() -> None:
    await zero_timeout()


def main() -> None:
    asyncio.run(main_coro())


main()
