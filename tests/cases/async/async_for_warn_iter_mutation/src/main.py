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


# post_loop: the iterator loan expires with the `async for`, so mutating the
# iterable after the loop is fine.
async def post_loop() -> None:
    src = Source(2)
    async for x in src:
        pass
    src.push(9)  # tpyc: ok
    print("post_loop:", src.seen)


# nested: the INNER `async for` ends without expiring the outer loan, so the
# append to the outer iterable after it still warns.
async def nested() -> None:
    outer = Source(2)
    inner = Source(1)
    async for x in outer:
        async for y in inner:
            inner.push(y)  # tpyc: warning(/Mutation of 'inner'/)
        outer.push(x)  # tpyc: warning(/Mutation of 'outer'/)
    print("nested:", outer.seen, inner.seen)


def main() -> None:
    asyncio.run(runner())
    asyncio.run(post_loop())
    asyncio.run(nested())


main()
