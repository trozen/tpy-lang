# An `async with` embeds the manager's __aenter__/__aexit__ coro structs by
# value, so they must be emitted first. The awaiter here is an async METHOD on a
# record declared BEFORE the manager, which is the order the method-first seed
# cannot satisfy on its own -- a free-function awaiter is always already ordered.
import asyncio
from tpy import Int32


class Runner:
    total: Int32

    def __init__(self) -> None:
        self.total = 0

    # Declared before Gate, and embeds Gate's two dunder coro frames.
    async def go(self) -> Int32:
        async with Gate(7) as v:
            self.total += v
        return self.total


class Gate:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    async def __aenter__(self) -> Int32:
        await asyncio.sleep(0)
        return self.n

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


async def amain() -> None:
    r = Runner()
    print(await r.go())


asyncio.run(amain())
