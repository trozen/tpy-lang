# Nested record ctors inside a lambda body (a position with no statement to
# hoist a temporary to): a ctor rvalue at a const-ref ctor slot, and a scalar
# at an Own[int32] ctor slot. Both values are fresh, so no alias is involved.
from typing import Callable
from tpy import Own, int32


class Inner:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Outer:
    n: int

    def __init__(self, i: Inner) -> None:
        self.n = i.v


def g(o: Outer) -> int:
    return o.n


def const_rvalue_arg() -> None:
    # `Inner(k)` binds Outer's const-ref slot inline.
    f: Callable[[int], int] = lambda k: g(Outer(Inner(k)))  # tpyc: ok
    print("const_rvalue_arg:", f(4))


class Cnt:
    n: int32

    def __init__(self, n: Own[int32]) -> None:
        self.n = n


def h(c: Cnt) -> int32:
    return c.n


def own_scalar_peel() -> None:
    # `Own` on a scalar is a no-op: `k + 1` passes as a plain scalar.
    f: Callable[[int32], int32] = lambda k: h(Cnt(k + 1))  # tpyc: ok
    print("own_scalar_peel:", f(4))


def main() -> None:
    const_rvalue_arg()
    own_scalar_peel()


main()
