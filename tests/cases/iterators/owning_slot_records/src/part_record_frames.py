# Records with a consuming `__iter__` iterated where a loop or clause gives
# them up: generator and coroutine frames take the same consuming route the
# function body takes, a tuple head unpacks each owned step, `own_iter()` of a
# record keeps its variant's owned iterator object, and a dead record's
# comprehension consumes it -- in a frame too.
from __future__ import annotations
import asyncio
from typing import Iterator, Self
from tpy import Own, int32, own_iter, uint64
from part_records import R, Bag, IterBag, Only


class Pairs:
    items: list[tuple[R, R]]

    def __init__(self) -> None:
        self.items = [(R(1), R(2)), (R(3), R(4))]

    def __iter__(self: Own[Self]) -> Iterator[Own[tuple[R, R]]]:
        while self.items:
            yield self.items.pop(0)


class Counts:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    # A consuming generator's frame owns its receiver: Send as the record is.
    def __iter__(self: Own[Self]) -> Iterator[int32]:  # tpyc: frame_send(yes)
        while self.items:
            yield self.items.pop(0)


class GenCounts[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    # A generic record's owned receiver may hold a `Ptr` or an `Rc` per
    # instantiation: one verdict for every `T` is non-Send.
    def __iter__(self: Own[Self]) -> Iterator[Own[T]]:  # tpyc: frame_send(no)
        while self.items:
            yield self.items.pop(0)


# Same for an owned generic parameter of a free generator.
def gen_owned_box[T](b: Own[GenCounts[T]]) -> Iterator[Own[T]]:  # tpyc: frame_send(no)
    for x in b:
        yield x


def gen_fresh() -> Iterator[Own[R]]:
    # A generator's loop over a fresh record steps its consuming iterator.
    for r in Only():  # tpyc: ok
        yield r


def gen_dead(keep: list[R]) -> Iterator[int32]:
    # A dead lending + consuming record in a frame is given up, as in a
    # function body: each element moves into `keep`, none is copied.
    b = Bag()
    for r in b:
        yield r.v
        keep.append(r)  # tpyc: ok


async def a_dead() -> int32:
    # A coroutine's loop over a dead record, suspending in its body.
    t = 0
    o = Only()
    for r in o:
        await asyncio.sleep(0)
        t += r.v
    return t


def gen_comp(keep: list[R]) -> Iterator[int32]:
    # A comprehension over a dead record held by the generator's frame
    # across a suspension consumes it.
    b = Bag()
    yield 0
    keep.extend([r for r in b])  # tpyc: ok
    yield len(keep)


async def a_comp() -> Own[list[R]]:
    b = Bag()
    await asyncio.sleep(0)
    return [r for r in b]  # tpyc: ok


class SizedIter:
    items: list[R]

    def __init__(self, items: Own[list[R]]) -> None:
        self.items = items

    def __iter__(self) -> SizedIter:
        return self

    def __next__(self) -> Own[R]:
        if not self.items:
            raise StopIteration
        return self.items.pop(0)

    def size(self) -> uint64:
        print("record frames size called")
        return 100


class SizedBag:
    def __iter__(self: Own[Self]) -> Own[SizedIter]:
        return SizedIter([R(5), R(6)])


def frames() -> None:
    got = list(gen_fresh())
    got[0].v = 50
    print("record frames gen fresh", got)
    keep: list[R] = []
    print("record frames gen dead", list(gen_dead(keep)), keep)
    print("record frames async dead", asyncio.run(a_dead()))
    keep2: list[R] = []
    print("record frames gen comp", list(gen_comp(keep2)), keep2)
    print("record frames async comp", asyncio.run(a_comp()))


def unpack() -> None:
    # A tuple head over a record whose steps are owned tuples, as the
    # scalar head (tuple equals scalar).
    keep: list[R] = []
    for a, b in Pairs():
        keep.append(a)  # tpyc: ok
        keep.append(b)
    print("record frames unpack for", keep)
    print("record frames unpack comp", [x for x, y in Pairs()])


def explicit() -> None:
    # `own_iter()` of a record whose consuming variant returns an owned
    # iterator object keeps that object, iterated in place.
    ib = IterBag()
    for r in own_iter(ib):
        print("record frames own_iter iterator class", r)


def dead_comprehension() -> None:
    # A comprehension over a dead consuming-only record consumes it.
    o = Only()
    print("record frames comp dead", [r.v for r in o])  # tpyc: ok
    # The consuming iterator is a user class: nothing asks its `size()` to
    # presize the result, as CPython asks nothing.
    print("record frames comp user iterator", [r for r in SizedBag()])


def send_frame() -> None:
    # The frame that owns a Send receiver runs as any consuming generator.
    print("record frames send frame", [n for n in Counts()])
    print("record frames generic frame", [n for n in GenCounts([3, 4])])
    print("record frames generic param frame",
          list(gen_owned_box(GenCounts(["a", "b"]))))


def main() -> None:
    send_frame()
    frames()
    unpack()
    explicit()
    dead_comprehension()
