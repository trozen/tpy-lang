# `continue` inside an `async for` body. Skip odd values; sum the rest.
import asyncio
from tpy import Own


class Counter:
    n: int
    limit: int

    def __init__(self, limit: int) -> None:
        self.n = 0
        self.limit = limit

    async def __anext__(self) -> int:
        if self.n >= self.limit:
            raise StopAsyncIteration
        self.n += 1
        return self.n


class Counts:
    limit: int

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def __aiter__(self) -> Own[Counter]:
        return Counter(self.limit)


async def sum_evens(c: Counts) -> int:
    total = 0
    async for x in c:
        if x % 2 != 0:
            continue
        total += x
    return total


async def main() -> None:
    c = Counts(6)
    print(await sum_evens(c))


asyncio.run(main())
