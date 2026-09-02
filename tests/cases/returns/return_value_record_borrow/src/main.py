# A `T&` lvalue read returned at a VALUE record's by-value return slot -- an
# lvalue ternary, a container element, a call passthrough, a value variant.
from dataclasses import dataclass

from tpy import Int32, ValueType


@dataclass(frozen=True)
class F(ValueType):
    k: Int32

    def __add__(self, o: "F") -> "F":  # tpyc: ok
        # The ternary is a C++ lvalue over two lvalues; the by-value return
        # copy-constructs from it.
        return self if self.k >= o.k else o  # tpyc: ok


class D(ValueType):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class E(ValueType):
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def pick(a: D, b: D) -> D:
    return a if a.n >= b.n else b  # tpyc: ok


def first(xs: list[D]) -> D:
    return xs[0]  # tpyc: ok


def again(xs: list[D]) -> D:
    # A by-value record call result handed straight to the return slot.
    return first(xs)  # tpyc: ok


def widen(a: D, b: D) -> D | E:
    # The same lvalue ternary at a value-VARIANT return slot.
    return a if a.n >= b.n else b  # tpyc: ok


def main() -> None:
    a = F(1)
    b = F(2)
    print((a + b).k)
    lo = D(3)
    hi = D(5)
    print(pick(hi, lo).n)
    xs = [D(7), D(8)]
    print(first(xs).n)
    print(again(xs).n)
    got = first(xs)
    xs[0] = D(99)
    # Not a copy-vs-alias probe: rebinding a slot leaves an alias pointing at
    # the old element under CPython too, and TPy rejects every value-record
    # mutation, so the two are indistinguishable here.
    print(got.n, first(xs).n)
    w = widen(lo, hi)
    if isinstance(w, D):
        print(w.n)


main()
