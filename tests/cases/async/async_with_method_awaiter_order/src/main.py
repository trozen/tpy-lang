# An `async with` embeds the manager's __aenter__/__aexit__ coro structs by
# value, so they must be emitted first. The awaiter here is an async METHOD on a
# record declared BEFORE the manager, which is the order the method-first seed
# cannot satisfy on its own -- a free-function awaiter is always already ordered.
import asyncio
from tpy import int32


class Runner:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    # Declared before Gate, and embeds Gate's two dunder coro frames.
    async def go(self) -> int32:
        async with Gate(7) as v:
            self.total += v
        return self.total


class Gate:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    async def __aenter__(self) -> int32:
        await asyncio.sleep(0)
        return self.n

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


async def amain() -> None:
    r = Runner()
    print(await r.go())


asyncio.run(amain())
