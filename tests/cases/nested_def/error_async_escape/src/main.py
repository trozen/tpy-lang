# A nested def in an async def lives on the coroutine frame -- returning
# it (or passing it as a value) is rejected.
import asyncio
from typing import Callable
from tpy import Int32


async def f() -> Callable[[Int32], Int32]:
    base = 10

    def add(x: Int32) -> Int32:
        return x + base

    return add  # tpyc: error(/cannot escape or be passed as a value/)


async def main() -> None:
    g = await f()
    print(g(1))


asyncio.run(main())
