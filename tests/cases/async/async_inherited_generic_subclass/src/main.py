# A generic subclass of a generic base (Child[U](Box[U])) instantiated
# concretely: the inherited async method's frame is Box's at the receiver's
# instantiation (Box[int32]). Also an inherited async method on a builtin
# subclass and `async with` / `async for` through inherited dunders.
import asyncio
from tpy import int32


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    async def fetch(self) -> T:
        await asyncio.sleep(0.001)
        return self.v


class Child[U](Box[U]):
    def __init__(self, v: U) -> None:
        super().__init__(v)


class Pool(list[int32]):
    def __init__(self) -> None:
        pass

    async def total(self) -> int32:
        await asyncio.sleep(0.001)
        n = 0
        for x in self:
            n += x
        return n


class Inherits(Pool):
    pass


class Guard[T]:
    v: T

    def __init__(self, v: T) -> None:
        # an open T may be a reference type, whose field store copies
        self.v = v  # tpyc: warning(/may copy T into field/)

    async def __aenter__(self) -> None:
        await asyncio.sleep(0)

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


class GuardSub[U](Guard[U]):
    def __init__(self, v: U) -> None:
        super().__init__(v)


class Ticks:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __aiter__(self) -> "Ticks":
        return self

    async def __anext__(self) -> int32:
        await asyncio.sleep(0)
        if self.i >= 2:
            raise StopAsyncIteration()
        self.i += 1
        return self.i


class TicksSub(Ticks):
    pass


# an inherited async method on a builtin subclass, and `async with` /
# `async for` through dunders a subclass inherits
async def inherited_async() -> None:
    p = Inherits()
    p.append(4)
    print("builtin-async:", await p.total())  # tpyc: ok
    gs = GuardSub[int32](3)
    async with gs:  # tpyc: ok
        print("async-with:", gs.v)
    async for t in TicksSub():  # tpyc: ok
        print("async-for:", t)


async def main_coro() -> None:
    c = Child[int32](7)
    r = await c.fetch()  # tpyc: ok
    print("got:", r)
    await inherited_async()


def main() -> None:
    asyncio.run(main_coro())


main()
