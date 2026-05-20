# An additional `await` inside an `async for` body. Verifies the
# resume-bind path for __anext__ composes with user awaits in the body
# (each iteration runs two suspensions: __anext__ + body await).
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


async def doubled(n: int) -> int:
    return n * 2


async def total(c: Counts) -> int:
    s = 0
    async for x in c:
        s += await doubled(x)
    return s


async def main() -> None:
    c = Counts(3)
    print(await total(c))


asyncio.run(main())
