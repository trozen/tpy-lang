# Borrow-conflict warning when a mutating method on the async-iterable
# is called during iteration. Same machinery as the sync-for
# warn_iter_mutation tests: an ITER borrow on the named iterable
# rejects mutating-method calls inside the loop body.
import asyncio
from tpy import Own


class SrcIter:
    cursor: int
    limit: int

    def __init__(self, limit: int) -> None:
        self.cursor = 0
        self.limit = limit

    async def __anext__(self) -> int:
        if self.cursor >= self.limit:
            raise StopAsyncIteration
        val = self.cursor
        self.cursor += 1
        return val


class Source:
    seen: list[int]
    limit: int

    def __init__(self, limit: int) -> None:
        self.seen = []
        self.limit = limit

    def __aiter__(self) -> Own[SrcIter]:
        return SrcIter(self.limit)

    def push(self, x: int) -> None:
        self.seen.append(x)


async def runner() -> None:
    src = Source(3)
    async for x in src:
        src.push(x)  # tpyc: warning(/Mutation of 'src'/)


def main() -> None:
    asyncio.run(runner())


main()
