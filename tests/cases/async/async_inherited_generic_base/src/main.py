# Inherited await / async with / async for on a generic base: a concrete
# subclass reuses the base's templated coro struct with the bound element type.
# Mutate-and-observe confirms the receiver is aliased across the boundary.
import asyncio
from tpy import int32


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    def put(self, v: T) -> None:
        self.v = v

    async def fetch(self) -> T:
        await asyncio.sleep(0.001)
        return self.v


class IntBox(Box[int32]):
    def __init__(self, v: int32) -> None:
        self.v = v


class Guard[T]:
    val: T
    entered: int32

    def __init__(self, val: T) -> None:
        self.val = val
        self.entered = 0

    async def __aenter__(self) -> T:
        await asyncio.sleep(0.001)
        self.entered += 1
        return self.val

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0.001)


class IntGuard(Guard[int32]):
    def __init__(self, val: int32) -> None:
        self.val = val
        self.entered = 0


class Counter[T]:
    cur: int32
    limit: int32
    seed: T

    def __init__(self, limit: int32, seed: T) -> None:
        self.cur = 0
        self.limit = limit
        self.seed = seed

    def __aiter__(self) -> "Counter[T]":
        return self

    async def __anext__(self) -> T:
        await asyncio.sleep(0.001)
        if self.cur >= self.limit:
            raise StopAsyncIteration
        self.cur += 1
        return self.seed


class IntCounter(Counter[int32]):
    def __init__(self, limit: int32, seed: int32) -> None:
        self.cur = 0
        self.limit = limit
        self.seed = seed


async def main_coro() -> None:
    # await: fetch through the inherited templated coro, then mutate the box
    # and re-fetch -- the second value proves the coro aliased the receiver.
    b = IntBox(7)
    print("fetch:", await b.fetch())
    b.put(99)
    print("refetch:", await b.fetch())

    # async with: the inherited __aenter__ bumps `entered` on the shared
    # manager; observing entered == 1 after exit proves it was not copied.
    g = IntGuard(42)
    async with g as v:
        print("entered:", v)
    print("count:", g.entered)

    # async for: the inherited __anext__ advances `cur` across suspensions;
    # a copied iterator would never advance.
    total = 0
    async for x in IntCounter(3, 10):
        total += x
    print("total:", total)


def main() -> None:
    asyncio.run(main_coro())


main()
