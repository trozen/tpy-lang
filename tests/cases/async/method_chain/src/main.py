# Async method awaiting another async method on the same instance.
# Each method gets its own coro struct; the outer's __sub_<n> inlines
# the inner. Self is shared via reference capture, so both observe the
# same instance state.
import asyncio
from tpy import int32


class Math:
    base: int32

    def __init__(self, b: int32) -> None:
        self.base = b

    async def double_base(self) -> int32:
        return self.base * 2

    async def quad_base(self) -> int32:
        d = await self.double_base()
        return d * 2


async def main_coro() -> None:
    m = Math(7)
    q = await m.quad_base()
    print(q)


asyncio.run(main_coro())
