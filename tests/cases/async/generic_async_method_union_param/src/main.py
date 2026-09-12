# Generic receiver's async method with an inferred deep-const union param:
# the sub-coro emplace arg keeps the const ptr-variant wrap like the
# non-generic sibling. The concurrently scheduled bump() mutates `a` while
# show() is suspended, observing that the frame borrows the arg, not a copy.
import asyncio
from tpy import int32, Own


class A:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class B:
    y: int32

    def __init__(self) -> None:
        self.y = 2


class Holder[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    async def show(self, u: A | B) -> int32:
        await asyncio.sleep(0.001)
        if isinstance(u, A):
            return u.x
        return u.y


class PlainHolder:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    async def show(self, u: A | B) -> int32:
        await asyncio.sleep(0.001)
        if isinstance(u, A):
            return u.x
        return u.y


async def bump(a: A) -> None:
    a.x = 41


async def main_coro() -> None:
    h = Holder(int32(5))
    a = A()
    t = asyncio.create_task(bump(a))
    print(await h.show(a))
    await t
    p = PlainHolder(7)
    b = B()
    print(await p.show(b))


def main() -> None:
    asyncio.run(main_coro())


main()
