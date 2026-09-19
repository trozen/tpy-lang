# An `Own[...]`-returning @property iterated inside a resumable frame: the read
# is the getter call, so the frame materializes it into its own `__for_src_N`
# slot and the iterators walk storage the frame owns across every suspension.
# The COPY is the subject: the caller's append between two resumes must not
# reach what the frame walks. A DEEPER chain still rejects in a frame
# (tests/cases/generators/error_gen_foreach_property_chain).
# The `self_yield` sections cover the opposite direction: a record YIELDING
# its own borrow-returning getter hands out the receiver's storage, so the
# consumer's mutation must reach the field -- a copy there would be silent.
# `self_yield_own` is the same position at an `Own`-returning getter, where
# the transfer is DECLARED and the consumer's append must therefore NOT reach
# the field; the two together are what says the slot verdict follows the
# getter's return convention rather than the syntax.
from typing import Iterator

import asyncio

from tpy import int32, Own


class Snap:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1, 2]

    @property
    def snapshot(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out

    # self_yield, Own flavour: the getter DECLARES the transfer, so what the
    # consumer receives is its own -- appending to it must NOT reach `_items`,
    # which is the opposite of the borrow flavours further down
    def yield_own(self) -> Iterator[Own[list[int32]]]:
        yield self.snapshot  # tpyc: ok


# generator: the frame owns the snapshot across the yield, so the caller's
# append below does not change what is walked
def gen_snapshot(s: Snap) -> Iterator[int32]:
    for x in s.snapshot:  # tpyc: ok
        yield x


async def tick() -> None:
    return


# async def whose loop body suspends: the same frame slot
async def drain(s: Snap) -> int32:
    n = 0
    for x in s.snapshot:  # tpyc: ok
        await tick()
        n += x
    return n


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    p: Point
    xs: list[int32]

    def __init__(self) -> None:
        self.p = Point(1)
        self.xs = [1, 2]

    @property
    def rec(self) -> Point:
        return self.p

    @property
    def data(self) -> list[int32]:
        return self.xs

    # self_yield, record flavour: the getter lends the receiver's storage,
    # so the yield hands out the same lvalue `yield self.p` would
    def yield_rec(self) -> Iterator[Point]:
        yield self.rec  # tpyc: ok

    # ... and the container flavour
    def yield_data(self) -> Iterator[list[int32]]:
        yield self.data  # tpyc: ok


def main() -> None:
    s = Snap()
    seen = 0
    for v in gen_snapshot(s):
        seen += v
        s._items.append(99)
    print("frame:", seen, len(s._items))
    print("async:", asyncio.run(drain(Snap())))
    own_src = Snap()
    for snap in own_src.yield_own():
        snap.append(42)
        print("self_yield_own:", len(snap))
    print("self_yield_own owner:", len(own_src._items))
    h = Holder()
    for pt in h.yield_rec():
        pt.x += 100
    for ys in h.yield_data():
        ys.append(3)
    print("self_yield:", h.p.x, len(h.xs), h.xs[2])


main()
