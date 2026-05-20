# Tuple unpack in `async for (a, b) in pairs:`. Inherits the parser's
# existing tuple-unpack rewrite, which routes through a synthetic loop
# var. Verifies the rewrite composes with the async-for path.
import asyncio
from tpy import Own


class PairIter:
    n: int
    limit: int

    def __init__(self, limit: int) -> None:
        self.n = 0
        self.limit = limit

    async def __anext__(self) -> tuple[int, int]:
        if self.n >= self.limit:
            raise StopAsyncIteration
        self.n += 1
        return (self.n, self.n * self.n)


class Pairs:
    limit: int

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def __aiter__(self) -> Own[PairIter]:
        return PairIter(self.limit)


async def sum_squares(p: Pairs) -> int:
    total = 0
    async for k, sq in p:
        total += sq
    return total


async def main() -> None:
    p = Pairs(4)
    print(await sum_squares(p))


asyncio.run(main())
