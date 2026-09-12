# A nested def in an async def lives on the coroutine frame -- returning
# it (or passing it as a value) is rejected.
import asyncio
from typing import Callable
from tpy import int32


async def f() -> Callable[[int32], int32]:
    base = 10

    def add(x: int32) -> int32:
        return x + base

    return add  # tpyc: error(/cannot escape or be passed as a value/)


async def main() -> None:
    g = await f()
    print(g(1))


asyncio.run(main())
