# An element a user `__getitem__` lends (returns by reference) handed as a
# receiver or a field to a frame or a loop head: it is the bag's element,
# bound in place, so the mutation the frame makes shows in the bag after.
# The records are @nocopy, so a silent copy of an element cannot compile, and
# fresh allocations across each suspension reuse any storage freed early.
import asyncio
from typing import Iterator

from tpy import Own, int32, nocopy


def make_junk() -> Own[list[list[int32]]]:
    junk: list[list[int32]] = []
    for k in range(20):
        junk.append([k] * 20)
    return junk


@nocopy
class Inner:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    async def cbump(self) -> int32:
        await asyncio.sleep(0)
        self.x += 100
        return self.x


@nocopy
class Outer:
    inner: Inner

    def __init__(self, x: int32) -> None:
        self.inner = Inner(x)

    async def co(self) -> int32:
        await asyncio.sleep(0)
        self.inner.x += 7
        return self.inner.x


@nocopy
class Bag:
    items: list[Outer]

    def __init__(self) -> None:
        self.items = [Outer(1), Outer(2)]

    def __getitem__(self, i: int32) -> Outer:
        return self.items[i]


# The owned side: a `__getitem__` that builds its element hands each read
# a fresh `Outer` of its own, which the loop or the frame holds.
@nocopy
class OwnBag:
    base: int32

    def __init__(self) -> None:
        self.base = 30

    def __getitem__(self, i: int32) -> Own[Outer]:
        return Outer(self.base + i)


def bump(a: Inner) -> Iterator[int32]:
    junk = make_junk()
    a.x += 100 + len(junk) - 20
    yield a.x


def bump_outer(a: Outer) -> Iterator[int32]:
    junk = make_junk()
    a.inner.x += 50 + len(junk) - 20
    yield a.inner.x


async def co_outer(a: Outer) -> int32:
    await asyncio.sleep(0)
    junk = make_junk()
    a.inner.x += 3 + len(junk) - 20
    return a.inner.x


async def awaited(bag: Bag) -> None:
    # The lent element's field as an awaited method's receiver.
    print("lent_field_recv", await bag[0].inner.cbump())  # tpyc: ok
    print("lent_field_recv after", bag[0].inner.x)
    # The lent element itself as an awaited method's receiver.
    print("lent_recv", await bag[1].co())  # tpyc: ok
    print("lent_recv after", bag[1].inner.x)


def sync_head(bag: Bag) -> None:
    # A field of the lent element at a generator loop head.
    for x in bump(bag[0].inner):  # tpyc: ok
        print("lent_head", x)
    print("lent_head after", bag[0].inner.x)


def gen_body(bag: Bag) -> Iterator[int32]:
    # In a generator body: a field of the lent element and the lent element
    # itself at a generator loop head, bound in place.
    for x in bump(bag[0].inner):  # tpyc: ok
        yield x
    for x in bump_outer(bag[1]):  # tpyc: ok
        yield x


async def async_body(bag: Bag) -> None:
    # In a coroutine body: the same loop heads, and the lent element as an
    # awaited coroutine's argument.
    for x in bump(bag[0].inner):  # tpyc: ok
        print("async_head", x)
    for x in bump_outer(bag[1]):  # tpyc: ok
        print("async_head_elem", x)
    print("async_arg", await co_outer(bag[1]))  # tpyc: ok
    print("async after", bag[0].inner.x, bag[1].inner.x)


async def owned_element() -> None:
    for x in bump_outer(OwnBag()[0]):  # tpyc: ok
        print("owned_head", x)
    print("owned_await", await co_outer(OwnBag()[1]))  # tpyc: ok


def main() -> None:
    bag = Bag()
    asyncio.run(awaited(bag))
    sync_head(bag)
    for x in gen_body(bag):
        print("gen_body", x)
    print("gen_body after", bag[0].inner.x, bag[1].inner.x)
    asyncio.run(async_body(bag))
    asyncio.run(owned_element())
