# Classes whose argument is no `Own`: a
# construction completed through `Own[T]`, a class method binding `T = P`,
# an inferred generic call, a builtin `list[Own[P]]`, and an alias.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32
from fixtures import P, mk


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def fill(self, f: Callable[[], Own[T]]) -> None:
        self.items.append(f())

    def get(self) -> T:
        return self.items[0]

    @classmethod
    def build(cls, f: Callable[[], Own[T]]) -> Own[C[T]]:
        c = C[T]()
        c.items.append(f())
        return c


def make_c[T](x: T) -> Own[C[T]]:
    c = C[T]()
    c.items.append(x)  # tpyc: warning(/may copy T into owned storage/)
    return c


type Pair[A, B] = C[A] | list[B] | list[Pair[A, B]]


def mixed(t: Pair[int32, Own[P]]) -> int32:  # tpyc: ok
    # `A` reaches the class, `B` only a list, whose elements it owns anyway
    return 1


def main() -> None:
    f: Callable[[], Own[P]] = mk
    # `f`'s `Own[P]` result meets `fill`'s `Own[T]`: `T = P`, which lends
    c = C()
    c.fill(f)  # tpyc: ok
    h = c.get()
    h.n = 9
    print("pending", c.items[0].n)
    b = C.build(f)  # tpyc: ok
    bh = b.get()
    bh.n = 9
    print("classmethod", b.items[0].n)
    m = make_c(P(1))
    mh = m.get()
    mh.n = 9
    print("inferred", m.items[0].n)
    # a builtin container's `Own` marks element ownership
    xs = list[Own[P]]()  # tpyc: ok
    xs.append(P(1))
    xh = xs[0]
    xh.n = 9
    print("builtin", xs[0].n)
