# Same seed-order hazard as async_with_method_awaiter_order, for `async for`:
# the frame embeds the async iterable's __anext__ coro struct, and the awaiter is
# an async method on a record declared before that iterable.
import asyncio
from tpy import int32


class Collector:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    # Declared before Countdown, and embeds its __anext__ coro frame.
    async def run(self) -> int32:
        async for v in Countdown(3):
            self.total += v
        return self.total


class Countdown:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __aiter__(self) -> "Countdown":
        return self

    async def __anext__(self) -> int32:
        if self.n <= 0:
            raise StopAsyncIteration()
        self.n -= 1
        await asyncio.sleep(0)
        return self.n


async def amain() -> None:
    c = Collector()
    print(await c.run())


asyncio.run(amain())
