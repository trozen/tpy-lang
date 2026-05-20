# `break` inside an `async for` body. The break should exit the loop
# directly without polling __anext__ again.
import asyncio
from tpy import Own


class Counter:
    n: int

    def __init__(self) -> None:
        self.n = 0

    async def __anext__(self) -> int:
        self.n += 1
        return self.n


class Counts:
    def __aiter__(self) -> Own[Counter]:
        return Counter()


async def first_above(c: Counts, threshold: int) -> int:
    result = -1
    async for x in c:
        if x > threshold:
            result = x
            break
    return result


async def main() -> None:
    c = Counts()
    print(await first_above(c, 5))


asyncio.run(main())
