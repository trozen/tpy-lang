# A narrowed Optional FIELD read inside an `async with` body is stale:
# `__aenter__` awaits before the body runs, so field-path narrow facts
# established outside die at the suspension.
import asyncio
from tpy import int32


class Gate:
    async def __aenter__(self) -> None:
        await asyncio.sleep(0)

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        await asyncio.sleep(0)


class Holder:
    f: int32 | None

    def __init__(self) -> None:
        self.f = 3

    async def read_in_with(self, g: Gate) -> int32:
        if self.f is not None:
            async with g:
                return self.f  # tpyc: error(/Type mismatch in return value/)
        return -1


async def drive() -> None:
    h = Holder()
    print(await h.read_in_with(Gate()))


asyncio.run(drive())
