# Returning an owning call's tuple local at `tuple[Own[A], B]`: B would dangle.
# docs/LANGUAGE_FEATURES.md "Borrow Form vs Storage Form".
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def mkt() -> Own[tuple[A, B]]:
    return (A(1), B(2))


def f() -> tuple[Own[A], B]:
    t = mkt()
    # the owning call's elements are the body's storage at the return
    return t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    r = f()
    print(r[0].v, r[1].v)


main()
