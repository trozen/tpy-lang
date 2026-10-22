# A *args element is analyzed once and received by the one receive step,
# at a plain and at a generic variadic parameter: a consuming call inside
# it is consumed once.
from typing import Self
from tpy import Own, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


def total(*xs: int32) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def count[T](*xs: T) -> int32:
    return len(xs)


def main() -> None:
    c = B(1)
    d = B(2)
    print("plain:", total(c.take().v, d.take().v, 3))  # tpyc: ok
    c = B(4)
    print("generic:", count(c.take().v, 5))  # tpyc: ok
    # (an unpacked list literal does not build yet,
    # BUGS.md#vararg-unpack-list-literal-ill-formed)


main()
