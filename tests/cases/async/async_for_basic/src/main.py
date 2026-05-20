# `async for x in ait:` happy path. Custom async iterator counts down
# from N; iteration ends when __anext__ raises StopAsyncIteration.
import asyncio
from tpy import Own


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    async def __anext__(self) -> int:
        if self.n <= 0:
            raise StopAsyncIteration
        self.n -= 1
        return self.n


class Counts:
    start: int

    def __init__(self, start: int) -> None:
        self.start = start

    def __aiter__(self) -> Own[Counter]:
        return Counter(self.start)


async def total(c: Counts) -> int:
    s = 0
    async for x in c:
        s += x
    return s


async def main() -> None:
    c = Counts(4)
    print(await total(c))


asyncio.run(main())
