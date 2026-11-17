# Records with a consuming `__iter__(self: Own[Self])` at an owning element
# slot: a fresh or dead record is handed over and steps its own consuming
# iterator (the `&&` member); a live one is lent, its elements copied, warned.
from __future__ import annotations
import asyncio
from typing import Iterable, Iterator, Self
from tpy import Own, dispatch, int32, nocopy


class R:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __repr__(self) -> str:
        return f"R({self.v})"


class Bag:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(1), R(2)]

    @dispatch
    def __iter__(self) -> Iterator[R]:
        for r in self.items:
            yield r

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        # The receiver is the frame's own: each element is popped out of it.
        while self.items:
            yield self.items.pop(0)


class BagIter:
    items: list[R]

    def __init__(self, items: Own[list[R]]) -> None:
        self.items = items

    def __iter__(self) -> BagIter:
        return self

    def __next__(self) -> Own[R]:
        if not self.items:
            raise StopIteration
        return self.items.pop(0)


class IterBag:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(3), R(4)]

    @dispatch
    def __iter__(self) -> Iterator[R]:
        return iter(self.items)

    @dispatch
    def __iter__(self: Own[Self]) -> Own[BagIter]:
        return BagIter(self.items)


class Only:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(5), R(6)]

    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        while self.items:
            yield self.items.pop(0)


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


class NBag:
    items: list[N]

    def __init__(self) -> None:
        self.items = []
        self.items.append(N(1))
        self.items.append(N(2))

    @dispatch
    def __iter__(self) -> Iterator[N]:
        for n in self.items:
            yield n

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[N]]:
        while self.items:
            yield self.items.pop(0)


def total(xs: Iterable[Own[R]]) -> Own[list[R]]:
    return [x for x in xs]


def loop(xs: Iterable[Own[R]]) -> Own[list[R]]:
    out: list[R] = []
    for x in xs:
        out.append(x)
    return out


def gen(xs: Iterable[Own[R]]) -> Iterator[Own[R]]:
    for x in xs:
        yield x


async def atotal(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def ntotal(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def handed_over() -> None:
    # A fresh record is handed over: the callee steps its `&&` __iter__.
    print("records fresh", total(Bag()))  # tpyc: ok
    # A record at its last use is moved in, no copy.
    b = Bag()
    print("records dead", loop(b))  # tpyc: ok
    # A live record is lent: each element is copied, warned.
    b2 = Bag()
    out = total(b2)  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    print("records live", out[0].v, len(b2.items))
    # The callee mutates what it took over: the moved elements are its own.
    got = loop(Bag())
    got[1].v = 20
    print("records mutate", got)
    # Generator and coroutine callees take the record into their frame.
    print("records gen", [r.v for r in gen(Bag())])
    print("records async", asyncio.run(atotal(Bag())))


def iterator_class() -> None:
    # A consuming variant returning an iterator class beside a lending one.
    print("records iter class fresh", total(IterBag()))  # tpyc: ok
    ib = IterBag()
    print("records iter class dead", loop(ib))  # tpyc: ok
    print("records iter class lend", [r.v for r in IterBag()])


def consuming_only() -> None:
    # A record whose only __iter__ consumes: fresh and dead hand it over.
    print("records only fresh", total(Only()))  # tpyc: ok
    o = Only()
    print("records only dead", loop(o))  # tpyc: ok
    # A consuming `for` over a fresh or a dead one.
    for r in Only():
        print("records only for", r)
    o2 = Only()
    for r in o2:
        print("records only for dead", r)


def nocopy_elements() -> None:
    # @nocopy elements: a fallback to lending would not compile.
    print("records nocopy fresh", ntotal(NBag()))  # tpyc: ok
    nb = NBag()
    print("records nocopy dead", ntotal(nb))  # tpyc: ok


def consuming_for() -> None:
    # A consuming `for` over a fresh record moves each element out.
    keep: list[R] = []
    for r in Bag():
        keep.append(r)  # tpyc: ok
    print("records for fresh", keep)
    # A comprehension over a fresh one does too.
    print("records comp fresh", [p for p in Bag()])  # tpyc: ok
    # A lent one is iterated by its lending __iter__.
    b = Bag()
    print("records for lent", [q.v for q in b], len(b.items))


def main() -> None:
    handed_over()
    iterator_class()
    consuming_only()
    nocopy_elements()
    consuming_for()
