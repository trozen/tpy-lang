# A reference-member tuple PARAM yielded by name aliases the caller's object
# (pointer borrow form in the generator frame): mutating the yielded element
# is visible in the caller, matching CPython. Regression guard for the
# param-sourced silent-copy divergence (the frame used to store the storage
# form, copying the member).
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(p: tuple[Int32, Box]) -> Iterator[tuple[Int32, Box]]:
    yield p


def main() -> None:
    b = Box(5)
    for pair in gen((1, b)):
        pair[1].val = 99
    print(b.val)


main()
