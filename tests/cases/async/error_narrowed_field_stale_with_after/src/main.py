# A narrowed Optional field established INSIDE an `async with` body is
# stale after the block: `__aexit__` awaits before the following code
# runs, so the fact dies at that suspension.
import asyncio
from tpy import Int32


class Gate:
    async def __aenter__(self) -> None:
        await asyncio.sleep(0)

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        await asyncio.sleep(0)


class Holder:
    f: Int32 | None

    def __init__(self) -> None:
        self.f = 3

    async def read_after_with(self, g: Gate) -> Int32:
        async with g:
            if self.f is None:
                return -1
        return self.f  # tpyc: error(/Type mismatch in return value/)


async def drive() -> None:
    h = Holder()
    print(await h.read_after_with(Gate()))


asyncio.run(drive())
