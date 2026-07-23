# The documented escape hatch for spawning a borrowed result: declare
# `-> Own[C]` and return an explicit copy. The task result is then an
# independent object -- mutating the original after the spawn is NOT
# visible through it (copy semantics, acknowledged by the copy() spelling).
import asyncio
from tpy import Own, copy


class C:
    v: int

    def __init__(self) -> None:
        self.v = 1

    async def snapshot(self) -> Own["C"]:
        await asyncio.sleep(0)
        return copy(self)


async def main() -> None:
    c = C()
    t = asyncio.create_task(c.snapshot())
    r = await t
    c.v = 99
    print(r.v)
    print(c.v)


asyncio.run(main())
