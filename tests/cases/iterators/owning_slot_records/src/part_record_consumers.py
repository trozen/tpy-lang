# Records with a consuming `__iter__` at the builtin consumers, combinators
# and other owning element slots: given up, a record is asked for its
# iterator once and that iterator is stepped (never its own `__iter__`), its
# elements handed on rather than copied.
from __future__ import annotations
from typing import Iterable, Iterator, Self
from tpy import Array, Own, copy, int32
from part_records import R, Bag, Only, N, NBag


class Steps:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Steps:
        print("record consumers iterator __iter__")
        return self

    def __next__(self) -> int32:
        if self.n == 2:
            raise StopIteration
        self.n += 1
        return self.n


class Source:
    def __iter__(self: Own[Self]) -> Own[Steps]:
        print("record consumers source __iter__")
        return Steps()


class NamedSteps(Steps):
    # Methods merely named `begin` / `end` do not make the iterator a C++
    # range: a consumer still steps its `__next__`, never its `__iter__`.
    def begin(self) -> int32:
        return 7

    def end(self) -> int32:
        return 8


class NamedSource:
    def __iter__(self: Own[Self]) -> Own[NamedSteps]:
        print("record consumers named source __iter__")
        return NamedSteps()


class Walk:
    items: list[R]

    def __init__(self, items: Own[list[R]]) -> None:
        self.items = items

    def __iter__(self) -> Walk:
        return self

    def __next__(self) -> Own[R]:
        if not self.items:
            raise StopIteration
        return self.items.pop(0)


class Holder:
    items: list[R]

    def __init__(self, xs: Iterable[Own[R]]) -> None:
        self.items = [x for x in xs]

    def add(self, xs: Iterable[Own[R]]) -> None:
        for x in xs:
            self.items.append(x)


def total(xs: Iterable[Own[R]]) -> Own[list[R]]:
    return [x for x in xs]


def comp_len(xs: Iterable[Own[R]]) -> int32:
    return len([x for x in xs])


def ntotal(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def nval(n: Own[N]) -> int32:
    return n.v


def side_effects() -> None:
    # Each consumer runs the source's `__iter__` once and steps what it
    # returned, as CPython's `iter()` does; a slice store then asks that
    # iterator's own `__iter__` too, as CPython's slice assignment does. Each
    # result is bound first: a print's own arguments write as they are
    # evaluated (BUGS.md#print-partial-output-on-raise).
    xs: list[int32] = []
    xs.extend(Source())
    print("record consumers extend", xs)
    listed = list(Source())
    print("record consumers list", listed)
    ordered = sorted(Source())
    print("record consumers sorted", ordered)
    ys: list[int32] = [9, 9, 9]
    ys[1:2] = Source()
    print("record consumers slice store", ys)
    named: list[int32] = []
    named.extend(NamedSource())
    print("record consumers named extend", named)
    named_listed = list(NamedSource())
    print("record consumers named list", named_listed)
    named_ordered = sorted(NamedSource())
    print("record consumers named sorted", named_ordered)
    zs: list[int32] = [9, 9, 9]
    zs[1:2] = NamedSource()
    print("record consumers named slice store", zs)


def builtins() -> None:
    # A dead or fresh record at a builtin collector is given up.
    o = Only()
    got = list(o)  # tpyc: ok
    got[0].v = 50
    print("record consumers list dead", got)
    print("record consumers sorted fresh", sorted(Only(), key=lambda r: -r.v))
    out: list[R] = []
    out.extend(Bag())
    print("record consumers extend fresh", out)


def combinators() -> None:
    # A combinator holding a fresh record steps its consuming iterator: a
    # @nocopy element lent and copied would not compile.
    print("record consumers map", list(map(nval, NBag())))


def callees() -> None:
    # Method and constructor callees take a fresh record over.
    h = Holder(Bag())  # tpyc: ok
    h.add(Only())  # tpyc: ok
    print("record consumers ctor and method", h.items)
    # `copy(b0)` is the caller's fresh copy: handed over, no element copied.
    b0 = Bag()
    print("record consumers copy", total(copy(b0)), len(b0.items))  # tpyc: ok


def arrays_and_iterators() -> None:
    # A dead @nocopy Array is handed over (moving each element).
    a: Array[N, 2] = [N(1), N(2)]
    print("record consumers nocopy array", ntotal(a))  # tpyc: ok
    # A comprehension over an owning parameter fed a lent self-iterator
    # steps its `__next__`.
    w = Walk([R(1), R(2)])
    print("record consumers walk comp", comp_len(w))
    print("record consumers walk left", len(w.items))


def main() -> None:
    side_effects()
    builtins()
    combinators()
    callees()
    arrays_and_iterators()
