# A generic function reference instantiated at `T = Own[X]` instantiates the
# generic as a call does (its copy is warned), its bound asked of `X`; and a
# `reduce` seed read after the call, which the owned seed copies. Both copies
# differ from CPython's aliasing, by design.
from functools import reduce
from typing import Callable
from tpy import Own, ReferenceType, int32


class X:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[X]:
    return X(7)


def first_fn[T](f: Callable[[], T], xs: list[T]) -> T:
    return xs[0]


def first_ref[T: ReferenceType](f: Callable[[], T], xs: list[T]) -> T:
    return xs[0]


def own_acc(a: Own[X], b: X) -> Own[X]:
    a.n += b.n
    return a


def main() -> None:
    # function reference, unbounded: `T = Own[X]` copies the element out
    h: Callable[[Callable[[], Own[X]], list[X]], Own[X]] = first_fn  # tpyc: warning(/copies X into owned storage/)
    xs = [X(2)]
    r = h(make, xs)
    r.n = 5
    print("refs-unbounded", xs[0].n, r.n)
    # function reference, `T: ReferenceType` holds of `X` at `T = Own[X]`
    g: Callable[[Callable[[], Own[X]], list[X]], Own[X]] = first_ref  # tpyc: warning(/copies X into owned storage/)
    r2 = g(make, xs)
    r2.n = 6
    print("refs-bounded", xs[0].n, r2.n)
    # a named seed read after the call: the owned seed copies it (warned),
    # so `init` keeps its value where CPython's `reduce` mutates it
    ps = [X(1), X(2), X(3)]
    init = X(10)
    r3 = reduce(own_acc, ps, init)  # tpyc: warning(/copies X into owned storage/)
    r3.n += 100
    print("refs-reduce-named", r3.n, init.n)
