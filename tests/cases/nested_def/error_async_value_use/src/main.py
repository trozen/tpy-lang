# Binding a nested def to a Callable variable inside an async def is a
# value use of a frame member -- rejected.
import asyncio
from typing import Callable
from tpy import int32


async def f() -> int32:
    base = 5

    def get() -> int32:
        return base

    cb: Callable[[], int32] = get  # tpyc: error(/cannot escape or be passed as a value/)
    return cb()


async def main() -> None:
    print(await f())


asyncio.run(main())
