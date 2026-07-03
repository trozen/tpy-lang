# Awaiting a bound coroutine inside try/except where the coroutine
# raises: the prebuilt slot is reset on the catch path (no dangling
# in-flight frame) and the handler runs.
import asyncio


async def boom(n: int) -> int:
    if n > 0:
        raise ValueError("bad input")
    return n


async def main_coro() -> None:
    c = boom(1)
    try:
        print(await c)
    except ValueError:
        print("caught")
    await asyncio.sleep(0.001)


def main() -> None:
    asyncio.run(main_coro())


main()
