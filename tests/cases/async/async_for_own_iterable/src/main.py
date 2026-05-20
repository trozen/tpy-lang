# `async for` over an `Own[T]` iterable. Regression test: the codegen's
# __anext__ sub-struct-name resolution must unwrap `Own[T]` before
# looking up the iterable's __aiter__ method, otherwise it crashes with
# an internal CodeGenError about a non-record iterable type.
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


class Source:
    start: int

    def __init__(self, start: int) -> None:
        self.start = start

    def __aiter__(self) -> Own[Counter]:
        return Counter(self.start)


def make() -> Own[Source]:
    return Source(3)


async def total() -> int:
    s = 0
    async for x in make():
        s += x
    return s


async def main() -> None:
    print(await total())


asyncio.run(main())
