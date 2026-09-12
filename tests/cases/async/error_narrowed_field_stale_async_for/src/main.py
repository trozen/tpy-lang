# A narrowed Optional FIELD read inside an `async for` body is stale:
# `__anext__` awaits before each iteration, so field-path narrow facts
# established outside die at the suspension.
import asyncio
from tpy import int32


class Ticks:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __aiter__(self) -> "Ticks":
        return self

    async def __anext__(self) -> int32:
        await asyncio.sleep(0)
        if self.n >= 2:
            raise StopAsyncIteration
        self.n += 1
        return self.n


class Holder:
    f: int32 | None

    def __init__(self) -> None:
        self.f = 3

    async def read_in_for(self, t: Ticks) -> int32:
        if self.f is not None:
            async for _i in t:
                return self.f  # tpyc: error(/Type mismatch in return value/)
        return -1


async def drive() -> None:
    h = Holder()
    print(await h.read_in_for(Ticks()))


asyncio.run(drive())
