# Returning a fresh-record tuple local at `tuple[Own[A], B]`: B would dangle.
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


def f() -> tuple[Own[A], B]:
    t = (A(1), B(2))
    # the borrowed element is the body's own storage
    return t  # tpyc: error(/dangl/)


def main() -> None:
    r = f()
    print(r[0].v, r[1].v)


main()
