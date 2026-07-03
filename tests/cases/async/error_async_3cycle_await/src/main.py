# A 3-node inline-await cycle (a -> b -> c -> a) is rejected like the 2-cycle:
# by-value sub-futures can't form a cycle. Exercises the cycle-detection path
# with a longer cycle than ping/pong.
import asyncio
from tpy import Int32


async def a(n: Int32) -> Int32:  # tpyc: error(/recursive coroutine embedding/)
    await asyncio.sleep(0)
    return await b(n - 1)


async def b(n: Int32) -> Int32:
    return await c(n - 1)


async def c(n: Int32) -> Int32:
    if n <= 0:
        return 0
    return await a(n - 1)


async def main_coro() -> None:
    print(await a(6))


def main() -> None:
    asyncio.run(main_coro())


main()
