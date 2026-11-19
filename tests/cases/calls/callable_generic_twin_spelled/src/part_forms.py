# Mutually recursive generics (two and three members, entered at any
# member, and a figure-eight) report the copy only the `Own` instantiation
# makes; a `T | None` parameter at `T = P` lends; a list built from a local
# holding a call's open result at `T = P` copies with the hedge's warning.
# A tuple or `T | None` holding `T` at `T = Own[P]` is refused
# (calls/error_own_type_arg_composite_leaf*).
from typing import Protocol
from tpy import Fn, Own, int32


class Grow(Protocol):
    def grow(self, v: int32) -> int32: ...


class P:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def grow(self, v: int32) -> int32:
        self.xs.append(v)
        return len(self.xs)


def ident(p: P) -> P:
    return p


def grow_it[T: Grow](x: T) -> int32:
    return x.grow(9)


# a `T | None` parameter written through (lent at `T = P`)
def opt_mut[T: Grow](x: T | None) -> int32:  # tpyc: ok
    if x is not None:
        return grow_it(x)
    return 0


# mutually recursive generics: the stored element returned at `-> T`
def pick_a[T](xs: list[T], n: int32) -> T:
    if n == 0:
        return xs[0]
    return pick_b[T](xs, n - 1)


def pick_b[T](xs: list[T], n: int32) -> T:
    if n == 0:
        return xs[len(xs) - 1]
    return pick_a[T](xs, n - 1)


# a three-member cycle, entered at its middle member
def cyc_a[T](xs: list[T], n: int32) -> T:
    if n == 0:
        return xs[0]
    return cyc_b[T](xs, n - 1)


def cyc_b[T](xs: list[T], n: int32) -> T:
    return cyc_c[T](xs, n)


def cyc_c[T](xs: list[T], n: int32) -> T:
    return cyc_a[T](xs, n)


# a figure-eight: two cycles (f8_a <-> f8_b, f8_b <-> f8_c) sharing f8_b
def f8_a[T](xs: list[T], n: int32) -> T:
    if n == 0:
        return xs[0]
    return f8_b[T](xs, n - 1)


def f8_b[T](xs: list[T], n: int32) -> T:
    if n % 2 == 0:
        return f8_a[T](xs, n)
    return f8_c[T](xs, n - 1)


def f8_c[T](xs: list[T], n: int32) -> T:
    return f8_b[T](xs, n)


# a call's open result held in a local, as a list element
def lit[T](f: Fn[[T], T], a: T) -> Own[list[T]]:
    y = f(a)
    return [y]  # tpyc: warning(/may copy T into owned storage/)


def main() -> None:
    r = P(6)
    print("forms-opt-lend", opt_mut[P](r), r.xs)  # tpyc: ok
    ps = [P(1), P(2)]
    c = pick_b[Own[P]](ps, 1)  # tpyc: warning(/copies P into owned storage/)
    c.grow(10)
    print("forms-cycle", ps[0].xs, c.xs)
    # entered at the other member
    a = pick_a[Own[P]](ps, 1)  # tpyc: warning(/copies P into owned storage/)
    a.grow(11)
    print("forms-cycle-a", ps[1].xs, a.xs)
    # at `T = P` the element lends: no copy to report
    b = pick_b[P](ps, 0)  # tpyc: ok
    b.grow(12)
    print("forms-cycle-lend", ps[1].xs)
    k = cyc_b[Own[P]](ps, 1)  # tpyc: warning(/copies P into owned storage/)
    k.grow(13)
    print("forms-cycle-3", ps[0].xs, k.xs)
    # entered at the member only the shared one reaches
    g = f8_c[Own[P]](ps, 1)  # tpyc: warning(/copies P into owned storage/)
    g.grow(14)
    print("forms-cycle-8", ps[0].xs, g.xs)
    # at `T = P` the element copies (CPython's list shares `p`)
    p = P(4)
    ls = lit[P](ident, p)
    l0 = ls[0]
    l0.grow(11)
    print("forms-lit-lend", p.xs, l0.xs)
