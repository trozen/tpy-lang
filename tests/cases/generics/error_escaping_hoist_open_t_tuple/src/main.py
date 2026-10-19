# An if-hoisted open-T tuple local an escaping closure captures is refused, as
# before the open-T borrow form: its borrow form would point into the body.
from typing import Callable
from tpy import int32, Fn, Own


def read(a: int32) -> int32:
    return a


def mko[T](a: Own[T]) -> Own[T]:
    return a


def mk[T](a: T) -> tuple[T, int32]:
    return (a, 1)


def factory[T](a: Own[T], f: Fn[[T], int32], flag: bool) -> Callable[[], int32]:
    o = mko(a)
    p = mk(o)
    # the hoisted y is what the returned closure captures
    if flag:  # tpyc: error(/if\.hoist_type/)
        y = p
    else:
        y = p

    def h() -> int32:
        return f(y[0])
    out: Callable[[], int32] = h
    return out


def main() -> None:
    print(factory(123, read, True)())


main()
