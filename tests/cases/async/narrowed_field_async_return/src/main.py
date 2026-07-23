# A narrowed value-Optional FIELD returns its inner value at the async
# return slot when no await intervenes since the guard; re-guarding
# after an await restores the fact.
import asyncio
from tpy import Int32


class Holder:
    f: Int32 | None

    def __init__(self, v: Int32 | None) -> None:
        self.f = v

    async def direct(self) -> Int32:
        if self.f is not None:
            return self.f
        return -1

    async def reguard(self) -> Int32:
        await asyncio.sleep(0)
        if self.f is not None:
            return self.f
        return -2


async def drive() -> None:
    h = Holder(7)
    print(await h.direct())
    print(await h.reguard())
    n = Holder(None)
    print(await n.direct())
    print(await n.reguard())


asyncio.run(drive())
