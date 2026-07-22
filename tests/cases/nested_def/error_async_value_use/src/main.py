# Binding a nested def to a Callable variable inside an async def is a
# value use of a frame member -- rejected.
import asyncio
from typing import Callable
from tpy import Int32


async def f() -> Int32:
    base = 5

    def get() -> Int32:
        return base

    cb: Callable[[], Int32] = get  # tpyc: error(/cannot escape or be passed as a value/)
    return cb()


async def main() -> None:
    print(await f())


asyncio.run(main())
