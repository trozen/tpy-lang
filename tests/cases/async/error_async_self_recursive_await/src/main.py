# A coroutine that inline-awaits itself is a 1-cycle: its frame would store
# itself by value (`optional<__coro_f>`), which is infinite-size. Rejected with
# the same clean diagnostic as mutual recursion (the topo sort keeps the
# self-edge, so the cycle is detected) rather than a raw incomplete-type error.
import asyncio
from tpy import Int32


async def f(n: Int32) -> Int32:  # tpyc: error(/recursive coroutine embedding/)
    if n <= 0:
        return 0
    await asyncio.sleep(0)
    return await f(n - 1)


async def main_coro() -> None:
    print(await f(3))


def main() -> None:
    asyncio.run(main_coro())


main()
