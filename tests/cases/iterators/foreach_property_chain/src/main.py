# A @property for-each iterable is a getter CALL wearing field syntax, so the
# sync route admits a property TAIL at any chain depth and binds the returned
# container as an lvalue the loop outlives; a resumable frame admits one hop.
# The loop's loan names the RECEIVER, not the container the getter returned, so
# a mutation through that storage during the loop is unwarned
# (BUGS.md#property-iter-loan-misses-getter-storage).
# Every section writes through the loop variable and the owner observes the
# write, so a silent copy would show in output.txt.
from typing import Iterator

import asyncio

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Inner:
    boxes: list[Box]

    def __init__(self) -> None:
        self.boxes = [Box(1), Box(2)]

    @property
    def items(self) -> list[Box]:
        return self.boxes

    # method, one hop: the spelling a resumable frame also admits
    def bump_own(self) -> None:
        for b in self.items:  # tpyc: ok
            b.n += 1


class Outer:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()

    # method, two hops: the same chain spelled off `self`
    def bump(self) -> None:
        for b in self.inner.items:  # tpyc: ok
            b.n += 100


# free function, two hops
def bump_chain(o: Outer) -> None:
    for b in o.inner.items:  # tpyc: ok
        b.n += 10


# generator, one hop: the only property chain depth a frame admits
def bump_one_hop(i: Inner) -> Iterator[int32]:
    for b in i.items:  # tpyc: ok
        b.n += 1000
        yield b.n


# generator, two hops, body does NOT suspend: the route is chosen per LOOP, so
# this loop lowers on the sync route and takes its any-depth verdict
def gen_nosusp(o: Outer) -> Iterator[int32]:
    total = 0
    for b in o.inner.items:  # tpyc: ok
        b.n += 10000
        total += b.n
    yield total


async def tick() -> None:
    return


# async def, one hop: the await in the body is what puts the loop on the frame
# route, the async twin of the generator section above
async def bump_await(i: Inner) -> int32:
    total = 0
    for b in i.items:  # tpyc: ok
        await tick()
        b.n += 100000
        total += b.n
    return total


async def main_coro(i: Inner) -> None:
    print("async_one_hop:", await bump_await(i))


def main() -> None:
    o = Outer()
    bump_chain(o)
    print("free_fn:", o.inner.boxes[0].n, o.inner.boxes[1].n)
    o.bump()
    print("method:", o.inner.boxes[0].n, o.inner.boxes[1].n)
    o.inner.bump_own()
    print("one_hop:", o.inner.boxes[0].n, o.inner.boxes[1].n)
    for n in bump_one_hop(o.inner):
        print("frame_one_hop:", n)
    print("after_frame:", o.inner.boxes[0].n, o.inner.boxes[1].n)
    for t in gen_nosusp(o):
        print("frame_nosusp:", t)
    print("after_nosusp:", o.inner.boxes[0].n, o.inner.boxes[1].n)
    asyncio.run(main_coro(o.inner))
    print("after_async:", o.inner.boxes[0].n, o.inner.boxes[1].n)


main()
