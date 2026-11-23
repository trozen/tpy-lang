# A part of a fresh value -- a field of an element, a method result, a
# slice of an element -- handed to a frame or a loop head: the fresh value
# it lives in is held for as long as the frame or the loop reads the part.
import asyncio
from typing import Iterator

from tpy import Own, int32, nocopy
from tplib.rc import Rc


@nocopy
class Inner:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@nocopy
class Outer:
    inner: Inner

    def __init__(self, x: int32) -> None:
        self.inner = Inner(x)


def mk_list_outer(n: int32) -> Own[list[Outer]]:
    res: list[Outer] = [Outer(n)]
    return res


def make_junk() -> Own[list[list[int32]]]:
    # Fresh allocations that reuse any storage a freed fresh value left.
    junk: list[list[int32]] = []
    for k in range(20):
        junk.append([k] * 20)
    return junk


def inner_items(a: Inner) -> Iterator[int32]:
    yield a.x


async def read_inner(s: Inner) -> int32:
    await asyncio.sleep(0.0)
    junk = make_junk()
    return s.x + len(junk) - 20


@nocopy
class Acc:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def get(self) -> "Acc":
        return self

    def sync_add(self, x: int32) -> int32:
        return self.base + x

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        junk = make_junk()
        self.base += x + len(junk) - 20
        return self.base


@nocopy
class Holder:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def make_acc(self, n: int32) -> Own[Acc]:
        return Acc(self.k + n)


def acc_items(a: Acc) -> Iterator[int32]:
    yield a.base


def make_strs(n: int32) -> Own[list[str]]:
    res: list[str] = ["ab" + str(n) * 30 + "xyz", "q"]
    return res


def str_chars(s: str) -> Iterator[str]:
    for c in s:
        yield c


async def show(s: str) -> int32:
    await asyncio.sleep(0.0)
    junk = make_junk()
    return len(s) + len(junk) - 20


async def field_of_element(n: int32) -> None:
    # A field of an element of a fresh list at an awaited argument: the
    # frame holds the list.
    print("root_field_awaited", await read_inner(mk_list_outer(n)[0].inner))  # tpyc: ok


def field_of_element_head(n: int32) -> None:
    # The same field at a loop head: the head holds the list for the loop.
    for x in inner_items(mk_list_outer(n)[0].inner):  # tpyc: ok
        junk = make_junk()
        print("root_field_head", x + len(junk) - 20)


def field_of_element_gen(n: int32) -> Iterator[int32]:
    # ... and at a loop head of a generator body: a frame holder keeps it.
    for x in inner_items(mk_list_outer(n)[0].inner):  # tpyc: ok
        junk = make_junk()
        yield x + len(junk) - 20


async def method_root(h: Holder) -> None:
    # An `Own[Acc]` method result as a receiver: held as the storage it is.
    print("root_method_sync", h.make_acc(1).sync_add(5))  # tpyc: ok
    print("root_method_awaited", await h.make_acc(1).add(5))  # tpyc: ok
    # A qualified call's fresh Deref wrapper: held as the storage it is.
    print("root_rc_awaited", await Rc.new(Acc(h.k)).add(5))  # tpyc: ok


def method_root_head(h: Holder) -> None:
    for x in acc_items(h.make_acc(1).get()):  # tpyc: ok
        junk = make_junk()
        print("root_method_head", x + len(junk) - 20)


async def slice_of_element(n: int32) -> None:
    # A slice of an element of a fresh list at a frame's `str` parameter,
    # which the frame copies in.
    print("root_slice_awaited", await show(make_strs(n)[0][1:8]))  # tpyc: ok


def slice_of_element_head(n: int32) -> None:
    for c in str_chars(make_strs(n)[0][1:4]):  # tpyc: ok
        junk = make_junk()
        print("root_slice_head", c, len(junk))


def main() -> None:
    asyncio.run(field_of_element(25))
    field_of_element_head(7)
    for x in field_of_element_gen(9):
        print("root_field_gen", x)
    h = Holder(10)
    asyncio.run(method_root(h))
    method_root_head(h)
    asyncio.run(slice_of_element(5))
    slice_of_element_head(6)
