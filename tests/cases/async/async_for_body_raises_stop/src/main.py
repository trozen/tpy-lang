# `StopAsyncIteration` raised from inside the `async for` BODY (not
# __anext__) must propagate, not be silently caught by the loop's
# auto-handler. Regression test for the body-inside-the-TryRegion bug.
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


async def runner(c: Counts) -> str:
    try:
        async for x in c:
            if x == 2:
                # Raised from the body, not __anext__. Should escape the
                # loop's auto-handler and surface here.
                raise StopAsyncIteration("body-raise")
        return "loop-finished"
    except StopAsyncIteration as e:
        return "caught: " + str(e)


async def main() -> None:
    c = Counts(5)
    print(await runner(c))


asyncio.run(main())
