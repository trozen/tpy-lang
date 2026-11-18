# Method positions: an inferred-const method of a generic class (its `T`
# parameter renders const), a method type parameter, and two `T` parameters in
# one closure; plus a reassigned ValueType-bounded parameter.
from typing import Callable
from tpy import ValueType

from fixtures import C, make_str


class Box[T]:
    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def mk(self, x: T) -> Callable[[], None]:
        def g() -> None:  # tpyc: ok
            # const method: the capture keeps the const the signature renders
            # (BUGS.md#open-t-param-const-method-binding-not-const)
            print("meth.T", x)
        return g

    def mk2[U](self, y: U) -> Callable[[], None]:
        def g() -> None:  # tpyc: ok
            print("meth.U", y)
        return g


def two[T, U](x: T, y: U) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("two", x, y)
    return g


def rebound[T: ValueType](x: T, y: T) -> Callable[[], None]:
    def g() -> None:  # tpyc: warning(/'x' is reassigned after the closure is created/)
        print("rebound", x)
    x = y
    return g


def main() -> None:
    b = Box(C(0))
    c2 = C(3)
    f2 = b.mk(c2)
    c2.n = 4
    f2()
    b.mk2(make_str(5))()
    c = C(1)
    f4 = two(c, make_str(5))
    c.n = 9
    f4()
    # Calling it would print the warned divergence (1 here, 2 in CPython).
    rebound(1, 2)
    print("rebound made")
