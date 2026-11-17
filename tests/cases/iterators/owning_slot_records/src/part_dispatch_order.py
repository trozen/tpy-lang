# A `@dispatch` pair whose consuming variant (`self: Own[Self]`) is declared
# FIRST: on a live receiver TPy runs the lending variant, and so does the
# CPython stub, whatever the declaration order (and however `Self` is
# spelled). A fresh receiver whose elements are moved out takes the consuming
# variant in TPy; the stub runs the lending one, which the dispatch contract
# makes equivalent. A non-receiver `Self` parameter (`Own[Self]`, quoted,
# aliased or plain) matches only an instance of the class defining the
# method (a classmethod's too), in TPy and the stub alike, whichever variant
# is declared first.
from __future__ import annotations
import typing
from typing import Iterator, Self
from typing import Self as This
from tpy import Own, dispatch, int32
from part_records import R


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[int32]]:
        while self.items:
            yield self.items.pop(0)

    @dispatch
    def __iter__(self) -> Iterator[int32]:
        for r in self.items:
            yield r


class RBag:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(1), R(2)]

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        while self.items:
            yield self.items.pop(0)

    @dispatch
    def __iter__(self) -> Iterator[R]:
        for r in self.items:
            yield r


class Counter:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]

    @dispatch
    def size(self: Own[Self]) -> int32:
        n = len(self.items)
        self.items.clear()
        return n

    @dispatch
    def size(self) -> int32:
        return len(self.items)


class Quoted:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]

    @dispatch
    def count(self: Own["Self"]) -> int32:
        n = len(self.items)
        self.items.clear()
        return n

    @dispatch
    def count(self) -> int32:
        return len(self.items)


class Aliased:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]

    @dispatch
    def count(self: Own["This"]) -> int32:
        n = len(self.items)
        self.items.clear()
        return n

    @dispatch
    def count(self) -> int32:
        return len(self.items)

    @dispatch
    def total(self: Own["typing.Self"]) -> int32:
        n = len(self.items)
        self.items.clear()
        return n

    @dispatch
    def total(self) -> int32:
        return len(self.items)


class Merger:
    parts: list[Merger]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.parts = []

    @dispatch
    def merge(self, other: int32) -> int32:
        return self.n * other

    @dispatch
    def merge(self, other: Own[Self]) -> int32:
        n = self.n + other.n
        self.parts.append(other)
        return n


class MergerFirst:
    parts: list[MergerFirst]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.parts = []

    @dispatch
    def merge(self, other: Own[Self]) -> int32:
        n = self.n + other.n
        self.parts.append(other)
        return n

    @dispatch
    def merge(self, other: int32) -> int32:
        return self.n * other

    @dispatch
    def quoted(self, other: Own["Self"]) -> int32:
        n = self.n + other.n
        self.parts.append(other)
        return n

    @dispatch
    def quoted(self, other: int32) -> int32:
        return self.n * other

    @dispatch
    def aliased(self, other: Own[This]) -> int32:
        n = self.n + other.n
        self.parts.append(other)
        return n

    @dispatch
    def aliased(self, other: int32) -> int32:
        return self.n * other

    @dispatch
    def join(self, other: Self) -> int32:
        return self.n - other.n

    @dispatch
    def join(self, other: R) -> int32:
        return self.n * other.v


class SubMerger(MergerFirst):
    pass


class Maker:
    def __init__(self, n: int32) -> None:
        self.n = n

    @classmethod
    @dispatch
    def pick(cls, other: int32) -> int32:
        return 20

    @classmethod
    @dispatch
    def pick(cls, other: Self) -> int32:
        return 10


def main() -> None:
    b = Bag()
    t = 0
    # The subject: a live receiver iterates through the lending variant.
    for x in b:
        t += x
    print("order live iter", t, len(b.items))
    c = Counter()
    # The subject: a live receiver calls the lending variant.
    n = c.size()
    print("order live method", n, len(c.items))
    q = Quoted()
    # The subject: a quoted `Own["Self"]` is the consuming variant too.
    n = q.count()
    print("order quoted self", n, len(q.items))
    t = 0
    # The subject: a fresh receiver read only (no element moved out) lends.
    for x in Bag():
        t += x
    print("order fresh iter lent", t)
    out: list[R] = []
    # The subject: a fresh receiver whose elements are moved out iterates
    # through the consuming variant.
    for r in RBag():
        out.append(r)
    print("order fresh iter consuming", out)
    # The subject: a fresh receiver calls the consuming variant.
    print("order fresh method", Counter().size())
    a = Aliased()
    # The subject: `Own["This"]` (an alias of `Self`) is the consuming variant.
    n = a.count()
    print("order alias self", n, len(a.items))
    # The subject: `Own["typing.Self"]` is the consuming variant too.
    n = a.total()
    print("order qualified self", n, len(a.items))
    m = Merger(2)
    # The subject: `Own[Self]` on a non-receiver parameter matches a Merger.
    print("order own self param", m.merge(Merger(5)), m.merge(3))
    f = MergerFirst(2)
    # The subject: `Own[Self]` declared first matches only a MergerFirst.
    print("order own self first", f.merge(3), f.merge(MergerFirst(5)))
    # The subject: quoted and aliased `Own[Self]` declared first likewise.
    print("order own self spelled", f.quoted(3), f.quoted(MergerFirst(5)),
          f.aliased(4), f.aliased(MergerFirst(6)))
    # The subject: a plain `Self` parameter declared first likewise.
    print("order self first", f.join(R(4)), f.join(MergerFirst(1)))
    # The subject: `Self` names the defining class on a subclass receiver.
    print("order own self subclass", SubMerger(3).merge(2),
          SubMerger(3).merge(MergerFirst(1)))
    # The subject: a fresh receiver takes the consuming variant of a quoted
    # alias / qualified `Self` in TPy; the stub lends, to the same result.
    print("order alias self fresh", Aliased().count(), Aliased().total())
    # The subject: on a classmethod `Self` names the defining class too,
    # with the `int32` variant declared first.
    print("order classmethod self", Maker.pick(Maker(1)), Maker.pick(3))
