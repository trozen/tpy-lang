# async sibling of the missing-return error: non-None coroutine return
# type with a reachable end of body.
import asyncio
from tpy import Int32


async def f(n: Int32) -> Int32:  # tpyc: error(/'f' can reach the end of the function without returning/)
    await asyncio.sleep(0)
    if n > 0:
        return 1


async def main_coro() -> None:
    print(await f(1))


asyncio.run(main_coro())
