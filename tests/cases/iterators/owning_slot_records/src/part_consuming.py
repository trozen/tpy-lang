# Every type with a consuming `__iter__` joins the one consuming entry: an
# Array at its last use is handed over, `own_iter()` takes a set, a dict and a
# record, and a comprehension over an owning parameter honours a list
# subclass's own `__iter__`.
from __future__ import annotations
from typing import Iterable, Iterator, Self
from tpy import Array, Own, int32, own_iter


class R:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __repr__(self) -> str:
        return f"R({self.v})"

    def __hash__(self) -> int:
        return self.v

    def __eq__(self, o: R) -> bool:
        return self.v == o.v


# The override CPython dispatches to; a `list[R]` reference still calls
# `list.__iter__` (the warned difference).
class Sevens(list[R]):  # tpyc: warning(/hides 'list.__iter__'/)
    def __iter__(self) -> Iterator[R]:
        for i in range(2):
            y = R(7)
            yield y


class Only:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(5), R(6)]

    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        while self.items:
            yield self.items.pop(0)


def take(xs: Iterable[Own[R]]) -> Own[list[R]]:
    return [x for x in xs]


def mk_rows() -> Own[list[list[R]]]:
    return [[R(1), R(2)], [R(3)]]


def mk_sevens() -> Own[Sevens]:
    xs = Sevens()
    xs.append(R(1))
    xs.append(R(2))
    return xs


def arrays() -> None:
    # An annotated Array at its last use is handed over: no element copy.
    a: Array[R, 2] = [R(1), R(2)]
    out = take(a)  # tpyc: ok
    out[0].v = 10
    print("consuming array", out)
    # A consuming `for` over a dead Array moves each element out.
    b: Array[R, 2] = [R(3), R(4)]
    keep: list[R] = []
    for r in b:
        keep.append(r)  # tpyc: ok
    print("consuming array for", keep)


def subclass() -> None:
    # The comprehension steps the subclass's own __iter__ (its elements are
    # copied, warned): `[R(7), R(7)]`, as CPython.
    print("consuming subclass", take(mk_sevens()))  # tpyc: warning(/copies R elements/)


def explicit() -> None:
    # own_iter() takes every type with a consuming __iter__.
    s = {R(1), R(2)}
    print("consuming own_iter set", sorted([r.v for r in own_iter(s)]))
    d = {R(3): 1}
    for k in own_iter(d):
        print("consuming own_iter dict", k)
    o = Only()
    print("consuming own_iter record", take(own_iter(o)))  # tpyc: ok
    xs = [1, 2]
    t = 0
    for x in own_iter(xs):
        t += x
    print("consuming own_iter ints", t)
    cs = [R(8), R(9)]
    # an unannotated list literal local is inferred an Array here, which
    # own_iter() takes too
    print("consuming own_iter literal local", take(own_iter(cs)))  # tpyc: ok
    # a comprehension clause variable owning its row moves it into the
    # owning slot, once per row
    print("consuming own_iter rows", [take(r) for r in own_iter(mk_rows())])  # tpyc: ok


def main() -> None:
    arrays()
    subclass()
    explicit()
